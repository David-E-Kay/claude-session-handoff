# Chapter Index Recall Implementation Plan

> **For agentic workers:** At David's request (2026-09-27) this plan is executed with
> superpowers:subagent-driven-development: Sonnet implementers, an Opus reviewer per task, Codex for
> the final review (global CLAUDE.md §4). Implementers never commit; the controller reviews the
> uncommitted diff and David confirms each commit (Gate 2). Ledger:
> `.superpowers/sdd/2026-09-26-chapter-index-recall/progress.md`. Steps use checkbox (`- [ ]`) syntax.

**Goal:** An always-on chapter index (Stop hook → SQLite) plus lookup commands, so a
fresh session can recover detail the handoff left out, one rung at a time.

**Architecture:** One stdlib Python file, `hooks/chapters.py`, run with a subcommand.
`record` is the Stop hook: it reads the new tail of the session transcript, splits it
into chapters at real user prompts, and upserts them into `~/.claude/chapter-index.db`.
`list` / `show` / `output` / `status` are the lookup ladder. `import` and `summarise`
(Ollama) come later in the plan.

**Tech Stack:** Python 3 stdlib only — `sqlite3`, `json`, `re`, `argparse`,
`subprocess`, `urllib`.

**Spec:** `docs/superpowers/specs/2026-09-26-chapter-index-recall-design.md`

## Global Constraints

- Python stdlib only. No `pip install`.
- `record` always exits 0, prints nothing on success, never blocks a reply.
- Outline only: raw tool results are never written to the database.
- Database path: `CHAPTER_INDEX_DB` env var, else `~/.claude/chapter-index.db`.
- Error log: `~/.claude/chapter-index.log` (same directory as the DB), truncated past ~256 KB.
- Caps: prompt in plain line 100 chars; reply sentence 160; decision 500; `show` 6000 unless `--full`; `output` 3000.
- All CLI output is UTF-8 (`sys.stdout.reconfigure(encoding="utf-8")`) — Windows consoles default to cp1252 and choke on "—".
- **Invariants:** the spec's Invariants table (7 rules) is binding; each task's tests enforce the ones it touches.
- **Gate 2:** every "Commit" step means *ask David to confirm, then commit*. Nothing is committed without that confirmation.
- **A test that has never failed is not a test:** each task includes a mutation step. Mutate from a file copy and restore from that copy — never `git checkout`.

## Existing conventions (grounding)

| Category | Where it is done today | Carry forward |
|---|---|---|
| Naming | `hooks/handoff-save.py:21` `last_text_from_transcript`, `:48` `save` — snake_case functions, module docstring at top | Same. New file is `chapters.py` (no hyphen) so tests can `import chapters`. |
| Error handling | `hooks/handoff-save.py:86-109` — `main()` inside `try/except Exception: pass`, then `sys.exit(0)`; user-facing failure via `systemMessage` JSON (`:99-101`) | Same outer wrapper. `record` stays silent; errors go to the log file instead of `systemMessage` (it runs every reply — a message would be noise). |
| Logging | **None exists in this repo.** | The log file is a new pattern introduced by the spec; keep it to one line per error. |
| Data access | Filesystem via `pathlib`, UTF-8 `read_text`/`write_text` (`hooks/handoff-save.py:65,75,82`); transcript parsed line-by-line skipping bad JSON (`:21-38`) | Same transcript approach, but in binary with a byte offset. **No database code exists in this repo** — SQLite is new. |
| Tests | `hooks/test_handoff_save.py` — plain script, `HOOK = Path(__file__).with_name(...)` (`:9`), subprocess `run(payload)` helper (`:21`), `tempfile.TemporaryDirectory` (`:32`), bare `assert x, detail`, prints `ok` (`:89`) | Same file shape: `hooks/test_chapters.py`. Pure functions are tested by `import chapters`; commands through a subprocess helper, like `run()`. |

## File map

- `hooks/chapters.py` — create. Parsing (Task 1), three-way classify (Task 1a), store + `record` (Task 2), lookup CLI (Task 3), `import` + `rebuild` (Task 4), `summarise` (Task 5).
- `hooks/test_chapters.py` — create; grows one section per task.
- `skills/session-handoff/SKILL.md` — modify (Task 6).
- `README.md` — modify (Task 6).
- `~/.claude/hooks/`, `~/.claude/skills/`, `~/.claude/settings.json` — install (Task 7, approval required).

