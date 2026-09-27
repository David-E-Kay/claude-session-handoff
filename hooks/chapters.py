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
