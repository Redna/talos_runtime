# X-ray Formal Model Design

## Problem

X-ray has two conflicting data pipelines (live SSE streaming vs. Spine trajectory polling) that fight over the same DOM, producing broken renders: accumulated thinking blocks, duplicated tool calls, orphaned content, and `<thinking>` tag parsing hacks.

## Root Cause

No formal data model. Events are ad-hoc strings (`stream_token`, `thinking_token`, `tool_call`) mapped directly to DOM mutations. There is no intermediate representation, no concept of a "turn", and no single source of truth.

## Solution

Replace both pipelines with a single authoritative source: Spine's `/trajectory` endpoint, polled periodically. No live streaming to X-ray. The display model is derived from the OpenAI Chat Completion message format.

## Data Model

### Source: OpenAI Chat Completion message

Each message from Spine's `/trajectory` is a `ChatCompletionMessage`:

```python
@dataclass
class ChatCompletionMessage:
    role: str                        # "system" | "user" | "assistant" | "tool"
    content: str | None              # clean text, no <thinking> tags
    reasoning: str | None            # extracted thinking/reasoning content
    tool_calls: list[ToolCall] | None
    tool_call_id: str | None         # role="tool" only
    name: str | None                 # role="tool" only

@dataclass
class ToolCall:
    id: str
    type: str                       # always "function"
    function: FunctionCall

@dataclass
class FunctionCall:
    name: str
    arguments: str                   # JSON string
```

### Derived: Display Turn

The client groups messages into `Turn` objects for rendering:

```typescript
type Turn =
  | { type: "system", message: Message }
  | { type: "user", message: Message }
  | { type: "assistant",
      message: Message,
      thinking: string | null,       // message.reasoning, or extracted
      displayContent: string | null, // message.content, stripped
      toolResults: Message[] }
  | { type: "orphan_tools", messages: Message[] }

type Message = {
  role: string
  content: string | null
  reasoning: string | null
  tool_calls: ToolCall[] | null
  tool_call_id: string | null
  name: string | null
}
```

## Changes

### 1. Spine: Extract `reasoning` field in `/trajectory`

In `control_plane.py`, parse `<thinking>` tags from content and split into `reasoning` and `content`:

```python
def _extract_reasoning(content: str) -> tuple[str, str | None]:
    """Split <thinking>...</thinking> tags from content."""
    if not content:
        return "", None
    import re
    pattern = r"<thinking>([\s\S]*?)</thinking>"
    matches = re.findall(pattern, content)
    if not matches:
        return content, None
    reasoning = "\n".join(matches)
    cleaned = re.sub(pattern, "", content).strip()
    return cleaned, reasoning
```

Trajectory endpoint returns `reasoning` as a separate field in each message dict.

### 2. Gate: Remove X-ray streaming events

Remove all `_xray_broadcast` calls from the chat completions handler for content events. Keep only:
- `think_start` / `think_end` (for status indicator)
- `state_update`
- `container_status`
- `commit_info`
- `event`

Remove: `stream_token`, `thinking`, `thinking_token`, `tool_call`, `tool_result`, `trajectory`.

### 3. X-ray client: Poll-only architecture

Remove `_subscribe_gate_stream` entirely. Keep:
- `_poll_spine_state` (status, controls)
- `_poll_spine_trajectory` (primary data source)
- `_poll_spine_events` (event log)
- `_poll_health_probes` (container dots)
- `_poll_spine_commit` (git info)

WebSocket remains for the above non-trajectory events.

### 4. X-ray JS: Single render path

Replace all streaming DOM mutation functions (`appendLiveToken`, `appendThinkingToken`, `renderThinkingBlock`, `appendLiveToolCall`, `createAssistantBubble`, `streamThinkBuffer`) with:

```javascript
function onTrajectoryUpdate(messages) {
  const turns = buildTurns(messages);
  renderTurns(turns);
}
```

`buildTurns()` groups consecutive messages: assistant + following tool results = one turn. `renderTurns()` clears the transcript and rebuilds from the turn model.

Each assistant turn renders:
- Thinking block (from `reasoning` field, collapsible)
- Content (from `content` field, collapsible if >800 chars)
- Tool calls (from `tool_calls`, with resolved argument formatting)
- Tool results (from grouped tool messages, matched by `tool_call_id`)

### 5. CSS: Turn styling

```css
.turn {
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.turn .msg-tool {
  margin-left: 16px;
}
```

## What Gets Removed

- Gate: `stream_token` broadcast, `thinking` broadcast, `thinking_token` broadcast, `tool_call` broadcast, `trajectory` broadcast (already removed), `Calling: X` synthetic text, `<thinking>` tag embedding in response content
- X-ray JS: `appendLiveToken`, `streamThinkBuffer`, `appendThinkingToken`, `renderThinkingBlock`, `appendLiveToolCall`, `createAssistantBubble`, `startThink`/`endThink` DOM logic, `finishThinking`, `parseThinkingContent`, `appendToAssistantBody`
- X-ray JS: `thinkActive`, `lastThinkEnd`, `currentAssistantEl`, `currentThinkingEl` state variables
- X-ray client: `_subscribe_gate_stream`, `_subscribe_gate_state`