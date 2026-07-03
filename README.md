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

## Between-task awareness in plan-driven workflows

The hook only fires on `UserPromptSubmit`, and only checks usage at that single moment — it never re-fires mid-turn. In an auto/subagent-orchestrated workflow (subagents on a cheaper model, reviewed by a stronger one, no per-task human approval), there may be exactly one `UserPromptSubmit` for an entire multi-task stretch: whatever message kicked it off (e.g. "implement the rest of the plan").

Subagents run in their own isolated context window — only their returned output feeds back into the main/orchestrator session, which is what actually accumulates tokens over a long run. (Confirmed via the hook's own debug dump at `~/.claude/hooks/context-debug.json`, which only ever contains a literal typed `prompt`, never a synthetic subagent event.)

Because the hook's warning is injected as a standing instruction ("if you're over the threshold, flag it before continuing") that persists for the rest of that turn, the model can end up acting on it several tool calls and tasks later — at whatever point looks like a sensible pause — rather than the instant it was injected. That's the model choosing a moment within one firing, not the hook firing again.

Caveat: `context-debug.json` is overwritten on every firing, so this is inferred from the hook's own logic, not confirmed against a live multi-task run. To pin down the exact trigger next time, copy or timestamp that file each time it fires and compare against the transcript.

## Credits

`skills/session-handoff/SKILL.md` credit: [Nate Herk](https://www.linkedin.com/in/nateherkelman/).
