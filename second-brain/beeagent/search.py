"""检索（§2、§4.4）：BM25 覆盖会话 / 每日回顾 / 事实 / 语音笔记；语义检索只覆盖会话（这里是桩）；
相关会话 = 关键词重叠预计算并持久化到 conversation.suggested_links（对应源码注释
"Related conversations are precomputed server-side (AI-extracted keywords, ranked, persisted)"）。
todos 与 insights 和 Bee 一样不可检索，只能列表读取。"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .store import Store
from .textutil import STOP_WORDS, tokenize  # noqa: F401  (tokenize 供外部复用)

QUESTION_NOISE = re.compile(
    r"(请问|帮我|给我|一下|我上次|上次|之前|以前|那次|那天|有没有|是不是|是什么|是啥|什么时候|什么|哪天|哪里|"
    r"怎么样|怎么|如何|为什么|谁|吗|呢|吧|啊|说过|讲过|提到|提过|聊过|聊到|关于|记得|我的|我们|我|的|了|过|是|在|和|跟)")


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.N = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.N) if self.N else 0.0
        self.tf = [Counter(d) for d in docs]
        df = Counter(t for d in self.tf for t in d)
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def score(self, q: list[str], i: int) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in q:
            if t not in tf:
                continue
            f = tf[t]
            s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
        return s

    def top(self, q: list[str], k: int = 5) -> list[tuple[int, float]]:
        scored = [(i, self.score(q, i)) for i in range(self.N)]
        return sorted([x for x in scored if x[1] > 0], key=lambda x: -x[1])[:k]


@dataclass
class Hit:
    kind: str          # conversation / daily / fact / journal
    id: str
    title: str
    date: str
    score: float
    coverage: float    # 查询词元在该文档里出现的比例（用于"找不到就说没找到"）
    snippet: str = ""
    extra: dict = field(default_factory=dict)


def clean_query(q: str) -> str:
    q = QUESTION_NOISE.sub(" ", q)
    q = re.sub(r"[？?！!，,。.、：:；;\"“”'‘’（）()\s]+", " ", q)
    return " ".join(w for w in q.split() if w not in STOP_WORDS)


def _snippet(text_lines: list[str], qtok: set[str], idf: dict | None = None) -> str:
    """取命中查询词元 IDF 之和最高的一行（摘要行或带时间的原句）。"""
    best, bs = "", 0.0
    idf = idf or {}
    for line in text_lines:
        if not line.strip():
            continue
        toks = set(tokenize(line))
        s = sum(idf.get(t, 1.0) for t in toks & qtok) + min(len(line), 60) / 600
        if s > bs:
            best, bs = line, s
    return best.strip()


def _corpus(store: Store, filter_: str, since: str, until: str):
    docs = []
    if filter_ in ("all", "conversations"):
        for c in store.list_conversations(since, until, with_utts=True, state="COMPLETED"):
            lines = [c.short_summary, *c.summary.splitlines(), " ".join(c.keywords)] + \
                    [f"[{u.spoken_at[11:16]}] {u.text}" for u in c.utterances]
            docs.append(("conversation", c.id, c.short_summary or c.id, c.start_time[:16], lines))
    if filter_ in ("all", "daily"):
        for d in store.list_dailies(since[:10], until[:10] if until != "9999" else "9999"):
            docs.append(("daily", d.date, d.short_summary, d.date, [d.short_summary, *d.summary.splitlines()]))
    if filter_ in ("all", "facts"):
        for f in store.list_facts():
            if since <= (f.created_at or "") < until or not since:
                docs.append(("fact", str(f.id), f.text[:40], f.created_at[:10], [f.text]))
    if filter_ in ("all", "journals"):
        for j in store.list_journals():
            docs.append(("journal", str(j.id), j.text[:30], j.created_at[:16],
                         [j.text, j.aiResponse.get("cleanedUpText", "")]))
    return docs


def search(store: Store, query: str, filter_: str = "all", limit: int = 5, since: str = "",
           until: str = "9999", mode: str = "keyword", min_coverage: float = 0.5) -> list[Hit]:
    """BM25 检索。只返回查询词元覆盖率 ≥ min_coverage 的结果，避免"随便匹配到一个字"就当作有记录。"""
    if mode == "semantic":
        return semantic_search(store, query, limit, since, until)
    docs = _corpus(store, filter_, since, until)
    if not docs:
        return []
    qtok = tokenize(clean_query(query)) or tokenize(query)
    if not qtok:
        return []
    bm = BM25([tokenize("\n".join(d[4])) for d in docs])
    hits = []
    for i, s in bm.top(qtok, k=max(limit * 3, 10)):
        kind, did, title, date, lines = docs[i]
        cov = sum(1 for t in set(qtok) if t in bm.tf[i]) / len(set(qtok))
        if cov < min_coverage:
            continue
        hits.append(Hit(kind, did, title, date, round(s, 3), round(cov, 2), _snippet(lines, set(qtok), bm.idf)))
    return hits[:limit]


def semantic_search(store: Store, query: str, limit: int = 5, since: str = "", until: str = "9999"):
    """语义检索桩：Bee 的 /neural 只覆盖会话。接向量库（pgvector / Milvus + bge-m3 等）后在这里实现。
    现在退化为只搜会话的 BM25，并在结果 extra 里标注。"""
    hits = search(store, query, "conversations", limit, since, until, mode="keyword")
    for h in hits:
        h.extra["note"] = "semantic 未实现，已退化为 BM25"
    return hits


# ---------------- 相关会话（预计算） ----------------
def _kwset(c) -> set[str]:
    toks = set(c.keywords)
    for k in c.keywords:
        toks |= set(tokenize(k))
    return toks


def compute_related(store: Store, top_k: int = 3) -> int:
    convs = store.list_conversations(state="COMPLETED")
    n = 0
    for c in convs:
        a = _kwset(c)
        scored = []
        for o in convs:
            if o.id == c.id:
                continue
            b = _kwset(o)
            if not a or not b:
                continue
            j = len(a & b) / len(a | b)
            if j > 0.05:
                shared = [k for k in c.keywords if k in o.keywords] or sorted(a & b, key=len, reverse=True)[:3]
                scored.append({"id": o.id, "short_summary": o.short_summary, "score": round(j, 3),
                               "shared_keywords": shared[:5]})
        c.suggested_links = sorted(scored, key=lambda x: -x["score"])[:top_k]
        store.save_conversation(c, replace_utterances=False)
        n += len(c.suggested_links)
    return n
