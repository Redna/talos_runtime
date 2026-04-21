# X-Ray JSONL Message Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the complex streaming x-ray pipeline with a simple JSONL file-based message trace where Gate writes complete OpenAI-format messages and XRay reads them.

**Architecture:** Gate writes daily JSONL files containing complete messages (including reasoning) in OpenAI chat format. XRay tails these files and renders messages incrementally. All token-level SSE streaming is removed. Frontend uses a single append-only render path.

**Tech Stack:** Python (FastAPI, aiohttp), Vanilla JS/HTML/CSS, JSONL, Docker Compose

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `gate/app.py` | Modify | Add `MessageTraceWriter`, integrate into completions, remove broadcast code + SSE endpoints |
| `gate/test_xray.py` | Modify | Rewrite tests for `MessageTraceWriter` instead of `_xray_broadcast` |
| `gate/test_trace_writer.py` | Create | Unit tests for `MessageTraceWriter` |
| `xray/xray_client.py` | Modify | Replace SSE/trajectory polls with JSONL tailer |
| `xray/static/app.js` | Modify | Replace dual streaming/trajectory rendering with single append-only path |
| `xray/static/index.html` | Modify | Remove pending-indicator, simplify stream header |
| `xray/static/style.css` | Modify | Remove unused streaming styles, add message-append styles |
| `xray/app.py` | Modify | Update full_snapshot to include messages |
| `docker-compose.yml` | Modify | Add shared volume to gate, add DATA_DIR env |

---

### Task 1: Create MessageTraceWriter in Gate

**Files:**
- Create: `gate/test_trace_writer.py`
- Modify: `gate/app.py:42-49` (add new class after xray broadcast section)

- [ ] **Step 1: Write the failing test for MessageTraceWriter**

```python
# gate/test_trace_writer.py
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone


@pytest.fixture
def trace_dir(tmp_path):
    return tmp_path / "data"


def test_write_messages_creates_daily_file(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer.write_messages(
        request_messages=[
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "Begin."},
        ],
        response_message={"role": "assistant", "content": "I will start.", "tool_calls": []},
        turn=0,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    assert trace_file.exists()

    lines = trace_file.read_text().strip().split("\n")
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["role"] == "system"
    assert first["content"] == "You are Talos."
    assert "_ts" in first
    assert first["_turn"] == 0


def test_write_messages_deduplication(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer.write_messages(
        request_messages=[
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "Begin."},
        ],
        response_message={"role": "assistant", "content": "I will start."},
        turn=0,
    )

    writer.write_messages(
        request_messages=[
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "Begin."},
            {"role": "assistant", "content": "I will start."},
            {"role": "tool", "tool_call_id": "c1", "content": "result1"},
        ],
        response_message={"role": "assistant", "content": "Next step."},
        turn=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    lines = trace_file.read_text().strip().split("\n")
    assert len(lines) == 6
    last = json.loads(lines[-1])
    assert last["role"] == "assistant"
    assert last["content"] == "Next step."
    assert last["_turn"] == 1


def test_write_messages_includes_reasoning(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer.write_messages(
        request_messages=[{"role": "user", "content": "Go"}],
        response_message={
            "role": "assistant",
            "content": "I will act.",
            "reasoning": "Let me think about this...",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/app/main.py"}'}}],
        },
        turn=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    lines = trace_file.read_text().strip().split("\n")
    resp = json.loads(lines[-1])
    assert resp["reasoning"] == "Let me think about this..."
    assert resp["tool_calls"][0]["function"]["name"] == "read_file"


def test_day_rollover(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer._current_date = "2025-12-31"
    writer.write_messages(
        request_messages=[{"role": "user", "content": "test"}],
        response_message={"role": "assistant", "content": "ok"},
        turn=0,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    assert trace_file.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/zeus/content/talos_runtime && python -m pytest gate/test_trace_writer.py -v`
Expected: FAIL — `ImportError: cannot import name 'MessageTraceWriter' from 'app'`

- [ ] **Step 3: Implement MessageTraceWriter in gate/app.py**

Add after the `_xray_broadcast` function (line 50) and before the pricing/cost logic:

```python
from datetime import datetime, timezone


class MessageTraceWriter:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir) / "messages"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._last_written_count = 0
        self._trace_turn = 0
        self._current_date = ""
        self._file = None

    def _ensure_file(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            self._file = open(self.data_dir / f"{today}.jsonl", "a", encoding="utf-8")
            self._current_date = today

    def write_messages(
        self,
        request_messages: list[dict],
        response_message: dict,
        turn: int | None = None,
    ):
        self._ensure_file()
        ts = datetime.now(timezone.utc).isoformat()
        if turn is not None:
            self._trace_turn = turn
        else:
            self._trace_turn += 1
            turn = self._trace_turn

        for msg in request_messages[self._last_written_count :]:
            line = {**msg, "_ts": ts, "_turn": turn}
            self._file.write(json.dumps(line, default=str) + "\n")

        self._last_written_count = len(request_messages)

        resp_line = {**response_message, "_ts": ts, "_turn": turn}
        self._file.write(json.dumps(resp_line, default=str) + "\n")
        self._file.flush()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None
            self._current_date = ""
```

