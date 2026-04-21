# Talos Bare-Minimum Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the bare-minimum rewrite by committing review fixes, implementing the `think` IPC handler, updating `entrypoint.sh` and `spine_config.json` for the new architecture, verifying gate/xray alignment, and closing out the development branch.

**Architecture:** The spine now uses `HealthMonitor` (not `HealthTracker`), no `ControlPlane` or `SnapshotManager`, and observability comes from shared volume files. The `think` IPC handler must proxy to the gate's `/v1/chat/completions` endpoint, parse the response, record messages into the stream, and return `tool_calls`/`context_pct`/`tokens_used` to the cortex. The `entrypoint.sh` references `/spine/snapshots` and `control_plane_port` — both obsolete.

**Tech Stack:** Python 3.12, asyncio, pytest, httpx (gate proxy), JSON-RPC over Unix sockets

---

## File Map

### Modified files
```
talos/spine/ipc_server.py        (implement think handler — gate proxy)
talos/spine/config.py             (add gate_url convenience, already has it)
entrypoint.sh                     (remove snapshots/ reference, add trajectories/)
spine_config.json                 (remove obsolete fields)
Dockerfile                        (CMD uses python -m spine now, not seed_agent.py)
```

### New files
```
talos/spine/gate_proxy.py         (gate HTTP client for LLM inference)
talos/tests/spine/test_gate_proxy.py
```

---

## Task 1: Commit Review Fixes to refactor/bare-minimum

**Files:**
- All uncommitted changes in the talos submodule

- [ ] **Step 1: Stage all changes**

```bash
cd /home/zeus/content/talos_runtime/talos
git add -A
```

- [ ] **Step 2: Verify staged changes**

```bash
git diff --cached --stat
```

Expected: Shows deletions of dead files, modifications to main.py, config.py, stream.py, events.py, etc., and new files (guards.py, __main__.py, test_guards.py, test_stream.py).

- [ ] **Step 3: Commit**

```bash
git commit -m "fix: code review fixes — delete dead code, fix main.py imports, add cortex/__main__.py, strengthen spine guard, fix EventLogger payload nesting, add piggyback double-injection guard, add context_pct to _build_hud, remove obsolete config fields"
```

- [ ] **Step 4: Run tests to verify clean state**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -m pytest tests/ -v --tb=short
```

Expected: 142 passed

---

## Task 2: Gate Proxy — HTTP Client for LLM Inference

The spine's `think` handler currently returns a stub. It needs to forward the LLM payload to the gate, parse the response, record it in the stream, and return tool_calls + metadata to the cortex.

**Files:**
- Create: `talos/spine/gate_proxy.py`
- Test: `talos/tests/spine/test_gate_proxy.py`

- [ ] **Step 1: Write failing test**

```python
# talos/tests/spine/test_gate_proxy.py
import json
import pytest
from unittest.mock import patch, MagicMock
from spine.gate_proxy import GateProxy


@pytest.fixture
def proxy():
    return GateProxy(gate_url="http://localhost:4000/v1/chat/completions")


def test_proxy_creation(proxy):
    assert proxy.gate_url == "http://localhost:4000/v1/chat/completions"


def test_call_returns_tool_calls(proxy):
    fake_response = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "bash_command",
                                "arguments": '{"command": "ls"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "context_pct": 0.35,
        },
    }
    with patch("spine.gate_proxy.httpx.Client") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_response
        mock_client = MockClient.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp
        result = proxy.call(messages=[{"role": "user", "content": "hello"}], tools=[])
    assert result["tool_calls"][0]["name"] == "bash_command"
    assert result["context_pct"] == 0.35
    assert result["tokens_used"] == 120


def test_call_no_tool_calls(proxy):
    fake_response = {
        "id": "chatcmpl-2",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "I'm thinking about it.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
            "context_pct": 0.2,
        },
    }
    with patch("spine.gate_proxy.httpx.Client") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_response
        mock_client = MockClient.return_value.__enter__.return_value
        mock_client.post.return_value = mock_resp
        result = proxy.call(messages=[{"role": "user", "content": "hello"}], tools=[])
    assert result["tool_calls"] == []
    assert result["assistant_message"] == "I'm thinking about it."


def test_call_connection_error(proxy):
    with patch("spine.gate_proxy.httpx.Client") as MockClient:
        mock_client = MockClient.return_value.__enter__.return_value
        mock_client.post.side_effect = Exception("connection refused")
        with pytest.raises(Exception):
            proxy.call(messages=[], tools=[])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -m pytest tests/spine/test_gate_proxy.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'spine.gate_proxy'`

- [ ] **Step 3: Implement gate_proxy.py**

```python
# talos/spine/gate_proxy.py
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("spine.gate_proxy")


