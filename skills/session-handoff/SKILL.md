---
name: session-handoff
description: Two directions, same skill. WRITING — use when the user says "session handoff", "wrap up session", "hand off", "handoff summary", or wants a structured end-of-session summary before clearing context; writes it to project memory and prints it, covering decisions, shipped changes, key files, running state, verification steps, deferrals, and open questions. READING — use when the user explicitly asks to resume: "resume", "resume from before", "resume from last session", "pick up where we left off", "continue from last time", "carry on from yesterday", "catch me up", "where did we leave off", "what was I working on", "load the last handoff", or a near-equivalent explicit request. Loads the stored handoff so a fresh agent continues seamlessly. Do NOT invoke the reading half merely because a request resembles earlier work in this repo — resuming requires the user to ask for it.
---

# Session Handoff

Produce a repeatable end-of-session summary so the user can `/clear` and start a fresh agent without losing continuity. The next agent should be able to pick up by reading this summary alone.

This is a **context-handoff artifact**, not a status report. The audience is a future instance of you, not a stakeholder.

## When to invoke

**To write a handoff** — user says: "session handoff", "wrap up session", "hand off", "handoff summary", "let's wrap up", "summarize before I clear", or any near-equivalent. Also invoke proactively if the user says they're about to `/clear` without having run it yet. Follow "How to produce the summary" below.

**To read one** — user opens a session with "resume from last session", "resume from before", "pick up where we left off", "continue from last time", "what was I working on", or any near-equivalent. Skip straight to "Reading a handoff in a fresh session" below; do not write anything.

**Automated trigger:** The `context-threshold-warn.py` hook (`~/.claude/hooks/`) alerts at 60% of the 200k model context window, prompting the user to run this skill.

## How to produce the summary

1. **Review the full conversation**, not just the last few turns. Handoffs miss things when they only summarize recent context.
2. **Pull state from these sources (in order):**
   - Plan files referenced this session (check `C:\Users\david\.claude\plans\` if a plan was mentioned).
   - TodoWrite state — any in-progress or pending tasks.
   - Background processes you started with `run_in_background` — shell IDs are load-bearing for the next agent.
   - Files created or modified this session — you know what you touched; don't grep to re-discover.
   - Memory files written or updated (`C:\Users\david\.claude\projects\<project>\memory\`).
   - Unresolved questions — things you asked the user that never got a clear answer, or things the user asked that got deflected.
3. **Do NOT audit the filesystem.** This is synthesis of what happened in THIS session. No `git log`, no broad `Glob` sweeps. If you didn't touch it this session, it doesn't belong here.
4. **Write it to project memory, then print it in chat.** See "Where the handoff goes" below.

## Where the handoff goes

The handoff is written into this repo's project memory directory — the same `.../projects/<repo-slug>/memory/` path given in your system prompt. That directory is the append-only log; `MEMORY.md` inside it is loaded into context automatically at the start of every session in this repo, so the next agent finds the handoff without being told it exists.

1. **Write the handoff file:** `<memory-dir>/handoff-<YYYY-MM-DD-HHMM>.md`, using the memory frontmatter format:
   ```markdown
   ---
   name: handoff-<YYYY-MM-DD-HHMM>
   description: Session handoff — <one-line title>
   metadata:
     type: project
   ---
   ```
   followed by the output template below, verbatim.

2. **Update `MEMORY.md` to point at it — replacing the previous handoff line, not appending a new one:**
   ```
   - [Latest session handoff, <YYYY-MM-DD>](handoff-<YYYY-MM-DD-HHMM>.md) — prior session summary. Pick up here: <the "Pick up here" line>
   ```
   Keep this line purely descriptive. It must not instruct a fresh agent to open the file — `MEMORY.md` is auto-injected into every session, and an imperative here would make resuming fire on topic similarity rather than on the user's explicit request. Loading a handoff is opt-in by phrasing, and the frontmatter triggers are the only gate. Do not add "read this first", "before continuing", or similar.

   Exactly one handoff line lives in `MEMORY.md` at any time. Older handoff files stay on disk and are found with `ls`/`Glob` on `handoff-*.md` when history is actually needed.
   <!-- ponytail: one rolling pointer instead of a summary tree — handoffs number in the dozens, not millions. If MEMORY.md ever bloats, that's the signal to compact, not now. -->

3. **Print the same handoff in chat** so the user can read it without opening the file.

## Reading a handoff in a fresh session

When the user asks to resume, do this before anything else — before answering, before exploring, before touching code:

1. Read the newest `handoff-*.md` in this repo's memory directory. Get the path from the handoff line in `MEMORY.md`; if that line is missing, `Glob` for `handoff-*.md` and take the last filename alphabetically (the timestamp sorts).
2. Read whatever it names under "Key files for next session", including the plan file if there is one.
3. Report the "Pick up here" line back to the user in one sentence, then proceed.

If no `handoff-*.md` exists, say so plainly — do not reconstruct a summary from `git log` or the filesystem. There is nothing to resume from.

Older handoffs are the history: same `Glob`, read further back only if the newest one references something you need.

## Output template — use exactly this structure, every time

```
# Session Handoff — <one-line title of what this session was about>

## Where it started
<2-3 sentences: what the user asked for, key framing or constraints that emerged>

## Decisions locked + what shipped
- <decision or change> — <why, and where it lives (absolute path if a file)>
- ...

## Key files for next session
- `<absolute path>` — <why the next agent should read this first>
- Plan file: `<path>` (if a plan drove the session)
- Memory files touched: `<paths>` (if any)

## Running state
- Background processes: <shell IDs + what they are + how to kill> — or "none"
- Dev servers / ports: <url + port> — or "none"
- Open worktrees / branches: <paths> — or "none"

## Verification — how to confirm things still work
- `<command>` — <expected outcome>
- ...

## Deferred + open questions
- Deferred: <item> — <why pushed to later>
- Open: <question needing the user's input> — <context>

## Pick up here
<1-2 sentences: the single most likely next action for a fresh agent>
```

## Hard rules

1. **Write to project memory and print in chat — both, every time.** Never write the handoff anywhere else (no `~/.claude/handoffs/`, no file in the repo).
2. **Never invent state.** If a section has nothing to report, write "none" — do not omit the section. Structure stability is the whole point.
3. **Absolute paths always.** The next agent may have a different working directory.
4. **If a plan file drove the session, name it first** in "Key files" so the next agent reads it before anything else.
5. **No emojis, no hype, no "great job" summaries.** Terse and concrete — paths, commands, shell IDs, decisions. Match the tone of a seasoned engineer handing off at end-of-shift.
6. **Background process IDs are critical.** If you started any `run_in_background` shells, their IDs must appear in "Running state" with the kill command — the next agent cannot find them otherwise.

## Anti-patterns — do not do these

- Summarizing the last 3 turns and calling it a handoff.
- Listing files by relative path.
- Skipping the "Running state" section because "nothing is running" — write "none" instead.
- Appending a second handoff line to `MEMORY.md` instead of replacing the existing one. The index holds one pointer; the log holds the history.
- Editing or deleting a previous `handoff-*.md`. They are append-only log entries.
- Adding a "what went well / what went poorly" retrospective. This isn't a retro.
- Recommending next steps beyond the single "Pick up here" line. The next agent decides; you just hand off.