Also add at module level (near other config vars after line 37):

```python
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
_trace_writer = MessageTraceWriter(DATA_DIR)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/zeus/content/talos_runtime && python -m pytest gate/test_trace_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gate/app.py gate/test_trace_writer.py
git commit -m "feat(gate): add MessageTraceWriter for JSONL message trace"
```

---

### Task 2: Integrate MessageTraceWriter into Gate Completions Handler

**Files:**
- Modify: `gate/app.py:235-571` (chat_completions handler)

- [ ] **Step 1: Write the test for trace writing on non-streaming completion**

Add to `gate/test_trace_writer.py`:

```python
def test_non_streaming_completion_writes_trace(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    msgs = [
        {"role": "system", "content": "You are Talos."},
        {"role": "user", "content": "Begin."},
    ]
    resp_msg = {
        "role": "assistant",
        "content": "I will act.",
        "tool_calls": [],
    }
    writer.write_messages(msgs, resp_msg, turn=0)

    writer.write_messages(
        msgs + [resp_msg, {"role": "tool", "tool_call_id": "x", "content": "ok"}],
        {"role": "assistant", "content": "Step 2."},
        turn=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    lines = [json.loads(l) for l in trace_file.read_text().strip().split("\n")]
    assert len(lines) == 6
    assert lines[3]["role"] == "tool"
    assert lines[4]["role"] == "assistant"
    assert lines[4]["content"] == "Step 2."
```

- [ ] **Step 2: Run test**

