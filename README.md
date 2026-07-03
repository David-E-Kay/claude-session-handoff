# claude-session-handoff

A Claude Code skill + hook pair for wrapping up a session cleanly before you `/clear` or run out of context.

- **`skills/session-handoff/SKILL.md`** — produces a structured, chat-only handoff summary (decisions, key files, running state, verification steps, open questions) so a fresh Claude Code session can pick up where the last one left off.
- **`hooks/context-threshold-warn.py`** — a `UserPromptSubmit` hook that watches token usage and nudges you to run the handoff skill once you cross 120k tokens, before context quality degrades.

They work as a pair but don't require each other: the skill can be triggered manually at any time by saying "session handoff" / "wrap up session"; the hook just automates *when* to remember to do that.

## Install

1. **Copy the skill:**
   ```
   cp -r skills/session-handoff ~/.claude/skills/session-handoff
   ```

2. **Copy the hook:**
   ```
   cp hooks/context-threshold-warn.py ~/.claude/hooks/context-threshold-warn.py
   ```

3. **Register the hook** by merging this into your `~/.claude/settings.json` (create the file if it doesn't exist):
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
       ]
     }
   }
   ```
   If you already have a `UserPromptSubmit` array, append this hook entry to it rather than replacing the array. Use an absolute path (not `~`) on Windows.

4. Restart/start a new Claude Code session for the hook to take effect.

## Notes

- The hook's `THRESHOLD_TOKENS` (default 120,000) is a fixed cutoff, not model-aware. Adjust it in the script if you're on a smaller context window.
- The skill is chat-output only by design — it never writes files or updates memory, so it's safe to run repeatedly.

## Credits

`skills/session-handoff/SKILL.md` credit: [Nate Herk](https://www.linkedin.com/in/nateherkelman/).
