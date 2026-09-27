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

print("ok")