---

### Task 1: Transcript parsing — prompts and chapters

**Status: complete — commit `49ef916`.** The committed code differs from the code below (extra test cases,
`[Request interrupted by user` in `NOT_PROMPTS`, unknown `<tag` → not a prompt); the ledger records why.
Read `hooks/chapters.py` itself, not this section, for the current shape.

**Files:**
- Create: `hooks/chapters.py`
- Create: `hooks/test_chapters.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `prompt_text(entry: dict) -> str | None` — the prompt if this transcript line starts a chapter, else `None`.
  - `feed(chapters: list[dict], entry: dict) -> bool` — applies one transcript line to `chapters` (the last element is the open chapter); appends a new chapter on a real prompt; returns `True` if anything changed.
  - Chapter dict keys: `prompt: str, replies: list[str], actions: list[dict], decisions: list[str], files: dict[str, bool], branch: str, started_at: str, ended_at: str`, plus `id: int` once stored (Task 2).
  - Action dict: `{"tool": str, "arg": str, "tool_use_id": str, "failed": bool}`.

- [ ] **Step 1: Write the failing test**

Create `hooks/test_chapters.py`:

```python
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
assert P(user("skill body", isMeta=True)) is None
assert P(user("summary", isCompactSummary=True)) is None
assert P(user("sub", isSidechain=True)) is None
assert P(result("t1", "file contents")) is None
assert P(asst(text("hi"))) is None

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python hooks/test_chapters.py`
Expected: `ModuleNotFoundError: No module named 'chapters'` — compile-time RED, the intended signal (the code under test does not exist).

- [ ] **Step 3: Write minimal implementation**

Create `hooks/chapters.py`:

```python
#!/usr/bin/env python3
"""Chapter index: record every session into SQLite and look it up in layers.

A chapter is one real user prompt plus everything Claude did until the next one.
`record` is a Stop hook; `list` / `show` / `output` / `status` are the lookup
ladder a fresh session uses only when its handoff lacks a fact. Raw tool output
is never stored — only an outline of actions. Stdlib only.
"""

import json
import re
import sys

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
NOT_PROMPTS = ("<task-notification>", "<local-command-")
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