Run: `cd /home/zeus/content/talos_runtime && python -m pytest gate/test_trace_writer.py::test_non_streaming_completion_writes_trace -v`
Expected: PASS (tests the writer's deduplication, which is the core logic)

- [ ] **Step 3: Integrate into non-streaming completion handler**

In `gate/app.py`, after line 502 (`background_tasks.add_task(log_completion, body, resp_json, backend_key)`) and before `return resp_json`, add:

```python
    _trace_writer.write_messages(
        body.get("messages", []),
        resp_json.get("choices", [{}])[0].get("message", {}),
        turn=None,
    )
```

- [ ] **Step 4: Integrate into streaming completion handler**

In the `stream_proxy()` function (around line 299), add accumulation variables at the top of the function, after `_xray_broadcast({"type": "think_start"...})`:

```python
    _accumulated_content = ""
    _accumulated_reasoning = ""
    _accumulated_tool_calls = []
```

Replace the token-level `_xray_broadcast` calls with accumulation. In the streaming chunk processing (lines 320-361), replace:

```python
                                if reasoning:
                                    _xray_broadcast(
                                        {
                                            "type": "thinking_token",
                                            "content": reasoning,
                                            "model": model,
                                            "ts": time.time(),
                                        }
                                    )
                                if content:
                                    _xray_broadcast(
                                        {
                                            "type": "stream_token",
                                            "content": content,
                                            "model": model,
                                            "ts": time.time(),
                                        }
                                    )
                                if tool_calls:
                                    for tc in tool_calls:
                                        if tc.get("function", {}).get("name"):
                                            _xray_broadcast(
                                                {
                                                    "type": "tool_call",
                                                    "id": tc.get("id", ""),
                                                    "name": tc["function"]["name"],
                                                    "arguments": tc["function"].get(
                                                        "arguments", "{}"
                                                    ),
                                                    "model": model,
                                                    "ts": time.time(),
                                                }
                                            )
```

with:

```python
                                if reasoning:
                                    _accumulated_reasoning += reasoning
                                if content:
                                    _accumulated_content += content
                                if tool_calls:
                                    for tc in tool_calls:
                                        if tc.get("function", {}).get("name"):
                                            _accumulated_tool_calls.append(tc)
```

After the stream ends (after line 373, after the `think_end` broadcast), and before the `background_tasks.add_task(log_completion...)` line (374), add the trace write:

```python
    _trace_writer.write_messages(
        body.get("messages", []),
        {
            "role": "assistant",
            "content": _accumulated_content,
            "reasoning": _accumulated_reasoning,
            "tool_calls": _accumulated_tool_calls,
        },
        turn=None,
    )
```

- [ ] **Step 5: Run all gate tests**

Run: `cd /home/zeus/content/talos_runtime && python -m pytest gate/test_trace_writer.py gate/test_routing.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gate/app.py
git commit -m "feat(gate): integrate MessageTraceWriter into completions handler"
```

---

### Task 3: Remove Gate xray Broadcast Code and SSE Endpoints

**Files:**
- Modify: `gate/app.py:42-49, 297-403, 405-571, 823-875`
- Modify: `gate/test_xray.py`

- [ ] **Step 1: Remove `_xray_subscribers` and `_xray_broadcast()`**

In `gate/app.py`, delete lines 42-49:

```python
# DELETE these lines:
_xray_subscribers: list[asyncio.Queue] = []


def _xray_broadcast(event: dict):
    if not _xray_subscribers:
        return
    for q in _xray_subscribers:
        q.put_nowait(event)
```

- [ ] **Step 2: Remove all remaining `_xray_broadcast()` calls from streaming handler**

In the `stream_proxy()` function, remove the `think_start` broadcast (line 300):

```python
# DELETE: _xray_broadcast({"type": "think_start", "model": model, "ts": time.time()})
```

Remove the `think_end` broadcast (lines 363-373):

```python
# DELETE the _xray_broadcast({"type": "think_end", ...}) block
```

Remove the `error` broadcast (lines 386-393):

```python
# DELETE the _xray_broadcast({"type": "error", ...}) block
```

- [ ] **Step 3: Remove all `_xray_broadcast()` calls from non-streaming handler**

Remove the `think_start` broadcast (line 407):

```python
# DELETE: _xray_broadcast({"type": "think_start", "model": model, "ts": time.time()})
```

Remove the `thinking` broadcast (lines 441-449) and the `stream_token` broadcasts (lines 453-461, 462-473), and the `tool_call` broadcasts (lines 474-485).

Remove the `think_end` broadcast (lines 487-501) and the error broadcasts (lines 505-524, 539-558).

Also remove the reasoning content injection into the response message (lines 450-452):

```python
# DELETE: resp_json["choices"][0]["message"]["content"] = (
#     f"<thinking>\n{reasoning}\n</thinking>\n{content}"
# )
```

- [ ] **Step 4: Remove SSE xray endpoints**

Delete the three endpoint functions (lines 823-875):

```python
# DELETE: /v1/xray/stream endpoint
# DELETE: /v1/xray/state endpoint
# DELETE: /v1/xray/events endpoint
```

Keep `/v1/xray/history` and `/v1/xray/history/{filename}` (they serve call logs, different concern).

- [ ] **Step 5: Rewrite gate/test_xray.py**

```python
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone


def test_xray_history_list():
    from starlette.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.get("/v1/xray/history?count=5")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_xray_history_detail_not_found():
    from starlette.testclient import TestClient
    from app import app

    client = TestClient(app)
    response = client.get("/v1/xray/history/nonexistent.json")
    assert response.status_code == 404


def test_sse_endpoints_removed():
    from starlette.testclient import TestClient
    from app import app

    client = TestClient(app)
    for endpoint in ["/v1/xray/stream", "/v1/xray/state", "/v1/xray/events"]:
        response = client.get(endpoint)
        assert response.status_code == 404, f"Endpoint {endpoint} should be removed"
```

- [ ] **Step 6: Run all gate tests**

Run: `cd /home/zeus/content/talos_runtime && python -m pytest gate/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gate/app.py gate/test_xray.py
git commit -m "refactor(gate): remove xray SSE broadcast code and endpoints"
```

---

### Task 4: Update Docker Compose for Shared Volume

**Files:**
- Modify: `docker-compose.yml:64-83` (xray service) and `docker-compose.yml:36-62` (gate service)

- [ ] **Step 1: Add shared volume mount to gate service**

In `docker-compose.yml`, add `DATA_DIR` env and `./xray_data:/data` volume to the gate service. The gate service is at lines 36-62. Add to its `environment` section:

```yaml
      - DATA_DIR=/data
```

And add to its `volumes` section:

```yaml
      - ./xray_data:/data:rw
```

- [ ] **Step 2: Verify compose file syntax**

Run: `cd /home/zeus/content/talos_runtime && docker compose config 2>&1 | head -5`
Expected: No YAML syntax errors

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(infra): add shared xray_data volume to gate for JSONL trace"
```

---

### Task 5: Rewrite XRayClient with JSONL Tailer

**Files:**
- Modify: `xray/xray_client.py` (full rewrite of message handling)

- [ ] **Step 1: Rewrite XRayClient class**

Replace the entire `xray/xray_client.py` with:

```python
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger("xray.client")


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
        self._data_dir = Path(os.getenv("XRAY_DATA_DIR", "/data"))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._messages: list[dict] = []
        self._max_messages = 500
        self._file_offset = 0
        self._current_trace_path: Path | None = None
        self.is_paused = False
        self._last_state_event: dict = {}

    async def start(self):
        self._running = True
        tasks = [
            asyncio.create_task(self._tail_message_trace()),
            asyncio.create_task(self._poll_spine_state()),
            asyncio.create_task(self._poll_health_probes()),
            asyncio.create_task(self._poll_spine_commit()),
        ]
        await asyncio.gather(*tasks)

    async def stop(self):
        self._running = False

    def get_full_snapshot(self) -> dict:
        events = self._events if isinstance(self._events, list) else []
        return {
            "state": self._state,
            "events": events[-200:],
            "commit": self._commit,
            "container_status": self._container_status,
            "messages": self._messages[-self._max_messages :],
        }

    async def _tail_message_trace(self):
        while self._running:
            try:
                today = datetime.date.today().isoformat()
                path = self._data_dir / "messages" / f"{today}.jsonl"
                if path.exists():
                    if path != self._current_trace_path:
                        self._current_trace_path = path
                        self._file_offset = 0
                    with open(path, "r", encoding="utf-8") as f:
                        f.seek(self._file_offset)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            msg = json.loads(line)
                            self._messages.append(msg)
                            if len(self._messages) > self._max_messages:
                                self._messages = self._messages[-self._max_messages :]
                            self.on_event({"type": "message", "message": msg})
                        self._file_offset = f.tell()
            except FileNotFoundError:
                pass
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("[XRay] Skipping malformed JSONL line: %s", e)
            except Exception:
                await asyncio.sleep(2)
                continue
            await asyncio.sleep(1)

    async def _poll_spine_state(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/state")
                    if resp.status_code == 200:
                        self._state = resp.json()
                        self.is_paused = self._state.get("is_paused", False)
                    try:
                        health_resp = await client.get(f"{self.spine_url}/health")
                        if health_resp.status_code == 200:
                            health_data = health_resp.json()
                            self._state["spine_status"] = health_data.get(
                                "status", "unknown"
                            )
                            if "consecutive_failures" in health_data:
                                self._state["consecutive_failures"] = health_data[
                                    "consecutive_failures"
                                ]
                    except Exception:
                        self._state["spine_status"] = "offline"
                    try:
                        events_resp = await client.get(
                            f"{self.spine_url}/events?tail=200"
                        )
                        if events_resp.status_code == 200:
                            data = events_resp.json()
                            if isinstance(data, list):
                                self._events = data
                    except Exception:
                        pass
                    new_event = {
                        "type": "state_update",
                        "is_paused": self.is_paused,
                        **self._state,
                    }
                    if new_event != self._last_state_event:
                        self._last_state_event = new_event
                        self.on_event(new_event)
                    backoff = 1.0
            except Exception:
                backoff = min(backoff * 2, 30.0)
            await asyncio.sleep(3)

    async def _poll_health_probes(self):
        while self._running:
            status = {}
            for name, url in [
                ("gate", f"{self.gate_url}/healthz"),
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
                ollama_host = os.environ.get(
                    "OLLAMA_HOST", "host.docker.internal:11434"
                )
                if not ollama_host.startswith("http"):
                    ollama_host = f"http://{ollama_host}"
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(f"{ollama_host}/api/tags")
                    data = resp.json()
                    status["ollama"] = (
                        "healthy"
                        if isinstance(data, dict) and "models" in data
                        else "unhealthy"
                    )
            except Exception:
                status["ollama"] = "offline"
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

- [ ] **Step 2: Verify the file tailer exists in the data dir**

Run: `ls /home/zeus/content/talos_runtime/xray/xray_client.py`
Expected: File exists

- [ ] **Step 3: Quick syntax check**

Run: `cd /home/zeus/content/talos_runtime && python -c "import importlib.util; spec = importlib.util.spec_from_file_location('xray_client', 'xray/xray_client.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add xray/xray_client.py
git commit -m "refactor(xray): replace SSE/trajectory polls with JSONL tailer"
```

---

### Task 6: Update XRay FastAPI App

**Files:**
- Modify: `xray/app.py:76-96` (WebSocket endpoint)

- [ ] **Step 1: Update WebSocket snapshot to include messages**

In `xray/app.py`, update the `websocket_endpoint` function. Change the snapshot send (line 84) to include messages:

```python
        try:
            snapshot = client.get_full_snapshot()
            await ws.send_json({"type": "full_snapshot", **snapshot})
```

This already works since `get_full_snapshot()` now returns `messages` — no change needed to the send logic. But verify the import and update the `api_state` endpoint to also return messages:

In `api_state` (line 99-103), update:

```python
@app.get("/api/state")
async def api_state():
    if _xray_client:
        return {
            **_xray_client._state,
            "messages": _xray_client._messages[-100:],
        }
    return {}
```

- [ ] **Step 2: Commit**

```bash
git add xray/app.py
git commit -m "feat(xray): include messages in snapshot and state API"
```

---

### Task 7: Rewrite Frontend app.js

**Files:**
- Modify: `xray/static/app.js` (full rewrite of Stream view rendering)

- [ ] **Step 1: Rewrite app.js with single-path message rendering**

Replace the entire `xray/static/app.js` with:

```javascript
let ws=null,state={},events=[],commit={},containers={},autoScroll=true,currentTurnEl=null;
const CONTAINER_KEYS=new Set(["gate","talos","ollama","llamacpp"]);
const COLLAPSE_LINES=8;
const COLLAPSE_CHARS=800;

function switchView(name){
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  const view=document.getElementById('view-'+name);
  if(view)view.classList.add('active');
  const tab=document.querySelector('.tab[data-view="'+name+'"]');
  if(tab)tab.classList.add('active');
}

function updateStatusUI(){
    const badge=document.getElementById("status-badge");
    const dot=document.getElementById("status-dot");
    const text=document.getElementById("status-text");
    const pauseBtn=document.getElementById("pause-btn");
    if(state.is_paused){
        badge.className="status-badge status-paused";
        dot.textContent="\u23f8";
        text.textContent="Paused";
        if(pauseBtn){pauseBtn.textContent="Resume";pauseBtn.className="btn btn-resume";pauseBtn.onclick=()=>sendCommand("resume")}
    }else{
        badge.className="status-badge status-running";
        dot.textContent="\u25cf";
        text.textContent="Running";
        if(pauseBtn){pauseBtn.textContent="Pause";pauseBtn.className="btn btn-pause";pauseBtn.onclick=()=>sendCommand("pause")}
    }
}

document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.tab').forEach(tab=>{
    tab.addEventListener('click',()=>switchView(tab.dataset.view));
  });
  setupScrollPause();
  updateStatusUI();
  connect();
});

function connect(){
  const proto=location.protocol==="https:"?"wss:":"ws:";
  ws=new WebSocket(proto+"//"+location.host+"/ws");
  ws.onopen=()=>{document.getElementById("ws-status").className="status-dot connected"};
  ws.onclose=()=>{document.getElementById("ws-status").className="status-dot error";setTimeout(connect,5000)};
  ws.onmessage=e=>{const msg=JSON.parse(e.data);handleMessage(msg)};
}

function handleMessage(msg){
  switch(msg.type){
    case"full_snapshot":
      state=msg.state||{};
      events=msg.events||[];
      commit=msg.commit||{};
      containers=msg.container_status||{};
      renderAll();
      renderAllMessages(msg.messages||[]);
      break;
    case"state_update":
      state={...state,...msg};
      if(msg.is_paused!==undefined)updateStatusUI();
      renderState();renderHealth();
      break;
    case"state":
      state={...state,...msg};
      if(msg.is_paused!==undefined)updateStatusUI();
      renderState();
      break;
    case"message":
      appendMessage(msg.message);
      break;
    case"container_status":
      containers={};
      for(const[k,v]of Object.entries(msg)){if(CONTAINER_KEYS.has(k))containers[k]=v}
      renderContainers();
      break;
    case"commit_info":
      commit=msg;renderCommit();
      break;
    case"event":
      events.push(msg);renderEvents();
      break;
  }
}

function renderAll(){
  renderState();renderHealth();renderContainers();renderEvents();renderCommit();
  updateStatusUI();
}

function renderState(){
  if(state.context_pct!==undefined)updateContextBar(state.context_pct);
  if(state.tokens_used!==undefined)document.getElementById("tokens-total").textContent="Total: "+state.tokens_used;
  if(state.turn!==undefined)document.getElementById("turn-count").textContent="Turn: "+state.turn;
  if(state.model)document.getElementById("model-info").textContent=state.model;
  if(state.spend!==undefined)document.getElementById("spend").textContent="Spend: $"+state.spend.toFixed(2);
}

function renderHealth(){
  const el=document.getElementById("spine-status");
  const spineStatus=state.spine_status||state.status||"unknown";
  el.textContent="Spine: "+spineStatus;
  if(spineStatus==="healthy")el.style.color="var(--green)";
  else if(spineStatus==="stalled")el.style.color="var(--red)";
  else el.style.color="var(--yellow)";
  document.getElementById("lazarus").textContent="Failures: "+(state.consecutive_failures!=null?state.consecutive_failures:"\u2014");
}

function renderContainers(){
  const el=document.getElementById("container-dots");
  el.innerHTML="";
  for(const[name,status]of Object.entries(containers)){
    if(!CONTAINER_KEYS.has(name))continue;
    const d=document.createElement("div");d.className="container-dot";
    const dot=document.createElement("span");dot.className="dot";
    dot.style.backgroundColor=status==="healthy"?"var(--green)":status==="offline"?"var(--dim)":"var(--red)";
    d.appendChild(dot);d.appendChild(document.createTextNode(name));el.appendChild(d);
  }
}

function dedupEvents(evts){const seen=new Set();return evts.filter(e=>{const key=e.type+"|"+e.ts;return!seen.has(key)&&(seen.add(key),true)})}

function renderEvents(){
  const el=document.getElementById("event-list");el.innerHTML="";
  const recent=dedupEvents(events.slice(-50));
  for(const ev of recent){
    const div=document.createElement("div");let cls="event-item";
    const type=ev.type||ev.event_type||"";
    if(type.includes("restart"))cls+=" restart";
    else if(type.includes("crash"))cls+=" crash";
    else if(type.includes("override"))cls+=" override";
    else if(type.includes("started"))cls+=" started";
    div.className=cls;
    const ts=document.createElement("span");ts.className="ts";ts.textContent=(ev.ts||"").substring(11,19);div.appendChild(ts);
    let summary=type.replace(/^(spine\.|cortex\.)/,"");
    if(ev.reason)summary+=" : "+ev.reason;
    if(ev.exit_code)summary+=" (exit "+ev.exit_code+")";
    if(ev.tool)summary+=" \u25b8 "+ev.tool;
    if(ev.success===false)summary+=" \u2717";else if(ev.success===true)summary+=" \u2713";
    div.appendChild(document.createTextNode(summary));el.appendChild(div);
  }
  el.scrollTop=el.scrollHeight;
}

function renderCommit(){
  const el=document.getElementById("commit-info");
  if(!commit.candidate){el.textContent="No commit info";return}
  let text="Candidate: "+commit.candidate.substring(0,8);
  if(commit.candidate_msg)text+=" \u2014 "+commit.candidate_msg;
  if(commit.stable)text+=" | Stable: "+commit.stable.substring(0,8);
  if(commit.ahead)text+=" | "+commit.ahead+" ahead";
  el.textContent=text;
}

function updateContextBar(pct){
  const fill=document.getElementById("context-fill");
  const ctxt=document.getElementById("context-text");
  const pctNum=Math.round(pct*100);
  fill.style.width=pctNum+"%";
  ctxt.textContent=pctNum+"%";
  if(pctNum<60)fill.style.backgroundColor="var(--green)";
  else if(pctNum<85)fill.style.backgroundColor="var(--yellow)";
  else fill.style.backgroundColor="var(--red)";
}

async function sendCommand(cmd){await fetch("/api/command",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:cmd})})}

function setupScrollPause(){
  const el=document.getElementById("transcript");if(!el)return;
  el.addEventListener("scroll",()=>{const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<60;autoScroll=atBottom});
}

function maybeScroll(el){if(autoScroll)el.scrollTop=el.scrollHeight}

function formatArgs(argsStr){
  if(!argsStr)return"";
  let parsed=argsStr;
  if(typeof argsStr==="string"){try{parsed=JSON.parse(argsStr)}catch{return argsStr}}
  if(typeof parsed!=="object")return String(parsed);
  if(Array.isArray(parsed))return parsed.map(i=>String(i)).join("\n");
  const lines=[];
  for(const[k,v]of Object.entries(parsed)){
    const val=typeof v==="string"?v:JSON.stringify(v,null,2);
    lines.push(k+": "+val);
  }
  return lines.join("\n");
}

function makeCollapsibleBody(text,div){
  const body=document.createElement("div");body.className="msg-body";body.textContent=text;
  const len=text.length;const lines=text.split("\n").length;
  if(len>COLLAPSE_CHARS||lines>COLLAPSE_LINES){
    body.classList.add("collapsed");
    const toggle=document.createElement("div");toggle.className="msg-toggle";toggle.textContent="Show "+len+" chars";
    toggle.addEventListener("click",()=>{div.classList.toggle("expanded");toggle.textContent=div.classList.contains("expanded")?"Hide ("+len+" chars)":"Show "+len+" chars"});
    div.appendChild(toggle);
  }else{
    body.classList.add("expanded");
  }
  return body;
}

function renderAllMessages(messages){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  transcript.innerHTML="";
  currentTurnEl=null;
  const turns=buildTurns(messages);
  for(const turn of turns){
    appendTurn(transcript,turn);
  }
  maybeScroll(transcript);
}

function appendMessage(msg){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  if(document.querySelector('.view.active')?.id!=='view-stream')switchView('stream');

  const role=msg.role||"unknown";

  if(role==="assistant"){
    const turn={type:"assistant",assistant:msg,toolResults:[]};
    appendTurn(transcript,turn);
    currentTurnEl={el:transcript.lastElementChild,toolResults:turn.toolResults,assistant:msg};
    return;
  }

  if(role==="tool"){
    if(currentTurnEl){
      const resultDiv=renderToolResult(msg,null);
      currentTurnEl.el.appendChild(resultDiv);
      maybeScroll(transcript);
      return;
    }
    const turn={type:"orphan_tools",messages:[msg]};
    appendTurn(transcript,turn);
    return;
  }

  const turn={type:role,messages:[msg]};
  appendTurn(transcript,turn);
  maybeScroll(transcript);
}

function buildTurns(messages){
  var turns=[];
  var i=0;
  while(i<messages.length){
    var m=messages[i];
    var role=m.role||"unknown";
    if(role==="system"){
      turns.push({type:"system",messages:[m]});i++;continue;
    }
    if(role==="user"){
      turns.push({type:"user",messages:[m]});i++;continue;
    }
    if(role==="assistant"){
      var turn={type:"assistant",assistant:m,toolResults:[]};
      i++;
      while(i<messages.length&&messages[i].role==="tool"){
        turn.toolResults.push(messages[i]);i++;
      }
      turns.push(turn);continue;
    }
    if(role==="tool"){
      var turn={type:"orphan_tools",messages:[m]};i++;
      while(i<messages.length&&messages[i].role==="tool"){turn.messages.push(messages[i]);i++;}
      turns.push(turn);continue;
    }
    turns.push({type:"other",messages:[m]});i++;
  }
  return turns;
}

function appendTurn(transcript,turn){
  if(turn.type==="system"||turn.type==="user"||turn.type==="other"){
    var m=turn.messages[0];var role=m.role||"unknown";
    var content=typeof m.content==="string"?m.content:(m.content!=null?JSON.stringify(m.content):"");
    var div=document.createElement("div");div.className="msg msg-"+role;
    var label=document.createElement("div");label.className="msg-label";
    label.textContent=role;div.appendChild(label);
    if(content)div.appendChild(makeCollapsibleBody(content,div));
    transcript.appendChild(div);
    return;
  }

  if(turn.type==="orphan_tools"){
    for(var k=0;k<turn.messages.length;k++){
      transcript.appendChild(renderToolResult(turn.messages[k],null));
    }
    return;
  }

  var m=turn.assistant;
  var content=typeof m.content==="string"?m.content:(m.content!=null?JSON.stringify(m.content):"");
  var toolCalls=m.tool_calls||[];
  var reasoning=m.reasoning||"";
  if(!reasoning&&typeof content==="string"){
    var thinkMatch=content.match(/<thinking>([\s\S]*?)<\/thinking>/);
    if(thinkMatch){reasoning=thinkMatch[1];content=content.replace(/<thinking>[\s\S]*?<\/thinking>/,"").trim()}
  }

  var turnDiv=document.createElement("div");turnDiv.className="turn";

  var asstDiv=document.createElement("div");asstDiv.className="msg msg-assistant";
  var asstLabel=document.createElement("div");asstLabel.className="msg-label";asstLabel.textContent="assistant (turn "+(m._turn||"\u2014")+")";asstDiv.appendChild(asstLabel);

  if(reasoning){
    var thinkDiv=document.createElement("div");thinkDiv.className="msg msg-thinking collapsed";
    var thinkLabel=document.createElement("div");thinkLabel.className="msg-label";thinkLabel.textContent="reasoning";thinkDiv.appendChild(thinkLabel);
    var thinkBody=document.createElement("div");thinkBody.className="think-body";thinkBody.textContent=reasoning;thinkDiv.appendChild(thinkBody);
    var thinkToggle=document.createElement("div");thinkToggle.className="msg-toggle";thinkToggle.textContent="Show reasoning";
    thinkToggle.addEventListener("click",(function(td,tt){return function(){td.classList.toggle("expanded");tt.textContent=td.classList.contains("expanded")?"Hide reasoning":"Show reasoning"}})(thinkDiv,thinkToggle));
    thinkDiv.appendChild(thinkToggle);
    asstDiv.appendChild(thinkDiv);
  }

  if(content){
    asstDiv.appendChild(makeCollapsibleBody(content,asstDiv));
  }

  if(toolCalls.length>0){
    for(var ci=0;ci<toolCalls.length;ci++){
      var tc=toolCalls[ci];
      var tcName=(tc.function&&tc.function.name)||"tool";
      var tcArgs=(tc.function&&tc.function.arguments)||"{}";
      var tcId=tc.id||"";
      var formatted=formatArgs(tcArgs);
      var sub=document.createElement("div");sub.className="tool-sub";
      var header=document.createElement("div");header.className="tool-header";
      if(formatted.length>100){
        header.textContent="\u25b8 "+tcName;
        var argsEl=document.createElement("div");argsEl.className="tool-args collapsed";argsEl.textContent=formatted;
        header.style.cursor="pointer";
        header.addEventListener("click",(function(h,a,n){return function(e){e.stopPropagation();a.classList.toggle("collapsed");h.textContent=a.classList.contains("collapsed")?"\u25b8 "+n:"\u25bd "+n}})(header,argsEl,tcName));
        sub.appendChild(header);sub.appendChild(argsEl);
      }else{
        header.textContent="\u25b8 "+tcName+"("+formatted+")";
        sub.appendChild(header);
      }
      asstDiv.appendChild(sub);
    }
  }

  turnDiv.appendChild(asstDiv);

  var toolResultMap={};
  for(var ri=0;ri<turn.toolResults.length;ri++){
    var tr=turn.toolResults[ri];
    var tid=tr.tool_call_id||"";
    if(tid)toolResultMap[tid]=tr;
  }

  if(toolCalls.length>0){
    for(var ci=0;ci<toolCalls.length;ci++){
      var tc=toolCalls[ci];var tcId=tc.id||"";
      var result=toolResultMap[tcId];
      if(result){
        turnDiv.appendChild(renderToolResult(result,tc));
      }
    }
    for(var ri=0;ri<turn.toolResults.length;ri++){
      var tr=turn.toolResults[ri];var tid=tr.tool_call_id||"";
      var matched=toolCalls.some(function(tc){return(tc.id||"")===tid});
      if(!matched)turnDiv.appendChild(renderToolResult(tr,null));
    }
  }else{
    for(var ri=0;ri<turn.toolResults.length;ri++){
      turnDiv.appendChild(renderToolResult(turn.toolResults[ri],null));
    }
  }

  transcript.appendChild(turnDiv);
}

function renderToolResult(m,tc){
  var content=typeof m.content==="string"?m.content:(m.content!=null?JSON.stringify(m.content):"");
  var toolName=m.name||"tool";
  if((!toolName||toolName==="tool")&&tc){
    toolName=(tc.function&&tc.function.name)||"tool";
  }
  var div=document.createElement("div");div.className="msg msg-tool";
  var label=document.createElement("div");label.className="msg-label";label.textContent=toolName;
  if(content.includes("[TOOL ERROR]")||content.includes("[EXIT 1]")){var failSpan=document.createElement("span");failSpan.className="fail";failSpan.textContent=" \u2717";label.appendChild(failSpan)}
  else{var okSpan=document.createElement("span");okSpan.className="ok";okSpan.textContent=" \u2713";label.appendChild(okSpan)}
  div.appendChild(label);
  if(content)div.appendChild(makeCollapsibleBody(content,div));
  return div;
}
```

Key changes from original:
- Removed: `thinkActive`, `currentAssistantEl`, `currentThinkingEl`, `streamThinkBuffer`, `pendingTrajectory`, `lastThinkEnd`, `callPending`, `lastTrajectoryKey`, `prevMsgCount`
- Removed: `startThink`, `endThink`, `appendLiveToken`, `appendThinkingToken`, `finishThinking`, `renderThinkingBlock` (streaming), `appendLiveToolCall`, `appendError`, `parseThinkingContent`
- Removed: All `case` handlers for `stream_token`, `thinking_token`, `thinking`, `tool_call`, `tool_result`, `think_start`, `think_end`, `error`, `event`
- New: `appendMessage(msg)` for incremental message rendering
- New: `renderAllMessages(messages)` for snapshot rendering
- New: `appendTurn(transcript, turn)` shared rendering for both paths
- Reasoning now read from `msg.reasoning` field (not `<thinking>` tag parsing), with fallback for old format

- [ ] **Step 2: Commit**

```bash
git add xray/static/app.js
git commit -m "refactor(xray): replace streaming/trajectory dual render with single message path"
```

---

### Task 8: Update Frontend HTML and CSS

**Files:**
- Modify: `xray/static/index.html`
- Modify: `xray/static/style.css`

- [ ] **Step 1: Simplify index.html**

Remove the `pending-indicator` section from the top-bar (lines 21-24):

```html
<!-- DELETE these lines: -->
            <span id="pending-indicator" class="pending-indicator hidden">
                <span class="spinner"></span>
                <span id="pending-text">Waiting on LLM...</span>
            </span>
```

Remove the `notices-badge` (line 29):

```html
<!-- DELETE: -->
            <span id="notices-badge" class="notices-badge hidden" title="Queued notices"></span>
```

Remove the `stream-header` section from the stream view (lines 77-81):

```html
<!-- DELETE: -->
        <div id="stream-header">
            <span id="stream-status" class="status-dot"></span>
            <span id="stream-turn">Turn —</span>
            <span id="stream-model">—</span>
        </div>
```

- [ ] **Step 2: Clean up style.css**

Remove streaming-related styles that are no longer used. Delete the `.pending-indicator` styles (line 99-100):

```css
/* DELETE: */
.pending-indicator{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#92400e}
.spinner{width:12px;height:12px;border:2px solid #fef3c7;border-top-color:#92400e;border-radius:50%;animation:spin .8s linear infinite}
```

Delete the `@keyframes spin` (line 102):

```css
/* DELETE: */
@keyframes spin{to{transform:rotate(360deg)}}
```

Delete the `.notices-badge` styles (line 107-108):

```css
/* DELETE: */
.notices-badge{background:var(--yellow);color:var(--bg);font-size:11px;font-weight:bold;padding:2px 8px;border-radius:10px;animation:notice-pulse 1.5s ease-in-out infinite}
@keyframes notice-pulse{0%,100%{opacity:1}50%{opacity:.6}}
```

Delete the `#stream-header` styles (line 60):

```css
/* DELETE: */
#stream-header{padding:6px 16px;background:var(--card);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;font-size:12px;color:var(--dim);flex-shrink:0}
```

- [ ] **Step 3: Commit**

```bash
git add xray/static/index.html xray/static/style.css
git commit -m "refactor(xray): remove streaming UI elements and styles"
```

---

### Task 9: Remove sse-starlette Dependency

**Files:**
- Modify: `gate/requirements.txt`
- Modify: `xray/requirements.txt`

- [ ] **Step 1: Remove sse-starlette from gate/requirements.txt**

Remove line 6:

```
sse-starlette>=1.6
```

The file becomes:

```
fastapi>=0.111.0
uvicorn>=0.30.0
httpx>=0.27.0
python-dotenv>=1.0.1
python-multipart>=0.0.9
```

- [ ] **Step 2: Remove sse-starlette from xray/requirements.txt**

Remove line 4:

```
sse-starlette>=1.6
```

The file becomes:

```
fastapi>=0.110
uvicorn[standard]>=0.29
httpx>=0.27
python-dotenv>=1.0
```

- [ ] **Step 3: Verify no remaining sse-starlette imports in gate/app.py**

The `sse_starlette` imports were all inside the three SSE endpoint functions deleted in Task 3. Verify they're gone:

```bash
grep -n "sse_starlette" gate/app.py
```

Expected: no output (all references removed by Task 3)

- [ ] **Step 4: Commit**

```bash
git add gate/requirements.txt xray/requirements.txt gate/app.py
git commit -m "chore: remove sse-starlette dependency (no longer needed)"
```

---

### Task 10: Final Verification

**Files:** None (testing only)

- [ ] **Step 1: Verify Python imports are clean**

Run: `cd /home/zeus/content/talos_runtime && python -c "import importlib.util; spec = importlib.util.spec_from_file_location('app', 'gate/app.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('gate OK')"`
Expected: gate OK

Run: `cd /home/zeus/content/talos_runtime && python -c "import importlib.util; spec = importlib.util.spec_from_file_location('xray_client', 'xray/xray_client.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('xray OK')"`
Expected: xray OK

- [ ] **Step 2: Run all gate tests**

Run: `cd /home/zeus/content/talos_runtime && python -m pytest gate/ -v`
Expected: All PASS

- [ ] **Step 3: Verify no references to removed code**

Run: `cd /home/zeus/content/talos_runtime && grep -r "_xray_broadcast" gate/ xray/ || echo "CLEAN"`
Expected: CLEAN

Run: `cd /home/zeus/content/talos_runtime && grep -r "stream_token\|thinking_token\|think_start\|think_end" xray/ || echo "CLEAN"`
Expected: CLEAN

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete x-ray JSONL message trace migration"
```