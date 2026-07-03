#!/usr/bin/env python3
"""UserPromptSubmit hook: warn when total context tokens used exceed THRESHOLD_TOKENS.

Fires at a fixed token count regardless of model context window. For 1M Opus
this catches the soft quality cliff at ~120K; for smaller-window models,
exceeding 120K means the user has chosen heavy-context work knowing the risk.
Always exits 0 — never blocks.
"""

import json
import sys
from pathlib import Path

THRESHOLD_TOKENS = 120_000

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


DEBUG_PATH = Path.home() / ".claude" / "hooks" / "context-debug.json"


def dump_debug(payload: dict):
    try:
        DEBUG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def used_from_payload(payload: dict):
    cw = payload.get("context_window")
    if not isinstance(cw, dict):
        return None
    for key in ("used_tokens", "used"):
        raw = cw.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return int(raw)
    pct = cw.get("used_percentage")
    total = cw.get("total", cw.get("total_tokens", cw.get("context_window_size")))
    if isinstance(pct, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return int(total * pct / 100)
    return None


def used_from_transcript(transcript_path: str):
    p = Path(transcript_path)
    if not p.is_file():
        return None
    last_usage = None
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if isinstance(usage, dict):
                last_usage = usage
    if not last_usage:
        return None
    return sum(int(last_usage.get(k, 0) or 0) for k in USAGE_KEYS)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    dump_debug(payload)

    used = used_from_payload(payload)
    if used is None:
        transcript_path = payload.get("transcript_path")
        if transcript_path:
            try:
                used = used_from_transcript(transcript_path)
            except Exception:
                used = None

    if used is None or used < THRESHOLD_TOKENS:
        return

    used_k = used // 1000
    threshold_k = THRESHOLD_TOKENS // 1000

    print(
        f"[CONTEXT WARNING] Context is at {used_k}k tokens used (threshold {threshold_k}k). "
        "In your next response, if the user runs the session handoff skill explicitly or conversationally, do nothing."
        "Otherswise, briefly and prominently alert the user that they should consider running "
        "a session handoff (this is an actual .claude skill), then /compact or start a fresh session before context quality "
        "degrades. Keep the alert to one sentence at the top of your reply. DO NOT mention the SUPERPOWERS skill"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
