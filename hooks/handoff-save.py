#!/usr/bin/env python3
"""Stop hook: save a session handoff to project memory without spending model turns.

The session-handoff skill has the model print the handoff once, in chat, starting
with a `<!-- handoff-memory-dir: <path> -->` marker. This hook sees the finished
reply, writes it to `<path>/handoff-<YYYY-MM-DD-HHMM>.md` with memory frontmatter,
and repoints the single handoff line in `<path>/MEMORY.md`. Timestamp comes from
the real clock. Replies without the marker on their first line are ignored.
Always exits 0 — never blocks.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

MARKER = re.compile(r"\A\s*<!--\s*handoff-memory-dir:\s*(.+?)\s*-->\s*\n")


def last_text_from_transcript(transcript_path: str):
    p = Path(transcript_path)
    if not p.is_file():
        return None
    last = None
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                msg = json.loads(line).get("message")
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    last = block["text"]
    return last


def section(body: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    return " ".join(m.group(1).split()) if m else ""


def save(text: str):
    m = MARKER.match(text)
    if not m:
        return None
    mem_dir = Path(m.group(1))
    body = text[m.end():].lstrip("\n")
    title_m = re.search(r"^# Session Handoff\s*[—-]\s*(.+)$", body, re.M)
    title = title_m.group(1).strip() if title_m else "untitled"

    now = datetime.now()
    base = stem = f"handoff-{now:%Y-%m-%d-%H%M}"
    n = 1
    while (mem_dir / f"{stem}.md").exists():  # never overwrite an append-only log entry
        n += 1
        stem = f"{base}-{n}"
    path = mem_dir / f"{stem}.md"
    mem_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {stem}\ndescription: Session handoff — {title}\nmetadata:\n  type: project\n---\n\n{body}",
        encoding="utf-8",
    )

    pointer = (
        f"- [Latest session handoff, {now:%Y-%m-%d}]({path.name}) — prior session summary. "
        f"Pick up here: {section(body, 'Pick up here')}"
    )
    index = mem_dir / "MEMORY.md"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    old = [i for i, l in enumerate(lines) if "](handoff-" in l]
    if old:
        lines[old[0]] = pointer
        lines = [l for i, l in enumerate(lines) if i not in old[1:]]
    else:
        lines.append(pointer)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    text = payload.get("last_assistant_message")
    if not isinstance(text, str) and payload.get("transcript_path"):
        text = last_text_from_transcript(payload["transcript_path"])
    if not isinstance(text, str) or not MARKER.match(text):
        return
    try:
        path = save(text)
        msg = f"Handoff saved to {path}"
    except Exception as e:
        msg = f"Handoff NOT saved ({e}). Ask Claude to write it to memory manually."
    print(json.dumps({"systemMessage": msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
