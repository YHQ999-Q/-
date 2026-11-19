"""用本地假 OpenAI 兼容服务测"真实模型"代码路径（提示词渲染、JSON 解析、工具调用循环），全程离线。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beeagent import chat, grow  # noqa: E402
from beeagent.config import FIXTURES_DIR, get_settings  # noqa: E402
from beeagent.llm import LLM  # noqa: E402
from beeagent.pipeline import ingest, process  # noqa: E402
from beeagent.store import Store  # noqa: E402

SEEN = []


class FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        prompt = msgs[-1]["content"] if msgs[-1]["role"] != "tool" else ""
        SEEN.append(prompt[:40])
        msg = {"role": "assistant", "content": ""}
        if body.get("tools"):
            if msgs[-1]["role"] == "tool":
                msg["content"] = "根据 11 月 13 日的记录：老师要求录课时不要出现学校标识。（出处：会话）"
            else:
                msg["tool_calls"] = [{"id": "t1", "type": "function",
                                      "function": {"name": "search", "arguments": json.dumps({"query": "录课"})}}]
        elif "判断场景" in prompt:
            msg["content"] = json.dumps({"scene": "lecture", "short_summary": "测试课：标题",
                                         "summary_markdown": "## 要记住的\n- 日期待通知", "keywords": ["录课", "课程"]})
        elif "长期有用的事实" in prompt:
            msg["content"] = json.dumps({"facts": [{"op": "create", "text": f"测试事实{i}", "tags": ["education", "x"]} for i in range(5)]})
        elif "执行助理" in prompt:
            msg["content"] = "```json\n" + json.dumps({"suggestions": [{"emoji": "📝", "text": "提交作业", "details": "老师说的", "alarm_at": None}]}) + "\n```"
        elif "成长教练" in prompt:
            msg["content"] = json.dumps([{"title": f"洞察{i}", "description": "d" * 160, "suggested_actions": ["a", "b"]} for i in range(9)])
        else:
            msg["content"] = "{}"
        out = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


class LLMPathTest(unittest.TestCase):
    def test_real_mode_with_fake_server(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {"OPENAI_COMPATIBLE_BASE_URL": f"http://127.0.0.1:{srv.server_port}/v1", "OPENAI_API_KEY": "k",
               "MODEL": "fake", "BEEAGENT_MOCK": ""}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            s = get_settings()
            llm = LLM(s)
            self.assertFalse(llm.mock)
            with tempfile.TemporaryDirectory() as d:
                st = Store(Path(d) / "x.db")
                ingest(st, str(FIXTURES_DIR / "raw" / "2026-11-13"))
                convs = process(st, llm, s, log=lambda *_: None)
                self.assertEqual(convs[0].short_summary, "测试课：标题")
                n = sum(1 for f in st.list_facts() if f.source_conversation_id == convs[0].id)
                self.assertEqual(n, 3)                                   # 5 条被截到 3 条
                self.assertTrue(all(set(f.tags) == {"education"} for f in st.list_facts()))
                ins, _ = grow.run_grow(st, llm, "2026-11-14T03:23:00")
                self.assertEqual(len(ins), 8)                            # 9 条截到 8 条
                self.assertTrue(all(len(i.suggested_actions) == 1 for i in ins))
                ans, trace = chat.ask(st, llm, "录课有什么要求", now="2026-11-14T10:00:00")
                self.assertEqual(trace[0]["tool"], "search")
                self.assertIn("录课", ans)
                st.close()
        finally:
            srv.shutdown()
            srv.server_close()
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
