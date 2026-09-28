#!/usr/bin/env python3
"""Chapter index: record every session into SQLite and look it up in layers.

A chapter is one real user prompt plus everything Claude did until the next one.
`record` is a Stop hook; `list` / `show` / `output` / `status` are the lookup
ladder a fresh session uses only when its handoff lacks a fact. Raw tool output
is never stored — only an outline of actions. Stdlib only.
"""

import argparse
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


SHOW_CAP, OUTPUT_CAP = 6000, 3000
NOTHING = "No chapters recorded yet."


def open_ro():
    """Read-only connection for lookups, or None when nothing has been recorded. Never creates the file."""
    if not DB_PATH.is_file():
        return None
    return sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def last_success(con):
    return con.execute("SELECT MAX(updated_at) FROM sessions").fetchone()[0] or ""


def last_error_lines(n=5):
    try:
        return LOG_PATH.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []


def warning():
    errs = last_error_lines(1)
    con = open_ro()
    if con is None:
        return ""
    try:
        ok = last_success(con)
    finally:
        con.close()
    if errs and errs[-1][:19] > ok:
        return f"WARNING: recording has failed since {errs[-1][:19]}; run `chapters.py status`.\n"
    return ""


def cmd_list(all_projects, limit, before):
    con = open_ro()
    if con is None:
        return NOTHING
    try:
        q, args = "SELECT id, session_id, project, branch, started_at, plain_line, ai_line FROM chapters WHERE 1=1", []
        if not all_projects:
            q, args = q + " AND project=?", [project_key(os.getcwd())]
        if before:
            q, args = q + " AND id<?", args + [before]
        rows = con.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        con.close()
    if not rows:
        return "No chapters recorded for this project." if not all_projects else NOTHING
    sessions = {}
    for r in rows:  # newest first; dict keeps first-seen order, so sessions come out newest first
        sessions.setdefault(r[1], []).append(r)
    out = []
    for chs in sessions.values():
        first = chs[-1]
        where = f", {Path(first[2]).name}" if all_projects else ""
        out.append(f"Session {first[4][:10]} ({first[3]}{where})")
        out += [f" #{r[0]}  {r[6] or r[5]}" for r in reversed(chs)]
    return "\n".join(out)


def cmd_show(chapter_id, full):
    con = open_ro()
    if con is None:
        return NOTHING
    try:
        if not con.execute("SELECT 1 FROM chapters WHERE id=?", (chapter_id,)).fetchone():
            return f"No such chapter: #{chapter_id}"
        ch = load_chapter(con, chapter_id)
    finally:
        con.close()
    lines = [f"#{ch['id']}  {ch['started_at'][:16]}  {ch['branch']}  {Path(ch['project']).name}",
             f"Prompt: {ch['prompt']}", "Actions:"]
    lines += [f" {i}. {a['tool']} {a['arg']}{' (failed)' if a['failed'] else ''}" for i, a in enumerate(ch["actions"], 1)]
    if ch["decisions"]:
        lines += ["Decisions:"] + [f" - {d}" for d in ch["decisions"]]
    lines += ["Replies:"] + ch["replies"]
    s = "\n".join(lines)
    return s if full or len(s) <= SHOW_CAP else s[:SHOW_CAP] + "\n[truncated — use --full]"