class GateProxy:
    def __init__(self, gate_url: str, model: str = ""):
        self.gate_url = gate_url
        self.model = model

    def call(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str = "",
    ) -> dict[str, Any]:
        body = {
            "messages": messages,
            "tools": tools if tools else None,
            "tool_choice": "auto" if tools else None,
        }
        effective_model = model or self.model
        if effective_model:
            body["model"] = effective_model

        with httpx.Client(timeout=600.0) as client:
            resp = client.post(self.gate_url, json=body)
            resp.raise_for_status()
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            args_str = func.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except (json.JSONDecodeError, ValueError):
                args = {}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args,
            })

        return {
            "assistant_message": message.get("content", ""),
            "tool_calls": tool_calls,
            "context_pct": usage.get("context_pct", 0.0),
            "tokens_used": usage.get("total_tokens", 0),
            "finish_reason": choice.get("finish_reason", ""),
        }
```

- [ ] **Step 4: Run tests**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -m pytest tests/spine/test_gate_proxy.py -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add spine/gate_proxy.py tests/spine/test_gate_proxy.py
git commit -m "feat: add gate proxy for LLM inference via HTTP"
```

---

## Task 3: Wire think IPC Handler to Gate Proxy

**Files:**
- Modify: `talos/spine/ipc_server.py`
- Modify: `talos/spine/main.py`

The `think` handler currently returns `{"status": "stub"}`. It needs to:
1. Build the LLM payload from `stream.build_payload(tools, hud_data)`
2. Call `gate_proxy.call()` with the payload
3. Record the assistant message and tool calls in the stream
4. Piggyback the HUD
5. Write state.json
6. Return `tool_calls`, `context_pct`, `tokens_used`, `turn` to the cortex

- [ ] **Step 1: Update ipc_server.py**

Replace the `think` handler stub and add `gate_proxy` to `__init__`:

In `__init__`, add `gate_proxy` parameter:
```python
def __init__(
    self,
    cfg: SpineConfig,
    supervisor: Any,
    stream: StreamManager,
    events: EventLogger,
    gate_proxy: Any | None = None,
):
    self.cfg = cfg
    self.supervisor = supervisor
    self.stream = stream
    self.events = events
    self.gate_proxy = gate_proxy
    self._server: asyncio.Server | None = None
```

In `_handle_request`, replace the think stub:
```python
if method == "think":
    if not self.gate_proxy:
        return self._error(req_id, -32000, "No gate proxy configured")
    payload = self.stream.build_payload(
        params.get("tools", []),
        params.get("hud_data", {}),
    )
    try:
        result = self.gate_proxy.call(
            messages=payload,
            tools=params.get("tools", []),
        )
    except Exception as e:
        logger.error(f"[Spine] Gate proxy error: {e}")
        return self._error(req_id, -32000, f"Gate error: {e}")
    # Record assistant message in stream
    assistant_content = result.get("assistant_message", "")
    raw_tool_calls = result.get("tool_calls", [])
    openai_tool_calls = [
        {
            "id": tc["id"],
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(tc["arguments"]),
            },
        }
        for tc in raw_tool_calls
    ]
    self.stream.add_message({
        "role": "assistant",
        "content": assistant_content,
        "tool_calls": openai_tool_calls if openai_tool_calls else None,
    })
    self.stream.turn += 1
    # Piggyback HUD
    hud_data = params.get("hud_data", {})
    hud_data["turn"] = self.stream.turn
    hud_data["context_pct"] = result.get("context_pct", 0.0)
    self.stream.piggyback_hud(hud_data)
    # Write state
    self.stream.write_state(
        focus=hud_data.get("focus", ""),
        context_pct=result.get("context_pct", 0.0),
        urgency=hud_data.get("urgency", "nominal"),
    )
    self.events.emit("spine.think", {"turn": self.stream.turn, "context_pct": result.get("context_pct", 0.0)})
    return self._success(req_id, {
        "tool_calls": raw_tool_calls,
        "context_pct": result.get("context_pct", 0.0),
        "tokens_used": result.get("tokens_used", 0),
        "turn": self.stream.turn,
        "assistant_message": assistant_content,
    })
```

- [ ] **Step 2: Update main.py to create GateProxy and pass to IPCServer**

