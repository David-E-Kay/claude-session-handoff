#!/usr/bin/env python3
"""Chapter index: record every session into SQLite and look it up in layers.

A chapter is one real user prompt plus everything Claude did until the next one.
`record` is a Stop hook; `list` / `show` / `output` / `status` are the lookup
ladder a fresh session uses only when its handoff lacks a fact. Raw tool output
is never stored — only an outline of actions. Stdlib only.
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
NOT_PROMPTS = ("<task-notification>", "<local-command-", "[Request interrupted by user")
ARG_KEYS = ("file_path", "notebook_path", "command", "pattern", "description", "url", "skill", "query", "prompt")
FILE_TOOLS = {"Read": False, "Edit": True, "Write": True, "NotebookEdit": True}  # value = changes the file
DECISION_CAP = 500


def blocks(entry):
    c = (entry.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def block_text(bs):
    return "\n".join(b.get("text", "") for b in bs if isinstance(b, dict) and b.get("type") == "text")


def result_text(block):
    c = block.get("content")
    return c if isinstance(c, str) else block_text(c) if isinstance(c, list) else ""


def tag(s, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", s, re.S)
    return m.group(1).strip() if m else ""


def one_line(s, cap):
    s = " ".join(s.split())
    return s if len(s) <= cap else s[: cap - 1] + "…"


SKIP = ("skip", "", "")


def classify(entry):
    """Sort one transcript line: ("prompt", text, "") starts a chapter; ("unknown", shape, sample)
    is a user line no rule recognises (logged for review, never a prompt); anything else is SKIP."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return SKIP
    content = (entry.get("message") or {}).get("content")
    other = []  # non-text block types, e.g. "image"
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return SKIP
        other = [str(b.get("type")) for b in content if isinstance(b, dict) and b.get("type") != "text"]
        content = block_text(content)
    if not isinstance(content, str):
        content = ""
    s = REMINDER.sub("", content).strip()
    if not s:
        if content.strip() and not other:
            return SKIP  # only system reminders
        return ("unknown", "no-text", ",".join(other)[:200] or "empty")
    if s.startswith(NOT_PROMPTS):
        return SKIP
    if s.startswith(("<command-message>", "<command-name>")):
        cmd = " ".join(x for x in (tag(s, "command-name"), tag(s, "command-args")) if x)
        if cmd:
            return ("prompt", cmd, "")
    m = re.match(r'<scheduled-task name="([^"]*)"', s)
    if m:
        return ("prompt", f"scheduled: {m.group(1)}", "")
    if s.startswith("<") and not s.startswith(("<!-- attach", "<!-- reply")) and "<pasted_content" not in s:
        return ("unknown", re.match(r"<([^\s>]*)", s).group(1) or "<", one_line(s, 200))
    return ("prompt", s, "")


def prompt_text(entry):
    """The user's prompt if this transcript line starts a chapter, else None."""
    kind, value, _ = classify(entry)
    return value if kind == "prompt" else None


def arg_summary(name, inp):
    if name == "AskUserQuestion":
        return " | ".join(q.get("question", "") for q in inp.get("questions") or [] if isinstance(q, dict))[:200]
    for k in ARG_KEYS:
        if isinstance(inp.get(k), str):
            return inp[k][:80]
    return ""


