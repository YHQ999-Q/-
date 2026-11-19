"""配置：环境变量 + 可选 .env（无第三方依赖）。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = PACKAGE_DIR / "prompts"
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "fixtures"
DATA_DIR = PROJECT_ROOT / "data"

# Bee 事实标签固定小词表（§3.4 实测，11 个）
FACT_TAGS = ["general", "technology", "preferences", "hobbies", "entertainment", "health",
             "food", "sports", "politics", "education", "transcription"]
SCENES = ["lecture", "meeting", "one_on_one", "casual", "media", "mixed"]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class Settings:
    base_url: str
    api_key: str
    model: str
    db_path: Path
    timezone: str
    user_name: str
    silence_gap_s: int
    preroll_s: int
    force_mock: bool

    @property
    def use_llm(self) -> bool:
        return bool(self.api_key and self.base_url and self.model) and not self.force_mock


def get_settings() -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env")
    env = os.environ.get
    return Settings(
        base_url=env("OPENAI_COMPATIBLE_BASE_URL", "").rstrip("/"),
        api_key=env("OPENAI_API_KEY", ""),
        model=env("MODEL", ""),
        db_path=Path(env("BEEAGENT_DB") or str(DATA_DIR / "beeagent.db")),
        timezone=env("BEEAGENT_TZ", "Asia/Shanghai"),
        user_name=env("BEEAGENT_USER_NAME", "用户"),
        silence_gap_s=int(env("BEEAGENT_SILENCE_GAP_S", "360")),   # §3.1：≈6 分钟静音关会话
        preroll_s=int(env("BEEAGENT_PREROLL_S", "30")),            # §3.1：约 30 秒预录回填
        force_mock=env("BEEAGENT_MOCK", "").lower() in {"1", "true", "yes"},
    )


def load_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def scene_templates() -> dict:
    return load_json(CONFIG_DIR / "scene_templates.json", {})


def user_rules() -> dict:
    return load_json(CONFIG_DIR / "user_rules.json", {"rules": []})
