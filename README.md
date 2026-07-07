# claude-session-handoff

A Claude Code skill + hook pair for wrapping up a session cleanly before you `/clear` or run out of context.

- **`skills/session-handoff/SKILL.md`** — produces a structured, chat-only handoff summary (decisions, key files, running state, verification steps, open questions) so a fresh Claude Code session can pick up where the last one left off.
- **`hooks/context-threshold-warn.py`** — a `UserPromptSubmit` hook that watches token usage and nudges you to run the handoff skill once you cross 120k tokens, before context quality degrades.
- **`hooks/context-threshold-handoff-task.py`** — a `PreToolUse` hook (matcher `Task`) that catches the same threshold *between* delegated tasks in a subagent-orchestrated run, where no user prompt fires to trigger the hook above.

They work together but none require each other: the skill can be triggered manually at any time by saying "session handoff" / "wrap up session"; the hooks just automate *when* to remember to do that.

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

4. Restart/start a new Claude Code session for the hooks to take effect.

## Notes

- Each hook's `THRESHOLD_TOKENS` (default 120,000) is a fixed cutoff, not model-aware. Adjust it in the scripts if you're on a smaller context window (set both to keep them in sync).
- The skill is chat-output only by design — it never writes files or updates memory, so it's safe to run repeatedly.

## How the two hooks divide the work

`UserPromptSubmit` only fires when you send a message. In a long autonomous run (subagents orchestrated with no per-task user turn) it never sees the threshold crossing until you next type. The `PreToolUse:Task` hook fills that gap: it fires right before the orchestrator spawns the *next* subagent — the natural "between task N and N+1" boundary — and measures the main session's cumulative tokens live at that instant.

Why `PreToolUse:Task` and not `SubagentStop`? A `SubagentStop` hook can *measure* the main context, but its output does **not** reach the orchestrator's context, so it can't deliver the warning. `PreToolUse` output (via `additionalContext`) does reach the orchestrator. So the warning rides in just before the next delegation rather than just after the last one — same boundary, and the channel that actually works.

Both hooks stay silent below the threshold: the scripts run, but they print nothing, so they inject **zero** tokens until a warning actually fires (~100 tokens when it does).

## Credits

`skills/session-handoff/SKILL.md` credit: [Nate Herk](https://www.linkedin.com/in/nateherkelman/).
