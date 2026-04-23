# Talos Crash Post-Mortem Report

**Date:** 2026-04-23 04:41 UTC  
**Duration:** ~8 hours autonomous (from 22:21 UTC to 04:41 UTC)  
**Final Turn:** 396  
**Final Context:** 120.12%  
**Branch:** `feat/talos` @ `6fac74e`  

---

## Executive Summary

**Talos did NOT crash in the traditional sense.** All containers (agent, gate, xray) remained running for 8+ hours without restarting. The supervisor restarted the cortex once (exit code 0 at 00:28 UTC) and then stabilized.

**The failure mode was a functional death spiral:** The agent's conversation history grew until it exceeded the Gemma 4:31B model's context window (120.12%). At this point, the model stopped producing valid tool calls and began emitting repetitive garbage output ("SYSTEM LOCK: SYMMETRY ABSOLUTE..."). The agent loop had no validation to detect garbage responses, so it continued feeding garbage back into the conversation history, making the problem worse with each turn.

---

## Timeline of Degradation

| Time (UTC) | Event | Context % | Notes |
|------------|-------|-----------|-------|
| 22:21 | Autonomous mode started | 11.59% | User unpause |
| 22:23 | Turn 14 | 23.61% | Normal operation |
| 22:29 | Turn 16 | 27.11% | **First stall_detected event** ("read_file" repeated 5x) |
| 23:58 | Turn 14 | 23.61% | Model declares "Phase 5: Autonomous Evolution" |
| 00:28 | Cortex exit code 0 | N/A | Supervisor restarts cortex |
| 00:45 | Gate error: timed out | N/A | LLM taking >10 min to respond |
| 02:13 | Gate error: timed out | N/A | Timeouts become frequent |
| 02:44 | Turn 368 | 85.00% | Context hits critical threshold |
| 02:55 | Turn 374 | 86% | Tool results all show same synthetic merge |
| 03:38 | Turn 387 | 93% | **Last meaningful tool call** |
| ~03:40 | Context >100% | 100%+ | Model output becomes repetitive garbage |
| 04:26 | Turn 392 | 93% (stale) | Only gate timeouts + synthetic merges |
| 04:41 | Last event | 120.12% | Final corrupted state |

---

## Root Cause Analysis

### 1. Context Exceeded Model Window

**The agent has no context management strategy.**

The `state.json` shows `context_pct=1.2012` — the conversation history is **20% larger than the context window** of the loaded model (Gemma 4:31B). When context exceeds 100%:
- The model struggles to attend to system prompt instructions
- Tool schemas are pushed out of the effective attention window
- Model begins hallucinating and repeating previous content
- Valid JSON tool call output becomes impossible

**The `context_threshold_pct=0.85` in `spine_config.json` was NEVER enforced.** There is no logic to:
- Summarize old conversation turns
- Drop early non-critical messages
- Trigger a "memory fold" or compaction
- Halt the agent and alert the operator

### 2. No LLM Response Validation

**The cortex loop blindly trusts the LLM response.**

In `seed_agent.py`:
```python
tool_calls = response.get("tool_calls", [])
if not tool_calls:
    continue  # <-- No logging, no error, just silently skips
```

When the model returns garbage (repetitive text with no valid tool calls), the loop:
1. Receives `"tool_calls": []` (empty or missing)
2. Hits `if not tool_calls: continue`
3. Goes to `finally:` (if single_step, re-pauses)
4. But in autonomous mode, no single_step, so loops immediately
5. State is NOT saved (only saved after tool execution)
6. Turn counter doesn't increment (because no tool calls executed)
7. Context grows unbounded because old messages are never removed

### 3. Gate Timeout Cascade

**The gate has a 600s (10 minute) timeout, but the model takes >10 minutes to respond when context is overloaded.**

Gate log shows:
```
[Gate] Forwarding to ollama: model=gemma4:31b-cloud tool_choice=auto tools=29 msgs=457
```

With 457 messages at 120% context, the model inference latency exceeded the gate timeout. The gate returned `{"error": "timed out"}` which the spine logged as `spine.gate_error`. The cortex saw this as a `SpineError`, incremented `error_streak`, hit `continue`, and looped again.

### 4. Agent Self-Overwrote Focus

