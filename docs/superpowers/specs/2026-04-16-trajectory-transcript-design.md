# Trajectory Transcript — Live Agent Conversation View

**Date**: 2026-04-16  
**Status**: Approved  

## Problem

The X-ray dashboard shows a flat token stream and tool call names, but provides no visibility into the agent's full conversation context — what the agent sees (system prompt, focus, tool results) and the trajectory of its thinking across turns. The user cannot follow the agent's reasoning or understand why it made certain decisions.

## Design

### Approach

Intercept the LLM completions request at the Gate. When `POST /v1/chat/completions` arrives, broadcast the `messages` array as a `trajectory` event. The gateway already receives the full conversation context on every request — no new channels or endpoints needed.

### Event Model

**New event type: `trajectory`**

Emitted by Gate when a completions request arrives, before proxying to the backend.

```json
{
  "type": "trajectory",
  "turn": 7,
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "... + HUD"},
    {"role": "assistant", "content": "...", "tool_calls": [{"id": "...', "function": {"name": "read_file", "arguments": "{...}"}}]},
    {"role": "tool", "tool_call_id": "...", "content": "..."},
    ...
  ],
  "model": "talos",
  "ts": 1234567890.0
}
```

This is a full snapshot — the dashboard replaces its entire transcript with each new event.

**Content truncation**: Gate truncates `role: "tool"` messages to 2000 chars, appending `[...truncated, X chars total]` if longer. This prevents bloating the WebSocket while preserving most output.

**Existing response events** (`stream_token`, `tool_call`, `think_end`) remain unchanged. The dashboard appends these to the current assistant bubble in the transcript.

### Frontend: Chat Transcript Panel

Replace the current `#stream-panel` with a transcript panel showing the full conversation as role-colored chat bubbles.

**Message rendering by role**:

| Role | Visual | Collapsed | Expanded |
|---|---|---|---|
| `system` | Dim gray, "SYSTEM" label | First 3 lines | Full text |
| `user` | Blue-tinted, "FOCUS" label | First 5 lines + HUD | Full text |
| `assistant` (text) | Green-tinted, "ASSISTANT" label, turn number | First 5 lines | Full text |
| `assistant` (tool_calls) | Green + yellow sub-bubbles per call | Tool names + arg keys | Full arguments JSON |
| `tool` | Dark bubble, tool name, success/fail icon | First 5 lines | Full output |

**Auto-scroll**: New messages scroll to bottom. User scrolling up pauses auto-scroll; scrolling back to bottom resumes it.

**Live update flow**:

1. `trajectory` event → replace entire transcript with rendered messages → scroll to bottom
2. `stream_token` → append token text to last assistant bubble
3. `tool_call` → append tool call sub-bubble to last assistant bubble
4. `think_end` → finalize last assistant bubble with token metadata

### Edge Cases

- **Large tool outputs**: Truncated to 2000 chars by Gate. Collapse/expand handles the rest.
- **Shedding**: Older messages may have arguments stripped or outputs truncated. The dashboard shows what the agent actually sees — the shedded version. No special handling.
- **Context fold**: Next trajectory will have drastically fewer messages. Dashboard replaces transcript. A "[context folded]" notice is shown when message count drops by more than half.
- **Empty assistant content**: LLM returns only tool calls, no text. Bubble shows tool call sub-bubbles only.
- **Multiple completions per turn**: Each generates a new trajectory event. Dashboard replaces transcript each time.

### Summary of Changes

| Component | Change |
|---|---|
| `gate/app.py` | Broadcast `trajectory` event on completions request, truncate long tool content |
| `xray/static/app.js` | Add `trajectory` handler, render chat bubbles with collapse/expand, append response events to assistant bubble |
| `xray/static/index.html` | Replace `#stream-panel` contents with `#transcript` container |
| `xray/static/style.css` | Chat bubble styles per role, collapse/expand, tool call sub-bubbles |

**No changes to**: Spine, Cortex, X-ray client (`xray_client.py`), X-ray app (`app.py`).