In `main()`, after creating the supervisor and before creating ipc_server:
```python
from spine.gate_proxy import GateProxy
gate_proxy = GateProxy(cfg.gate_url, model=os.environ.get("TALOS_MODEL", ""))
ipc_server = IPCServer(cfg, supervisor, stream_mgr, event_logger, gate_proxy)
```

- [ ] **Step 3: Write test for think handler with gate proxy**

Add to `tests/spine/test_ipc_server.py`:

```python
@pytest.mark.asyncio
async def test_ipc_think_with_proxy(tmp_path):
    cfg = SpineConfig()
    cfg.socket_path = str(tmp_path / "test.sock")
    cfg.spine_dir = str(tmp_path / "spine")
    cfg.constitution_path = str(tmp_path / "CONSTITUTION.md")
    cfg.identity_path = str(tmp_path / "identity.md")
    cfg.memory_dir = str(tmp_path / "memory")
    Path(cfg.constitution_path).write_text("# Principles\nAgency.")
    Path(cfg.identity_path).write_text("# Identity\nTalos.")
    Path(cfg.spine_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.memory_dir).mkdir(parents=True, exist_ok=True)
    events = EventLogger(str(Path(cfg.spine_dir) / "events"))
    stream = StreamManager(cfg)
    health = HealthMonitor(stall_timeout=600.0, startup_timeout=30.0)
    supervisor = Supervisor(cfg, events, health, stream)
    mock_proxy = MagicMock()
    mock_proxy.call.return_value = {
        "assistant_message": "I'll help",
        "tool_calls": [{"id": "c1", "name": "bash_command", "arguments": {"command": "ls"}}],
        "context_pct": 0.35,
        "tokens_used": 120,
        "finish_reason": "tool_calls",
    }
    server = IPCServer(cfg, supervisor, stream, events, gate_proxy=mock_proxy)
    await server.start()
    reader, writer = await asyncio.open_unix_connection(cfg.socket_path)
    req = {"jsonrpc": "2.0", "id": 1, "method": "think", "params": {"tools": [], "hud_data": {}}}
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    data = await reader.readline()
    resp = json.loads(data.decode())
    assert resp["result"]["tool_calls"][0]["name"] == "bash_command"
    assert resp["result"]["context_pct"] == 0.35
    writer.close()
    await writer.wait_closed()
    await server.stop()
```

- [ ] **Step 4: Run all tests**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -m pytest tests/ -v --tb=short
```

Expected: All PASS (including new think test)

- [ ] **Step 5: Commit**

```bash
git add spine/ipc_server.py spine/main.py tests/spine/test_ipc_server.py
git commit -m "feat: wire think IPC handler to gate proxy for LLM inference"
```

---

## Task 4: Update entrypoint.sh

**Files:**
- Modify: `entrypoint.sh`

The entrypoint currently:
- Creates `/spine/snapshots` and `/spine/crashes` directories (obsolete)
- Doesn't create `/spine/trajectories` (needed for fork-on-fold)
- Starts spine with `python -m spine` (correct for new architecture)
- References `control_plane_port` in spine_config.json (obsolete)

- [ ] **Step 1: Update directory creation**

Replace:
```bash
mkdir -p /spine/events /spine/snapshots /spine/crashes
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/snapshots /spine/crashes
```

With:
```bash
mkdir -p /spine/events /spine/trajectories
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/trajectories
```

- [ ] **Step 2: Verify the existing spine startup command is correct**

The entrypoint already runs `python -m spine /spine/spine_config.json &` — this is correct for the new architecture. No change needed.

- [ ] **Step 3: Verify no other control-plane references**

```bash
grep -n "control_plane\|snapshot\|4001" /home/zeus/content/talos_runtime/entrypoint.sh
```

Expected: No matches (except possibly comments)

- [ ] **Step 4: Commit**

```bash
cd /home/zeus/content/talos_runtime && git add entrypoint.sh
git commit -m "fix: update entrypoint for bare-minimum architecture — trajectories not snapshots"
```

---

## Task 5: Update spine_config.json

**Files:**
- Modify: `spine_config.json`

Remove obsolete fields (`control_plane_port`, `active_window`, `max_context_tokens`, `stall_timeout`, `snapshot_interval`, `max_reversal_depth`, `shed_tool_output_max_chars`) and add `gate_model` as the model field for the gate proxy.

- [ ] **Step 1: Write updated config**

```json
{
  "memory_dir": "/memory",
  "spine_dir": "/spine",
  "constitution_path": "/app/CONSTITUTION.md",
  "identity_path": "/app/identity.md",
  "app_dir": "/app",
  "socket_path": "/tmp/spine.sock",
  "context_threshold_pct": 0.85,
  "gate_url": "http://gate:4000/v1/chat/completions"
}
```

- [ ] **Step 2: Verify it loads correctly**

```bash
cd /home/zeus/content/talos_runtime/talos && python -c "
import json
from spine.config import load_config
cfg = load_config('/home/zeus/content/talos_runtime/spine_config.json')
print(f'gate_url={cfg.gate_url}')
print(f'socket_path={cfg.socket_path}')
print(f'spine_dir={cfg.spine_dir}')
print(f'context_threshold_pct={cfg.context_threshold_pct}')
"
```

Expected: All fields print correctly with no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/zeus/content/talos_runtime && git add spine_config.json
git commit -m "fix: update spine_config.json — remove obsolete fields, align with bare-minimum config"
```

