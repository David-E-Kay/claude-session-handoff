# CLAUDE.md

**The main checkout is a live folder.** The maintainer's machine runs the `session-handoff` plugin straight from the main checkout of this repo (installed as a local-directory marketplace, which loads in place). Whatever is checked out there is what runs their handoffs, hooks and chapter recording from the next session on.

- Never edit, commit on, or switch branches in the main folder. Every change, even a doc typo, happens on a branch in a worktree under `.claude/worktrees/`, and only finished, tested work is merged into `master`.
- Before merging, run the tests from the worktree: `python hooks/test_context_warn.py && python hooks/test_handoff_task.py && python hooks/test_chapters.py && python hooks/test_handoff_save.py` (each prints `ok`), and `claude plugin validate .` (only the missing-`version` warning is expected).
