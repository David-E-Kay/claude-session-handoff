# CLAUDE.md

**The main checkout is a live folder.** The maintainer's machine installs the `session-handoff` plugin from the main checkout of this repo (a local-directory marketplace). Claude Code copies what is checked out there into `~/.claude/plugins/cache/session-handoff/session-handoff/<commit>/` and runs that copy, so the main checkout is what every update ships.

- Never edit, commit on, or switch branches in the main folder. Every change, even a doc typo, happens on a branch in a worktree under `.claude/worktrees/`, and only finished, tested work is merged into `master`.
- Before merging, run the tests from the worktree: `python hooks/test_context_warn.py && python hooks/test_handoff_task.py && python hooks/test_chapters.py && python hooks/test_handoff_save.py` (each prints `ok`), and `claude plugin validate .` (only the missing-`version` warning is expected).
- After merging, run `claude plugin update session-handoff@session-handoff` so the maintainer's machine picks up the merge from the next session on. Their lookup allow rule is `Bash(chapter-index:*)`, which names no folder, so it needs no change after an update.