---

## Task 6: Update Dockerfile CMD

**Files:**
- Modify: `Dockerfile`

The current CMD is `["/venv/bin/python", "cortex/seed_agent.py"]` — this starts the cortex directly. But the new architecture has the spine as the entrypoint which manages the cortex lifecycle (via supervisor). The entrypoint.sh already runs `python -m spine /spine/spine_config.json &`. The CMD is never reached because the entrypoint script does `wait $SPINE_PID` at the end.

However, if the entrypoint fails to launch the spine, the container exits. The CMD should be a no-op fallback since the entrypoint handles everything.

- [ ] **Step 1: Update CMD**

Replace:
```
CMD ["/venv/bin/python", "cortex/seed_agent.py"]
```

With:
```
CMD ["sleep", "infinity"]
```

The entrypoint.sh already runs `python -m spine` and then `wait` on it. The CMD is just a safety net.

- [ ] **Step 2: Commit**

```bash
cd /home/zeus/content/talos_runtime && git add Dockerfile
git commit -m "fix: Dockerfile CMD is safety net — entrypoint runs spine process"
```

---

## Task 7: Verify Gate/Xray Alignment

**Files:**
- Review: `gate/app.py` — `_normalize_content()`, `_normalize_tool_calls()`
- Review: `xray/xray_client.py` — file-based spine state reads
- Review: `xray/app.py` — sentinel file commands

These were already updated in a prior task, but we need to verify they work correctly with the new architecture.

- [ ] **Step 1: Verify gate normalization still works**

The gate's `_normalize_content()` strips `<|channel|>` and `<|...|>` tokens. The gate's `_normalize_tool_calls()` parses Gemma `<|tool_call|>` tokens. These are independent of the talos rewrite — they operate on the LLM response content, which hasn't changed. No modifications needed.

Verify by checking that the gate reads `_normalize_content` and `_normalize_tool_calls` on every chat response:
```bash
grep -n "_normalize_content\|_normalize_tool_calls" /home/zeus/content/talos_runtime/gate/app.py | head -10
```

Expected: Multiple hits in both streaming and non-streaming paths.

- [ ] **Step 2: Verify xray reads spine state from files**

The xray client now reads `/spine/state.json`, `/spine/health.json`, `/spine/commit.json` directly. Verify the fields match what the spine writes:

Check spine `write_state()` fields:
```bash
grep -A 10 "def write_state" /home/zeus/content/talos_runtime/talos/spine/stream.py
```

Expected: `turn`, `context_pct`, `focus`, `urgency`, `memory_file_count`, `last_files`

Check spine `write_health()` fields:
```bash
grep -A 10 "def write_health" /home/zeus/content/talos_runtime/talos/spine/supervisor.py
```

Expected: `status`, `consecutive_failures`, `last_stable_commit`

Check xray reads:
```bash
grep -A 5 "state.json\|health.json" /home/zeus/content/talos_runtime/xray/xray_client.py
```

Expected: xray reads `state.json` for state_update events and `health.json` for talos health status.

- [ ] **Step 3: Verify xray sentinel file commands**

```bash
grep -A 10 "api/command" /home/zeus/content/talos_runtime/xray/app.py
```

Expected: `pause` → `touch .paused`, `resume` → `rm .paused && touch .wake`, `force_restart` → `touch .restart`

- [ ] **Step 4: Note — xray `is_paused` reads from `state.json`**

The xray client sets `self.is_paused` from `self._state.get("is_paused", False)` at line 101. But `state.json` (written by `stream.py:write_state()`) doesn't include `is_paused`. The pause state comes from the `.paused` sentinel file, not state.json.

