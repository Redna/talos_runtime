# Pause/Resume for Cortex — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add genuine pause/resume control to Cortex via X-ray UI, leveraging the existing wake-file pattern.

**Architecture:** Pause creates `/spine/.paused` flag file; Resume and Telegram remove it. Pre-LLM hook in stream.py polls the flag and blocks until cleared. X-ray polls spine state and displays status.

**Tech Stack:** aiofiles (existing), asyncio, FastAPI, WebSocket, SSE

---

## File Map

| File | Role |
|------|------|
| `spine/control_plane.py` | `pause`/`resume` commands: create/remove `.paused` and `.wake` files |
| `spine/telegram.py` | On message: check `.paused`, remove + create `.wake` |
| `spine/stream.py` | Pre-LLM `is_paused()` check + `_wait_while_paused()` polling |
| `spine/ipc_server.py` | Expose `is_paused` in `/state` endpoint |
| `xray/xray_client.py` | Poll `/state` for `is_paused`, track `call_pending` flag |
| `xray/app.py` | Broadcast `paused` state + `call_pending` to WS clients |
| `xray/static/app.js` | Status badge, pending spinner, Pause/Resume button toggle |
| `xray/static/index.html` | Status badge element in header |

---

## Tasks

### Task 1: Spine — Add `is_paused()` and `_wait_while_paused()` to stream.py

**Files:**
- Modify: `talos/spine/stream.py`

- [ ] **Step 1: Read stream.py to find the `_call_llm` method and `_init_message`**

Run: `rg "_call_llm|is_paused|_wait_while" talos/spine/stream.py`

- [ ] **Step 2: Add `is_paused()` helper near the top of Stream class**

Add after the `__init__` or near other helper methods:

```python
async def is_paused(self) -> bool:
    return Path("/spine/.paused").exists()

async def _wait_while_paused(self):
    while await self.is_paused():
        await asyncio.sleep(0.5)
```

- [ ] **Step 3: Call `_wait_while_paused()` at the start of every LLM call**

Find `_call_llm` (or wherever the LLM call is made) and add at the very top of the method body:

```python
await self._wait_while_paused()
```

If there's a non-streaming LLM path (e.g., `_call_llm_non_streaming`), add the same check there.

- [ ] **Step 4: Commit**

```bash
git add talos/spine/stream.py
git commit -m "feat: add pause check before LLM calls"
```

---

### Task 2: Spine — Wire pause/resume commands in control_plane.py

**Files:**
- Modify: `talos/spine/control_plane.py`

- [ ] **Step 1: Read control_plane.py to find the command handling section**

Run: `rg "pause|resume|command" talos/spine/control_plane.py`

- [ ] **Step 2: Find the `elif command in ("pause", "resume"` block and replace it**

Replace the existing `pause`/`resume` handling (around line 76) with:

```python
elif command == "pause":
    Path("/spine/.paused").touch()
    self.stream.queue_system_notice("[SYSTEM | Paused — waiting for resume or Telegram]")
    return web.Response(status=200)

elif command == "resume":
    if Path("/spine/.paused").exists():
        Path("/spine/.paused").unlink()
        Path("/spine/.wake").touch()
    return web.Response(status=200)
```

Make sure `Path` is imported at the top (`from pathlib import Path`).

- [ ] **Step 3: Commit**

```bash
git add talos/spine/control_plane.py
git commit -m "feat: pause/resume commands create/remove .paused flag file"
```

---

### Task 3: Spine — Telegram wakes Cortex when paused

**Files:**
- Modify: `talos/spine/telegram.py`

- [ ] **Step 1: Read telegram.py to find the message handling section**

Run: `rg "on_telegram_message|async def" talos/spine/telegram.py`

- [ ] **Step 2: Find where inbound messages are handled (likely `on_telegram_message` or similar)**

Add after receiving a message, before creating the notice:

```python
paused_file = Path("/spine/.paused")
if paused_file.exists():
    paused_file.unlink()
    Path("/spine/.wake").touch()
```

- [ ] **Step 3: Commit**

```bash
git add talos/spine/telegram.py
git commit -m "feat: telegram wakes cortex when paused"
```

---

### Task 4: Spine — Expose `is_paused` in `/state` endpoint

**Files:**
- Modify: `talos/spine/ipc_server.py`

- [ ] **Step 1: Read ipc_server.py to find the `/state` handler**

Run: `rg "state|/state" talos/spine/ipc_server.py`

- [ ] **Step 2: Find the state dict construction and add `is_paused`**

In the handler that returns state, add:

```python
"is_paused": stream.is_paused() if hasattr(stream, "is_paused") else False,
```

If `is_paused` is an async method, use:

```python
"is_paused": await stream.is_paused(),
```

- [ ] **Step 3: Commit**

```bash
git add talos/spine/ipc_server.py
git commit -m "feat: expose is_paused in /state endpoint"
```

---

### Task 5: X-ray Client — Poll and track paused state

**Files:**
- Modify: `xray/xray_client.py`

- [ ] **Step 1: Read xray_client.py to understand how state polling works**

Run: `rg "state|pending|SpineState" xray/xray_client.py`

- [ ] **Step 2: Add `is_paused` and `call_pending` fields to XRayClient.__init__**

Add:

```python
self.is_paused = False
self.call_pending = False
```

- [ ] **Step 3: Update `_poll_spine_state` to extract `is_paused`**

