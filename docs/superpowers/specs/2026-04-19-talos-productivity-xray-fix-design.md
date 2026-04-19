# Talos Productivity & X-Ray Fix

## Problem Statement

After 8 hours of autonomous operation, Talos completed only 4 commits across 8,420 turns. The root cause is a "groundhog day loop": fold destroys nearly all context → agent re-reads the same files → context fills again → fold destroys → repeat. 17,400 fold calls occurred in 8 hours. The X-ray UI also suffers from flickering and WS instability due to trajectory re-rendering on every state update.

### Issues

1. **Fold destroys too much context** — collapses 2,270+ messages to 3, leaving the agent with no material to continue work
2. **No context backpack** — the fold synthesis prompt gives the LLM almost nothing to compress, producing useless syntheses
3. **X-ray trajectory re-renders on every WS event** — causing flickering with large message counts
4. **X-ray WS instability** — SSE timeout mismatch, state polls generating excessive WS pushes
5. **Spend not tracked to memory** — Gate reports $8.50 spent but financial_ledger.json shows $0

## Design

### 1. Rich Fold Prompt

When `_enforce_fold` triggers in `spine/stream.py`, inject a context backpack into the fold synthesis request. The backpack contains:

- Current focus string
- Memory keys (read from `/memory/agent_memory.json` via MemoryStore)
- Last 3 un-shedded tool results from the active window
- Turn count and tokens used
- File tree snapshot of `/app` (top 2 levels, via `ls`)

**Implementation**: Modify `_enforce_fold` to build a backpack string and include it in the forced fold prompt message. The fold still collapses to 3+2 messages (see item 2), but the LLM now has rich material to compress into a useful synthesis.

The backpack is appended to the system message or as a separate user message before the fold tool call, giving the LLM all key context to produce an informative DELTA-pattern summary.

### 2. Active Window Preservation During Fold

Instead of collapsing to 3 messages (system + genesis + synthesis), preserve the last 2 tool results:

After fold:
- `messages[0]` = system prompt
- `messages[1]` = genesis
- `messages[2]` = last tool result (from active window, un-shedded)
- `messages[3]` = second-to-last tool result
- `messages[4]` = assistant fold synthesis

**Implementation**: Modify `apply_fold` to accept and preserve the last 2 tool messages from the pre-fold message list. The synthesis still replaces the conversation, but these anchor messages survive, giving the agent immediate continuity.

### 3. X-Ray Trajectory Debounce

`renderTrajectory` in `xray/static/app.js` currently re-runs on every `trajectory` WebSocket message. With large message counts, this causes visible flickering.

Changes:
- Only re-render when `totalCount` or `showingCount` changes, OR on `think_end` events
- `state_update` events only call `renderState()` (numeric displays), never `renderTrajectory`
- Add a last-rendered key check (already partially implemented with `lastTrajectoryKey`)

### 4. X-Ray SSE Stability

Changes in `xray/xray_client.py`:
- Match SSE subscribe timeout to Gate's 1800s (currently using default httpx timeout)
- Batch state polls: only push a WS state_update if the state has actually changed since the last push (diff check)

Changes in `xray/static/app.js`:
- WS reconnect delay already increased to 5s (previously deployed)

### 5. Spend Tracking to Memory

On each `think_end` event processed by the X-ray client, record the token spend into the agent's `/memory/financial_ledger.json` via the Spine control plane.

**Implementation**: Add a small utility in `spine/control_plane.py` that writes spend data to the memory JSON file. Called from the think handler after each successful LLM call. Alternatively, the cortex agent itself can track this via the `store_fact` tool if the HUD includes spend data — no Spine changes needed if the HUD already shows spend.

Simpler approach: include spend in the HUD data (`_format_hud`), so the agent sees it in context and can choose to store it. This requires adding a `spend` field to `HUDData` and populating it from Gate's response headers.

## Files Affected

| File | Changes |
|------|---------|
| `spine/stream.py` | Rich fold prompt, backpack injection, `apply_fold` preserves last 2 tool results |
| `spine/ipc_types.py` | Add `spend` field to `HUDData` |
| `gate/app.py` | Include spend in think_end broadcast |
| `xray/static/app.js` | Trajectory debounce, state_update only calls renderState |
| `xray/xray_client.py` | SSE timeout match, state diff batching |

## Out of Scope

- Telegram notice persistence (current one-shot behavior accepted)
- Memory slot limit increase (50 is sufficient if agent uses it)
- Constitution changes (P9 already mandates store_memory)