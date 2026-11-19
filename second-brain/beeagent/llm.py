"""LLM 客户端：OpenAI 兼容接口（DeepSeek / Qwen / Kimi …）+ 离线确定性 mock。

- OPENAI_COMPATIBLE_BASE_URL + OPENAI_API_KEY + MODEL 三项都设置才走真实模型；
- 否则（或 BEEAGENT_MOCK=1）每个模块改用确定性的规则实现，保证 demo 离线可跑；
- 真实调用失败时打印警告并回退 mock，不中断流程。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from typing import Optional

from .config import PROMPTS_DIR, Settings

PROMPT_FILES = {
    "summarize": "5.1_summarize", "facts": "5.2_facts", "todos": "5.3_todo_suggestions",
    "daily": "5.4_daily", "grow": "5.5_grow", "chat": "5.6_chat_agent", "journal": "5.7_journal",
}


def load_prompt(key: str) -> str:
    """读取 prompts/ 下的还原提示词；// 开头的注释行不发给模型。"""
    raw = (PROMPTS_DIR / f"{PROMPT_FILES.get(key, key)}.txt").read_text(encoding="utf-8")
    return "\n".join(l for l in raw.splitlines() if not l.startswith("//")).strip()


def render(_tpl: str, /, **values) -> str:
    """{{var}} → 值；dict/list 转 JSON；未提供的变量替换为「（无）」。"""
    def sub(m):
        v = values.get(m.group(1).strip(), "（无）")
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False, indent=1)
        return str(v)
    return re.sub(r"\{\{\s*([\w\.]+)\s*\}\}", sub, _tpl)


def extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mock = not settings.use_llm

    @property
    def mode(self) -> str:
        return "mock" if self.mock else f"llm:{self.settings.model}"

    def _post(self, body: dict) -> Optional[dict]:
        req = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.settings.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            print(f"[warn] LLM 调用失败，本步回退 mock：{e}", file=sys.stderr)
            return None

    def chat_json(self, prompt: str, system: str = "只输出 JSON，不要解释。",
                  temperature: float = 0.2):
        """返回解析后的 JSON；mock 或失败返回 None（调用方回退规则实现）。"""
        if self.mock:
            return None
        data = self._post({"model": self.settings.model, "temperature": temperature,
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": prompt}]})
        if not data:
            return None
        try:
            return extract_json(data["choices"][0]["message"]["content"])
        except (KeyError, ValueError, IndexError) as e:
            print(f"[warn] LLM 输出不是合法 JSON，本步回退 mock：{e}", file=sys.stderr)
            return None

    def chat_tools(self, messages: list, tools: list) -> Optional[dict]:
        """OpenAI function-calling 单步；返回 assistant message dict。"""
        if self.mock:
            return None
        data = self._post({"model": self.settings.model, "temperature": 0.2,
                           "messages": messages, "tools": tools, "tool_choice": "auto"})
        try:
            return data["choices"][0]["message"] if data else None
        except (KeyError, IndexError):
            return None