def feed(chapters, entry):
    """Apply one transcript line to `chapters` (last one is open). True if anything changed."""
    prompt = prompt_text(entry)
    ts = entry.get("timestamp", "")
    if prompt is not None:
        chapters.append({"prompt": prompt, "replies": [], "actions": [], "decisions": [], "files": {},
                         "branch": entry.get("gitBranch", ""), "started_at": ts, "ended_at": ts})
        return True
    if not chapters or entry.get("isSidechain"):
        return False
    ch = chapters[-1]
    changed = False
    if entry.get("type") == "assistant":
        for b in blocks(entry):
            if b.get("type") == "text" and b.get("text", "").strip():
                ch["replies"].append(b["text"].strip())
                changed = True
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", ""), b.get("input") or {}
                ch["actions"].append({"tool": name, "arg": arg_summary(name, inp),
                                      "tool_use_id": b.get("id", ""), "failed": False})
                path = inp.get("file_path") or inp.get("notebook_path")
                if name in FILE_TOOLS and isinstance(path, str):
                    ch["files"][path] = ch["files"].get(path, False) or FILE_TOOLS[name]
                changed = True
    elif entry.get("type") == "user":
        s = block_text(blocks(entry)) if blocks(entry) else (entry.get("message") or {}).get("content") or ""
        if isinstance(s, str) and s.lstrip().startswith("<task-notification>"):
            ch["actions"].append({"tool": "task-notification", "arg": tag(s, "summary")[:80] or "background task finished",
                                  "tool_use_id": "", "failed": False})
            changed = True
        for b in blocks(entry):
            if b.get("type") != "tool_result":
                continue
            for a in ch["actions"]:
                if a["tool_use_id"] == b.get("tool_use_id"):
                    a["failed"] = bool(b.get("is_error"))
                    if a["tool"] == "AskUserQuestion":
                        ch["decisions"].append(result_text(b)[:DECISION_CAP])
                    changed = True
    if changed:
        ch["ended_at"] = ts or ch["ended_at"]
    return changed


