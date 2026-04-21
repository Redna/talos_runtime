# X-Ray JSONL Message Trace

Replace the complex streaming pipeline (Gate SSE token broadcasts + XRayClient multi-poll tasks + frontend dual rendering) with a simple JSONL file-based approach where Gate writes complete OpenAI-format messages and XRay reads them.

## Problem

The current x-ray message pipeline is overly complex:

1. **Gate** broadcasts individual tokens (`stream_token`, `thinking_token`, `tool_call`) via SSE during LLM streaming, plus reconstructs content for non-streaming responses
2. **XRayClient** runs 8 concurrent async tasks: 2 SSE subscribers, 3 HTTP polls, 1 health probe, 1 commit poll, 1 stats persist
3. **Frontend** has two render paths: live streaming (token-by-token assembly with `<thinking>` tag parsing, buffer management) and trajectory rendering (full rebuild from spine message list after think ends). These two paths fight each other (streaming during active think, trajectory takeover after think_end with a `pendingTrajectory` debouncing mechanism)

This makes the UI code hard to understand, fragile, and difficult to extend. The key insight: for steering an autonomous agent, you need to see what the LLM sees — complete messages with reasoning and tool interactions. Token-level streaming adds complexity without value for this use case.

## Solution

### JSONL Format

Daily files at `/data/messages/YYYY-MM-DD.jsonl`. Each line is a JSON object in OpenAI chat message format:

```jsonl
{"role":"system","content":"You are Talos...","_ts":"2026-04-19T10:00:00Z","_turn":0}
{"role":"user","content":"Begin your evolution","_ts":"2026-04-19T10:00:01Z","_turn":0}
{"role":"assistant","content":"I'll start by...","reasoning":"Let me think about what to do first...","tool_calls":[{"id":"call_abc","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"/app/main.py\"}"}}],"_ts":"2026-04-19T10:00:15Z","_turn":1}
{"role":"tool","tool_call_id":"call_abc","name":"read_file","content":"file contents here...","_ts":"2026-04-19T10:00:15Z","_turn":1}
```

- Standard OpenAI fields: `role`, `content`, `reasoning`, `tool_calls`, `tool_call_id`, `name`
- Metadata fields (prefixed `_`): `_ts` (ISO 8601 timestamp), `_turn` (spine turn number)
- `reasoning` is a top-level field on assistant messages containing the model's chain-of-thought (OpenAI format supports this)
- Tool results appear after their parent assistant message in the file

### Gate Writer

**When:** After each `/v1/chat/completions` call completes (both streaming and non-streaming).

**What to write:** The "delta" — new messages not yet written from this conversation. Specifically:
1. Messages from the request's `messages` array that appear after the last-written position
2. The assistant response message

