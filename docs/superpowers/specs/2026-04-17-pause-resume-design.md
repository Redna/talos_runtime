# Pause/Resume for Cortex — X-ray Control

## Status: Approved

## Overview

Leverage the existing wake file pattern to implement genuine pause that halts Cortex. Pause creates `/spine/.paused`, Resume/Telegram removes it.

## Mechanism

### Pause — X-ray → Spine `/command`

1. Create `/spine/.paused` flag file
2. Queue system notice: `[SYSTEM | Paused — waiting for resume or Telegram]`
3. In-flight LLM calls complete naturally; pause takes effect before next call

### Resume — X-ray → Spine `/command`

1. Remove `/spine/.paused`
2. Create `/spine/.wake` to interrupt any polling sleep
3. Cortex proceeds on next turn

### Telegram wake while paused

- On inbound message, if `/spine/.paused` exists → remove it + create `.wake`

### Pre-LLM check (stream.py)

- Before every LLM call, check `/spine/.paused`
- If present, poll 0.5s until removed, then proceed
- Uses existing async file-polling pattern from `reflect()`

## UI Feedback

### Status Indicator (always visible in header)

| State | Display | Color |
|-------|---------|-------|
| Cortex active | `● Running` | Green |
| Paused, waiting | `⏸ Paused` | Amber |
| Pause lifted, waking | `↻ Resuming...` (pulsing) | Blue |
| No active call, not paused | `○ Idle` | Grey |

### Pending Indicator

- When paused + call pending/blocked: show "Waiting on LLM..." with spinner
- When paused + no call pending: show "No active call"

### Button

- Pause button → Resume button (text + color shifts amber→green when paused)

## Components

| File | Change |
|------|--------|
| `spine/control_plane.py` | `pause` creates `.paused`, `resume` removes `.paused` + creates `.wake` |
| `spine/telegram.py` | On message: check `.paused`, remove + create `.wake` |
| `spine/stream.py` | `await _wait_while_paused()` pre-LLM call check; expose `is_paused()` state |
| `spine/ipc_server.py` | Expose `/paused` state via existing state endpoint |
| `xray/xray_client.py` | Poll `/paused` state, track `pending_call` flag |
| `xray/app.py` | Expose `paused` state to WS clients via state broadcast |
| `xray/static/app.js` | Status badge with dynamic color, pending spinner, button toggle |
| `xray/static/index.html` | Status badge in header |

## Dependencies

- `aiofiles` already used in `reflect()` — no new dependencies
- Uses same async file-polling pattern already in `reflect()`