DB_PATH = Path(os.environ.get("CHAPTER_INDEX_DB") or Path.home() / ".claude" / "chapter-index.db")
LOG_PATH = DB_PATH.with_name("chapter-index.log")
LOG_CAP = 256 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY, tool TEXT NOT NULL DEFAULT 'claude', project TEXT,
    transcript_path TEXT, offset INTEGER NOT NULL DEFAULT 0, open_chapter_id INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS chapters(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, project TEXT,
    branch TEXT, started_at TEXT, ended_at TEXT, prompt TEXT, replies TEXT, actions TEXT, decisions TEXT,
    files TEXT, plain_line TEXT, ai_line TEXT, revision INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS chapters_project ON chapters(project, id);
CREATE TABLE IF NOT EXISTS unknown_shapes(shape TEXT PRIMARY KEY, count INTEGER NOT NULL, first_seen TEXT,
    last_seen TEXT, session_id TEXT, sample TEXT, reviewed INTEGER NOT NULL DEFAULT 0);
"""
JSON_COLS = ("replies", "actions", "decisions", "files")


def now():
    return datetime.now().isoformat(timespec="seconds")


def log_error(msg):
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_CAP:
            LOG_PATH.write_text(LOG_PATH.read_text(encoding="utf-8")[-LOG_CAP // 2:], encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{now()} ERROR {' '.join(str(msg).split())}\n")
    except OSError:
        pass


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
    con.executescript(SCHEMA)
    return con


def project_key(cwd):
    p = str(Path(cwd).resolve()).replace("\\", "/") if cwd else ""
    if os.name == "nt":
        p = p.lower()
    return re.split(r"/\.claude/worktrees/", p, maxsplit=1)[0]


def plain_line(ch):
    first = re.split(r"(?<=[.!?])\s", ch["replies"][-1].strip(), maxsplit=1)[0] if ch["replies"] else ""
    files = sorted(ch["files"], key=lambda f: not ch["files"][f])
    names = [Path(f).name + ("*" if ch["files"][f] else "") for f in files[:5]]
    line = f'"{one_line(ch["prompt"], 100)}"'
    if first:
        line += f" — {one_line(first, 160).rstrip('.')}"
    return line + (f". Files: {', '.join(names)}" if names else "")


def load_chapter(con, chapter_id):
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())
    con.row_factory = None
    for k in JSON_COLS:
        row[k] = json.loads(row[k] or ("{}" if k == "files" else "[]"))
    return row


def read_new(path, offset):
    """Complete JSON lines after `offset`, and the offset just past the last complete line."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    entries = []
    for raw in data[:end].splitlines():
        try:
            e = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(e, dict):
            entries.append(e)
    return entries, offset + end, bool(data[:end].strip())


def save_chapter(con, ch, session_id, project):
    vals = {k: json.dumps(ch[k]) for k in JSON_COLS}
    common = (ch["branch"], ch["started_at"], ch["ended_at"], ch["prompt"], vals["replies"], vals["actions"],
              vals["decisions"], vals["files"], plain_line(ch))
    if ch.get("id"):
        con.execute("UPDATE chapters SET branch=?, started_at=?, ended_at=?, prompt=?, replies=?, actions=?, "
                    "decisions=?, files=?, plain_line=?, revision=revision+1 WHERE id=?", (*common, ch["id"]))
    else:
        ch["id"] = con.execute("INSERT INTO chapters(branch, started_at, ended_at, prompt, replies, actions, decisions, "
                               "files, plain_line, revision, session_id, project) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                               (*common, session_id, project)).lastrowid
    return ch["id"], con.execute("SELECT revision FROM chapters WHERE id=?", (ch["id"],)).fetchone()[0]


def note_unknown(con, shape, sample, session_id, seen):
    con.execute("INSERT INTO unknown_shapes(shape, count, first_seen, last_seen, session_id, sample) "
                "VALUES (?,1,?,?,?,?) ON CONFLICT(shape) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
                (shape, seen, seen, session_id, sample))


def record(payload):
    """Stop hook body: fold the transcript's new tail into the index. Returns [(chapter_id, revision)] written."""
    sid, tpath = payload.get("session_id"), payload.get("transcript_path")
    if not sid or not tpath or not Path(tpath).is_file():
        return []
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")  # take the write lock before reading the offset: no double-processing
        row = con.execute("SELECT project, offset, open_chapter_id FROM sessions WHERE session_id=?", (sid,)).fetchone()
        entries, new_offset, had_bytes = read_new(tpath, row[1] if row else 0)
        if had_bytes and not any("type" in e for e in entries):
            log_error(f"record {sid}: no recognisable transcript lines in {tpath}")
        cwd = payload.get("cwd") or next((e["cwd"] for e in entries if e.get("cwd")), "")
        project = row[0] if row else project_key(cwd)
        chs = [load_chapter(con, row[2])] if row and row[2] else []
        dirty = set()
        for e in entries:
            try:
                kind, shape, sample = classify(e)
                if kind == "unknown":  # same transaction as the chapters: logged exactly once
                    note_unknown(con, shape, sample, sid, e.get("timestamp") or now())
                if feed(chs, e):  # feed only touches the last chapter or appends one
                    dirty.add(len(chs) - 1)
            except Exception as exc:  # malformed line: log and keep going, never lose the run
                note_unknown(con, f"parse-error:{type(exc).__name__}", one_line(repr(exc), 200),
                             sid, e.get("timestamp") or now())
                continue
        written = [save_chapter(con, chs[i], sid, project) for i in sorted(dirty)]
        open_id = chs[-1]["id"] if chs else None
        con.execute("INSERT INTO sessions(session_id, project, transcript_path, offset, open_chapter_id, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET transcript_path=excluded.transcript_path, "
                    "offset=excluded.offset, open_chapter_id=excluded.open_chapter_id, updated_at=excluded.updated_at",
                    (sid, project, tpath, new_offset, open_id, now()))
        con.execute("COMMIT")
        return written
    except sqlite3.OperationalError as e:
        if con.in_transaction:
            con.execute("ROLLBACK")
        if "locked" not in str(e):  # locked = another session is writing; the unmoved offset catches up next Stop
            raise
        return []
    finally:
        con.close()


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "record":
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return
        try:
            record(payload)
        except Exception as e:
            log_error(f"record {payload.get('session_id')}: {e!r}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception:
        pass
    sys.exit(0)