def cmd_output(chapter_id, n):
    con = open_ro()
    if con is None:
        return NOTHING
    try:
        row = con.execute("SELECT c.actions, s.transcript_path FROM chapters c JOIN sessions s USING(session_id) "
                          "WHERE c.id=?", (chapter_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return f"No such chapter: #{chapter_id}"
    actions = json.loads(row[0])
    if not 1 <= n <= len(actions) or not actions[n - 1]["tool_use_id"]:
        return f"Chapter #{chapter_id} has no action {n} with a stored result."
    if not Path(row[1]).is_file():
        return "The original conversation file no longer exists, so this raw output is unavailable."
    tid = actions[n - 1]["tool_use_id"]
    with open(row[1], encoding="utf-8", errors="replace") as f:
        for line in f:
            if tid not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            for b in blocks(e):
                if b.get("type") == "tool_result" and b.get("tool_use_id") == tid:
                    s = result_text(b)
                    return s if len(s) <= OUTPUT_CAP else s[:OUTPUT_CAP] + f"\n[truncated at {OUTPUT_CAP} chars]"
    return "That output was not found in the conversation file."


def cmd_status():
    con = open_ro()
    if con is None:
        return NOTHING
    try:
        ok = last_success(con)
        n, p = con.execute("SELECT COUNT(*), COUNT(DISTINCT project) FROM chapters").fetchone()
        u = con.execute("SELECT COUNT(*) FROM unknown_shapes WHERE reviewed=0").fetchone()[0]
    finally:
        con.close()
    errs = last_error_lines()
    return "\n".join([f"Last recorded: {ok or 'never'}", f"Chapters: {n} across {p} projects",
                      f"Unknown shapes awaiting review: {u}" + (" (run `chapters.py unknowns`)" if u else ""),
                      "Recent errors: " + ("none" if not errs else "\n" + "\n".join(errs))])


def cmd_unknowns(show_all, mark):
    if not DB_PATH.is_file():
        return NOTHING
    if mark:  # the one lookup-side write; touches only unknown_shapes
        con = connect()
        try:
            n = con.execute("UPDATE unknown_shapes SET reviewed=1 WHERE shape=?", (mark,)).rowcount
        finally:
            con.close()
        return f"Marked {mark} as reviewed." if n else f"No unknown shape named {mark}."
    con = open_ro()
    try:
        rows = con.execute("SELECT shape, count, first_seen, last_seen, session_id, sample, reviewed FROM unknown_shapes"
                           + ("" if show_all else " WHERE reviewed=0") + " ORDER BY count DESC, shape").fetchall()
    finally:
        con.close()
    if not rows:
        return "No unknown message shapes awaiting review."
    out = []
    for shape, n, first, last, sid, sample, reviewed in rows:
        out.append(f"{shape}  x{n}  {first[:10]}..{last[:10]}  session {sid}{'  (reviewed)' if reviewed else ''}")
        out.append(f"    {sample}")
    return "\n".join(out)


def cmd_summary(session_id):
    """Latest chapter of a session as JSON, or null. The read path for other programs (e.g. the dashboard)."""
    con = open_ro()
    if con is None:
        return "null"
    try:
        row = con.execute("SELECT id, revision, ended_at, ai_line, plain_line FROM chapters WHERE session_id=? "
                          "ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    finally:
        con.close()
    return json.dumps(dict(zip(("id", "revision", "ended_at", "ai_line", "plain_line"), row)) if row else None)


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
    else:
        ap = argparse.ArgumentParser(prog="chapters.py")
        sub = ap.add_subparsers(dest="cmd", required=True)
        p = sub.add_parser("list")
        p.add_argument("--all-projects", action="store_true")
        p.add_argument("--limit", type=int, default=150)
        p.add_argument("--before", type=int)
        p = sub.add_parser("show")
        p.add_argument("id", type=int)
        p.add_argument("--full", action="store_true")
        p = sub.add_parser("output")
        p.add_argument("id", type=int)
        p.add_argument("n", type=int)
        sub.add_parser("status")
        p = sub.add_parser("unknowns")
        p.add_argument("--all", action="store_true")
        p.add_argument("--mark-reviewed", metavar="SHAPE")
        p = sub.add_parser("summary")
        p.add_argument("session_id")
        a = ap.parse_args(argv[1:])
        try:
            if a.cmd in ("status", "summary"):  # summary is JSON for programs: no warning line
                print(cmd_status() if a.cmd == "status" else cmd_summary(a.session_id))
                return
            body = {"list": lambda: cmd_list(a.all_projects, a.limit, a.before),
                    "show": lambda: cmd_show(a.id, a.full),
                    "output": lambda: cmd_output(a.id, a.n),
                    "unknowns": lambda: cmd_unknowns(a.all, a.mark_reviewed)}[a.cmd]()
            print(warning() + body)
        except Exception as e:  # not log_error: warning() would read it as a recording failure
            print("null" if a.cmd == "summary" else f"Lookup failed: {e!r}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception:
        pass
    sys.exit(0)
