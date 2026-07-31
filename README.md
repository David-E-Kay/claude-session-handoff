# claude-session-handoff

A Claude Code skill + hook pair for wrapping up a session cleanly before you `/clear` or run out of context — and picking it back up in the next one.

- **`skills/session-handoff/SKILL.md`** — runs in two directions. **Writing:** produces a structured handoff summary (decisions, key files, running state, verification steps, open questions) and stores it in the repo's project memory directory, so there's nothing to copy-paste. **Reading:** when you explicitly ask to resume, loads that stored handoff back into a fresh session.
- **`hooks/context-threshold-warn.py`** — a `UserPromptSubmit` hook that watches token usage and nudges you to run the handoff skill once you cross 120k tokens, before context quality degrades.
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
   If you already have `UserPromptSubmit` or `PreToolUse` arrays, append these entries rather than replacing the arrays. Use an absolute path (not `~`) on Windows.

   The second hook is optional — skip it if you don't run subagent-orchestrated plans and only want the prompt-time warning.

4. Restart/start a new Claude Code session for the hooks and skill to take effect.

## Usage

Two phrases, one loop.

**Ending a session** — say **"session handoff"** (or "wrap up session", "hand off"). The skill writes the handoff to project memory and prints it in chat. Then `/clear` or quit; nothing to copy.

Run it whenever the next thing you'd do is `/clear`, `/compact`, or close the window — and whenever the context hook nudges you at 120k. Running it more than once per session is fine.

**Starting the next one** — say **"resume from before"** (or "resume", "pick up where we left off", "continue from last time", "catch me up", "where did we leave off", "load the last handoff"). The skill reads the newest stored handoff, opens the files it names, and tells you where to pick up.

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

## Credits

`skills/session-handoff/SKILL.md` credit: [Nate Herk](https://www.linkedin.com/in/nateherkelman/).

The persistence model — append-only memory log plus a small budget of it read at wake time — is borrowed from [OptMem](https://github.com/VictorTaelin/OptMem) by [Victor Taelin](https://github.com/VictorTaelin).
