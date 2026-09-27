"""Self-check for chapters.py. Run: python hooks/test_chapters.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chapters  # noqa: E402

HOOK = Path(__file__).with_name("chapters.py")
CWD = "C:/work/proj"
TS = "2026-09-26T10:00:00Z"


def user(content, **kw):
    return {"type": "user", "message": {"role": "user", "content": content},
            "cwd": CWD, "gitBranch": "feat", "timestamp": TS, **kw}


def asst(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)},
            "cwd": CWD, "gitBranch": "feat", "timestamp": TS}


def text(t):
    return {"type": "text", "text": t}


def tool(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid, content, err=False):
    return user([{"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": err}],
                toolUseResult={})


# --- Task 1: prompt classification ---
P = chapters.prompt_text
assert P(user("fix the bug")) == "fix the bug"
assert P(user([text("fix the bug")])) == "fix the bug"
assert P(user("<command-message>session-handoff</command-message>\n<command-name>/session-handoff</command-name>")) == "/session-handoff"
assert P(user("<command-name>/model</command-name><command-message>model</command-message><command-args>opus</command-args>")) == "/model opus"
assert P(user("<!-- attach -->\n> quoted\nmy question")).endswith("my question")
assert P(user('<scheduled-task name="tare-usage-report" file="x">\nbody')) == "scheduled: tare-usage-report"
assert P(user("<system-reminder>\nnote\n</system-reminder>\nreal ask")) == "real ask"
assert P(user("<system-reminder>\nnote only\n</system-reminder>")) is None
assert P(user("<task-notification>\n<summary>done</summary>")) is None
assert P(user("<local-command-stdout>Set model</local-command-stdout>")) is None
assert P(user([text("[Request interrupted by user]")])) is None
assert P(user([text("[Request interrupted by user for tool use]")])) is None
assert P(user("skill body", isMeta=True)) is None
assert P(user("summary", isCompactSummary=True)) is None
assert P(user("sub", isSidechain=True)) is None
assert P(result("t1", "file contents")) is None
assert P(user([{"type": "tool_result", "tool_use_id": "t1", "content": "x"}, text("extra")])) is None
assert P(asst(text("hi"))) is None
assert P(user("<unknown-tag>x</unknown-tag>")) is None
assert P(user('<pasted_content id="ab">\nstuff\n</pasted_content>\nmy note')).startswith("<pasted_content")
assert P(user("<!-- reply -->\n> quoted\nanswer")).endswith("answer")

# --- Task 1a: three-way classify; unknown shapes are named, never prompts ---
C = chapters.classify
IMG = {"type": "image", "source": {}}
assert C(user("fix the bug")) == ("prompt", "fix the bug", "")
assert C(result("t1", "x")) == C(asst(text("hi"))) == ("skip", "", "")
assert C(user("<system-reminder>\nnote only\n</system-reminder>")) == ("skip", "", "")
assert C(user("<task-notification>\n<summary>done</summary>")) == ("skip", "", "")
assert C(user("<odd-tag a=1>x</odd-tag>")) == ("unknown", "odd-tag", "<odd-tag a=1>x</odd-tag>")
assert C(user("<system-reminder>r</system-reminder>\n<odd>hi")) == ("unknown", "odd", "<odd>hi"), "sample drops reminders"
assert len(C(user("<odd>" + "y" * 300))[2]) == 200
assert C(user([IMG])) == ("unknown", "no-text", "image")
assert C(user([IMG, text("<system-reminder>r</system-reminder>")])) == ("unknown", "no-text", "image"), "image + reminder only"
assert C(user("")) == ("unknown", "no-text", "empty")
assert C(user("<command-args>x</command-args>"))[:2] == ("unknown", "command-args")
assert chapters.feed([], user([IMG])) is False, "an unknown shape never opens a chapter"

# --- Task 1: building chapters ---
chs = []
for e in [
    asst(text("before any prompt is ignored")),
    user("first ask"),
    asst(text("Looking."), tool("t1", "Read", file_path="a.py")),
    result("t1", "SECRET FILE BODY"),
    asst(tool("t2", "Edit", file_path="b.py"), tool("t3", "Bash", command="pytest -q")),
    result("t2", "ok"),
    result("t3", "boom", err=True),
    asst(tool("t4", "AskUserQuestion", questions=[{"question": "Which way?"}])),
    result("t4", 'User answered: "Which way?"="Left"'),
    user("<task-notification>\n<summary>build finished</summary>"),
    asst(text("Done. Chose left.")),
    user("second ask"),
    asst(text("Second answer.")),
]:
    chapters.feed(chs, e)

assert len(chs) == 2, chs
c = chs[0]
assert c["prompt"] == "first ask"
assert c["replies"] == ["Looking.", "Done. Chose left."], c["replies"]
assert [a["tool"] for a in c["actions"]] == ["Read", "Edit", "Bash", "AskUserQuestion", "task-notification"], c["actions"]
assert c["actions"][0]["arg"] == "a.py"
assert c["actions"][2] == {"tool": "Bash", "arg": "pytest -q", "tool_use_id": "t3", "failed": True}
assert c["actions"][3]["arg"] == "Which way?"
assert c["actions"][4]["arg"] == "build finished"
assert c["decisions"] == ['User answered: "Which way?"="Left"'], c["decisions"]
assert c["files"] == {"a.py": False, "b.py": True}, c["files"]
assert "SECRET FILE BODY" not in json.dumps(chs), "raw tool output must never be stored"
assert c["branch"] == "feat" and c["started_at"] == TS
assert chs[1]["prompt"] == "second ask" and chs[1]["replies"] == ["Second answer."]
assert chapters.feed(chs, asst(text("more"))) is True and chs[1]["replies"][-1] == "more"

# --- Task 2: store + record ---
def run(*args, payload=None, env=None, cwd=None):
    return subprocess.run([sys.executable, str(HOOK), *args], input=json.dumps(payload) if payload is not None else "",
                          capture_output=True, text=True, encoding="utf-8", timeout=30, env=env, cwd=cwd)


def write_lines(path, entries, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


import sqlite3  # noqa: E402

assert chapters.project_key("C:/work/proj/.claude/worktrees/topic-x") == chapters.project_key("C:/work/proj")
assert chapters.project_key("C:/work/proj/sub") != chapters.project_key("C:/work/proj")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    env = {**os.environ, "CHAPTER_INDEX_DB": str(tmp / "idx.db")}
    tr = tmp / "s1.jsonl"
    pay = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(tr), "cwd": CWD + "/.claude/worktrees/w"}

    write_lines(tr, [user("first ask"), asst(text("Did it. Then more."), tool("t1", "Edit", file_path="x.py")),
                     result("t1", "RAW")])
    r = run("record", payload=pay, env=env)
    assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
    db = sqlite3.connect(tmp / "idx.db")
    rows = db.execute("SELECT id, project, prompt, revision, plain_line FROM chapters").fetchall()
    assert len(rows) == 1, rows
    assert rows[0][1] == chapters.project_key(CWD), rows
    assert rows[0][4] == '"first ask" — Did it. Files: x.py*', rows[0][4]
    assert "RAW" not in json.dumps(db.execute("SELECT * FROM chapters").fetchall())
    assert db.execute("SELECT tool FROM sessions WHERE session_id='s1'").fetchone()[0] == "claude"

    # Same chapter grows: updated in place, revision bumps, no duplicate.
    write_lines(tr, [asst(text("Finished."))], "a")
    run("record", payload=pay, env=env)
    rows = db.execute("SELECT id, revision, plain_line FROM chapters").fetchall()
    assert len(rows) == 1 and rows[0][1] == 2 and "Finished." in rows[0][2], rows

    # Re-running with nothing new changes nothing.
    run("record", payload=pay, env=env)
    assert db.execute("SELECT revision FROM chapters").fetchone()[0] == 2

    # A trailing partial line is deferred, not lost.
    with open(tr, "a", encoding="utf-8") as f:
        f.write(json.dumps(user("second ask"))[:20])
    run("record", payload=pay, env=env)
    assert db.execute("SELECT COUNT(*) FROM chapters").fetchone()[0] == 1
    with open(tr, "a", encoding="utf-8") as f:
        f.write(json.dumps(user("second ask"))[20:] + "\n")
    run("record", payload=pay, env=env)
    assert [r[0] for r in db.execute("SELECT prompt FROM chapters ORDER BY id")] == ["first ask", "second ask"]

    # Unknown shapes: logged, never a chapter; a repeat bumps the count; a no-op re-run does not.
    tr3 = tmp / "s3.jsonl"
    write_lines(tr3, [user("<odd-tag>x</odd-tag>"), user("real ask"), user([{"type": "image", "source": {}}])])
    pay3 = {**pay, "session_id": "s3", "transcript_path": str(tr3)}
    run("record", payload=pay3, env=env)
    write_lines(tr3, [user("<odd-tag>y</odd-tag>")], "a")
    run("record", payload=pay3, env=env)
    run("record", payload=pay3, env=env)
    assert dict(db.execute("SELECT shape, count FROM unknown_shapes")) == {"odd-tag": 2, "no-text": 1}
    assert db.execute("SELECT sample, session_id, reviewed FROM unknown_shapes WHERE shape='odd-tag'").fetchone() \
        == ("<odd-tag>x</odd-tag>", "s3", 0)
    assert [r[0] for r in db.execute("SELECT prompt FROM chapters WHERE session_id='s3'")] == ["real ask"]

    # Two writers at once on the same session: no duplicates.
    tr2 = tmp / "s2.jsonl"
    write_lines(tr2, [user(f"ask {i}") for i in range(30)])
    pay2 = {**pay, "session_id": "s2", "transcript_path": str(tr2)}
    procs = [subprocess.Popen([sys.executable, str(HOOK), "record"], stdin=subprocess.PIPE, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
    for p in procs:
        p.communicate(json.dumps(pay2).encode(), timeout=30)
    assert db.execute("SELECT COUNT(*) FROM chapters WHERE session_id='s2'").fetchone()[0] == 30

    # Garbage payload / missing transcript: exit 0, silent.
    assert run("record", payload={"session_id": "x", "transcript_path": str(tmp / "nope.jsonl")}, env=env).returncode == 0
    r = subprocess.run([sys.executable, str(HOOK), "record"], input="not json", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == "", r.stdout

    # A transcript with no recognisable lines is logged, not recorded.
    bad = tmp / "bad.jsonl"
    bad.write_text("garbage\nmore garbage\n", encoding="utf-8")
    run("record", payload={**pay, "session_id": "bad", "transcript_path": str(bad)}, env=env)
    assert "bad" in (tmp / "chapter-index.log").read_text(encoding="utf-8")

    # A malformed line (bare string in a content list) is logged as a parse error, not fatal:
    # the good chapter before it still lands and the offset still advances.
    tr4 = tmp / "s4.jsonl"
    write_lines(tr4, [user("real ask"), asst("oops")])
    pay4 = {**pay, "session_id": "s4", "transcript_path": str(tr4)}
    run("record", payload=pay4, env=env)
    row4 = db.execute("SELECT offset FROM sessions WHERE session_id='s4'").fetchone()
    assert row4 and row4[0] == tr4.stat().st_size, row4
    assert [r[0] for r in db.execute("SELECT prompt FROM chapters WHERE session_id='s4'")] == ["real ask"]
    assert any(sh.startswith("parse-error:") for sh, in db.execute("SELECT shape FROM unknown_shapes"))
    db.close()

print("ok")
