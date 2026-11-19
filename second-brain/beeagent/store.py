"""SQLite 存储（MVP）。表结构对齐 Bee 资源：conversations / utterances / facts / todos /
todo_suggestions / dailies / insights / journals，外加一张原始语音流表 stream（尚未切分的 utterance）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import (Conversation, Daily, Fact, Insight, Journal, Todo, TodoSuggestion,
                     Utterance)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stream (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, speaker TEXT, spoken_at TEXT,
  duration REAL, source TEXT, conversation_id TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, start_time TEXT, end_time TEXT, state TEXT, short_summary TEXT,
  summary TEXT, scene TEXT, keywords TEXT, suggested_links TEXT, rules_applied TEXT,
  template_version TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS utterances (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, text TEXT, speaker TEXT,
  start REAL, "end" REAL, spoken_at TEXT, realtime INTEGER, source TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, tags TEXT, confirmed INTEGER,
  source_conversation_id TEXT, status TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS todo_suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, emoji TEXT, text TEXT, details TEXT, alarm_at TEXT,
  source_conversation_id TEXT, kind TEXT, status TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, completed INTEGER, alarm_at TEXT,
  created_at TEXT, source_suggestion_id INTEGER
);
CREATE TABLE IF NOT EXISTS dailies (
  date TEXT PRIMARY KEY, short_summary TEXT, summary TEXT, conversations_count INTEGER,
  conversations TEXT, calendar_summary TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT, suggested_actions TEXT,
  generated_at TEXT, evidence TEXT
);
CREATE TABLE IF NOT EXISTS journals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, aiResponse TEXT, created_at TEXT, state TEXT
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def reset(self):
        for t in ["stream", "conversations", "utterances", "facts", "todo_suggestions", "todos",
                  "dailies", "insights", "journals"]:
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.execute("DELETE FROM sqlite_sequence")
        self.conn.commit()

    # ---------- 原始语音流 ----------
    def add_stream(self, utts: list[Utterance]) -> int:
        self.conn.executemany(
            "INSERT INTO stream(text,speaker,spoken_at,duration,source) VALUES (?,?,?,?,?)",
            [(u.text, u.speaker, u.spoken_at, max(u.end - u.start, 0.0), u.source) for u in utts])
        self.conn.commit()
        return len(utts)

    def unsegmented_stream(self) -> list[Utterance]:
        rows = self.conn.execute(
            "SELECT * FROM stream WHERE conversation_id IS NULL ORDER BY spoken_at, id").fetchall()
        return [Utterance(text=r["text"], speaker=r["speaker"], spoken_at=r["spoken_at"],
                          start=0.0, end=r["duration"], id=r["id"], source=r["source"]) for r in rows]

    def mark_stream(self, ids: list[int], cid: str):
        self.conn.executemany("UPDATE stream SET conversation_id=? WHERE id=?", [(cid, i) for i in ids])
        self.conn.commit()

    # ---------- 会话 ----------
    def save_conversation(self, c: Conversation, replace_utterances: bool = True):
        c.updated_at = c.updated_at or now_iso()
        self.conn.execute(
            "INSERT OR REPLACE INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (c.id, c.start_time, c.end_time, c.state, c.short_summary, c.summary, c.scene,
             _j(c.keywords), _j(c.suggested_links), _j(c.rules_applied), c.template_version,
             c.updated_at))
        if replace_utterances:
            self.conn.execute("DELETE FROM utterances WHERE conversation_id=?", (c.id,))
            self.conn.executemany(
                'INSERT INTO utterances(conversation_id,text,speaker,start,"end",spoken_at,realtime,source)'
                " VALUES (?,?,?,?,?,?,?,?)",
                [(c.id, u.text, u.speaker, u.start, u.end, u.spoken_at, int(u.realtime), u.source)
                 for u in c.utterances])
        self.conn.commit()

    def _conv(self, r, with_utts: bool) -> Conversation:
        c = Conversation(id=r["id"], start_time=r["start_time"], end_time=r["end_time"],
                         state=r["state"], short_summary=r["short_summary"] or "",
                         summary=r["summary"] or "", scene=r["scene"] or "",
                         keywords=json.loads(r["keywords"] or "[]"),
                         suggested_links=json.loads(r["suggested_links"] or "[]"),
                         rules_applied=json.loads(r["rules_applied"] or "[]"),
                         template_version=r["template_version"] or "", updated_at=r["updated_at"])
        if with_utts:
            rows = self.conn.execute(
                "SELECT * FROM utterances WHERE conversation_id=? ORDER BY spoken_at, id", (c.id,))
            c.utterances = [Utterance(text=u["text"], speaker=u["speaker"], start=u["start"],
                                      end=u["end"], spoken_at=u["spoken_at"],
                                      realtime=bool(u["realtime"]), id=u["id"], source=u["source"])
                            for u in rows]
        return c

    def get_conversation(self, cid: str, with_utts: bool = True) -> Optional[Conversation]:
        r = self.conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._conv(r, with_utts) if r else None

    def list_conversations(self, since: str = "", until: str = "9999", with_utts=False,
                           state: Optional[str] = None) -> list[Conversation]:
        q = "SELECT * FROM conversations WHERE start_time>=? AND start_time<?"
        args = [since, until]
        if state:
            q += " AND state=?"
            args.append(state)
        rows = self.conn.execute(q + " ORDER BY start_time", args).fetchall()
        return [self._conv(r, with_utts) for r in rows]

    # ---------- 事实 ----------
    def add_fact(self, f: Fact) -> int:
        f.created_at = f.created_at or now_iso()
        cur = self.conn.execute(
            "INSERT INTO facts(text,tags,confirmed,source_conversation_id,status,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (f.text, _j(f.tags), int(f.confirmed), f.source_conversation_id,
             "confirmed" if f.confirmed else f.status, f.created_at))
        self.conn.commit()
        return cur.lastrowid

    def list_facts(self, confirmed: Optional[bool] = None, include_rejected=False) -> list[Fact]:
        q, args = "SELECT * FROM facts WHERE 1=1", []
        if confirmed is not None:
            q += " AND confirmed=?"
            args.append(int(confirmed))
        if not include_rejected:
            q += " AND status!='rejected'"
        return [Fact(text=r["text"], tags=json.loads(r["tags"]), confirmed=bool(r["confirmed"]),
                     source_conversation_id=r["source_conversation_id"], status=r["status"],
                     created_at=r["created_at"], id=r["id"])
                for r in self.conn.execute(q + " ORDER BY id", args)]

    def set_fact_status(self, fid: int, status: str) -> bool:
        cur = self.conn.execute("UPDATE facts SET status=?, confirmed=? WHERE id=?",
                                (status, int(status == "confirmed"), fid))
        self.conn.commit()
        return cur.rowcount > 0

    # ---------- 待办建议 / 待办 ----------
    def add_suggestion(self, s: TodoSuggestion) -> int:
        s.created_at = s.created_at or now_iso()
        cur = self.conn.execute(
            "INSERT INTO todo_suggestions(emoji,text,details,alarm_at,source_conversation_id,kind,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (s.emoji, s.text, s.details, s.alarm_at, s.source_conversation_id, s.kind, s.status,
             s.created_at))
        self.conn.commit()
        return cur.lastrowid

    def list_suggestions(self, status: Optional[str] = "open") -> list[TodoSuggestion]:
        q, args = "SELECT * FROM todo_suggestions", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        return [TodoSuggestion(**{k: r[k] for k in r.keys()})
                for r in self.conn.execute(q + " ORDER BY id", args)]

    def get_suggestion(self, sid: int) -> Optional[TodoSuggestion]:
        r = self.conn.execute("SELECT * FROM todo_suggestions WHERE id=?", (sid,)).fetchone()
        return TodoSuggestion(**{k: r[k] for k in r.keys()}) if r else None

    def set_suggestion_status(self, sid: int, status: str):
        self.conn.execute("UPDATE todo_suggestions SET status=? WHERE id=?", (status, sid))
        self.conn.commit()

    def add_todo(self, t: Todo) -> int:
        t.created_at = t.created_at or now_iso()
        cur = self.conn.execute(
            "INSERT INTO todos(text,completed,alarm_at,created_at,source_suggestion_id) VALUES (?,?,?,?,?)",
            (t.text, int(t.completed), t.alarm_at, t.created_at, t.source_suggestion_id))
        self.conn.commit()
        return cur.lastrowid

    def list_todos(self, open_only: bool = False) -> list[Todo]:
        q = "SELECT * FROM todos" + (" WHERE completed=0" if open_only else "") + " ORDER BY id"
        return [Todo(text=r["text"], completed=bool(r["completed"]), alarm_at=r["alarm_at"],
                     created_at=r["created_at"], source_suggestion_id=r["source_suggestion_id"],
                     id=r["id"]) for r in self.conn.execute(q)]

    # ---------- 每日 / 洞察 / 语音笔记 ----------
    def save_daily(self, d: Daily):
        d.created_at = d.created_at or now_iso()
        self.conn.execute("INSERT OR REPLACE INTO dailies VALUES (?,?,?,?,?,?,?)",
                          (d.date, d.short_summary, d.summary, d.conversations_count,
                           _j(d.conversations), d.calendar_summary, d.created_at))
        self.conn.commit()

    def get_daily(self, date: str) -> Optional[Daily]:
        r = self.conn.execute("SELECT * FROM dailies WHERE date=?", (date,)).fetchone()
        if not r:
            return None
        return Daily(date=r["date"], short_summary=r["short_summary"], summary=r["summary"],
                     conversations_count=r["conversations_count"],
                     conversations=json.loads(r["conversations"]),
                     calendar_summary=r["calendar_summary"], created_at=r["created_at"])

    def list_dailies(self, since: str = "", until: str = "9999") -> list[Daily]:
        rows = self.conn.execute("SELECT date FROM dailies WHERE date>=? AND date<? ORDER BY date",
                                 (since, until)).fetchall()
        return [self.get_daily(r["date"]) for r in rows]

    def add_insight(self, i: Insight) -> int:
        cur = self.conn.execute(
            "INSERT INTO insights(title,description,suggested_actions,generated_at,evidence) VALUES (?,?,?,?,?)",
            (i.title, i.description, _j(i.suggested_actions), i.generated_at, _j(i.evidence)))
        self.conn.commit()
        return cur.lastrowid

    def list_insights(self, since: str = "") -> list[Insight]:
        return [Insight(title=r["title"], description=r["description"],
                        suggested_actions=json.loads(r["suggested_actions"]),
                        generated_at=r["generated_at"], evidence=json.loads(r["evidence"]), id=r["id"])
                for r in self.conn.execute(
                    "SELECT * FROM insights WHERE generated_at>=? ORDER BY id", (since,))]

    def add_journal(self, j: Journal) -> int:
        j.created_at = j.created_at or now_iso()
        cur = self.conn.execute("INSERT INTO journals(text,aiResponse,created_at,state) VALUES (?,?,?,?)",
                                (j.text, _j(j.aiResponse), j.created_at, j.state))
        self.conn.commit()
        return cur.lastrowid

    def list_journals(self) -> list[Journal]:
        return [Journal(text=r["text"], aiResponse=json.loads(r["aiResponse"]),
                        created_at=r["created_at"], state=r["state"], id=r["id"])
                for r in self.conn.execute("SELECT * FROM journals ORDER BY id")]

    def counts(self) -> dict:
        out = {}
        for t in ["stream", "conversations", "utterances", "facts", "todo_suggestions", "todos",
                  "dailies", "insights", "journals"]:
            out[t] = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
