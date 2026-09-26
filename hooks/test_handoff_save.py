"""Self-check for handoff-save.py. Run: python hooks/test_handoff_save.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).with_name("handoff-save.py")


def handoff(mem_dir, title="Cost fix", pick="Run the end-to-end test."):
    return (
        f"<!-- handoff-memory-dir: {mem_dir} -->\n"
        f"# Session Handoff — {title}\n\n"
        "## Where it started\nSomething.\n\n"
        f"## Pick up here\n{pick}\nSecond line.\n"
    )


def run(payload):
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def handoffs(mem):
    return sorted(mem.glob("handoff-*.md"))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    mem = tmp / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "- [Topic](project_x.md) — keep me\n"
        "- [Latest session handoff, 2026-01-01](handoff-2026-01-01-0900.md) — old\n"
        "- [Other](feedback_y.md) — keep me too\n",
        encoding="utf-8",
    )

    # 1. Final message passed directly in the payload.
    r = run({"hook_event_name": "Stop", "last_assistant_message": handoff(mem)})
    assert r.returncode == 0, r.stderr
    files = handoffs(mem)
    assert len(files) == 1, files
    body = files[0].read_text(encoding="utf-8")
    assert body.startswith(f"---\nname: {files[0].stem}\ndescription: Session handoff — Cost fix\n"), body
    assert "handoff-memory-dir" not in body, "marker must be stripped"
    assert "# Session Handoff — Cost fix" in body
    index = (mem / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    assert index[0] == "- [Topic](project_x.md) — keep me", index
    assert index[2] == "- [Other](feedback_y.md) — keep me too", index
    assert sum("](handoff-" in l for l in index) == 1, index
    assert f"]({files[0].name}) — prior session summary. Pick up here: Run the end-to-end test. Second line." in index[1], index
    assert "saved" in r.stdout, r.stdout

    # 2. Second handoff in the same minute must not overwrite the first.
    r = run({"hook_event_name": "Stop", "last_assistant_message": handoff(mem, "Again", "Next.")})
    assert len(handoffs(mem)) == 2, handoffs(mem)
    index = (mem / "MEMORY.md").read_text(encoding="utf-8")
    assert index.count("](handoff-") == 1 and "Pick up here: Next." in index, index

    # 3. No payload field: fall back to the transcript's last assistant reply.
    transcript = tmp / "t.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "session handoff"}},
        {"type": "assistant", "requestId": "r1", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]}},
        {"type": "assistant", "requestId": "r1", "message": {"role": "assistant", "content": [{"type": "text", "text": handoff(mem, "From transcript", "T.")}]}},
    ]
    transcript.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
    run({"hook_event_name": "Stop", "transcript_path": str(transcript)})
    assert len(handoffs(mem)) == 3
    assert "Pick up here: T." in (mem / "MEMORY.md").read_text(encoding="utf-8")

    # 4. Ordinary replies are ignored, including ones that merely mention a handoff.
    for text in ["All done.", "# Session Handoff — no marker\n## Pick up here\nx", "see <!-- handoff-memory-dir: x --> later"]:
        r = run({"hook_event_name": "Stop", "last_assistant_message": text})
        assert r.returncode == 0 and r.stdout == "", (text, r.stdout)
    assert len(handoffs(mem)) == 3

    # 5. MEMORY.md missing: created with just the pointer.
    mem2 = tmp / "fresh"
    mem2.mkdir()
    run({"hook_event_name": "Stop", "last_assistant_message": handoff(mem2)})
    assert (mem2 / "MEMORY.md").read_text(encoding="utf-8").count("](handoff-") == 1

print("ok")
