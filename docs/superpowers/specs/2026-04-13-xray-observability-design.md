# Talos X-ray: Real-time Observability Dashboard

**Date:** 2026-04-13
**Status:** Approved

## Problem

The Talos agent runs as an opaque Docker stack. When something goes wrong — crashes, stalls, unexpected behavior — the only observability is `docker compose logs` and the `talosctl monitor` terminal dashboard, which reads sparse state from a handful of endpoints. Critical data (live LLM token stream, event history, commit-to-restart correlation, consecutive failure counts) is either not exposed or stubbed out.

## Solution

A lightweight web dashboard called **X-ray** that gives a real-time "x-ray" view into the agent's internals: live LLM token stream, token/context counters, event history, restart tracking, commit evolution, and health status. The X-ray subscribes to two data sources — the Gate (for live stream data) and the Spine (for state/events) — and broadcasts aggregated data to browser clients via WebSocket.

## Architecture

```
Browser (http://localhost:4040)
  │ WebSocket
  ▼
xray container (FastAPI + WS hub)
  │ SSE                    │ HTTP (poll 3s)
  ▼                        ▼
Gate (port 4000)           Spine (port 4001)
/v1/xray/stream  ──────►  (live tokens, tool calls)
/v1/xray/state   ──────►  (request summaries)
/v1/xray/events  ──────►  (gate-level events)
                          /state   (enhanced state)
                          /events  (JSONL event history)
                          /health  (real checks)
```

**Key invariant:** The X-ray is a pure consumer. No traffic flows through it. If it crashes, the agent is unaffected. The only modification to the Gate is adding SSE subscriber queues — zero overhead when no X-ray clients are connected.

## Gate SSE Tap

### Subscriber model

A global `_xray_subscribers: list[asyncio.Queue]` in `gate/app.py`. When handling a streaming `/v1/chat/completions` request, after yielding each chunk to the caller, the handler also `put_nowait()`s it to every subscriber queue. Non-streaming requests push a summary event.

If no subscribers exist, the put is skipped. Queues have `maxsize=0` (unlimited) so `put_nowait()` never blocks the Gate.

### New Gate endpoints

**`GET /v1/xray/stream`** (SSE)
- Publishes live LLM stream events: `token`, `tool_call`, `tool_result`, `think_start`, `think_end`
- On connect, creates a new `asyncio.Queue`, adds to subscribers. On disconnect, removes it.
- Heartbeat every 15s when idle.

**`GET /v1/xray/state`** (SSE)
- Pushes state deltas after each completed request: `{model, backend, tokens_in, tokens_out, context_pct, turn}`
- Heartbeat every 10s.

**`GET /v1/xray/events`** (SSE)
- Gate-level events: request start, request complete, error, model routing decision.
- Heartbeat every 15s.

**`GET /v1/xray/history`** (HTTP)
- Lists recent Gate call log files from `/runtime_logs/`
- Returns `[{filename, model, timestamp, tokens_in, tokens_out, backend}, ...]`
- Query param `?count=50` limits results.

**`GET /v1/xray/history/{filename}`** (HTTP)
- Returns the full call log JSON for a specific past conversation.

### Chunk event format

```json
{"type": "token", "content": "def hello", "model": "gemma4:31b-cloud", "ts": 1713053120.5}
{"type": "tool_call", "id": "call_abc", "name": "bash", "arguments": {"command": "ls"}, "ts": ...}
{"type": "tool_result", "id": "call_abc", "output": "file1.py\nfile2.py", "success": true, "ts": ...}
{"type": "think_start", "model": "gemma4:31b-cloud", "ts": ...}
{"type": "think_end", "tokens_in": 1500, "tokens_out": 320, "context_pct": 0.45, "ts": ...}
```

## Spine Control Plane Enhancements

### Enhanced `/state`

New fields added to `StreamManager.get_state()`:

```json
{
  "context_pct": 0.45,
  "turn": 12,
  "tokens_used": 15432,
  "message_count": 24,
  "queued_notices": 0,
  "model": "gemma4:31b-cloud",
  "gate_url": "http://gate:4000",
  "consecutive_failures": 0,
  "focus": "fixing the IPC bug in spine_client.py",
  "last_think_ts": 1713053120.5,
  "cortex_pid": 12345,
  "first_think_done": true,
  "status": "healthy"
}
```

