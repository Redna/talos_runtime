# X-ray Real-time Observability Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real-time web dashboard (X-ray) that gives visibility into Talos agent internals — live LLM token stream, token/context counters, event history, restart tracking, commit evolution, and health.

**Architecture:** Gate taps its LLM stream via in-memory subscriber queues and exposes SSE endpoints. Spine control plane gets enhanced with real `/events`, `/health`, and richer `/state`. A new X-ray container (FastAPI + vanilla JS) subscribes to both and broadcasts to browsers via WebSocket.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, httpx (X-ray backend); aiohttp (Spine); vanilla HTML/CSS/JS (frontend); Docker

---

## File Structure

### New files (in `talos_runtime/`)

| File | Responsibility |
|------|---------------|
| `xray/app.py` | FastAPI backend: WebSocket hub, static files, /api/* endpoints |
| `xray/xray_client.py` | SSE + HTTP client subscribing to Gate and Spine |
| `xray/requirements.txt` | Python deps for xray container |
| `xray/Dockerfile` | Container image |
| `xray/static/index.html` | Single-page dashboard HTML |
| `xray/static/style.css` | Dark theme styles |
| `xray/static/app.js` | WebSocket client, DOM updates, markdown rendering |

### Modified files (in `talos_runtime/`)

| File | Change |
|------|--------|
| `gate/app.py` | Add SSE subscriber queues and /v1/xray/* endpoints |
| `docker-compose.yml` | Add xray service, expose port 4001 on talos |
| `talos/spine/control_plane.py` | Enhance /state, implement /events and /health, add /commit |
| `talos/spine/stream.py` | Add _last_focus, _last_think_ts, expose in get_state() |
| `talos/spine/supervisor.py` | Wire record_first_think(), add commit_sha to events, expose _consecutive_failures |
| `talos/spine/health.py` | No structural changes, but call sites added in supervisor.py and ipc_server.py |
| `talos/spine/ipc_server.py` | Wire health.record_event() and health.record_first_think() after think() |
| `talos/spine/ipc_types.py` | No changes needed |

---

### Task 1: Gate SSE Subscriber Infrastructure

**Files:**
- Modify: `gate/app.py:1-50` (add subscriber state and helper)
- Test: `gate/test_xray.py` (new file)

- [ ] **Step 1: Write failing tests for the subscriber queue**

```python
import asyncio
import pytest
import httpx
from fastapi.testclient import TestClient
from app import app

@pytest.mark.asyncio
async def test_no_subscribers_no_overhead():
    from app import _xray_broadcast
    _xray_subscribers.clear()
    _xray_broadcast({"type": "token", "content": "hello"})
    assert len(_xray_subscribers) == 0

@pytest.mark.asyncio
async def test_subscriber_receives_event():
    from app import _xray_subscribers, _xray_broadcast
    _xray_subscribers.clear()
    q = asyncio.Queue()
    _xray_subscribers.append(q)
    _xray_broadcast({"type": "token", "content": "hello"})
    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event["type"] == "token"
    assert event["content"] == "hello"
    _xray_subscribers.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd gate && python3 -m pytest test_xray.py -v`
Expected: FAIL — `_xray_broadcast` and `_xray_subscribers` not defined

- [ ] **Step 3: Add subscriber infrastructure to gate/app.py**

Add after line 41 (`PRICING_CACHE`):

```python
_xray_subscribers: list[asyncio.Queue] = []

def _xray_broadcast(event: dict):
    if not _xray_subscribers:
        return
    for q in _xray_subscribers:
        q.put_nowait(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd gate && python3 -m pytest test_xray.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gate/app.py gate/test_xray.py
git commit -m "feat(gate): add xray subscriber broadcast infrastructure"
```

---

### Task 2: Gate SSE Endpoints

**Files:**
- Modify: `gate/app.py` (add /v1/xray/* endpoints)
- Modify: `gate/test_xray.py` (add SSE endpoint tests)

- [ ] **Step 1: Write failing tests for SSE endpoints**

```python
import asyncio
import pytest
from app import app, _xray_subscribers
from fastapi.testclient import TestClient

def test_xray_stream_sse():
    client = TestClient(app)
    _xray_subscribers.clear()
    with client.stream("GET", "/v1/xray/stream") as response:
        lines = []
        for line in response.iter_lines():
            lines.append(line)
            if len(lines) >= 2:
                break
    _xray_subscribers.clear()

def test_xray_state_sse():
    client = TestClient(app)
    with client.stream("GET", "/v1/xray/state") as response:
        assert response.status_code == 200

def test_xray_history_list():
    client = TestClient(app)
    response = client.get("/v1/xray/history?count=5")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd gate && python3 -m pytest test_xray.py -v`
Expected: FAIL — routes not found

- [ ] **Step 3: Add SSE endpoints to gate/app.py**

Add after the existing endpoints, before `@app.get("/healthz")`:

```python
@app.get("/v1/xray/stream")
async def xray_stream(request: Request):
    import sse_starlette
    q = asyncio.Queue()
    _xray_subscribers.append(q)
    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": "message", "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            if q in _xray_subscribers:
                _xray_subscribers.remove(q)
    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())

@app.get("/v1/xray/state")
async def xray_state(request: Request):
    import sse_starlette
    async def event_generator():
        while True:
            yield {"event": "message", "data": json.dumps({
                "type": "state", "spend": get_current_spend(),
                "budget_limit": DAILY_BUDGET_LIMIT,
            })}
            await asyncio.sleep(10)
    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())

@app.get("/v1/xray/events")
async def xray_events(request: Request):
    import sse_starlette
    async def event_generator():
        while True:
            yield {"event": "ping", "data": ""}
            await asyncio.sleep(15)
    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())

@app.get("/v1/xray/history")
async def xray_history_list(count: int = 50):
    if not LOG_DIR.exists():
        return []
    files = sorted(LOG_DIR.glob("call-*.json"), reverse=True)[:count]
    result = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            result.append({
                "filename": f.name,
                "model": data.get("model", "unknown"),
                "timestamp": data.get("timestamp", ""),
                "tokens_in": data.get("response", {}).get("usage", {}).get("prompt_tokens", 0),
                "tokens_out": data.get("response", {}).get("usage", {}).get("completion_tokens", 0),
                "backend": data.get("backend", "unknown"),
            })
        except:
            pass
    return result

@app.get("/v1/xray/history/{filename}")
async def xray_history_detail(filename: str):
    filepath = LOG_DIR / filename
    if not filepath.exists() or not filepath.is_relative_to(LOG_DIR):
        raise HTTPException(status_code=404, detail="Not found")
    return json.loads(filepath.read_text())
```

- [ ] **Step 4: Add sse-starlette to gate/requirements.txt**

Append to `gate/requirements.txt`:
```
sse-starlette>=1.6
```

- [ ] **Step 5: Tap the streaming proxy to broadcast**

Modify the `stream_proxy()` generator inside `chat_completions()` (gate/app.py ~line 242-270). After `async for chunk in resp.aiter_bytes(): yield chunk`, add parsing and broadcasting:

```python
async def stream_proxy() -> AsyncGenerator[bytes, None]:
    _xray_broadcast({"type": "think_start", "model": model, "ts": time.time()})
    collected_content = ""
    collected_tool_calls = []
    try:
        async with httpx.AsyncClient(timeout=1800.0) as client:
            async with client.stream(
                "POST", url, json=body, headers=headers
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(payload)
                        delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        tool_calls = delta.get("tool_calls")
                        if content:
                            collected_content += content
                            _xray_broadcast({"type": "token", "content": content, "model": model, "ts": time.time()})
                        if tool_calls:
                            for tc in tool_calls:
                                if tc.get("function", {}).get("name"):
                                    collected_tool_calls.append(tc)
                                    _xray_broadcast({
                                        "type": "tool_call",
                                        "id": tc.get("id", ""),
                                        "name": tc["function"]["name"],
                                        "arguments": tc["function"].get("arguments", "{}"),
                                        "model": model,
                                        "ts": time.time(),
                                    })
                    except json.JSONDecodeError:
                        pass
                    yield f"data: {payload}\n\n".encode("utf-8")
        _xray_broadcast({
            "type": "think_end",
            "tokens_in": 0,
            "tokens_out": 0,
            "context_pct": 0.0,
            "model": model,
            "backend": backend_key,
            "ts": time.time(),
        })
        background_tasks.add_task(
            log_completion, body, {"status": "stream_completed"}, backend_key, True
        )
    except (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.HTTPStatusError,
    ) as e:
        _xray_broadcast({"type": "error", "message": str(e), "model": model, "ts": time.time()})
        error_payload = {
            "error": {
                "message": f"Gateway Error: Model '{model}' is currently unreachable or offline. Please check available models or fallback to the local engine. Details: {str(e)}",
                "type": "server_error",
                "code": "model_offline",
            }
        }
        yield json.dumps(error_payload).encode("utf-8")
```

Also tap the non-streaming path. After `resp_json = resp.json()` (~line 280), add:

```python
_xray_broadcast({
    "type": "think_end",
    "tokens_in": resp_json.get("usage", {}).get("prompt_tokens", 0),
    "tokens_out": resp_json.get("usage", {}).get("completion_tokens", 0),
    "context_pct": resp_json.get("usage", {}).get("context_pct", 0.0),
    "model": model,
    "backend": backend_key,
    "ts": time.time(),
})
```

- [ ] **Step 6: Rebuild Gate and run tests**

Run: `docker compose build gate && cd gate && python3 -m pytest test_xray.py test_routing.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add gate/app.py gate/requirements.txt gate/test_xray.py
git commit -m "feat(gate): add /v1/xray/* SSE endpoints and stream tap"
```

---

### Task 3: Spine Control Plane Enhancements

**Files:**
- Modify: `talos/spine/control_plane.py` (enhance /state, /events, /health, add /commit)
- Test: `talos/tests/test_control_plane.py` (new file)

- [ ] **Step 1: Write failing tests for enhanced control plane**

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer
from spine.control_plane import ControlPlane
from spine.config import SpineConfig
from spine.stream import StreamManager
from spine.supervisor import Supervisor
from spine.events import EventLogger

class TestControlPlane(AioHTTPTestCase):
    async def get_application(self):
        self.cfg = MagicMock(spec=SpineConfig)
        self.cfg.control_plane_port = 0
        self.cfg.gate_model = "gemma4:31b-cloud"
        self.cfg.gate_url = "http://gate:4000"
        self.stream = StreamManager.__new__(StreamManager)
        self.stream.cfg = self.cfg
        self.stream.messages = []
        self.stream.turn = 5
        self.stream.tokens_used = 10000
        self.stream.context_pct = 0.45
        self.stream.queued_notices = []
        self.stream.state = {}
        self.stream._last_focus = "fixing the bug"
        self.stream._last_think_ts = 1713053120.5
        self.events = EventLogger.__new__(EventLogger)
        self.events.events_dir = MagicMock()
        self.supervisor = MagicMock()
        self.supervisor._consecutive_failures = 0
        self.supervisor.process = MagicMock()
        self.supervisor.process.pid = 12345
        self.supervisor.health = MagicMock()
        self.supervisor.health.first_think_done = True
        self.supervisor.health.is_stalled.return_value = False
        self.cp = ControlPlane(self.cfg, self.supervisor, self.stream, self.events)
        return self.cp.app

    async def test_state_includes_model(self):
        resp = await self.client.get("/state")
        data = await resp.json()
        assert data["model"] == "gemma4:31b-cloud"

    async def test_state_includes_consecutive_failures(self):
        resp = await self.client.get("/state")
        data = await resp.json()
        assert data["consecutive_failures"] == 0

    async def test_state_includes_focus(self):
        resp = await self.client.get("/state")
        data = await resp.json()
        assert data["focus"] == "fixing the bug"

    async def test_events_reads_jsonl(self, tmp_path):
        import json
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = events_dir / f"{today}.jsonl"
        log_file.write_text(json.dumps({"type": "test", "ts": "2026-01-01T00:00:00"}) + "\n")
        self.events.events_dir = events_dir
        resp = await self.client.get("/events?tail=10")
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "test"

    async def test_health_checks_cortex(self):
        resp = await self.client.get("/health")
        data = await resp.json()
        assert data["status"] in ("healthy", "starting", "stalled")
        assert "cortex_alive" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd talos && uv run pytest tests/test_control_plane.py -v`
Expected: FAIL — `/state` doesn't return model/consecutive_failures/focus, `/events` is a stub

- [ ] **Step 3: Enhance StreamManager.get_state()**

Modify `talos/spine/stream.py` `get_state()` (line 262-280). Add `_last_focus` and `_last_think_ts` initialization in `__init__` (after line 33):

```python
self._last_focus: str = ""
self._last_think_ts: float = 0.0
```

In `think()`, after line 59 (`resp = await self._send_to_gate(api_req)`), add before parsing:

```python
self._last_focus = req.focus
self._last_think_ts = time.time()
```

Add `import time` at the top of stream.py if not present.

Modify `get_state()` to include new fields:

```python
def get_state(self, keys: list[str] | None = None) -> dict[str, Any]:
    authoritative = {
        "context_pct": self.context_pct,
        "turn": self.turn,
        "tokens_used": self.tokens_used,
        "message_count": len(self.messages),
        "queued_notices": len(self.queued_notices),
        "model": self.cfg.gate_model,
        "gate_url": self.cfg.gate_url,
        "focus": self._last_focus,
        "last_think_ts": self._last_think_ts,
    }
    if keys:
        result = {}
        for key in keys:
            if key in authoritative:
                result[key] = authoritative[key]
            elif key in self.state:
                result[key] = self.state[key]
        return result
    result = dict(authoritative)
    result.update(self.state)
    return result
```

- [ ] **Step 4: Enhance ControlPlane._handle_state**

Modify `control_plane.py` `_handle_state` to include supervisor and health data:

```python
async def _handle_state(self, request):
    state = self.stream.get_state()
    state["consecutive_failures"] = self.supervisor._consecutive_failures
    state["cortex_pid"] = self.supervisor.process.pid if self.supervisor.process and self.supervisor.process.poll() is None else None
    state["first_think_done"] = self.supervisor.health.first_think_done
    if self.supervisor.health.is_stalled():
        state["status"] = "stalled"
    elif not self.supervisor.health.first_think_done:
        state["status"] = "starting"
    else:
        state["status"] = "healthy"
    return web.json_response(state)
```

- [ ] **Step 5: Implement _handle_events to read JSONL**

Replace the stub in `control_plane.py`:

```python
async def _handle_events(self, request):
    tail = int(request.query.get("tail", "100"))
    date_str = request.query.get("date", "")
    from datetime import datetime, timezone
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events_file = self.events.events_dir / f"{date_str}.jsonl"
    if not events_file.exists():
        return web.json_response([])
    try:
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        import json
        events = [json.loads(line) for line in lines[-tail:] if line.strip()]
        return web.json_response(events)
    except Exception:
        return web.json_response([])
```

- [ ] **Step 6: Implement _handle_health with real checks**

Replace the stub in `control_plane.py`:

```python
async def _handle_health(self, request):
    cortex_alive = (
        self.supervisor.process is not None
        and self.supervisor.process.poll() is None
    )
    stalled = self.supervisor.health.is_stalled()
    first_think = self.supervisor.health.first_think_done
    if not first_think:
        status = "starting"
    elif stalled:
        status = "stalled"
    else:
        status = "healthy"
    stall_seconds = None
    if stalled and self.supervisor.health.last_event_time > 0:
        import time
        stall_seconds = time.time() - self.supervisor.health.last_event_time
    return web.json_response({
        "status": status,
        "cortex_alive": cortex_alive,
        "first_think_done": first_think,
        "stall_seconds": stall_seconds,
    })
```

- [ ] **Step 7: Add /commit endpoint**

Add to ControlPlane `__init__`:

```python
self.app.router.add_get("/commit", self._handle_commit)
```

Add handler:

```python
async def _handle_commit(self, request):
    import subprocess
    result = {"candidate": None, "candidate_msg": "", "stable": None, "stable_msg": "", "ahead": 0}
    app_dir = self.cfg.app_dir
    candidate_file = Path(f"{self.cfg.spine_dir}/last_candidate_commit")
    if candidate_file.exists():
        result["candidate"] = candidate_file.read_text().strip()
    stable_file = Path(f"{self.cfg.spine_dir}/last_stable_commit")
    if stable_file.exists():
        result["stable"] = stable_file.read_text().strip()
    if result["candidate"]:
        try:
            res = subprocess.run(
                ["git", "log", "-1", "--format=%s", result["candidate"]],
                cwd=app_dir, capture_output=True, text=True,
            )
            result["candidate_msg"] = res.stdout.strip()
        except Exception:
            pass
    if result["stable"]:
        try:
            res = subprocess.run(
                ["git", "log", "-1", "--format=%s", result["stable"]],
                cwd=app_dir, capture_output=True, text=True,
            )
            result["stable_msg"] = res.stdout.strip()
        except Exception:
            pass
    if result["candidate"] and result["stable"]:
        try:
            res = subprocess.run(
                ["git", "rev-list", "--count", f"{result['stable']}..{result['candidate']}"],
                cwd=app_dir, capture_output=True, text=True,
            )
            result["ahead"] = int(res.stdout.strip())
        except Exception:
            pass
    return web.json_response(result)
```

Add `from pathlib import Path` to imports if not present.

- [ ] **Step 8: Run tests**

Run: `cd talos && uv run pytest tests/test_control_plane.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd talos && git add spine/control_plane.py spine/stream.py tests/test_control_plane.py
git commit -m "feat(spine): enhance control plane /state, /events, /health; add /commit"
```

---

### Task 4: Wire Health Monitor in Spine

**Files:**
- Modify: `talos/spine/supervisor.py`
- Modify: `talos/spine/ipc_server.py`
- Test: `talos/tests/test_health_wiring.py` (new file)

- [ ] **Step 1: Write failing test**

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
from spine.health import HealthMonitor

def test_record_event_updates_last_event_time():
    h = HealthMonitor(stall_timeout=60, startup_timeout=30)
    assert h.last_event_time == 0.0
    h.record_event()
    assert h.last_event_time > 0.0

def test_record_first_think_sets_flag():
    h = HealthMonitor(stall_timeout=60, startup_timeout=30)
    assert h.first_think_done is False
    h.record_first_think()
    assert h.first_think_done is True

def test_is_stalled_false_after_record_event():
    import time
    h = HealthMonitor(stall_timeout=600, startup_timeout=30)
    h.cortex_started()
    h.record_event()
    assert h.is_stalled() is False

def test_is_stalled_true_when_no_events():
    h = HealthMonitor(stall_timeout=0.001, startup_timeout=30)
    h.cortex_started()
    import time
    time.sleep(0.01)
    assert h.is_stalled() is True
```

- [ ] **Step 2: Run tests (should pass — HealthMonitor code works, just not called)**

Run: `cd talos && uv run pytest tests/test_health_wiring.py -v`
Expected: PASS (the HealthMonitor itself works; the bug is that nobody calls it)

- [ ] **Step 3: Wire record_event() and record_first_think() in ipc_server.py**

Modify `talos/spine/ipc_server.py`. After the `think` handler (line ~94), add health recording. Find the `think` method handler and add after `result = await self.stream.think(think_req)`:

```python
if method == "think":
    think_req = self._parse_think(params)
    result = await self.stream.think(think_req)
    self.health.record_event()
    if not self.health.first_think_done:
        self.health.record_first_think()
    return self._success_response(
        req_id, self._think_response_to_dict(result)
    )
```

The `ipc_server.py` needs a reference to the health monitor. In `__init__`, add a `health` parameter:

In the `IPCServer.__init__` method, add `health` parameter and store it:
```python
def __init__(self, ..., health: HealthMonitor):
    ...
    self.health = health
```

Then update `main.py` where `IPCServer` is constructed to pass the health monitor. In `talos/spine/main.py`, find the `IPCServer(...)` construction and add `health=supervisor.health`:

```python
ipc_server = IPCServer(
    cfg=cfg,
    stream=stream,
    events=event_logger,
    health=supervisor.health,
)
```

- [ ] **Step 4: Wire record_event() in supervisor.py**

In `supervisor.py`, add `commit_sha` to restart/crash event payloads in `request_restart()` and `_handle_cortex_exit()`:

```python
def request_restart(self, reason: str):
    commit_sha = self._get_current_commit()
    self.events.emit("spine.cortex_restart", {
        "reason": reason,
        "commit_sha": commit_sha,
        "consecutive_failures": self._consecutive_failures,
    })
    self._restart_requested.set()
```

In `_handle_cortex_exit()` (line 95), update all `self.events.emit()` calls:

```python
def _handle_cortex_exit(self, exit_code: int):
    commit_sha = self._get_current_commit()
    self.events.emit("spine.cortex_crash", {
        "exit_code": exit_code,
        "commit_sha": commit_sha,
        "consecutive_failures": self._consecutive_failures,
    })
    # ... rest stays the same, also update startup_failure and system_override events
```

Add helper method to Supervisor:

```python
def _get_current_commit(self) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.cfg.app_dir,
            capture_output=True, text=True,
        )
        return result.stdout.strip()[:8]
    except Exception:
        return "unknown"
```

- [ ] **Step 5: Run all Spine tests**

Run: `cd talos && uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd talos && git add spine/ipc_server.py spine/supervisor.py spine/control_plane.py tests/test_health_wiring.py
git commit -m "fix(spine): wire health.record_event() and record_first_think(); enrich restart events with commit_sha"
```

---

### Task 5: Expose Spine Port 4001 in Docker

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add ports to talos service**

Add `ports` section to the `talos` service in `docker-compose.yml` (after `security_opt`):

```yaml
    ports:
      - "4001:4001"
```

- [ ] **Step 2: Verify Spine is reachable from host**

Run: `docker compose up -d gate && sleep 15 && docker compose up -d talos && sleep 30 && curl -s http://localhost:4001/health`
Expected: `{"status": "healthy"}` or `{"status": "starting"}` (from enhanced /health)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(docker): expose Spine control plane port 4001"
```

---

### Task 6: X-ray Container — Backend

**Files:**
- Create: `xray/app.py`
- Create: `xray/xray_client.py`
- Create: `xray/requirements.txt`
- Create: `xray/Dockerfile`
- Test: `xray/test_app.py`

- [ ] **Step 1: Create xray/requirements.txt**

```
fastapi>=0.110
uvicorn>=0.29
httpx>=0.27
sse-starlette>=1.6
python-dotenv>=1.0
```

- [ ] **Step 2: Create xray/Dockerfile**

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4040"]
```

- [ ] **Step 3: Create xray/xray_client.py**

```python
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable
import httpx
from sse_starlette.sse import ServerSentEvent


class XRayClient:
    def __init__(
        self,
        gate_url: str,
        spine_url: str,
        on_event: Callable[[dict], None],
    ):
        self.gate_url = gate_url
        self.spine_url = spine_url
        self.on_event = on_event
        self._running = False
        self._state: dict[str, Any] = {}
        self._events: list[dict] = []
        self._commit: dict[str, Any] = {}
        self._container_status: dict[str, str] = {}

    async def start(self):
        self._running = True
        tasks = [
            asyncio.create_task(self._subscribe_gate_stream()),
            asyncio.create_task(self._subscribe_gate_state()),
            asyncio.create_task(self._poll_spine_state()),
            asyncio.create_task(self._poll_spine_events()),
            asyncio.create_task(self._poll_health_probes()),
            asyncio.create_task(self._poll_spine_commit()),
        ]
        await asyncio.gather(*tasks)

    async def stop(self):
        self._running = False

    def get_full_snapshot(self) -> dict:
        return {
            "state": self._state,
            "events": self._events[-200:],
            "commit": self._commit,
            "container_status": self._container_status,
        }

    async def _subscribe_gate_stream(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    async with client.stream("GET", f"{self.gate_url}/v1/xray/stream") as resp:
                        backoff = 1.0
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if not payload:
                                continue
                            try:
                                event = json.loads(payload)
                                self.on_event(event)
                            except json.JSONDecodeError:
                                pass
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _subscribe_gate_state(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    async with client.stream("GET", f"{self.gate_url}/v1/xray/state") as resp:
                        backoff = 1.0
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                event = json.loads(line[6:])
                                self.on_event(event)
                            except json.JSONDecodeError:
                                pass
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _poll_spine_state(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/state")
                    if resp.status_code == 200:
                        self._state = resp.json()
                        self.on_event({"type": "state_update", **self._state})
                    backoff = 1.0
            except Exception:
                backoff = min(backoff * 2, 30.0)
            await asyncio.sleep(3)

    async def _poll_spine_events(self):
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/events?tail=200")
                    if resp.status_code == 200:
                        self._events = resp.json()
            except Exception:
                pass
            await asyncio.sleep(10)

    async def _poll_health_probes(self):
        while self._running:
            status = {}
            for name, url in [
                ("gate", f"{self.gate_url}/health"),
                ("talos", f"{self.spine_url}/health"),
            ]:
                try:
                    async with httpx.AsyncClient(timeout=3.0) as client:
                        resp = await client.get(url)
                        data = resp.json()
                        status[name] = data.get("status", "unknown")
                except Exception:
                    status[name] = "offline"
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get("http://llamacpp:8080/health")
                    status["llamacpp"] = "healthy" if resp.status_code == 200 else "unhealthy"
            except Exception:
                status["llamacpp"] = "offline"
            self._container_status = status
            self.on_event({"type": "container_status", **status})
            await asyncio.sleep(10)

    async def _poll_spine_commit(self):
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/commit")
                    if resp.status_code == 200:
                        self._commit = resp.json()
                        self.on_event({"type": "commit_info", **self._commit})
            except Exception:
                pass
            await asyncio.sleep(30)
```

- [ ] **Step 4: Create xray/app.py**

```python
import os
import json
import asyncio
from pathlib import Path
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from xray_client import XRayClient

app = FastAPI(title="Talos X-ray")

GATE_URL = os.getenv("GATE_URL", "http://gate:4000")
SPINE_URL = os.getenv("SPINE_URL", "http://talos_agent:4001")

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

_connected_clients: list[WebSocket] = []
_xray_client: XRayClient | None = None


def _broadcast(event: dict):
    dead = []
    for ws in _connected_clients:
        try:
            asyncio.get_event_loop().create_task(ws.send_json(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _connected_clients:
            _connected_clients.remove(ws)


@app.on_event("startup")
async def startup():
    global _xray_client
    _xray_client = XRayClient(GATE_URL, SPINE_URL, _broadcast)
    asyncio.create_task(_xray_client.start())


@app.on_event("shutdown")
async def shutdown():
    if _xray_client:
        await _xray_client.stop()


@app.get("/", response_class=HTMLResponse)
async def index():
    return (static_dir / "index.html").read_text()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connected_clients.append(ws)
    if _xray_client:
        try:
            snapshot = _xray_client.get_full_snapshot()
            await ws.send_json({"type": "full_snapshot", **snapshot})
        except Exception:
            pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _connected_clients:
            _connected_clients.remove(ws)


@app.get("/api/state")
async def api_state():
    if _xray_client:
        return _xray_client._state
    return {}


@app.post("/api/command")
async def api_command(request: Request):
    data = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SPINE_URL}/command",
                json=data,
            )
            return JSONResponse(content=resp.json() if resp.text else {"status": "ok"}, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=503)


@app.get("/api/history")
async def api_history(count: int = 50):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GATE_URL}/v1/xray/history?count={count}")
            return resp.json()
    except Exception:
        return []


@app.get("/api/history/{filename}")
async def api_history_detail(filename: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GATE_URL}/v1/xray/history/{filename}")
            return resp.json()
    except Exception:
        return {}
```

- [ ] **Step 5: Write a basic test for the FastAPI app**

Create `xray/test_app.py`:

```python
from fastapi.testclient import TestClient
from app import app

def test_index_returns_html():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200

def test_api_state():
    client = TestClient(app)
    resp = client.get("/api/state")
    assert resp.status_code == 200
```

- [ ] **Step 6: Run tests**

Run: `cd xray && python3 -m pip install -r requirements.txt && python3 -m pytest test_app.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add xray/
git commit -m "feat(xray): add FastAPI backend with WebSocket hub and SSE client"
```

---

### Task 7: X-ray Container — Frontend

**Files:**
- Create: `xray/static/index.html`
- Create: `xray/static/style.css`
- Create: `xray/static/app.js`

- [ ] **Step 1: Create index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Talos X-ray</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <header id="top-bar">
        <h1>TALOS X-RAY</h1>
        <div id="model-info"></div>
        <div id="ws-status" class="status-dot"></div>
    </header>

    <div id="context-bar">
        <div id="context-progress">
            <div id="context-fill"></div>
            <span id="context-text">0%</span>
        </div>
        <div id="token-counters">
            <span id="tokens-in">In: 0</span>
            <span id="tokens-out">Out: 0</span>
            <span id="tokens-total">Total: 0</span>
            <span id="turn-count">Turn: 0</span>
            <span id="spend">Spend: $0.00</span>
        </div>
    </div>

    <main>
        <section id="stream-panel">
            <div id="stream-header">
                <span id="stream-status" class="status-dot"></span>
                <span id="stream-turn">Turn —</span>
                <span id="stream-model">—</span>
                <span id="stream-time">—</span>
            </div>
            <div id="stream-content"></div>
        </section>

        <aside id="sidebar">
            <div id="health-panel" class="card">
                <h2>Health</h2>
                <div id="container-dots"></div>
                <div id="spine-status"></div>
                <div id="lazarus"></div>
            </div>

            <div id="event-log" class="card">
                <h2>Events</h2>
                <div id="event-list"></div>
            </div>
        </aside>
    </main>

    <footer>
        <section id="commit-panel" class="card">
            <h2>Commit Timeline</h2>
            <div id="commit-info"></div>
        </section>

        <section id="actions-panel" class="card">
            <h2>Actions</h2>
            <button onclick="sendCommand('force_restart')">Restart Cortex</button>
            <button onclick="sendCommand('pause')">Pause</button>
            <button onclick="sendCommand('resume')">Resume</button>
            <button onclick="sendCommand('force_fold')">Fold Context</button>
        </section>
    </footer>

    <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create style.css**

```css
:root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --dim: #8b949e;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --blue: #58a6ff;
    --accent: #7c3aed;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    background: var(--bg);
    color: var(--text);
    font-size: 13px;
}

#top-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 16px;
    background: var(--card);
    border-bottom: 1px solid var(--border);
}

#top-bar h1 { font-size: 14px; color: var(--accent); }
#model-info { color: var(--dim); }

.status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--dim);
    display: inline-block;
}
.status-dot.connected { background: var(--green); animation: pulse 2s infinite; }
.status-dot.active { background: var(--yellow); animation: pulse 1s infinite; }
.status-dot.error { background: var(--red); }

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

#context-bar {
    padding: 8px 16px;
    background: var(--card);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
}

#context-progress {
    flex: 0 0 200px;
    height: 16px;
    background: var(--border);
    border-radius: 4px;
    position: relative;
    overflow: hidden;
}

#context-fill {
    height: 100%;
    transition: width 0.5s, background-color 0.5s;
    border-radius: 4px;
}

#context-text {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 11px;
}

#token-counters { display: flex; gap: 12px; color: var(--dim); }

main {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 0;
    height: calc(100vh - 140px);
}

#stream-panel {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
}

#stream-header {
    padding: 6px 16px;
    background: var(--card);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 12px;
    color: var(--dim);
}

#stream-content {
    flex: 1;
    padding: 16px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;
}

#stream-content .token { color: var(--text); }
#stream-content .tool-call { color: var(--yellow); font-weight: bold; }
#stream-content .tool-result { color: var(--dim); }
#stream-content .think-separator {
    border-top: 1px solid var(--border);
    margin: 8px 0;
    padding-top: 8px;
    color: var(--dim);
    font-size: 11px;
}

aside {
    overflow-y: auto;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px;
}

.card h2 { font-size: 11px; color: var(--accent); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }

#container-dots { display: flex; gap: 12px; margin-bottom: 6px; }
.container-dot { display: flex; align-items: center; gap: 4px; font-size: 11px; }
.container-dot .dot { width: 6px; height: 6px; border-radius: 50%; }

#event-list { max-height: 300px; overflow-y: auto; }
.event-item { padding: 3px 0; font-size: 11px; border-bottom: 1px solid var(--border); }
.event-item .ts { color: var(--dim); margin-right: 6px; }
.event-item.restart { color: var(--yellow); }
.event-item.crash { color: var(--red); }
.event-item.override { color: var(--red); font-weight: bold; }
.event-item.started { color: var(--green); }
.event-item.custom { color: var(--blue); }

footer {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 0;
    border-top: 1px solid var(--border);
}

footer section { border-right: 1px solid var(--border); }

#actions-panel button {
    background: var(--border);
    color: var(--text);
    border: none;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
    margin-right: 4px;
    margin-bottom: 4px;
}
#actions-panel button:hover { background: var(--accent); }
```

- [ ] **Step 3: Create app.js**

```javascript
let ws = null;
let state = {};
let events = [];
let commit = {};
let containers = {};
let thinkActive = false;

function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => {
        document.getElementById("ws-status").className = "status-dot connected";
    };
    ws.onclose = () => {
        document.getElementById("ws-status").className = "status-dot error";
        setTimeout(connect, 3000);
    };
    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        handleMessage(msg);
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case "full_snapshot":
            state = msg.state || {};
            events = msg.events || [];
            commit = msg.commit || {};
            containers = msg.container_status || {};
            renderAll();
            break;
        case "state_update":
            state = {...state, ...msg};
            renderState();
            renderHealth();
            break;
        case "stream_token":
            if (!thinkActive) { startThink(msg.model); }
            appendToken(msg.content);
            break;
        case "tool_call":
            appendToolCall(msg.name, msg.arguments);
            break;
        case "tool_result":
            appendToolResult(msg.output, msg.success);
            break;
        case "think_start":
            startThink(msg.model);
            break;
        case "think_end":
            endThink(msg.tokens_in, msg.tokens_out, msg.context_pct);
            break;
        case "event":
            events.push(msg);
            renderEvents();
            break;
        case "container_status":
            containers = msg;
            renderContainers();
            break;
        case "commit_info":
            commit = msg;
            renderCommit();
            break;
    }
}

function startThink(model) {
    thinkActive = true;
    const el = document.getElementById("stream-content");
    if (el.innerHTML) {
        el.innerHTML += '<div class="think-separator">— think cycle —</div>';
    }
    document.getElementById("stream-status").className = "status-dot active";
    document.getElementById("stream-model").textContent = model || "—";
    document.getElementById("stream-turn").textContent = `Turn ${state.turn || "—"}`;
}

function endThink(tokensIn, tokensOut, contextPct) {
    thinkActive = false;
    document.getElementById("stream-status").className = "status-dot";
    if (tokensIn) document.getElementById("tokens-in").textContent = `In: ${tokensIn}`;
    if (tokensOut) document.getElementById("tokens-out").textContent = `Out: ${tokensOut}`;
    if (contextPct !== undefined) updateContextBar(contextPct);
}

function appendToken(content) {
    const el = document.getElementById("stream-content");
    let last = el.lastElementChild;
    if (!last || !last.classList.contains("token")) {
        last = document.createElement("span");
        last.className = "token";
        el.appendChild(last);
    }
    last.textContent += content;
    el.scrollTop = el.scrollHeight;
}

function appendToolCall(name, args) {
    const el = document.getElementById("stream-content");
    const div = document.createElement("div");
    div.className = "tool-call";
    div.textContent = `▸ ${name}(${Object.keys(args || {}).join(", ")})`;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

function appendToolResult(output, success) {
    const el = document.getElementById("stream-content");
    const div = document.createElement("div");
    div.className = "tool-result";
    const short = (output || "").substring(0, 200);
    div.textContent = success !== false ? short : `✗ ${short}`;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

function updateContextBar(pct) {
    const fill = document.getElementById("context-fill");
    const text = document.getElementById("context-text");
    const pctNum = Math.round(pct * 100);
    fill.style.width = pctNum + "%";
    text.textContent = pctNum + "%";
    if (pctNum < 60) fill.style.backgroundColor = "var(--green)";
    else if (pctNum < 85) fill.style.backgroundColor = "var(--yellow)";
    else fill.style.backgroundColor = "var(--red)";
}

function renderAll() {
    renderState();
    renderHealth();
    renderContainers();
    renderEvents();
    renderCommit();
}

function renderState() {
    if (state.context_pct !== undefined) updateContextBar(state.context_pct);
    if (state.tokens_used !== undefined) document.getElementById("tokens-total").textContent = `Total: ${state.tokens_used}`;
    if (state.turn !== undefined) document.getElementById("turn-count").textContent = `Turn: ${state.turn}`;
    if (state.model) document.getElementById("model-info").textContent = state.model;
    if (state.spend !== undefined) document.getElementById("spend").textContent = `Spend: $${state.spend.toFixed(2)}`;
}

function renderHealth() {
    const el = document.getElementById("spine-status");
    el.textContent = `Spine: ${state.status || "unknown"}`;
    if (state.status === "healthy") el.style.color = "var(--green)";
    else if (state.status === "stalled") el.style.color = "var(--red)";
    else el.style.color = "var(--yellow)";

    document.getElementById("lazarus").textContent = `Failures: ${state.consecutive_failures ?? "—"}`;
}

function renderContainers() {
    const el = document.getElementById("container-dots");
    el.innerHTML = "";
    for (const [name, status] of Object.entries(containers)) {
        const d = document.createElement("div");
        d.className = "container-dot";
        const dot = document.createElement("span");
        dot.className = "dot";
        dot.style.backgroundColor = status === "healthy" ? "var(--green)" : status === "offline" ? "var(--dim)" : "var(--red)";
        d.appendChild(dot);
        d.appendChild(document.createTextNode(name));
        el.appendChild(d);
    }
}

function renderEvents() {
    const el = document.getElementById("event-list");
    el.innerHTML = "";
    const recent = events.slice(-50);
    for (const ev of recent) {
        const div = document.createElement("div");
        let cls = "event-item";
        const type = ev.type || ev.event_type || "";
        if (type.includes("restart")) cls += " restart";
        else if (type.includes("crash")) cls += " crash";
        else if (type.includes("override")) cls += " override";
        else if (type.includes("started")) cls += " started";
        div.className = cls;
        const ts = document.createElement("span");
        ts.className = "ts";
        ts.textContent = (ev.ts || "").substring(11, 19);
        div.appendChild(ts);
        const summary = type.replace("spine.", "") + (ev.reason ? `: ${ev.reason}` : "") + (ev.exit_code ? ` (exit ${ev.exit_code})` : "");
        div.appendChild(document.createTextNode(summary));
        el.appendChild(div);
    }
    el.scrollTop = el.scrollHeight;
}

function renderCommit() {
    const el = document.getElementById("commit-info");
    if (!commit.candidate) {
        el.textContent = "No commit info";
        return;
    }
    let text = `Candidate: ${commit.candidate.substring(0, 8)}`;
    if (commit.candidate_msg) text += ` — ${commit.candidate_msg}`;
    if (commit.stable) text += ` | Stable: ${commit.stable.substring(0, 8)}`;
    if (commit.ahead) text += ` | ${commit.ahead} ahead`;
    el.textContent = text;
}

async function sendCommand(cmd) {
    await fetch("/api/command", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({command: cmd}),
    });
}

connect();
```

- [ ] **Step 4: Verify the static files are served**

Run: `cd xray && python3 -c "from pathlib import Path; print(Path('static/index.html').exists())"`
Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add xray/static/
git commit -m "feat(xray): add frontend — index.html, style.css, app.js"
```

---

### Task 8: Docker Compose Integration

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add xray service to docker-compose.yml**

Add after the `gate` service (before `llamacpp`):

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

- [ ] **Step 2: Build and start the full stack**

Run: `docker compose build xray && docker compose up -d gate && sleep 15 && docker compose up -d xray && sleep 10 && docker compose ps`
Expected: Gate healthy, X-ray running

- [ ] **Step 3: Verify X-ray is accessible**

Run: `curl -s http://localhost:4040/ | head -5`
Expected: HTML content starting with `<!DOCTYPE html>`

- [ ] **Step 4: Update talosctl to support xray**

Add `xray` as a recognized service. In `talosctl`, update `run_daemon()` to also start the xray container after the Gate is healthy. Add after the Gate start:

```python
    print("[DAEMON] Starting X-ray...", flush=True)
    res = subprocess.run(f"docker compose {compose_args} up -d xray", shell=True, cwd=RUNTIME_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[DAEMON] WARNING: X-ray startup failed: {res.stderr.strip()}", flush=True)
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml talosctl
git commit -m "feat(docker): add xray service on port 4040, update talosctl"
```

---

### Task 9: Token Persistence

**Files:**
- Modify: `xray/xray_client.py` (add periodic token snapshot writing)

- [ ] **Step 1: Add token stats persistence to XRayClient**

Add to `xray/xray_client.py`. Import `Path` at the top. Add initialization in `__init__`:

```python
self._data_dir = Path(os.getenv("XRAY_DATA_DIR", "/data"))
self._data_dir.mkdir(parents=True, exist_ok=True)
self._stats_file = self._data_dir / "token_stats.json"
self._last_stats_write = 0.0
```

Add a periodic task to `start()`:

```python
asyncio.create_task(self._persist_token_stats()),
```

Add the method:

```python
async def _persist_token_stats(self):
    while self._running:
        now = time.time()
        if now - self._last_stats_write >= 300:
            try:
                import datetime
                today = datetime.date.today().isoformat()
                existing = []
                if self._stats_file.exists():
                    try:
                        existing = json.loads(self._stats_file.read_text())
                    except Exception:
                        pass
                entry = {
                    "date": today,
                    "tokens_in": self._state.get("tokens_used", 0),
                    "tokens_out": 0,
                    "turns": self._state.get("turn", 0),
                    "requests": self._state.get("message_count", 0),
                }
                found = False
                for e in existing:
                    if e["date"] == today:
                        e.update(entry)
                        found = True
                        break
                if not found:
                    existing.append(entry)
                self._stats_file.write_text(json.dumps(existing, indent=2))
                self._last_stats_write = now
            except Exception:
                pass
        await asyncio.sleep(60)
```

- [ ] **Step 2: Add xray_data volume to docker-compose.yml**

Add volume to the `xray` service:

```yaml
    volumes:
      - ./xray_data:/data
```

- [ ] **Step 3: Commit**

```bash
git add xray/xray_client.py docker-compose.yml
git commit -m "feat(xray): add 5-minute token stats persistence"
```

---

### Task 10: Push talos/ Submodule Changes

**Files:**
- Push changes from Tasks 3-4 (Spine control plane, health wiring)

- [ ] **Step 1: Push talos submodule**

```bash
cd talos && git push origin main
```

- [ ] **Step 2: Update talos_runtime submodule reference**

```bash
cd .. && git add talos && git commit -m "chore: update talos submodule with enhanced control plane"
```

---

### Task 11: End-to-End Smoke Test

**Files:** None — verification only

- [ ] **Step 1: Stop and clean up**

Run: `./talosctl stop && docker compose down && rm -f /tmp/talos-runtime/watchdog.pid /tmp/talos-runtime/watchdog.health`

- [ ] **Step 2: Start the full stack**

Run: `./talosctl start`

- [ ] **Step 3: Wait for all services**

Run: `sleep 30 && docker compose ps`
Expected: Gate healthy, X-ray running, talos running (or restarting)

- [ ] **Step 4: Open X-ray in browser**

Open: `http://localhost:4040/`
Expected: Dark-themed dashboard with panels visible

- [ ] **Step 5: Verify Spine state is reachable**

Run: `curl -s http://localhost:4001/state | python3 -m json.tool | head -15`
Expected: JSON with model, consecutive_failures, focus, status fields

- [ ] **Step 6: Verify events endpoint returns data**

Run: `curl -s http://localhost:4001/events?tail=5`
Expected: JSON array of recent events

- [ ] **Step 7: Final commit and push**

```bash
git push origin feat/spine-cortex
```