def prompt_text(entry):
    """The user's prompt if this transcript line starts a chapter, else None."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = block_text(content)
    if not isinstance(content, str):
        return None
    s = REMINDER.sub("", content).strip()
    if not s or s.startswith(NOT_PROMPTS):
        return None
    if s.startswith(("<command-message>", "<command-name>")):
        return " ".join(x for x in (tag(s, "command-name"), tag(s, "command-args")) if x) or None
    m = re.match(r'<scheduled-task name="([^"]*)"', s)
    return f"scheduled: {m.group(1)}" if m else s


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python hooks/test_chapters.py`
Expected: `ok`

- [ ] **Step 5: Prove the test can fail**

```bash
cp hooks/chapters.py hooks/chapters.py.bak
```
Delete the line `return None` under `if any(... "tool_result" ...)` in `prompt_text`. Run `python hooks/test_chapters.py` → expect an `AssertionError` at `P(result("t1", ...)) is None`. Then:
```bash
cp hooks/chapters.py.bak hooks/chapters.py && rm hooks/chapters.py.bak
```
Run again → `ok`.

- [ ] **Step 6: Confirm the rules against real transcripts (read-only)**

```bash
python -c "import sys,json,glob,collections; sys.path.insert(0,'hooks'); import chapters; c=collections.Counter(); [c.update([(chapters.prompt_text(json.loads(l)) or '')[:25]]) for f in glob.glob(__import__('os').path.expanduser('~/.claude/projects/*/*.jsonl'))[:200] for l in open(f,encoding='utf-8',errors='replace') if l.strip().startswith('{')]; print(c.most_common(40))"
```
Expected: the top entries are ordinary prompt openings plus `''` (non-prompts). No entry beginning `<` except `<!-- attach` / `<!-- reply` / `<pasted_content`. If another `<tag` shows up, stop and report it; do not quietly extend the rules.

- [ ] **Step 7: Commit (Gate 2 — ask David first)**

```bash
git add hooks/chapters.py hooks/test_chapters.py
git commit -m "Parse transcripts into chapters at real user prompts"
```

---

### Task 1a: Three-way classify — unknown shapes are named, never prompts

**Status:** detail written 2026-09-27 against commit `74c8935`. All code below was run in a
scratch copy of `hooks/` before being written here: RED seen, GREEN seen, mutation caught.
Better, not perfect — report any break rather than working around it.

**Files:**
- Modify: `hooks/chapters.py` — replace the whole `prompt_text` function (currently lines 40–61).
- Modify: `hooks/test_chapters.py` — insert a Task 1a section after the line
  `assert P(user("<!-- reply -->\n> quoted\nanswer")).endswith("answer")` (currently line 63).

**Interfaces:**
- Consumes: `REMINDER`, `NOT_PROMPTS`, `block_text`, `tag` (Task 1, unchanged).
- Produces:
  - `one_line(s: str, cap: int) -> str` — collapses whitespace, caps with `…`. Moved here from Task 2 (the sample needs it).
  - `SKIP = ("skip", "", "")`
  - `classify(entry: dict) -> tuple[str, str, str]` — `("prompt", text, "")`, `("unknown", shape, sample)` or `SKIP`.
    `shape` is the opening tag name (`<odd-tag a=1>` → `odd-tag`) or `no-text`. `sample` is ≤200 chars with
    system reminders stripped; for `no-text` it is the non-text block types (e.g. `image`) or `empty`.
  - `prompt_text(entry) -> str | None` — unchanged signature; now a thin wrapper over `classify`, so every
    Task 1 test and `feed()` keep working untouched.

Behaviour changes versus Task 1, both deliberate (spec: unknown shapes are logged, never silently lost):
- A user line with no text after stripping reminders, carrying a non-text block (image-only, or image plus
  a reminder), or with empty content, is `unknown`/`no-text`. A line that is *only* reminders stays `SKIP`.
- `<command-message>`/`<command-name>` text that yields no command name is `unknown` (shape = its first tag),
  instead of `None`. Resolves the ledger minor "command line starting with `<command-args>` returns None".

- [ ] **Step 1: Write the failing test**

Insert into `hooks/test_chapters.py` at the point named above:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python hooks/test_chapters.py`
Expected: `AttributeError: module 'chapters' has no attribute 'classify'` — compile-time RED, the code under test does not exist.

- [ ] **Step 3: Write minimal implementation**

In `hooks/chapters.py`, replace the whole `prompt_text` function with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python hooks/test_chapters.py`
Expected: `ok`

- [ ] **Step 5: Prove the test can fail**

Copy `hooks/chapters.py` to `hooks/chapters.py.bak`. In `classify`, change the line
`return ("unknown", "no-text", ",".join(other)[:200] or "empty")` to `return SKIP`. Run → expect an
`AssertionError` at `C(user([IMG])) == ("unknown", "no-text", "image")`. Restore from the `.bak`, delete it,
confirm `ok`.

- [ ] **Step 6: Confirm against real transcripts (read-only)**

```bash
python - <<'PY'
import sys, json, glob, os, collections
sys.path.insert(0, "hooks")
import chapters
kinds, shapes = collections.Counter(), collections.Counter()
for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
    for line in open(f, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            k, shape, _ = chapters.classify(e)
            kinds[k] += 1
            if k == "unknown":
                shapes[shape] += 1
print(dict(kinds), shapes.most_common())
PY
```
Expected (controller's run, 2026-09-27, 243 files): `{'skip': 98647, 'prompt': 1125}` and no unknown shapes.
Numbers will have grown; any unknown shape that appears is reported to David, not quietly added to the rules.

- [ ] **Step 7: Commit (Gate 2 — ask David first)**

```bash
git add hooks/chapters.py hooks/test_chapters.py
git commit -m "Classify transcript lines three ways; name unknown shapes"
```

---

### Task 2: Store and the `record` Stop hook

**Status:** detail rewritten 2026-09-27 for the revised spec (`tool` column, `unknown_shapes` table) and
verified in a scratch copy on top of Task 1a: RED seen, GREEN seen, mutations below caught.

**Files:**
- Modify: `hooks/chapters.py` (imports at top; append below the Task 1 code)
- Modify: `hooks/test_chapters.py` (insert a Task 2 section before the final `print("ok")`)

**Interfaces:**
- Consumes: `classify`, `feed`, `one_line` and the chapter dict shape from Tasks 1 and 1a.
- Produces:
  - `DB_PATH: Path`, `LOG_PATH: Path` — module constants.
  - `connect() -> sqlite3.Connection` — autocommit mode (`isolation_level=None`), schema ensured, 5 s busy timeout. **Creates the file**; lookups must not use it (Task 3 adds `open_ro`).
  - `project_key(cwd: str) -> str`
  - `plain_line(ch: dict) -> str`
  - `load_chapter(con, chapter_id: int) -> dict` — chapter dict including `id`.
  - `note_unknown(con, shape, sample, session_id, seen) -> None` — upsert into `unknown_shapes`: first sighting keeps its sample and session; repeats bump `count` and `last_seen`.
  - `record(payload: dict) -> list[tuple[int, int]]` — `(chapter_id, revision)` for every chapter written this run (Task 5 uses it). Unknown shapes are written inside the same transaction as the chapters and the offset, so a no-op re-run never double-counts.
  - `log_error(msg: str) -> None`
  - CLI: `python chapters.py record` reads the Stop payload JSON on stdin.
  - Tables `sessions` (with `tool TEXT NOT NULL DEFAULT 'claude'`), `chapters`, `unknown_shapes` exactly as in `SCHEMA` below.

- [ ] **Step 1: Write the failing test**

Insert before the final `print("ok")` in `hooks/test_chapters.py`:

```python
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
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python hooks/test_chapters.py`
Expected: `AttributeError: module 'chapters' has no attribute 'project_key'` — the new section runs and fails on the missing function.

- [ ] **Step 3: Write minimal implementation**

Replace the imports at the top of `hooks/chapters.py` with:

```python
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
```

Append below the Task 1 code:

```python
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
            kind, shape, sample = classify(e)
            if kind == "unknown":  # same transaction as the chapters: logged exactly once
                note_unknown(con, shape, sample, sid, e.get("timestamp") or now())
            if feed(chs, e):  # feed only touches the last chapter or appends one
                dirty.add(len(chs) - 1)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python hooks/test_chapters.py`
Expected: `ok`

- [ ] **Step 5: Prove the test can fail**

Two mutations, each from a `.bak` copy and restored from it (never `git checkout`):
1. In `record`, change `offset=excluded.offset` to `offset=0` → expect the partial-line assertion
   `SELECT COUNT(*) FROM chapters ... == 1` to fail.
2. In `record`, replace the `note_unknown(...)` call with `pass` → expect the
   `dict(db.execute("SELECT shape, count FROM unknown_shapes")) == {...}` assertion to fail.

On Windows the last line of the traceback will be a `PermissionError` from `TemporaryDirectory` cleanup (the
test's open `db` handle); the real `AssertionError` is above it. Look for the `line N, in <module>` frame.

Not used: the old `BEGIN` vs `BEGIN IMMEDIATE` mutation. Controller ran it 5 times, it never failed — with a
deferred `BEGIN` the second writer gets "database is locked" on its first write, rolls back, and leaves the
offset for the next Stop, so the two-writer test cannot tell the two apart. `BEGIN IMMEDIATE` stays (it makes
the loser wait instead of skip); the test guards "no duplicates", which both satisfy.

- [ ] **Step 6: Commit (Gate 2 — ask David first)**

```bash
git add hooks/chapters.py hooks/test_chapters.py
git commit -m "Record chapters and unknown shapes into SQLite from a Stop hook"
```

---

### Task 3: Lookup commands — `list`, `show`, `output`, `status`, `unknowns`, `summary`

**Status:** detail rewritten 2026-09-27 for the revised spec (read-only lookups, `unknowns`, `summary`,
status line for unknown shapes) and verified in a scratch copy on top of Task 2: RED seen, GREEN seen,
mutations below caught, smoke run on a real transcript produced a readable list.

**Files:**
- Modify: `hooks/chapters.py` (add `import argparse`; append functions above `main`; extend `main`)
- Modify: `hooks/test_chapters.py` (Task 3 section before the final `print("ok")`)

**Interfaces:**
- Consumes: `connect`, `load_chapter`, `project_key`, `blocks`, `result_text`, `DB_PATH`, `LOG_PATH`, the `unknown_shapes` table; test helpers `run`, `write_lines`.
- Produces:
  - `NOTHING = "No chapters recorded yet."`
  - `open_ro() -> sqlite3.Connection | None` — read-only (`file:…?mode=ro` URI); `None` if the DB file is missing. Never creates anything.
  - `cmd_list(all_projects: bool, limit: int, before: int | None) -> str`
  - `cmd_show(chapter_id: int, full: bool) -> str`
  - `cmd_output(chapter_id: int, n: int) -> str`
  - `cmd_status() -> str` — adds `Unknown shapes awaiting review: N`.
  - `cmd_unknowns(show_all: bool, mark: str | None) -> str` — `--mark-reviewed` is the one lookup-side write and touches only `unknown_shapes`; it still refuses to create a missing DB.
  - `cmd_summary(session_id: str) -> str` — JSON `{"id","revision","ended_at","ai_line","plain_line"}` of the session's latest chapter, or `null` (no chapters, or no DB). No warning line, so programs can parse it.
  - `warning() -> str` — `""` or one warning line; `""` when the DB is missing.
  - CLI: `list [--all-projects] [--limit N] [--before ID]`, `show ID [--full]`, `output ID N`, `status`, `unknowns [--all] [--mark-reviewed SHAPE]`, `summary SESSION_ID`. All print text and exit 0.

Decision taken while detailing (flag to David at Gate 2): the spec says a missing DB prints "No chapters
recorded yet."; `summary` prints `null` instead, because its readers are programs expecting JSON.

- [ ] **Step 1: Write the failing test**

Insert before the final `print("ok")`:

```python
# --- Task 3: lookup ---
with tempfile.TemporaryDirectory() as tmp:  # lookups against a missing database create nothing
    env = {**os.environ, "CHAPTER_INDEX_DB": str(Path(tmp) / "none.db")}
    for args in (["list"], ["show", "1"], ["output", "1", "1"], ["status"], ["unknowns"],
                 ["unknowns", "--mark-reviewed", "x"]):
        r = run(*args, env=env)
        assert r.returncode == 0 and r.stdout.strip() == "No chapters recorded yet.", (args, r.stdout, r.stderr)
    assert run("summary", "s1", env=env).stdout.strip() == "null"
    assert os.listdir(tmp) == [], os.listdir(tmp)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    env = {**os.environ, "CHAPTER_INDEX_DB": str(tmp / "idx.db")}
    proj = tmp / "proj"
    proj.mkdir()
    tr = tmp / "s1.jsonl"
    write_lines(tr, [
        {**user("first ask"), "cwd": str(proj)},
        asst(text("Reading."), tool("t1", "Read", file_path="a.py")),
        result("t1", "A" * 5000),
        asst(text("Done first.")),
        {**user("second ask"), "cwd": str(proj)},
        asst(text("x" * 7000)),
    ])
    run("record", payload={"session_id": "s1", "transcript_path": str(tr), "cwd": str(proj)}, env=env)

    out = run("list", env=env, cwd=str(proj)).stdout
    assert "Session 2026-09-26 (feat)" in out, out
    assert out.index("#1") < out.index("#2"), out
    assert '"first ask" — Done first. Files: a.py' in out, out
    assert run("list", env=env, cwd=str(tmp)).stdout.strip() == "No chapters recorded for this project.", "other project must be empty"
    assert "#1" in run("list", "--all-projects", env=env, cwd=str(tmp)).stdout

    show = run("show", "1", env=env).stdout
    assert "Prompt: first ask" in show and " 1. Read a.py" in show and "Done first." in show, show
    assert "AAAA" not in show, "show must not include raw output"
    long = run("show", "2", env=env).stdout
    assert "[truncated — use --full]" in long and len(long) < 6500, len(long)
    assert "[truncated" not in run("show", "2", "--full", env=env).stdout

    sm = json.loads(run("summary", "s1", env=env).stdout)
    assert sm["id"] == 2 and sm["revision"] == 1 and sm["ai_line"] is None and sm["plain_line"].startswith('"second ask"'), sm
    assert run("summary", "nope", env=env).stdout.strip() == "null"

    tu = tmp / "u.jsonl"
    write_lines(tu, [{**user("<odd-tag>q</odd-tag>"), "cwd": str(proj)}])
    run("record", payload={"session_id": "u", "transcript_path": str(tu), "cwd": str(proj)}, env=env)
    u = run("unknowns", env=env).stdout
    assert "odd-tag  x1" in u and "session u" in u and "<odd-tag>q</odd-tag>" in u, u
    assert "Unknown shapes awaiting review: 1" in run("status", env=env).stdout
    assert run("unknowns", "--mark-reviewed", "odd-tag", env=env).stdout.strip() == "Marked odd-tag as reviewed."
    assert "odd-tag" not in run("unknowns", env=env).stdout
    assert "(reviewed)" in run("unknowns", "--all", env=env).stdout
    assert "Unknown shapes awaiting review: 0" in run("status", env=env).stdout

    o = run("output", "1", "1", env=env).stdout
    assert o.startswith("AAA") and "[truncated at 3000 chars]" in o and len(o) < 3100, o[:50]
    tr.unlink()
    assert "no longer exists" in run("output", "1", "1", env=env).stdout
    assert "No such chapter" in run("show", "99", env=env).stdout

    st = run("status", env=env).stdout
    assert "Last recorded:" in st and "Recent errors: none" in st, st
    (tmp / "chapter-index.log").write_text("2999-01-01T00:00:00 ERROR boom\n", encoding="utf-8")
    assert run("list", "--all-projects", env=env).stdout.startswith("WARNING: recording has failed"), "warning line"
    assert run("summary", "s1", env=env).stdout.startswith("{"), "summary stays pure JSON"
    assert "boom" in run("status", env=env).stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python hooks/test_chapters.py`
Expected: `AssertionError` on the first missing-DB loop assertion (`r.stdout.strip() == "No chapters recorded yet."`) with empty stdout — `main` does not handle `list` yet.

- [ ] **Step 3: Write minimal implementation**

Add `import argparse` as the first import. Append above `main`:

```python
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
```

In `main`, after the `if cmd == "record":` block, add:

```python
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
        if a.cmd in ("status", "summary"):  # summary is JSON for programs: no warning line
            print(cmd_status() if a.cmd == "status" else cmd_summary(a.session_id))
            return
        body = {"list": lambda: cmd_list(a.all_projects, a.limit, a.before),
                "show": lambda: cmd_show(a.id, a.full),
                "output": lambda: cmd_output(a.id, a.n),
                "unknowns": lambda: cmd_unknowns(a.all, a.mark_reviewed)}[a.cmd]()
        print(warning() + body)
```

Implementer note: `argparse` calls `sys.exit(2)` on bad arguments, which the `__main__` wrapper's
`except Exception` does not catch (`SystemExit` is not an `Exception`) — fine for lookup commands; only
`record` must always exit 0.

- [ ] **Step 4: Run test to verify it passes**

Run: `python hooks/test_chapters.py`
Expected: `ok`

- [ ] **Step 5: Prove the test can fail**

Two mutations, each from a `.bak` copy and restored from it:
1. Replace the body of `open_ro` with `return connect()` → expect the missing-DB loop assertion to fail
   (the lookup created the file and found it empty). Guards invariant 4.
2. In `cmd_output`, delete the `if not Path(row[1]).is_file():` guard and its `return` → expect the
   `"no longer exists" in ...` assertion to fail.

- [ ] **Step 6: Smoke run against a real transcript (read-only copy)**

```bash
S="$(mktemp -d)"
cp "$(ls -t ~/.claude/projects/*/*.jsonl | sed -n 2p)" "$S/smoke.jsonl"
echo "{\"session_id\":\"smoke\",\"transcript_path\":\"$(cd "$S" && pwd -W)/smoke.jsonl\",\"cwd\":\"$PWD\"}" | CHAPTER_INDEX_DB="$S/smoke.db" python hooks/chapters.py record
CHAPTER_INDEX_DB="$S/smoke.db" python hooks/chapters.py list --all-projects
CHAPTER_INDEX_DB="$S/smoke.db" python hooks/chapters.py status
```
(`sed -n 2p` skips the newest file, which is the running session.) Expected: a readable chapter list whose
prompts match what was actually typed, and `Unknown shapes awaiting review: 0`. Show it to David.

- [ ] **Step 7: Commit (Gate 2 — ask David first)**

```bash
git add hooks/chapters.py hooks/test_chapters.py
git commit -m "Add read-only lookup commands, unknowns review and summary JSON"
```

---

### Task 4: One-time import and `rebuild` — PROVISIONAL

Purpose: index every existing transcript before Claude Code deletes old ones (`cleanupPeriodDays`, 30 by
default), and let a changed classification rule apply backwards. Protects: history that would otherwise be
lost, and the unknown-shape review loop (a shape found to be a real prompt only helps past sessions if they
can be re-split). Depends on: Task 2's `record`.

Approach: `chapters.py import` walks `~/.claude/projects/*/*.jsonl` (top level only), builds a payload per
file (`session_id` from the filename stem, `cwd` from the transcript lines) and calls the same record path, so
re-running is a no-op. `chapters.py rebuild [SESSION_ID | --all]` deletes a session's chapters and its
`sessions` row, then records it again from its transcript; a session whose transcript is gone is left
untouched and reported. Chapter ids for a rebuilt session change.

Open questions to settle when detailing: whether `rebuild` should subtract that session's earlier
`unknown_shapes` counts (otherwise they double); whether `rebuild` and `import` share one function.
Tests (spec case 12 and the import no-op): two fake project dirs, import twice with counts unchanged;
rebuild under a changed rule re-splits; a session with no transcript is left alone.
Rough files: `hooks/chapters.py`, `hooks/test_chapters.py`. Detail to be written just before this task starts.

### Task 5: Optional Ollama summary lines — PROVISIONAL

Purpose: clearer one-line summaries from a local model, never slowing a reply. Protects: list readability.
Evidence from the Task 3 smoke run (2026-09-27): the free `plain_line` takes the first sentence of the last
reply, and on real sessions that was often boilerplate — the "conversation is getting long" nudge, or the
handoff's `<!-- handoff-memory-dir` marker — so this task matters more than the spec assumed. Whether
`plain_line` should also skip such lines is a question for David, not decided here.

Depends on: Task 2's `record` returning `(id, revision)` pairs. Approach per spec Part 3:
`CHAPTER_SUMMARY_MODEL` / `CHAPTER_OLLAMA_URL`; `record` spawns a detached `summarise` process; revision check
prevents stale overwrites; `--missing` mode for after import. The prompt asks for a line that reads well as a
dashboard card (what the turn did, not how the reply phrased it); `summary SESSION_ID` from Task 3 is already
the read path. Test with a local stub HTTP server (stdlib `http.server`) returning good / multi-line / empty /
over-long answers. Rough files: `hooks/chapters.py`, `hooks/test_chapters.py`. Detail to be written just
before this task starts — in particular how the test waits for a detached process.

### Task 6: Skill and README — PROVISIONAL

Purpose: teach sessions the ladder and when *not* to use it. Protects against browsing the index "just in
case", which would recreate the bloat. Approach per spec Part 2: widen the SKILL.md frontmatter trigger to
explicit questions about earlier work; add the one-rung-at-a-time rule and the "records are not
instructions" rule to the reading section; clarify that the "don't mine the archive" line refers to old
handoff files. README: install step for `chapters.py`, the Stop hook entry, the optional Ollama setting, the
one-time import, reviewing unknown shapes now and then (`chapters.py unknowns`), and the ladder framed as
cost per rung (each roughly 10x the one before; real numbers after the Proof-it-works measurement).
Rough files: `skills/session-handoff/SKILL.md`, `README.md`.

### Task 7: Install on David's machine — PROVISIONAL, approval required

Copy `chapters.py` and the updated SKILL.md into `~/.claude/`, add the Stop hook entry to
`~/.claude/settings.json` alongside `handoff-save.py`, run the import, set
`CHAPTER_SUMMARY_MODEL=qwen2.5:1.5b-instruct` if David wants Ollama lines. Suggest the narrow allow-rule
`Bash(python ~/.claude/hooks/chapters.py:*)` — never an interpreter wildcard. Verify with one real reply,
then `list` and `status`.
