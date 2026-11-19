"""输入 / 转写桩（§3.2）。

本复刻不做真正的 ASR：接受已有转写（transcript.txt 或 JSON utterances）作为"实时稿"写入原始语音流。
会话关闭后由 `batch_retranscribe` 模拟 Bee 的"整段批量重转写并替换"（realtime=true → false）。

接真实 ASR：实现 ASRBackend.transcribe(audio_path) → list[Utterance]，例如
  - FunASR（Paraformer-zh / SenseVoice，中文首选，课堂可加热词）  TODO
  - Whisper large-v3（多语言）                                    TODO
  - 说话人分离：3D-Speaker / pyannote + 声纹注册（补 Bee 最大短板）   TODO
接 Bee 作为采集端（以后再做，现在不调用）：`bee changed` 增量 / `bee stream` SSE 的
new-utterance、update-conversation 事件 → 转成 Utterance 喂给 ingest，下游全部走本项目。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .models import Utterance

_LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*([^:：]{1,30})[:：]\s?(.*)$")
_DATE_IN_PATH = re.compile(r"(\d{4}-\d{2}-\d{2})")


class ASRBackend:
    """接口：音频 → utterances。"""

    def transcribe(self, audio_path: str) -> list[Utterance]:  # pragma: no cover
        raise NotImplementedError("TODO：接 FunASR / Whisper；本骨架只接受现成转写")


def _estimate_duration(text: str) -> float:
    """没有 end 时间时按语速估一个时长：中文约 4.5 字/秒。"""
    return max(1.0, round(len(text) / 4.5, 1))


def parse_transcript_txt(path: Path, date: str | None = None) -> list[Utterance]:
    """解析 `[HH:MM:SS] Speaker: text`；日期取参数或路径里的 YYYY-MM-DD。"""
    date = date or (m.group(1) if (m := _DATE_IN_PATH.search(str(path))) else None)
    if not date:
        raise ValueError(f"{path}: 需要日期（--date 或路径里含 YYYY-MM-DD）")
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line.strip())
        if not m or not m.group(5).strip():
            continue
        ts = datetime.fromisoformat(f"{date}T{m[1]}:{m[2]}:{m[3]}")
        text = m.group(5).strip()
        out.append(Utterance(text=text, speaker=m.group(4).strip(), spoken_at=ts.isoformat(),
                             start=0.0, end=_estimate_duration(text), realtime=True,
                             source=Path(path).stem))
    return out


def parse_utterances_json(path: Path) -> list[Utterance]:
    """JSON：[{text, speaker, spoken_at(ISO 或毫秒), start?, end?}] 或 Bee 会话 JSON（transcriptions[0].utterances）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "transcriptions" in data:
        data = data["transcriptions"][0]["utterances"]
    out = []
    for u in data:
        sa = u.get("spoken_at")
        if isinstance(sa, (int, float)):
            sa = datetime.fromtimestamp(sa / 1000).isoformat(timespec="seconds")
        dur = (u.get("end", 0) - u.get("start", 0)) if "end" in u else _estimate_duration(u["text"])
        out.append(Utterance(text=u["text"], speaker=u.get("speaker", "Unknown"), spoken_at=sa,
                             start=0.0, end=max(dur, 0.5), realtime=True, source=Path(path).stem))
    return out


def load_input(path: str, date: str | None = None) -> list[Utterance]:
    """单个文件或目录（递归读 *.txt / *.json）。"""
    p = Path(path)
    files = sorted(p.rglob("*")) if p.is_dir() else [p]
    out: list[Utterance] = []
    for f in files:
        if f.suffix == ".txt":
            out += parse_transcript_txt(f, date)
        elif f.suffix == ".json" and f.name not in {"meta.json", "facts.json", "todos.json"}:
            out += parse_utterances_json(f)
    out.sort(key=lambda u: u.spoken_at)
    return out


def batch_retranscribe(utts: list[Utterance]) -> list[Utterance]:
    """模拟 Bee 会话关闭后的整段重转写：替换实时稿，realtime=False（真实 ASR 在这里重跑整段音频）。"""
    if not utts:
        return utts
    t0 = datetime.fromisoformat(utts[0].spoken_at)
    for u in utts:
        rel = (datetime.fromisoformat(u.spoken_at) - t0).total_seconds()
        dur = u.end - u.start if u.end > u.start else _estimate_duration(u.text)
        u.start, u.end, u.realtime = rel, rel + dur, False
    return utts


def add_seconds(iso: str, s: float) -> str:
    return (datetime.fromisoformat(iso) + timedelta(seconds=s)).isoformat(timespec="seconds")
