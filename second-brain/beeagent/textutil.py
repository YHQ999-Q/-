"""纯标准库的中文文本工具：分词（CJK 二元组 + 英文词）、关键词抽取、时间表达解析。

mock 模式靠这些规则跑通全流程；接入真实模型后，关键词等由 LLM 输出（§5.1 keywords）。"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

CJK = r"\u4e00-\u9fff"
_RUN = re.compile(rf"[{CJK}]+|[A-Za-z][A-Za-z0-9\-\+\.]*|\d+(?:\.\d+)?")
STOP_CHARS = set("的了是在我你他她它们这那就都也还要会说个吗呢吧啊嗯呃哦哈噢唉一不有和与或很把被对给让从到上下来去么什怎为因所以但而且如果然后其实啥些里过着得地之其该想看做好再又才只没被比更最候")
STOP_WORDS = {"我们", "他们", "你们", "就是", "然后", "这个", "那个", "什么", "怎么", "因为", "所以",
              "但是", "如果", "其实", "可以", "一个", "一些", "这种", "那种", "觉得", "还是", "已经",
              "时候", "知道", "东西", "问题", "现在", "自己", "大家", "同学", "老师", "比如", "对吧",
              "出现", "开始", "大概", "方面", "属于", "相关", "一下", "这样", "那样", "这些", "那些",
              "可能", "需要", "差不多", "估计", "具体", "刚刚", "内容", "二零", "代表", "不同", "部分", "应该", "主要", "进行", "包括", "或者", "以及", "非常", "特别", "一样"}
LATIN_STOP = set("""ok the a an and of to is in it unknown that this these those you your we our they
their he she his her i me my so if or but not no yes be are was were been have has had do does did
can could would should will just like what which who when where why how about with for from on at by
as there here then than very really actually also okay yeah um uh one two all some any more most
into out up down over only even because well get got know think right let us them its it's don't
i'm you're that's going want make see say said thing things kind lot""".split())


def cjk_bigrams(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def tokenize(text: str) -> list[str]:
    """BM25 用：中文二元组 + 小写英文词 + 数字。"""
    out = []
    for m in _RUN.finditer(text or ""):
        t = m.group(0)
        if re.match(rf"[{CJK}]", t):
            out.extend(cjk_bigrams(t))
        else:
            tl = t.lower().strip(".")
            if tl and tl not in LATIN_STOP:
                out.append(tl)
    return out


def _clean_gram(g: str) -> bool:
    return not (g[0] in STOP_CHARS or g[-1] in STOP_CHARS or g in STOP_WORDS
                or sum(ch in STOP_CHARS for ch in g) > len(g) // 2)


def keywords(texts: list[str], k: int = 8, min_count: int = 2) -> list[str]:
    """确定性关键词：按"出现在几句话里"计数的 2–4 字 n-gram + 英文词 + 《》里的书名，保留最长不冗余的。"""
    df: Counter = Counter()
    boosted: Counter = Counter()
    for t in texts:
        grams = set()
        for b in re.findall(r"[《“\"]([^》”\"]{2,12})[》”\"]", t):
            boosted[b] += 1
        for m in _RUN.finditer(t):
            run = m.group(0)
            if re.match(rf"[{CJK}]", run):
                for n in (2, 3, 4):
                    for i in range(len(run) - n + 1):
                        g = run[i:i + n]
                        if _clean_gram(g):
                            grams.add(g)
            elif len(run) >= 2 and run.lower() not in LATIN_STOP and (len(run) >= 4 or run.isupper()) and not run.isdigit():
                grams.add(run if run.isupper() else run[0].upper() + run[1:])
        df.update(grams)
    cand = {g: c for g, c in df.items() if c >= (min_count if len(g) <= 2 else min_count + 1)}
    # 去冗余：短词几乎总出现在某个长词里，就保留长词
    for g in sorted(cand, key=len):
        for h in cand:
            if len(h) > len(g) and g in h and cand[h] >= 0.7 * cand[g]:
                cand[g] = 0
                break
    score = {g: c * (1 + 0.35 * (len(g) - 2)) for g, c in cand.items() if c}
    for b, c in boosted.items():
        score[b] = score.get(b, 0) + 3 * c
    ranked = sorted(score.items(), key=lambda x: (-x[1], x[0]))
    def overlaps(a: str, b: str) -> bool:
        if a in b or b in a:
            return True
        return any(a.endswith(b[:i]) or b.endswith(a[:i]) for i in range(2, min(len(a), len(b))))

    top = [g for g, _ in ranked[: k * 3]]
    merged = []
    for g in top:   # 两个高频短词能拼成一个出现过的长词（永恒+主义）就用长词
        for h in top:
            if g != h and (g + h) in df and df[g + h] >= 2:
                g = g + h
                break
        merged.append(g)
    out: list[str] = []
    for g in merged:
        if any(overlaps(g, o) for o in out):
            continue
        out.append(g)
        if len(out) >= k:
            break
    return out


# ---------------- 时间表达 → ISO（用于 alarm_at） ----------------
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
VAGUE_TIME = re.compile(r"(另行通知|之后再通知|再通知|会再告诉|再告诉大家|到时候再说|待定|时间待)")


def _num(s: str) -> Optional[int]:
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def parse_when(text: str, ref: datetime) -> Optional[datetime]:
    """只在出现明确日期时返回时间；"另行通知"等返回 None（§5.3：日期不确定不要填）。"""
    if VAGUE_TIME.search(text):
        return None
    day: Optional[datetime] = None
    if m := re.search(r"(\d{1,2})月(\d{1,2})[日号]", text):
        try:
            day = ref.replace(month=int(m[1]), day=int(m[2]))
        except ValueError:
            day = None
    elif "明天" in text or "明早" in text:
        day = ref + timedelta(days=1)
    elif "后天" in text:
        day = ref + timedelta(days=2)
    elif m := re.search(r"(下)?(?:周|星期|礼拜)([一二三四五六日天])", text):
        if m[1]:   # 下周X：下一个自然周的周X
            delta = 7 - ref.weekday() + _WD[m[2]]
        else:      # 周X：本周或之后最近的周X
            delta = (_WD[m[2]] - ref.weekday()) % 7 or 7
        day = ref + timedelta(days=delta)
    elif "今晚" in text or "今天" in text:
        day = ref
    if day is None:
        return None
    hour, minute = 23, 59
    if m := re.search(r"(早上|上午|中午|下午|晚上|今晚|明早)?\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})[点:：](半|\d{2})?", text):
        h = _num(m[2])
        if h is not None and h <= 24:
            if m[1] in ("下午", "晚上", "今晚") and h < 12:
                h += 12
            hour, minute = h % 24, (30 if m[3] == "半" else int(m[3]) if m[3] else 0)
    elif "明早" in text:
        hour, minute = 8, 0
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def short(text: str, n: int = 60) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"