In the `_poll_spine_state` method (or wherever `/state` is consumed), update the state dict merge to include:

```python
self.is_paused = state.get("is_paused", False)
self.call_pending = state.get("call_pending", False)
```

- [ ] **Step 4: Update `_broadcast` call to include paused state**

Find where `self._broadcast` is called with a state dict and add:

```python
{
    "type": "state",
    "is_paused": self.is_paused,
    "call_pending": self.call_pending,
    ...
}
```

Or merge into the existing state event.

- [ ] **Step 5: Commit**

```bash
git add xray/xray_client.py
git commit -m "feat: xray client tracks and broadcasts paused state"
```

---

### Task 6: X-ray — Broadcast `is_paused` and `call_pending` to WS clients

**Files:**
- Modify: `xray/app.py`

- [ ] **Step 1: Read app.py to find state broadcast section**

Run: `rg "broadcast|state" xray/app.py`

- [ ] **Step 2: Update state broadcast dict to include paused fields**

Find where the state dict is built for WS broadcast and add:

```python
"is_paused": self._xray_client.is_paused if hasattr(self, "_xray_client") else False,
"call_pending": self._xray_client.call_pending if hasattr(self, "_xray_client") else False,
```

- [ ] **Step 3: Commit**

```bash
git add xray/app.py
git commit -m "feat: xray broadcasts paused state to WS clients"
```

---

### Task 7: X-ray UI — Status badge, pending spinner, button toggle

**Files:**
- Modify: `xray/static/index.html`, `xray/static/app.js`

- [ ] **Step 1: Read current index.html header area**

Run: `rg "status|Pause|Resume|header" xray/static/index.html`

- [ ] **Step 2: Add status badge to index.html in the header**

Find the header or toolbar area and add:

```html
<span id="status-badge" class="status-badge status-idle">
  <span id="status-dot">●</span>
  <span id="status-text">Idle</span>
</span>
<span id="pending-indicator" class="pending-indicator hidden">
  <span class="spinner"></span>
  <span id="pending-text">Waiting on LLM...</span>
</span>
```

- [ ] **Step 3: Read current app.js for state handling**

Run: `rg "state|paused|pending|status" xray/static/app.js`

- [ ] **Step 4: Add state variable and update `handleMessage`**

Add at the top of the script or in `init()`:

```javascript
let isPaused = false;
let callPending = false;
```

In `handleMessage(event)`, where state events are handled, add:

```javascript
if (event.type === "state") {
    isPaused = event.is_paused || false;
    callPending = event.call_pending || false;
    updateStatusUI();
}
```

- [ ] **Step 5: Add `updateStatusUI()` function**

```javascript
function updateStatusUI() {
    const badge = document.getElementById("status-badge");
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    const pending = document.getElementById("pending-indicator");
    const pendingText = document.getElementById("pending-text");
    const pauseBtn = document.getElementById("pause-btn");

    if (isPaused) {
        badge.className = "status-badge status-paused";
        dot.textContent = "⏸";
        text.textContent = "Paused";
        if (pauseBtn) {
            pauseBtn.textContent = "Resume";
            pauseBtn.className = "btn btn-resume";
            pauseBtn.onclick = () => sendCommand("resume");
        }
        if (callPending) {
            pending.classList.remove("hidden");
            pendingText.textContent = "Waiting on LLM...";
        } else {
            pending.classList.remove("hidden");
            pendingText.textContent = "No active call";
        }
    } else {
        badge.className = "status-badge status-running";
        dot.textContent = "●";
        text.textContent = "Running";
        if (pauseBtn) {
            pauseBtn.textContent = "Pause";
            pauseBtn.className = "btn btn-pause";
            pauseBtn.onclick = () => sendCommand("pause");
        }
        pending.classList.add("hidden");
    }
}
```

- [ ] **Step 6: Add CSS for status states in style.css**

```css
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 600;
}
.status-running { background: #d1fae5; color: #065f46; }
.status-paused { background: #fef3c7; color: #92400e; }
.status-idle { background: #e5e7eb; color: #374151; }
.status-resuming { background: #dbeafe; color: #1e40af; animation: pulse 1.5s infinite; }

.pending-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: #92400e;
}
.spinner {
    width: 12px;
    height: 12px;
    border: 2px solid #fef3c7;
    border-top-color: #92400e;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}
.hidden { display: none; }

@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
@keyframes spin { to{transform:rotate(360deg)} }

.btn-pause { background: #fef3c7; color: #92400e; border: 1px solid #f59e0b; }
.btn-resume { background: #d1fae5; color: #065f46; border: 1px solid #10b981; }
```

- [ ] **Step 7: Commit**

```bash
git add xray/static/index.html xray/static/app.js xray/static/style.css
git commit -m "feat: add paused state badge and pending spinner to X-ray UI"
```

---

## Spec Coverage Check

- [x] Pause creates `/spine/.paused` flag file — Task 2
- [x] Resume removes `.paused` + creates `.wake` — Task 2
- [x] Telegram wakes when paused — Task 3
- [x] Pre-LLM check polls `.paused` — Task 1
- [x] Status indicator in header (Running/Paused/Resuming/Idle) — Task 7
- [x] Pending indicator (Waiting on LLM / No active call) — Task 7
- [x] Button toggles Pause↔Resume with color change — Task 7
- [x] `is_paused` exposed via `/state` — Task 4
- [x] X-ray polls state and broadcasts to clients — Tasks 5, 6