Sources:
- `model` → `cfg.gate_model`
- `gate_url` → `cfg.gate_url`
- `consecutive_failures` → `supervisor._consecutive_failures`
- `focus` → `stream._last_focus` (recorded from `ThinkRequest.focus` in the `think()` method)
- `last_think_ts` → `stream._last_think_ts`
- `cortex_pid` → `supervisor.process.pid`
- `first_think_done` → `health.first_think_done`
- `status` → derived from health monitor: `"healthy"` if `first_think_done` and not stalled, `"starting"` if not `first_think_done`, `"stalled"` if `is_stalled()`

### Functional `/events`

Replace the stub with actual JSONL file reading:
- `GET /events?tail=N` — reads last N events from today's `/spine/events/YYYY-MM-DD.jsonl`
- `GET /events?date=YYYY-MM-DD` — reads events from a specific date
- Default: `tail=100` if no params

### Real `/health`

Replace the always-healthy stub with actual checks:
- Cortex process alive (`supervisor.process.poll() is None`)
- IPC socket responsive (try connect to `/tmp/spine.sock`)
- Returns `{"status": "healthy"|"starting"|"stalled", "cortex_alive": bool, "ipc_responsive": bool, "first_think_done": bool, "stall_seconds": float|null}`

### Health monitor wiring

Connect the orphaned methods:
- `health.record_event()` called in `StreamManager.think()` after each successful LLM call
- `health.record_first_think()` called in `Supervisor._watch_cortex()` when `ThinkResponse` is first received
- `health.cortex_started()` already called in `Supervisor._start_cortex()` (working)

### Restart event enrichment

Add `commit_sha` and `consecutive_failures` to restart/crash event payloads:
```json
{"type": "spine.cortex_restart", "reason": "manual", "commit_sha": "abc123", "consecutive_failures": 2, "ts": "..."}
```

## X-ray Container

### Docker service

```yaml
xray:
  build:
    context: ./xray
    dockerfile: Dockerfile
  container_name: talos_xray
  ports:
    - "4040:4040"
  environment:
    - GATE_URL=http://gate:4000
    - SPINE_URL=http://talos_agent:4001
  depends_on:
    gate:
      condition: service_healthy
  networks:
    - talos_net
```

### File structure

```
talos_runtime/xray/
  app.py              # FastAPI: WS hub, static files, /api/* endpoints
  xray_client.py      # SSE client that subscribes to Gate + Spine
  requirements.txt    # fastapi, uvicorn, httpx
  static/
    index.html        # Single-page dashboard
    style.css         # Dark theme
    app.js            # WebSocket client, DOM updates, markdown renderer
  Dockerfile          # python:3.13-slim, install requirements, copy code
```

### Backend (app.py)

- Serves static files from `static/`
- `WS /ws` — WebSocket endpoint for browser clients
- `GET /api/state` — current snapshot (for initial page load)
- `POST /api/command` — proxy to Spine `/command` (restart, pause, resume, fold)
- `GET /api/history` — list recent Gate call logs
- `GET /api/history/{filename}` — fetch a specific call log

### SSE client (xray_client.py)

- Runs 3 concurrent asyncio tasks subscribing to Gate `/v1/xray/stream`, `/v1/xray/state`, `/v1/xray/events`
- Polls Spine `/state` every 3 seconds
- Reads Spine `/events?tail=200` on startup for history
- Aggregates all data and fans out to connected WebSocket clients
- Auto-reconnects on SSE disconnect with exponential backoff (1s, 2s, 4s, max 30s)

### Token persistence

Every 5 minutes, writes a lightweight snapshot to `/data/token_stats.json`:
```json
[{"date": "2026-04-13", "tokens_in": 45000, "tokens_out": 8200, "turns": 47, "requests": 23}]
```

Mounted as a Docker volume for persistence: `./xray_data:/data`.

### Spine port exposure

The `talos` service in `docker-compose.yml` needs port 4001 exposed:
```yaml
ports:
  - "4001:4001"
```

This allows the X-ray container to reach the Spine via `talos_agent:4001` on the Docker network, and `talosctl monitor` to work from the host via `localhost:4001`.

### Container status dots (Panel 3 source)

- **Gate health** — X-ray calls `GET http://gate:4000/health` every 10s
- **Talos container** — X-ray calls `GET http://talos_agent:4001/health` (Spine) every 10s
- **llamacpp** — X-ray calls `GET http://llamacpp:8080/health` every 10s (skipped if llamacpp not in Docker network)
- Each check is a lightweight HTTP probe. Failures toggle the dot from green to red with a 30s timeout before marking red.

### WebSocket message format (X-ray → browser)

All messages are JSON with a `type` field:

