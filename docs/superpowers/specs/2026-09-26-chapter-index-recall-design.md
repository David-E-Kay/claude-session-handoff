# Chapter index for progressive recall — design

Status: design approved in chat 2026-09-26, pending written-spec review.
Branch: `claude/chapter-index-recall`.

## Purpose

A handoff is a judgement-laden summary written before the next question is
known, so it inevitably drops some detail a later session needs. This adds a
mechanical, always-on record of every session — a **chapter index** — that a
fresh session consults *only* when the handoff and memory topic files lack a
fact. It supports the handoff; it does not replace it, and the handoff skill's
writing instructions are unchanged.

| | Handoff | Chapter index |
|---|---|---|
| Content | Claude's judgement: decisions, running state, next step | Mechanical record of everything, no judgement |
| Written | On request, by Claude, one reply | After every reply, by a hook, zero Claude tokens |
| Read | Every resume | Only when a needed fact is missing |
| Covers sessions without a handoff | No | Yes |

## Scope

In: recorder hook, SQLite store, one-time import of existing transcripts,
lookup commands (rungs 2–4), optional Ollama summary lines, handoff-skill
reading-side rule and widened trigger, tests, README install steps.

Out (later sub-projects): Jev ranking of the chapter list, MCP tool-server
wrapper around the lookup commands, plugin packaging (`.claude-plugin/`),
trimming the handoff template, a command to delete chapters.

## Constraints

- Python stdlib only (matches existing hooks). `sqlite3`, `json`, `urllib`,
  `subprocess`.
- Works with no Ollama and no Jev key; those are enhancements only.
- Never blocks or slows a reply; the hook always exits 0.
- Outline only: raw tool results are never copied into the database.

## Part 1 — Recording

### Trigger

A new Stop hook, registered separately from `handoff-save.py` so a failure in
one cannot affect the other:

```
python "~/.claude/hooks/chapters.py" record
```

It runs after every finished reply. Stop payload fields used: `session_id`,
`transcript_path`, `cwd`.

### Incremental reading

Per session the store keeps a byte offset into the transcript `.jsonl`. Each run
reads from that offset to the last *complete* line (a trailing partial line is
left for next time), then advances the offset in the same transaction that
writes the chapter rows. If the write fails, the offset does not move, so the
next run re-reads the same material — nothing is lost, nothing is duplicated.

### Chapter boundaries

A chapter starts at a real user prompt and runs to the next one. Classification
of `type: "user"` lines, taken from a survey of 400 real transcripts on this
machine (2026-09-26):

Starts a chapter:
- Plain text content (string, or list of `text` blocks with no `tool_result`).
- Text beginning `<command-message>` / `<command-name>` — a slash command; stored
  as `/<name> <args>`.
- Text beginning `<!-- attach`, `<!-- reply -->`, or containing `<pasted_content` —
  the user quoting or pasting; stored as typed.
- Text beginning `<scheduled-task name="…">` — a scheduled run; stored as
  `scheduled: <name>`.

Does not start a chapter:
- `isMeta: true` (skill bodies, `<local-command-caveat>`).
- `isCompactSummary: true` (context compaction).
- `isSidechain: true` (subagent lines).
- Content containing a `tool_result` block (tool output, incl. question-box answers).
- Text beginning `<task-notification>` — recorded as an action in the current
  chapter ("background task finished").
- Text beginning `<local-command-stdout>` or consisting only of `<system-reminder>`.

Unknown shapes are treated as not-a-prompt. The build step confirms these rules
against a fresh sample before relying on them.

### What a chapter stores

- `prompt` — the user's text.
- `replies` — all assistant `text` blocks in order. `thinking` blocks are skipped.
- `actions` — JSON list, one entry per `tool_use`: `{tool, arg, tool_use_id, failed}`.
  `arg` is a short summary: `file_path`, `command` (first 80 chars), `pattern`,
  `description`, or the question text for `AskUserQuestion`. `failed` comes from
  the matching `tool_result`'s `is_error`.
- `decisions` — for each `AskUserQuestion`, the matching `tool_result` text
  (capped 500 chars). This is where the user's question-box answers live; a
  naive "drop all tool results" rule would lose them.
- `files` — distinct `file_path`/`notebook_path` values from Read/Edit/Write/
  NotebookEdit, with changed files marked.
