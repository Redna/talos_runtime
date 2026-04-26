# Design: Identity-First System Prompt + Minimal Initial User Message

**Date:** 2026-04-26
**Scope:** `talos/spine/constitution.py`, `talos/spine/ipc_server.py`

## Problem

1. The system prompt currently concatenates `CONSTITUTION.md` before `identity.md`, which presents rules before self-concept.
2. The message stream starts with only a system prompt — no user message exists to anchor the agent's first turn. The only user message ever traced was a manual `"test"` API call.

## Design

### 1. System Prompt Order: Identity Before Constitution

**File:** `talos/spine/constitution.py`

Change `load_system_prompt()` to concatenate in this order:

```
{identity}

{constitution}
```

**Rationale:** The LLM internalizes "who am I" before absorbing "what must I do". Identity primes the self-model; constitution extends it. This matches how the agent conceptually thinks of itself.

### 2. Synthetic User Message on First Turn

**File:** `talos/spine/ipc_server.py`

When the cortex makes its first `think()` IPC call, inspect the message stream before calling `build_payload()`. If the stream contains **no user message** (`role != "user"` in any message), inject one:

```json
{"role": "user", "content": "[HUD] turn=1 context_pct=0.04 urgency=nominal memory_files=7 focus=none"}
```

Use the actual HUD data from the `think` request parameters.

**Rationale (Option C — Minimal HUD Dump):**
- Gives the model a minimal state anchor without prescribing action
- Forces autonomous intent generation from raw HUD numbers
- After the first turn, a user message exists in the stream, so no further synthetic injection occurs
- Zero editorial direction — the agent decides what to do from its own state

### 3. X-ray Visibility

No xray changes required. The gate's `MessageTraceWriter` already traces user messages. After this change, the stream tab will show:

1. `system` — identity + constitution (collapsed, scrollable)
2. `user` — `[HUD] turn=1 ...` (collapsed)
3. `assistant (turn 1)` — first autonomous reasoning

## Implementation Notes

- The injection happens in `IPCServer._handle_request()` inside the `"think"` branch
- Check for existing user messages before injecting (to avoid double-injection on restart)
- The injected message should be added via `stream.add_message()` so it becomes part of the stream state
- The HUD data is already available in `params["hud_data"]` at this point

## Files Modified

| File | Change |
|------|--------|
| `talos/spine/constitution.py` | Swap concatenation order (identity first) |
| `talos/spine/ipc_server.py` | Auto-inject HUD user message on first think |