```json
{"type": "stream_token", "content": "...", "model": "...", "ts": ...}
{"type": "tool_call", "id": "...", "name": "...", "arguments": {...}, "ts": ...}
{"type": "tool_result", "id": "...", "output": "...", "success": true, "ts": ...}
{"type": "think_start", "model": "...", "ts": ...}
{"type": "think_end", "tokens_in": ..., "tokens_out": ..., "context_pct": ..., "ts": ...}
{"type": "state_update", "context_pct": ..., "turn": ..., "tokens_used": ..., "model": ..., "consecutive_failures": ..., "status": ..., ...}
{"type": "event", "event_type": "spine.cortex_restart", "payload": {...}, "ts": "..."}
{"type": "container_status", "gate": "healthy", "talos": "starting", "llamacpp": "offline"}
{"type": "commit_info", "candidate": "abc123", "candidate_msg": "...", "stable": "def456", "ahead": 3}
{"type": "full_snapshot", "state": {...}, "events": [...], "commit": {...}}
```

On initial WebSocket connect, the server sends a `full_snapshot` message with current state, recent events, and commit info. Subsequent messages are incremental deltas.

## Dashboard Panels

### Panel layout

Single scrollable dark-themed page. Top bar: model + health. Center: stream viewer. Right: event log. Bottom: commit timeline + quick actions.

### Panel 1: Live Stream Viewer (center, largest)

- Streaming token display — LLM response tokens arriving in real-time, markdown rendered inline
- Active/Idle indicator — pulsing dot when a think cycle is active
- Turn metadata bar — turn number, model name, timestamp

### Panel 2: Token & Context Bar (top, full width)

- Context utilization bar — horizontal progress 0-100%, color-coded (green <60%, yellow 60-85%, red >85%)
- Token counters — input, output, total (cumulative)
- Turn counter
- Daily spend from Gate ledger

### Panel 3: Model & Health (top-right card)

- Current model (e.g., `gemma4:31b-cloud`)
- Backend routing (Ollama/cloud/local)
- Container status dots — Gate, talos, llamacpp
- Spine health — healthy/starting/stalled + stall seconds
- Consecutive failures (Lazarus count)

### Panel 4: Event Log (right sidebar)

- Scrolling event feed, color-coded:
  - `spine.cortex_restart` → yellow
  - `spine.cortex_crash` → red
  - `spine.system_override` → red bold
  - `spine.cortex_started` → green
  - Custom model events → blue
- Each event shows timestamp and payload summary
- Historical events loaded on connect via Spine `/events?tail=200`

### Panel 5: Commit Timeline (bottom-left)

- Candidate commit — SHA and short message
- Stable commit — last known-good
- Diff count — commits ahead of stable
- Restart mapping — shows which commit was active during each restart (from event log `commit_sha` field)

### Panel 6: Quick Actions (bottom-right, small)

- Restart Cortex button (calls Spine `/command` with `force_restart`)
- Pause/Resume buttons
- Fold context button
- These call X-ray `/api/command` which proxies to Spine

## Error Handling

| Scenario | Behavior |
|----------|----------|
| X-ray crashes | Agent unaffected. X-ray is a pure consumer. |
| Gate restarts | X-ray SSE client auto-reconnects with exponential backoff (1s→2s→4s→max 30s). On reconnect, requests state snapshot. |
| Spine unreachable | X-ray shows "Spine offline" in health panel. State panel shows "last updated N seconds ago". Stream/events from Gate continue. Warning banner: "Spine unreachable — limited observability". |
| Browser disconnects | X-ray server cleans up WebSocket. On reconnect, sends full state snapshot then resumes live updates. |
| No X-ray clients | Gate subscriber queues empty. `put_nowait()` skipped when subscriber list is empty. Zero overhead. |
| Queue overflow | Queues are unlimited (`maxsize=0`). If memory pressure is a concern, oldest events are silently dropped via a ring buffer approach. |

## Changes in `talos/` (agent repo)

All changes to the Spine are passive — they expose data without changing behavior:

| File | Change |
|------|--------|
| `spine/control_plane.py` | `/events` reads JSONL files. `/health` does real checks. `/state` returns enhanced fields. |
| `spine/supervisor.py` | Expose `_consecutive_failures` to control plane. Add `commit_sha` to restart/crash event payloads. Wire `record_first_think()` call. |
| `spine/stream.py` | Record `_last_focus` from think() calls. Record `_last_think_ts`. Call `health.record_event()` after each successful think. Expose model, gate_url, focus, last_think_ts via get_state(). |
| `spine/health.py` | Wire `record_event()` and `record_first_think()` so they are actually called. Expose `first_think_done`, `last_event_time` via state. |