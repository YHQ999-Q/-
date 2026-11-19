"""离线单元测试：python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ["BEEAGENT_MOCK"] = "1"          # 有 key 也强制离线
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beeagent import chat, facts, grow, search  # noqa: E402
from beeagent.config import FACT_TAGS, FIXTURES_DIR, get_settings  # noqa: E402
from beeagent.llm import LLM  # noqa: E402
from beeagent.models import Conversation, Daily, Fact, Utterance  # noqa: E402
from beeagent.pipeline import ingest, process, seed_facts  # noqa: E402
from beeagent.segment import build_conversations  # noqa: E402
from beeagent.store import Store  # noqa: E402
from beeagent.textutil import parse_when  # noqa: E402


def utts(offsets, base="2026-11-13T08:00:00", text="今天我们讲泰勒原理和课程目标"):
    t0 = datetime.fromisoformat(base)
    return [Utterance(text=f"{text}{i}", spoken_at=(t0 + timedelta(seconds=o)).isoformat(), start=0, end=2.0)
            for i, o in enumerate(offsets)]


class SegmentTest(unittest.TestCase):
    def test_split_by_silence(self):
        # 0..100 秒连续说话，静音 400 秒后再说 → 两段；静音 300 秒 → 一段
        self.assertEqual(len(build_conversations(utts([0, 50, 100, 502, 520]), gap_s=360)), 2)
        self.assertEqual(len(build_conversations(utts([0, 50, 100, 402, 420]), gap_s=360)), 1)

    def test_preroll_and_tail(self):
        c = build_conversations(utts([0, 60]), gap_s=360, preroll_s=30)[0]
        self.assertEqual(c.start_time, "2026-11-13T08:00:30")       # 首句早于 start_time 30 秒
        self.assertEqual(c.end_time, "2026-11-13T08:07:02")         # 最后一句结束 + 360 秒
        self.assertEqual(c.state, "COMPLETED")

    def test_capturing_until_silence(self):
        c = build_conversations(utts([0, 60]), gap_s=360, now="2026-11-13T08:03:00")[0]
        self.assertEqual(c.state, "CAPTURING")
        self.assertEqual(c.end_time, "")


class FactsTest(unittest.TestCase):
    def test_at_most_three_and_fixed_tags(self):
        llm = LLM(get_settings())
        conv = Conversation(id="c1", start_time="2026-11-13T08:00:00", scene="lecture",
                            short_summary="课程与教学论：泰勒原理", keywords=["泰勒原理", "课程目标"],
                            utterances=utts(range(0, 100, 5)))
        orig = facts._mock
        facts._mock = lambda c: [{"text": f"第{i}条足够不同的长期事实内容{'甲乙丙丁戊己'[i]}", "tags": ["education", "bogus"]}
                                 for i in range(6)]
        try:
            out = facts.extract_facts(conv, llm, existing=[])
        finally:
            facts._mock = orig
        self.assertLessEqual(len(out), 3)
        self.assertTrue(all(f.status == "pending" and not f.confirmed for f in out))
        self.assertTrue(all(set(f.tags) <= set(FACT_TAGS) for f in out))

    def test_sensitive_not_extracted(self):
        llm = LLM(get_settings())
        conv = Conversation(id="c2", start_time="2026-11-13T08:00:00", utterances=utts([0]))
        orig = facts._mock
        facts._mock = lambda c: [{"text": "最近经期不规律", "tags": ["health"]}]
        try:
            self.assertEqual(facts.extract_facts(conv, llm, existing=[]), [])
        finally:
            facts._mock = orig


class GrowTest(unittest.TestCase):
    def test_validate_exactly_one_action(self):
        items = [{"title": "A", "description": "d", "suggested_actions": ["x", "y", "z"]},
                 {"title": "B", "description": "d", "suggested_actions": []},
                 {"title": "C", "description": "d", "suggested_actions": "only one"}]
        out = grow.validate(items)
        self.assertEqual([i.title for i in out], ["A", "C"])
        self.assertTrue(all(len(i.suggested_actions) == 1 for i in out))


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.s = get_settings()
        cls.store = Store(Path(cls.tmp.name) / "t.db")
        cls.llm = LLM(cls.s)
        ingest(cls.store, str(FIXTURES_DIR / "raw"))
        seed_facts(cls.store)
        cls.convs = process(cls.store, cls.llm, cls.s, now="2026-11-14T03:23:00", log=lambda *_: None)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def test_six_conversations_from_stream(self):
        self.assertEqual(len(self.convs), 6)    # 三天的连续流被静音规则切回 6 段

    def test_facts_per_conversation(self):
        for c in self.convs:
            n = sum(1 for f in self.store.list_facts() if f.source_conversation_id == c.id)
            self.assertLessEqual(n, 3)

    def test_lecture_rule_applied(self):
        lec = [c for c in self.convs if c.scene == "lecture"]
        self.assertTrue(lec)
        self.assertTrue(all("config:rule-lecture-01" in c.rules_applied for c in lec))
        self.assertTrue(any("日期待通知" in c.summary for c in lec))

    def test_grow_each_insight_one_action(self):
        ins, _ = grow.run_grow(self.store, self.llm, "2026-11-14T03:23:00")
        self.assertGreater(len(ins), 0)
        self.assertLessEqual(len(ins), 8)
        self.assertTrue(all(len(i.suggested_actions) == 1 for i in ins))

    def test_bm25_expected_doc(self):
        hits = search.search(self.store, "泰勒原理", filter_="conversations")
        self.assertTrue(hits)
        self.assertTrue(hits[0].id.startswith("c20261113"))   # 课程与教学论（11/13）

    def test_chat_does_not_fabricate(self):
        ans, trace = chat.ask(self.store, self.llm, "我上次说的火星移民计划是什么？", now="2026-11-14T10:00:00")
        self.assertTrue(ans.startswith(chat.NOT_FOUND))
        self.assertEqual(trace[0]["tool"], "search")

    def test_chat_answers_with_date_and_source(self):
        ans, _ = chat.ask(self.store, self.llm, "录课有什么要求", now="2026-11-14T10:00:00")
        self.assertIn("2026-11-13", ans)
        self.assertIn("出处", ans)


class BM25UnitTest(unittest.TestCase):
    def test_ranking_and_empty_store(self):
        bm = search.BM25([search.tokenize(t) for t in ["后现代课程理论与多尔", "留学申请与雅思", "小米手环采集"]])
        self.assertEqual(bm.top(search.tokenize("雅思申请"))[0][0], 1)
        st = Store(":memory:")
        st.add_fact(Fact(text="偏好简洁的纪要", tags=["preferences"], confirmed=True))
        ans, _ = chat.ask(st, LLM(get_settings()), "量子力学期中考试是哪天", now="2026-11-14T10:00:00")
        st.close()
        self.assertTrue(ans.startswith(chat.NOT_FOUND))


class TimeTest(unittest.TestCase):
    def test_vague_date_not_filled(self):
        ref = datetime(2026, 11, 13, 9)
        self.assertIsNone(parse_when("录课时间之后再通知", ref))
        self.assertEqual(parse_when("下周五晚上8点交", ref), datetime(2026, 11, 20, 20, 0))


if __name__ == "__main__":
    unittest.main()