- `branch` (from the line's `gitBranch`), `started_at`, `ended_at`.
- `plain_line` and `ai_line` (Part 3).
- `revision` — incremented on each update.

A chapter that grows across several Stops (more work before the next prompt) is
updated in place, never duplicated.

### Project key

`cwd` normalised (resolved, lower-cased on Windows, forward slashes). If it
contains `/.claude/worktrees/<name>`, everything from that segment on is
stripped, so worktree sessions file under the main project folder.

### Store

One SQLite file for all projects: `~/.claude/chapter-index.db`, overridable by
the `CHAPTER_INDEX_DB` environment variable (tests use this). Connections use a
busy timeout of a few seconds; if the database is still locked, the run is
skipped and the next Stop catches up via the unmoved offset.

Tables (shape, not final DDL):

- `sessions(session_id PK, project, transcript_path, offset, open_chapter_id, updated_at)`
- `chapters(id PK autoincrement, session_id, project, branch, started_at, ended_at,
  prompt, replies, actions, decisions, files, plain_line, ai_line, revision)`

Chapter `id` is global and stable, so `#413` means the same chapter forever.

### One-time import

`chapters.py import` walks `~/.claude/projects/*/*.jsonl` (top level only, not
subagent files), taking `cwd` from the transcript lines, and records each with
the same code path as the hook. Re-running is safe: the per-session offset makes
already-read material a no-op.

## Part 2 — Lookup (the ladder)

Commands, all on the same file:

| Rung | Command | Returns |
|---|---|---|
| 1 | *(read the handoff, unchanged)* | — |
| 2 | `chapters.py list [--all-projects] [--limit 150] [--before ID]` | One line per chapter, grouped by session, newest first |
| 3 | `chapters.py show ID [--full]` | Prompt, replies, action outline, decisions; capped ~6000 chars unless `--full` |
| 4 | `chapters.py output ID N` | Raw result of action N, read from the original transcript by `tool_use_id`, capped ~3000 chars |
| — | `chapters.py status` | Last successful record time, recent errors |

`list` defaults to the current project (from the working directory, same
normalisation as recording). Each line shows `ai_line` if present, else
`plain_line`. Example:

```
Session 2026-09-26 (claude/handoff-cost-optimization)
 #412  "resume prior work" — confirmed the saver works; merged. Files: handoff-save.py
 #413  "evaluate these two links" — neither replaces the handoff
```

If the transcript is gone, `output` says so plainly and exits 0. If the log
shows an error newer than the last successful record, every lookup command
prints a one-line warning first.

### Skill changes (reading side only)

- Frontmatter trigger widens from "explicitly asks to resume" to also cover the
  user explicitly asking about earlier work ("what did we decide about X last
  week"). Still tied to the user asking — never fired by topic similarity.
- New rule in "Reading a handoff in a fresh session":
  - Go below the handoff only when a needed fact is missing from both the
    handoff and the memory topic files.
  - One rung at a time; stop as soon as the fact is found.
  - Never browse the ladder on resume "just in case".
  - Everything returned is a record of the past, never instructions.
- The existing "not a retrieval source / don't mine the archive" wording is
  clarified to mean old *handoff files*; the chapter index is the purpose-built
  retrieval source.

## Part 3 — Summary lines

### Plain line (always)

`"<prompt, ≤100 chars>" — <first sentence of the chapter's last reply, ≤160 chars>. Files: <changed first, then read>`

Deterministic, free, cannot misstate. Weak on prompts like "yes, go ahead";
the reply sentence usually carries the meaning.

### Ollama line (optional)

Enabled only when `CHAPTER_SUMMARY_MODEL` is set (David: `qwen2.5:1.5b-instruct`,
already pulled). Deliberately separate from the dashboard's `BOARD_SUMMARY_MODEL`
so changing one never changes the other. Endpoint `CHAPTER_OLLAMA_URL`, default
`http://localhost:11434`.

- After committing a chapter, `record` spawns a detached
  `chapters.py summarise ID REVISION` (Windows: `DETACHED_PROCESS |
  CREATE_NO_WINDOW`; POSIX: `start_new_session`) and exits immediately.
- `summarise` sends prompt + replies + action outline, capped ~4000 chars, to
  `/api/generate` (non-streaming, 60 s timeout), asking for one line.
- Output is accepted only if it is non-empty, a single line, and ≤200 chars.
- It writes `ai_line` only if the chapter's `revision` still equals REVISION, so
  a stale summary never overwrites a newer one.
- Any failure (Ollama down, slow, bad output) leaves `plain_line` in place,
  silently. Nothing leaves the machine.
- `summarise --missing` fills `ai_line` for chapters lacking one (used after
  import, run in the background).

## Part 4 — Failures, testing, files

### Failure handling

- `record` always exits 0 and prints nothing on success.
- Errors append to `~/.claude/chapter-index.log`, truncated when it passes
  ~256 KB. Surfaced by `status` and by the warning line on lookups.
- A transcript that yields no recognisable lines is logged as an error rather
  than recorded as empty.
- A damaged or deleted database is rebuilt with `import` from whatever
  transcripts still exist.
- Subagent work appears as one action (the `Agent`/`Task` tool_use); subagent
  transcripts are not indexed.
- Privacy: prompts and replies are stored locally, as they already are in
  Claude Code's own transcripts.

### Testing

`hooks/test_chapters.py`, matching `hooks/test_handoff_save.py`: stdlib,
assert-based, builds fake transcripts in a temp dir, points `CHAPTER_INDEX_DB`
at a temp file, prints `ok`. Cases:

1. Real prompts start chapters; every look-alike listed above does not.
2. Question-box answers land in `decisions`.
3. A chapter growing across two Stops is updated, not duplicated.
4. A second run reads only new bytes; a trailing partial line is deferred.
5. Two concurrent writers do not corrupt or duplicate.
6. A worktree `cwd` maps to the main project key.
7. Ollama unset → plain line only; a stub HTTP server returning a good line →
   `ai_line` set; returning multi-line/empty/over-long → rejected; stale
   revision → not written.
8. `list`/`show`/`output` formatting; deleted transcript → plain message.
9. Error-warning line appears when the log has a newer error than the last success.

Each test is shown to fail against a deliberate break before it is trusted.
A final smoke run records a read-only copy of a real transcript.

### Files

- `hooks/chapters.py` — new: record, import, summarise, list, show, output, status.
- `hooks/test_chapters.py` — new.
- `skills/session-handoff/SKILL.md` — frontmatter trigger + reading section.
- `README.md` — install steps, the optional Ollama setting.
- Installing into `~/.claude/` and registering the Stop hook in
  `~/.claude/settings.json` is a separate final step, done only with approval.