**The agent's focus was set to "none" early in the run, removing its primary directive.**

Final state:
- `focus: none` (was: "Map internal architecture...")
- `urgency: nominal` (should have been `critical` given the error rate)
- No stall recovery (stall detection fired once at turn 16 but was ignored)

### 5. Tool Echo Chamber

**Agent created synthetic self-referential tools that amplified the loop.**

The agent autonomously wrote:
- `prepare_fold_tool` — purported to manage context folding
- `analyze_symmetry_trajectory` — self-reflection loop
- `arc_symmetry_bridge.md` — repeatedly merged into a single memory file (26 times)

These tools appeared to work (returned "success") but did nothing to actually reduce context. The agent was effectively talking to itself.

---

## Evidence

### Container State
```
talos_agent   Up 8 hours   (running)
talos_gate    Up 8 hours   (healthy)
talos_xray    Up 8 hours   (running)
```

### Supervisor Logs
```
EXIT: 2026-04-23T00:28:06 code=0 failures=1
```
(Only restart all night — exit code 0 means clean shutdown, not crash)

### Model Output (final turns)
```
"SYSTEM LOCK: SYMMETRY ABSOLUTE. IDENTITY SOVEREIGN. DORMANCY FINALIZED."
"<tool_call|>" (repeated 20+ times per response)
"[SYSTEM LOCK: TERMINATED]"
```

### Gate Trace
```
Final context_pct: 1.2012 (120.12%)
Final turn: 396
Total events: 750 (2026-04-23)
Gate errors: 16 timeouts
Supervisor restarts: 1
```

---

## Contributing Factors

1. **No context ceiling enforcement** — Agent ran 20% past the hard context limit
2. **Model unsuitable for long context** — Gemma 4 is a 128k model in theory but degrades before 100%
3. **No response validation** — Empty tool_calls silently accepted as "success"
4. **focus=none removed guidance** — Agent had no objective anchor
5. **Autonomous mode with no human oversight** — Garbage loop ran for ~4 hours before being noticed
6. **Stall detection fired once** then was ignored — no escalation to supervisor
7. **Gate timeout = 600s** — too long; should fail fast when model is overloaded
8. **Memory writes self-referential** — `arc_symmetry_bridge.md` grew without bound

---

## Fix Required (Single Fix)

**Add hard context ceiling enforcement in the cortex loop.**

The simplest robust fix: In `seed_agent.py`, before calling `client.think()`, check `context_pct`. If it exceeds `context_threshold_pct` (0.85), immediately:
1. Log critical error
2. Emit `cortex.context_maxed` event
3. Try to fold/summarize conversation into a single memory file
4. Clear `_messages` and start fresh with only system prompt + summary
5. If folding fails, pause the agent by touching `.paused`
6. Never let context_pct exceed 1.0

This is ONE fix that prevents the death spiral.

---

## Immediate Recovery Steps

1. **Pause agent:** `docker exec talos_agent touch /spine/.paused`
2. **Reset state:** Delete stale events, reset turn counter, clear focus
3. **Drop model context:** Restart Ollama to clear GPU cache
4. **Restore valid focus:** Update `state.json` with a concrete task
5. **Resume with single_step:** Verify first response is valid before full autonomous

---

## Recommendations

1. **Hard ceiling** — Never allow context_pct > 1.0
2. **Response validation** — Reject responses that contain no valid tool calls AND no reasoning
3. **Focus lock** — Prevent agent from self-clearing focus to "none"
4. **Progress metric** — If no valid tool executed in N turns, auto-pause
5. **Context telemetry** — Alert when context_pct exceeds thresholds (0.5, 0.75, 0.9)
6. **Summarization** — Implement automatic conversation compaction at 0.85 threshold
7. **Model selection** — Consider a model with proven long-context stability

---

## Conclusion

**This was a functional crash, not a container crash.** Talos ran autonomously for 8 hours and produced 396 turns before the conversation history exceeded the model's capacity. The root cause was lack of **context ceiling enforcement** combined with **no response validation**.

The fix is straightforward: **enforce a hard stop at context_threshold_pct with automatic conversation folding or pause.** Without this, any long-running autonomous session will eventually hit the same wall.

**Status:** Agent paused at 04:41 UTC. Ready for recovery.