**Deduplication:** Gate tracks `_last_written_msg_count` per conversation (keyed by the request's message array length). On each request, messages from index `_last_written_msg_count` onward are written, then the assistant response is appended. This naturally handles the spine's think loop: each `think()` call sends the full message array, and only new entries since the last write are emitted.

**Reasoning extraction:** For streaming responses, accumulate `delta.reasoning` tokens into a `reasoning` field. For non-streaming, read `message.reasoning` directly. Include it as a top-level field on the assistant message.

**Implementation:**

```python
class MessageTraceWriter:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir / "messages"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._last_written_count = 0
        self._current_date = ""
        self._file = None

    def _ensure_file(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            self._file = open(self.data_dir / f"{today}.jsonl", "a")
            self._current_date = today

    def write_messages(self, request_messages: list[dict], response_message: dict, turn: int = 0):
        self._ensure_file()
        ts = datetime.now(timezone.utc).isoformat()

        for msg in request_messages[self._last_written_count:]:
            line = {**msg, "_ts": ts, "_turn": turn}
            self._file.write(json.dumps(line) + "\n")

        self._last_written_count = len(request_messages)

        resp_line = {**response_message, "_ts": ts, "_turn": turn}
        self._file.write(json.dumps(resp_line) + "\n")
        self._file.flush()
```

**For streaming responses:** The gate's `stream_proxy()` generator accumulates reasoning and content tokens into local variables during iteration. After the stream completes (the generator returns), write the new request messages and the complete assistant response. The write happens in `log_completion()` (already called as a background task after streaming ends) or in a dedicated post-stream hook.

**For non-streaming responses:** Write directly after receiving the response, before returning. The `write_messages()` call happens after `resp_json` is available.

**Turn number:** The gate doesn't have direct access to spine's turn counter. It extracts `_turn` from the last user message's metadata or infers it from the request message count (each think cycle adds ~3 messages: assistant + tool_results + next user). Alternatively, spine could include a `X-Turn` header in its request to gate — simplest approach is to count from zero and increment each time `write_messages` is called.

### Infrastructure Changes

**Docker Compose:**
- Add `./xray_data:/data` volume mount to gate service (xray already has this)
- Add `DATA_DIR=/data` environment variable to gate

**Gate cleanup:**
- Remove `_xray_subscribers` list and `_xray_broadcast()` function
- Remove SSE xray endpoints: `/v1/xray/stream`, `/v1/xray/state`, `/v1/xray/events`
- Keep `/v1/xray/history` and `/v1/xray/history/{filename}` as they serve call logs (different concern)
- Remove all `_xray_broadcast()` calls from streaming and non-streaming completion handlers

### XRay Client Rewrite

Replace 8 concurrent tasks with 4:

| Task | Interval | Purpose |
|------|----------|---------|
| `_tail_message_trace` | 1s | Read new JSONL lines, broadcast `message` events |
| `_poll_spine_state` | 3s | Status, health, context bar data |
| `_poll_health_probes` | 10s | Container health dots |
| `_poll_spine_commit` | 30s | Git commit info |

**Removed tasks:**
- `_subscribe_gate_stream` (SSE token stream) — replaced by JSONL tail
- `_subscribe_gate_state` (SSE spend stream) — replaced by spine state poll
- `_poll_spine_trajectory` — replaced by JSONL tail
- `_poll_spine_events` — keep event list in snapshot but remove dedicated poll (events come from spine state)
- `_persist_token_stats` — remove (token stats available from spine state)

**File tailer implementation:**

Uses synchronous file I/O in an async loop (files are local, reads are small). Tracks file position via byte offset persisted between polls. On startup or day change, reads the current day's file from the beginning to build initial state.

```python
async def _tail_message_trace(self):
    while self._running:
        try:
            today = datetime.date.today().isoformat()
            path = self._data_dir / "messages" / f"{today}.jsonl"
            if path.exists():
                if path != self._current_trace_path:
                    self._current_trace_path = path
                    self._file_offset = 0
                with open(path, "r") as f:
                    f.seek(self._file_offset)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        msg = json.loads(line)
                        self._messages.append(msg)
                        if len(self._messages) > self._max_messages:
                            self._messages = self._messages[-self._max_messages:]
                        self.on_event({"type": "message", "message": msg})
                    self._file_offset = f.tell()
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[XRay] Skipping malformed JSONL line: {e}")
        except Exception:
            await asyncio.sleep(2)
        await asyncio.sleep(1)
```

**On WebSocket connect (full_snapshot):** Send the last N messages from the in-memory list plus state/commit/containers.

### Frontend Rewrite

**Remove:** All streaming-related code: `thinkActive`, `currentAssistantEl`, `currentThinkingEl`, `streamThinkBuffer`, `pendingTrajectory`, `startThink`, `endThink`, `appendLiveToken`, `appendThinkingToken`, `finishThinking`, `renderThinkingBlock` (streaming version), `appendLiveToolCall`, `appendError`, `parseThinkingContent` (no more `<thinking>` tag parsing).

**Keep/refactor:** `renderTrajectory` is simplified to just iterate messages and render each one. The `buildTurns` grouping logic is simplified. `makeCollapsibleBody` stays. `formatArgs` stays.

**New event handling:**

```javascript
case "message":
    appendMessage(msg.message);
    break;
case "full_snapshot":
    // Load messages from snapshot, render all
    renderAllMessages(snapshot.messages);
    break;
```

**`appendMessage(msg)`:** Render a single message based on its `role`:
- `system`: render system block
- `user`: render user block
- `assistant`: render assistant block with reasoning (collapsible), content, tool_calls
- `tool`: render tool result block with success/fail indicator

**Message rendering:** Each message is rendered as a self-contained block. Assistant messages show:
1. Reasoning block (if `msg.reasoning` exists) — collapsible, collapsed by default
2. Content text — collapsible if long
3. Tool calls — each with name + expandable arguments

Tool result messages show under the assistant turn they belong to (matched by `tool_call_id`).

**Turn grouping:** Same concept as current `buildTurns()` — group consecutive assistant + tool results into a turn div for visual cohesion. But simplified: no more dealing with both streaming partial state and trajectory full state.

### Data Flow

```
Before:
  Cortex ──think()──> Spine ──POST──> Gate ──stream──> SSE ──> XRayClient ──WS──> Browser
                                        (token by token)     (8 poll tasks)    (dual render)
  
After:
  Cortex ──think()──> Spine ──POST──> Gate ──writes──> JSONL file
                   (full msg array)         (complete messages)
                                                         
  XRayClient ──tails──> JSONL file ──WS──> Browser
              (1s poll)                   (append-only render)
```

### Error Handling

- **File not found:** XRay tailer gracefully handles missing files (agent not started yet)
- **Malformed JSONL lines:** Skip with a warning log
- **Day boundary:** Both gate writer and xray reader handle midnight UTC rollover
- **Container restart:** Gate reopens the file; XRay resets offset and rebuilds from file
- **Large files:** XRay only keeps last N messages in memory for snapshot (configurable, default 500)

### What Gets Removed

| Component | What's removed |
|-----------|---------------|
| `gate/app.py` | `_xray_subscribers`, `_xray_broadcast()`, all `_xray_broadcast` calls, `/v1/xray/stream` endpoint, `/v1/xray/state` endpoint, `/v1/xray/events` endpoint |
| `xray/xray_client.py` | `_subscribe_gate_stream()`, `_subscribe_gate_state()`, `_poll_spine_trajectory()`, `_poll_spine_events()`, `_persist_token_stats()` |
| `xray/static/app.js` | All streaming code: `startThink`, `endThink`, `appendLiveToken`, `appendThinkingToken`, `appendLiveToolCall`, `appendError`, `finishThinking`, `parseThinkingContent`, dual render path, `thinkActive`/`pendingTrajectory` state |

### What Stays

| Component | What's kept |
|-----------|-------------|
| `gate/app.py` | `/v1/xray/history`, `/v1/xray/history/{filename}` (call logs, different concern), `log_completion()` |
| `xray/xray_client.py` | `_poll_spine_state()`, `_poll_health_probes()`, `_poll_spine_commit()`, `get_full_snapshot()` |
| `xray/static/app.js` | `renderState`, `renderHealth`, `renderContainers`, `renderEvents`, `renderCommit`, `makeCollapsibleBody`, `formatArgs`, `buildTurns` (simplified), `renderToolResult` |
| `xray/app.py` | WebSocket server, broadcast loop, `/api/state`, `/api/command`, `/api/history` |

### Files Changed

1. **`gate/app.py`** — Add `MessageTraceWriter`, integrate into completions handler, remove streaming broadcast code and SSE endpoints
2. **`xray/xray_client.py`** — Replace with JSONL tailer, remove SSE subscriptions and trajectory/events polls
3. **`xray/static/app.js`** — Rewrite message rendering to single-path append-only, remove all streaming code
4. **`xray/static/index.html`** — Minor: remove `pending-indicator` section (no more streaming status)
5. **`xray/static/style.css`** — Remove unused streaming styles, keep message rendering styles
6. **`xray/app.py`** — Update full_snapshot to include messages from JSONL, keep existing endpoints
7. **`docker-compose.yml`** — Add `./xray_data:/data` to gate, add `DATA_DIR=/data` env to gate
8. **`gate/requirements.txt`** — No new dependencies needed (uses stdlib `json`, `pathlib`)
9. **`xray/requirements.txt`** — No new dependencies needed (uses sync file I/O, not aiofiles)

### Open Questions Resolved During Review

- **Turn number tracking:** Gate maintains an internal `_trace_turn` counter, incrementing with each `write_messages()` call. This approximates the spine's turn counter without requiring IPC between the services.
- **File offset persistence on xray restart:** The xray client reads the current day's JSONL from the beginning on startup (offset=0). This means on xray restart, all messages for the current day are loaded into memory and rendered. Previous days are not loaded (only the current day file is tailed).
- **No `aiofiles` dependency:** The file tailer uses synchronous `open()`/`readline()` in the async loop since local file I/O is fast and the reads are incremental (seek + read from offset). This avoids adding `aiofiles` as a dependency.