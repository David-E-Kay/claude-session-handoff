# claude-session-handoff

A Claude Code skill + hook pair for wrapping up a session cleanly before you `/clear` or run out of context — and picking it back up in the next one.

- **`skills/session-handoff/SKILL.md`** — runs in two directions, plus lookup. **Writing:** produces a structured handoff summary (decisions, key files, running state, verification steps, open questions) and stores it in the repo's project memory directory, so there's nothing to copy-paste. **Reading:** when you explicitly ask to resume, loads that stored handoff back into a fresh session. **Lookup:** when you ask about earlier work and the handoff doesn't have the answer, looks it up in the chapter index.
- **`hooks/context-threshold-warn.py`** — a `UserPromptSubmit` hook that watches token usage and nudges you to run the handoff skill once you cross 120k tokens, before context quality degrades.
- **`hooks/handoff-save.py`** — a `Stop` hook that does the saving. The skill has Claude print the handoff once in chat; when that reply finishes, the hook writes it to project memory and updates the index. No extra model steps, so a handoff costs one reply instead of four or five. **Required** for the skill to save anything.
- **`hooks/chapters.py`** — an optional `Stop` hook that records every session, reply by reply, into a small local database (the *chapter index*). When a handoff leaves out a detail you later need, Claude can look it up there, one step at a time. It never runs a model unless you turn on the optional local summaries. See [The chapter index](#the-chapter-index).
- **`hooks/context-threshold-handoff-task.py`** — a `PreToolUse` hook (matcher `Task`) that catches the same threshold *between* delegated tasks in a subagent-orchestrated run, where no user prompt fires to trigger the hook above.

They work together but none require each other: the skill can be triggered manually at any time by saying "session handoff" or "resume from before"; the hooks just automate *when* to remember to write one.

## Install

1. **Copy the skill:**
   ```
   cp -r skills/session-handoff ~/.claude/skills/session-handoff
   ```

2. **Copy the hooks:**
   ```
   cp hooks/context-threshold-warn.py ~/.claude/hooks/context-threshold-warn.py
   cp hooks/context-threshold-handoff-task.py ~/.claude/hooks/context-threshold-handoff-task.py
   cp hooks/handoff-save.py ~/.claude/hooks/handoff-save.py
   cp hooks/chapters.py ~/.claude/hooks/chapters.py
   ```

3. **Register the hooks** by merging this into your `~/.claude/settings.json` (create the file if it doesn't exist):
   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "python \"~/.claude/hooks/context-threshold-warn.py\""
             }
           ]
         }
       ],
       "Stop": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "python \"~/.claude/hooks/handoff-save.py\""
             },
             {
               "type": "command",
               "command": "python \"~/.claude/hooks/chapters.py\" record"
             }
           ]
         }
       ],
       "PreToolUse": [
         {
           "matcher": "Task",
           "hooks": [
             {
               "type": "command",
               "command": "python \"~/.claude/hooks/context-threshold-handoff-task.py\""
             }
           ]
         }
       ]
     }
   }
   ```
   If you already have `UserPromptSubmit`, `Stop` or `PreToolUse` arrays, append these entries rather than replacing the arrays. Use an absolute path (not `~`) on Windows.

   The second hook is optional — skip it if you don't run subagent-orchestrated plans and only want the prompt-time warning. The `chapters.py` entry is optional too — handoffs work without it.

4. **Fill the chapter index from past sessions** (optional, once):
   ```
   python ~/.claude/hooks/chapters.py import
   ```
   Reads every existing transcript under `~/.claude/projects/` and records it. Safe to re-run: already-recorded material is skipped. About a minute for a few hundred sessions. In PowerShell, `~` isn't expanded here; use `python $HOME\.claude\hooks\chapters.py import`.

5. **Allow the lookups without a prompt each time** (optional): add `"Bash(python ~/.claude/hooks/chapters.py:*)"` to `permissions.allow` in `~/.claude/settings.json`. This rule covers only this script, never `python` in general. Keep the `~` form even on Windows: the rule matches the command text Claude runs, which is written that way in the skill.

6. Restart/start a new Claude Code session for the hooks and skill to take effect.

## Usage

Two phrases, one loop.

**Ending a session** — say **"session handoff"** (or "wrap up session", "hand off"). Claude prints the handoff in chat and the `Stop` hook saves it to project memory; you'll see "Handoff saved to …". Then `/clear` or quit; nothing to copy.

Run it whenever the next thing you'd do is `/clear`, `/compact`, or close the window — and whenever the context hook nudges you at 120k. Running it more than once per session is fine.

**Starting the next one** — say **"resume from before"** (or "resume", "pick up where we left off", "continue from last time", "catch me up", "where did we leave off", "load the last handoff"). The skill reads the newest stored handoff, opens the files it names, and tells you where to pick up.

**Asking about earlier work** — ask plainly: "what did we decide about the database last week?", "what was the error when we tried the import?". Claude checks the handoff and memory first. Only if the answer isn't there does it look in the chapter index, one step at a time, and it tells you which chapter the answer came from.

Start unrelated work in the same repo and you say neither — nothing stale loads. See [How handoffs persist across sessions](#how-handoffs-persist-across-sessions) for why that's a phrasing decision rather than a judgment call.

## Notes

- Each hook's `THRESHOLD_TOKENS` (default 120,000) is a fixed cutoff, not model-aware. Adjust it in the scripts if you're on a smaller context window (set both to keep them in sync).
- Running the skill twice in a session is safe: it writes a new dated handoff file and repoints the single index line at it.

## How handoffs persist across sessions

Nothing to install and nothing to run. The skill writes to Claude Code's own per-project memory directory (`~/.claude/projects/<repo-slug>/memory/`, requires memory to be enabled):

- `handoff-<YYYY-MM-DD-HHMM>.md` — one immutable file per handoff. The append-only log.
- `MEMORY.md` — the index, which Claude Code loads into context at the start of every session **in that repo**. The skill keeps exactly one handoff line in it, pointing at the newest file.

So a fresh session in the repo already sees "there is a handoff, here's the one-line pick-up-here". To load the full thing, open the session with **"resume from before"** — or "resume", "pick up where we left off", "continue from last time", "catch me up", "where did we leave off". Older handoffs sit on disk, found by globbing `handoff-*.md`.

Resuming is opt-in **by phrasing, not by topic**. Ask to resume and the handoff loads; start unrelated work in the same repo and nothing stale comes with it. The distinction matters: gating on topic similarity would mean the agent judges whether your request "looks like" prior work, which is exactly the non-deterministic behavior this avoids.

That's why the `MEMORY.md` index line is deliberately descriptive rather than imperative. It names the file and shows the one-line pick-up-here (~20 tokens, always in context), but it does not tell the agent to open it. The skill's frontmatter triggers are the only gate.

This is the idea behind [OptMem](https://github.com/VictorTaelin/OptMem) — an append-only memory log plus a small budget of it read at wake time — minus the machinery. OptMem builds a binary summary tree over fixed-width records so a million memories still wake in 0.03s. A repo accumulates handoffs in the dozens, and `MEMORY.md` is already the wake read, so the tree and its compaction commands earn nothing here. If the index ever bloats, that's the point to revisit.

## How the two hooks divide the work

`UserPromptSubmit` only fires when you send a message. In a long autonomous run (subagents orchestrated with no per-task user turn) it never sees the threshold crossing until you next type. The `PreToolUse:Task` hook fills that gap: it fires right before the orchestrator spawns the *next* subagent — the natural "between task N and N+1" boundary — and measures the main session's cumulative tokens live at that instant.

Why `PreToolUse:Task` and not `SubagentStop`? A `SubagentStop` hook can *measure* the main context, but its output does **not** reach the orchestrator's context, so it can't deliver the warning. `PreToolUse` output (via `additionalContext`) does reach the orchestrator. So the warning rides in just before the next delegation rather than just after the last one — same boundary, and the channel that actually works.

Both hooks stay silent below the threshold: the scripts run, but they print nothing, so they inject **zero** tokens until a warning actually fires (~100 tokens when it does).

## The chapter index

A handoff is a summary written before the next question is known, so it will sometimes leave out a detail you later need. The chapter index is the backstop. After every reply, the `chapters.py` hook appends the new part of the session's transcript to `~/.claude/chapter-index.db`: one row per prompt you typed, holding Claude's replies, a numbered list of the actions it took, and any answers you gave in a question box. The raw output of those actions is never copied in; only the list of what was done. Recording costs zero Claude tokens and never slows a reply. Everything stays on your machine, alongside Claude Code's own transcripts.

Claude reads it only when the handoff and memory lack a fact, and climbs one rung at a time:

| Rung | Command | Typical size |
|---|---|---|
| 1 | Read the newest handoff | ~5k characters |
| 2 | `chapters.py list` — one line per chapter in this project | ~11k characters for 66 chapters; capped at 150 |
| 3 | `chapters.py show ID` — one chapter in full | ~4k characters (cap 6k, `--full` lifts it) |
| 4 | `chapters.py output ID N` — the raw result of action N, read back from the original transcript | ~0.4k characters (cap 3k) |

Sizes measured on 257 real sessions. The rungs are similar in size. The cost of going deeper is that each step is one more read on top of the last, so Claude stops as soon as it has the fact. Rung 4 needs the original transcript; if Claude Code has since deleted it, rung 4 says so, and rungs 2 and 3 still work.

**Optional one-line summaries.** By default each chapter's line in `list` is built from your prompt and the first sentence of Claude's reply. For a tidier line, set `CHAPTER_SUMMARY_MODEL` to a local [Ollama](https://ollama.com) model (for example `qwen2.5:1.5b-instruct`) in the `"env"` block of `~/.claude/settings.json`, and `CHAPTER_OLLAMA_URL` if Ollama isn't at `http://127.0.0.1:11434`. Nothing leaves your machine. To summarise chapters recorded before you turned it on, run `python ~/.claude/hooks/chapters.py summarise --missing`. Start Ollama first: if it's down, this runs silently for a long time and summarises nothing. The `"env"` block only applies inside Claude Code, so either set `CHAPTER_SUMMARY_MODEL` in that terminal as well, or ask Claude to run the command for you.

**Checking on it now and then.** `chapters.py status` shows when it last recorded, any recent errors, and how many unrecognised message shapes are waiting. A message shape is the form a line of the transcript takes; the recorder only starts chapters at shapes it knows are typed prompts, and logs anything new instead of guessing. `chapters.py unknowns` lists them with a sample. If one is harmless, hide it with `chapters.py unknowns --mark-reviewed SHAPE`. If it should have been a prompt, that's a bug to report.

**Repair.** `chapters.py rebuild --all` re-records every session from its transcript, for example after an update changes how chapters are split. If the database is ever damaged, delete it and run `import` again. Set `CHAPTER_INDEX_DB` to keep the database somewhere else.

**For other programs.** `chapters.py summary SESSION_ID` prints the session's latest chapter as JSON, so a tool such as a dashboard can read it without opening the database.

## Credits

`skills/session-handoff/SKILL.md` credit: [Nate Herk](https://www.linkedin.com/in/nateherkelman/).

The persistence model — append-only memory log plus a small budget of it read at wake time — is borrowed from [OptMem](https://github.com/VictorTaelin/OptMem) by [Victor Taelin](https://github.com/VictorTaelin).