Fix the xray client to check the sentinel file instead:
```python
# In _poll_spine_state, replace:
self.is_paused = self._state.get("is_paused", False)
# With:
self.is_paused = (self.spine_dir / ".paused").exists()
```

- [ ] **Step 5: Commit xray fix**

```bash
cd /home/zeus/content/talos_runtime && git add xray/xray_client.py
git commit -m "fix: xray reads pause state from .paused sentinel file, not state.json"
```

---

## Task 8: Update Parent Repo Talos Submodule Pointer

**Files:**
- Modify: `talos` (submodule pointer)

The parent repo tracks the talos submodule. We need to update it to point to the latest commit on `refactor/bare-minimum`.

- [ ] **Step 1: Commit the talos submodule changes**

```bash
cd /home/zeus/content/talos_runtime
git add talos
git commit -m "chore: update talos submodule with bare-minimum review fixes and think handler"
```

---

## Task 9: Run Full Integration Test

- [ ] **Step 1: Run the talos test suite**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -m pytest tests/ -v --tb=short
```

Expected: All PASS (should be ~145+ tests now)

- [ ] **Step 2: Verify spine can start (import check)**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -c "from spine.main import main; print('Spine import OK')"
```

Expected: "Spine import OK"

- [ ] **Step 3: Verify cortex can start (import check)**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -c "from cortex.seed_agent import main; print('Cortex import OK')"
```

Expected: "Cortex import OK"

- [ ] **Step 4: Verify gate proxy can be created**

```bash
cd /home/zeus/content/talos_runtime/talos && PYTHONPATH=. python -c "from spine.gate_proxy import GateProxy; p = GateProxy('http://test:4000'); print('Gate proxy OK')"
```

Expected: "Gate proxy OK"

---

## Task 10: Finish Development Branch

**Files:**
- Git operations on `refactor/bare-minimum` and parent repo

- [ ] **Step 1: Ensure all talos changes are committed**

```bash
cd /home/zeus/content/talos_runtime/talos && git status
```

Expected: clean working tree

- [ ] **Step 2: Push talos submodule**

```bash
cd /home/zeus/content/talos_runtime/talos && git push origin refactor/bare-minimum 2>&1 || echo "Push may require remote setup"
```

- [ ] **Step 3: Ensure parent repo changes are committed**

```bash
cd /home/zeus/content/talos_runtime && git status
```

Expected: clean or only untracked docs

- [ ] **Step 4: Document the state of the codebase**

Update the summary of accomplishments:
- 145+ tests passing
- Spine starts with `python -m spine`
- Cortex starts with `python -m cortex` or `python cortex/seed_agent.py`
- Think IPC handler proxies to gate via `GateProxy`
- Spine write guard blocks `python -c`, `sed -i`, `dd of=` patterns
- Event logger nests payload (not flattens)
- HUD piggyback has double-injection guard
- `entrypoint.sh` creates `trajectories/` not `snapshots/`
- `spine_config.json` has no obsolete fields
- Xray reads pause from `.paused` sentinel file
- `Dockerfile` CMD is a safety net

---

## Self-Review Checklist

1. **Spec coverage:**
   - Think IPC handler → Task 3 (was the biggest gap)
   - Entrypoint update → Task 4
   - Spine config update → Task 5
   - Dockerfile update → Task 6
   - Gate/xray verification → Task 7
   - Submodule pointer → Task 8

2. **Placeholder scan:** No TBDs, TODOs, or vague steps. Each step has code or exact commands.

3. **Type consistency:**
   - `GateProxy.__init__(gate_url, model)` → `IPCServer` passes `cfg.gate_url`
   - `GateProxy.call()` returns `dict` with `tool_calls`, `context_pct`, `tokens_used`, `assistant_message` → matches what cortex `_build_hud` and agent loop expect
   - `IPCServer.__init__` now accepts `gate_proxy` parameter → `main.py` creates and passes it
   - `xray_client.py` `is_paused` reads from sentinel file → matches how `supervisor.is_paused()` works

4. **Missing items found:**
   - `TALOS_MODEL` env var is referenced in main.py but not in docker-compose.yml — should be added as an environment variable
   - The `Dockerfile` `PYTHONPATH` includes `/app:/app/cortex` but the cortex's `seed_agent.py` imports `from spine_client import ...` which requires `PYTHONPATH` to include `/app/cortex`. The spine imports `from spine.config import ...` which requires `/app`. Both are covered.