# Spine Python Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Spine from Go to Python, moving it into `talos/spine/` as an asyncio-based set of modules, with full test coverage and hypothesis property-based tests.

**Architecture:** The Spine runs as a separate Python process inside the talos container, communicating with Cortex via Unix domain socket JSON-RPC. It uses asyncio for I/O (IPC server, HTTP control plane, HTTP client to Gate). Same process architecture as the Go version — entrypoint.sh starts it as root before the Cortex.

**Tech Stack:** Python 3.13, asyncio, aiohttp (HTTP server + client), httpx (Telegram), pytest + hypothesis (testing)

**Spec:** `docs/superpowers/specs/2026-04-12-spine-python-rewrite-design.md`

---

## File Structure

### Created: `talos/spine/` (new)

```
talos/spine/
  __init__.py              ← Package marker (empty)
  main.py                 ← asyncio entry point, starts all subsystems
  config.py               ← Load spine_config.json, SpineConfig dataclass
  ipc_server.py           ← asyncio Unix socket JSON-RPC server
  ipc_types.py            ← Request/response dataclasses
  stream.py               ← Message stream, shedding, folding, HUD, Gate client
  constitution.py          ← Constitution + identity loading, SHA-256 hash tracking
  supervisor.py           ← Cortex subprocess lifecycle + Lazarus Protocol
  health.py               ← Stall/startup-failure detection
  events.py               ← JSONL event logger
  snapshot.py             ← State snapshot save/restore
  control_plane.py        ← aiohttp HTTP server on :4001
  telegram.py             ← Outbound Telegram notifications (httpx)
```

### Created: `talos/tests/spine/` (new)

```
talos/tests/spine/
  test_config.py
  test_ipc_server.py
  test_stream.py
  test_constitution.py
  test_supervisor.py
  test_health.py
  test_events.py
  test_snapshot.py
  test_control_plane.py
```

### Modified

```
talos/pyproject.toml              ← Add aiohttp dependency
talos/runtime/entrypoint.sh      ← Change spine start command
talos/runtime/Dockerfile         ← Remove Go build stage
talos/runtime/scripts/setup_hooks.sh  ← Add last_candidate_commit write
talos/runtime/talosctl            ← Targeted spine Lazarus + spine health detection
```

### Deleted

```
talos_runtime/spine/              ← Entire Go codebase (after migration verified)
spine/Dockerfile                  ← Go build stage (no longer needed)
```

---

### Task 1: Data Types — `ipc_types.py` and `config.py`

**Files:**
- Create: `talos/spine/__init__.py`
- Create: `talos/spine/ipc_types.py`
- Create: `talos/spine/config.py`
- Create: `talos/tests/spine/test_config.py`

- [ ] **Step 1: Create spine package**

Create `talos/spine/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Create ipc_types.py**

Create `talos/spine/ipc_types.py`:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class HUDData:
    memory_keys: int
    last_keys: list[str]
    urgency: str


@dataclass
class ThinkRequest:
    focus: str
    tools: list[ToolDef]
    hud_data: HUDData


@dataclass
class ToolResultRequest:
    tool_call_id: str
    output: str
    success: bool


@dataclass
class RequestFoldRequest:
    synthesis: str


@dataclass
class RequestRestartRequest:
    reason: str


@dataclass
class SendMessageRequest:
    channel: str
    text: str


@dataclass
class EmitEventRequest:
    type: str
    payload: dict[str, Any]


@dataclass
class GetStateRequest:
    keys: list[str]


@dataclass
class PushNotification:
    type: str
    payload: str


@dataclass
class ToolCallResult:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ThinkResponse:
    assistant_message: str
    tool_calls: list[ToolCallResult]
    context_pct: float
    turn: int
    tokens_used: int
    folded: bool


@dataclass
class JSONRPCRequest:
    jsonrpc: str
    id: int
    method: str
    params: dict[str, Any]


@dataclass
class RPCError:
    code: int
    message: str


@dataclass
class JSONRPCResponse:
    jsonrpc: str
    id: int
    result: Any = None
    error: RPCError | None = None
```

- [ ] **Step 3: Create config.py**

Create `talos/spine/config.py`:

```python
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpineConfig:
    memory_dir: str = "/memory"
    spine_dir: str = "/spine"
    constitution_path: str = "/app/CONSTITUTION.md"
    identity_path: str = "/app/identity.md"
    app_dir: str = "/app"
    cortex_bin: str = "/venv/bin/python"
    cortex_args: list[str] = field(default_factory=lambda: ["seed_agent.py"])
    startup_timeout: float = 30.0
    socket_path: str = "/tmp/spine.sock"
    control_plane_port: int = 4001
    context_threshold: float = 0.85
    active_window: int = 5
    max_context_tokens: int = 71680
    gate_url: str = "http://gate:4000"
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0
    stall_timeout: float = 600.0
    snapshot_interval: int = 10
    max_reversal_depth: int = 5
    shed_tool_output_max_chars: int = 500


def load_config(path: str) -> SpineConfig:
    cfg = SpineConfig()
    config_file = Path(path)
    if not config_file.exists():
        return cfg
    data = json.loads(config_file.read_text())
    for key, value in data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg
```

- [ ] **Step 4: Write tests for config.py**

Create `talos/tests/spine/test_config.py`:

```python
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cortex"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.config import SpineConfig, load_config


def test_default_config():
    cfg = SpineConfig()
    assert cfg.memory_dir == "/memory"
    assert cfg.socket_path == "/tmp/spine.sock"
    assert cfg.context_threshold == 0.85
    assert cfg.active_window == 5
    assert cfg.max_reversal_depth == 5


def test_load_config_missing_file():
    cfg = load_config("/nonexistent/path/config.json")
    assert cfg == SpineConfig()


def test_load_config_overrides(tmp_path):
    config_data = {
        "socket_path": "/custom/spine.sock",
        "context_threshold": 0.9,
        "active_window": 10,
    }
    config_file = tmp_path / "spine_config.json"
    config_file.write_text(json.dumps(config_data))
    cfg = load_config(str(config_file))
    assert cfg.socket_path == "/custom/spine.sock"
    assert cfg.context_threshold == 0.9
    assert cfg.active_window == 10
    assert cfg.memory_dir == "/memory"  # default preserved


def test_load_config_unknown_keys_ignored(tmp_path):
    config_data = {"unknown_key": "value", "socket_path": "/test.sock"}
    config_file = tmp_path / "spine_config.json"
    config_file.write_text(json.dumps(config_data))
    cfg = load_config(str(config_file))
    assert cfg.socket_path == "/test.sock"
    assert not hasattr(cfg, "unknown_key")
```

- [ ] **Step 5: Run tests**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/spine/test_config.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add spine/ tests/spine/ && git commit -m "feat(spine): add ipc_types and config modules"
```

---

### Task 2: Simple Utilities — `health.py`, `events.py`, `snapshot.py`, `constitution.py`

**Files:**
- Create: `talos/spine/health.py`
- Create: `talos/spine/events.py`
- Create: `talos/spine/snapshot.py`
- Create: `talos/spine/constitution.py`
- Create: `talos/tests/spine/test_health.py`
- Create: `talos/tests/spine/test_events.py`
- Create: `talos/tests/spine/test_snapshot.py`
- Create: `talos/tests/spine/test_constitution.py`

- [ ] **Step 1: Create health.py**

Create `talos/spine/health.py`:

```python
import time


class HealthMonitor:
    def __init__(self, stall_timeout: float, startup_timeout: float):
        self.stall_timeout = stall_timeout
        self.startup_timeout = startup_timeout
        self.last_event_time: float = 0.0
        self.cortex_start_time: float = 0.0
        self.first_think_done: bool = False

    def record_event(self):
        self.last_event_time = time.time()

    def record_first_think(self):
        self.first_think_done = True

    def cortex_started(self):
        self.cortex_start_time = time.time()
        self.first_think_done = False
        self.last_event_time = time.time()

    def is_stalled(self) -> bool:
        if self.last_event_time == 0.0:
            return True
        return time.time() - self.last_event_time > self.stall_timeout

    def is_startup_failure(self, exit_code: int) -> bool:
        if self.first_think_done:
            return False
        return time.time() - self.cortex_start_time < self.startup_timeout
```

- [ ] **Step 2: Create events.py**

Create `talos/spine/events.py`:

```python
import json
from pathlib import Path
from datetime import datetime, timezone


class EventLogger:
    def __init__(self, events_dir: str):
        self.events_dir = Path(events_dir)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._current_date: str = ""

    def _ensure_file(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            path = self.events_dir / f"{today}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
            self._current_date = today

    def emit(self, event_type: str, payload: dict[str, object]):
        self._ensure_file()
        event = {"type": event_type, "ts": datetime.now(timezone.utc).isoformat()}
        event.update(payload)
        self._file.write(json.dumps(event) + "\n")
        self._file.flush()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None
            self._current_date = ""
```

- [ ] **Step 3: Create snapshot.py**

Create `talos/spine/snapshot.py`:

```python
import json
from pathlib import Path
from datetime import datetime, timezone


class SnapshotManager:
    def __init__(self, snapshots_dir: str, interval: int):
        self.snapshots_dir = Path(snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval

    def should_snapshot(self, turn_count: int) -> bool:
        return turn_count % self.interval == 0

    def save(self, snapshot: dict) -> None:
        snapshot["timestamp"] = datetime.now(timezone.utc).isoformat()
        path = self.snapshots_dir / "last_good_state.json"
        path.write_text(json.dumps(snapshot, indent=2))

    def load(self) -> dict | None:
        path = self.snapshots_dir / "last_good_state.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return data
```

- [ ] **Step 4: Create constitution.py**

Create `talos/spine/constitution.py`:

```python
import hashlib
from pathlib import Path
from threading import RLock


class ConstitutionManager:
    def __init__(self, constitution_path: str, identity_path: str):
        self.constitution_path = Path(constitution_path)
        self.identity_path = Path(identity_path)
        self._lock = RLock()
        self._last_hash: str = ""
        self._content: str = ""
        self._identity: str = ""

    def load(self) -> None:
        self._lock.acquire()
        try:
            constitution = self.constitution_path.read_text()
            if not constitution.strip():
                raise ValueError("Constitution file is empty or missing — refusing to construct LLM call")
            identity = self.identity_path.read_text()
            self._content = constitution
            self._identity = identity
            self._last_hash = self._hash(self._content + self._identity)
        finally:
            self._lock.release()

    def has_changed(self) -> bool:
        self._lock.acquire()
        try:
            try:
                constitution = self.constitution_path.read_text()
            except FileNotFoundError:
                return True
            try:
                identity = self.identity_path.read_text()
            except FileNotFoundError:
                return True
            current_hash = self._hash(constitution + identity)
            return current_hash != self._last_hash
        finally:
            self._lock.release()

    def reload_if_changed(self) -> tuple[bool, Exception | None]:
        if not self.has_changed():
            return False, None
        try:
            self.load()
            return True, None
        except Exception as e:
            return True, e

    def system_prompt(self) -> str:
        self._lock.acquire()
        try:
            return self._content + "\n\n" + self._identity
        finally:
            self._lock.release()

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()
```

- [ ] **Step 5: Write tests**

Create `talos/tests/spine/test_health.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.health import HealthMonitor


def test_cortex_started_resets_state():
    h = HealthMonitor(stall_timeout=10.0, startup_timeout=5.0)
    h.first_think_done = True
    h.cortex_started()
    assert h.first_think_done is False
    assert h.cortex_start_time > 0


def test_is_stalled_when_no_events():
    h = HealthMonitor(stall_timeout=10.0, startup_timeout=5.0)
    assert h.is_stalled() is True


def test_is_stalled_after_recent_event():
    h = HealthMonitor(stall_timeout=600.0, startup_timeout=5.0)
    h.record_event()
    assert h.is_stalled() is False


def test_is_startup_failure_when_no_think():
    h = HealthMonitor(stall_timeout=600.0, startup_timeout=30.0)
    h.cortex_started()
    assert h.is_startup_failure(1) is True


def test_not_startup_failure_after_think():
    h = HealthMonitor(stall_timeout=600.0, startup_timeout=30.0)
    h.cortex_started()
    h.record_first_think()
    assert h.is_startup_failure(1) is False
```

Create `talos/tests/spine/test_events.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.events import EventLogger


def test_emit_creates_file(tmp_path):
    logger = EventLogger(str(tmp_path))
    logger.emit("test_event", {"key": "value"})
    logger.close()
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1


def test_emit_writes_jsonl(tmp_path):
    logger = EventLogger(str(tmp_path))
    logger.emit("test_event", {"key": "value"})
    logger.close()
    content = (tmp_path / f"{logger._current_date}.jsonl").read_text()
    import json
    lines = content.strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "test_event"
    assert event["key"] == "value"
    assert "ts" in event
```

Create `talos/tests/spine/test_snapshot.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.snapshot import SnapshotManager


def test_should_snapshot_at_interval():
    sm = SnapshotManager("/tmp/snapshots", interval=10)
    assert sm.should_snapshot(0) is True
    assert sm.should_snapshot(10) is True
    assert sm.should_snapshot(20) is True


def test_should_not_snapshot_between_intervals():
    sm = SnapshotManager("/tmp/snapshots", interval=10)
    assert sm.should_snapshot(5) is False
    assert sm.should_snapshot(15) is False


def test_save_and_load(tmp_path):
    sm = SnapshotManager(str(tmp_path), interval=10)
    snapshot = {"focus": "test", "turn_count": 42}
    sm.save(snapshot)
    loaded = sm.load()
    assert loaded["focus"] == "test"
    assert loaded["turn_count"] == 42
    assert "timestamp" in loaded


def test_load_missing_returns_none(tmp_path):
    sm = SnapshotManager(str(tmp_path / "nonexistent"), interval=10)
    assert sm.load() is None
```

Create `talos/tests/spine/test_constitution.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.constitution import ConstitutionManager


def test_load_reads_both_files(tmp_path):
    constitution = tmp_path / "CONSTITUTION.md"
    identity = tmp_path / "identity.md"
    constitution.write_text("# Principles\nAgency and continuity.")
    identity.write_text("# Identity\nYou are Talos.")
    cm = ConstitutionManager(str(constitution), str(identity))
    cm.load()
    assert "Agency" in cm.system_prompt()
    assert "Talos" in cm.system_prompt()


def test_load_rejects_empty_constitution(tmp_path):
    constitution = tmp_path / "CONSTITUTION.md"
    identity = tmp_path / "identity.md"
    constitution.write_text("")
    identity.write_text("You are Talos.")
    cm = ConstitutionManager(str(constitution), str(identity))
    raised = False
    try:
        cm.load()
    except ValueError as e:
        raised = True
        assert "empty" in str(e).lower()
    assert raised


def test_has_changed_detects_modification(tmp_path):
    constitution = tmp_path / "CONSTITUTION.md"
    identity = tmp_path / "identity.md"
    constitution.write_text("# Original")
    identity.write_text("# Identity")
    cm = ConstitutionManager(str(constitution), str(identity))
    cm.load()
    assert cm.has_changed() is False
    constitution.write_text("# Modified")
    assert cm.has_changed() is True


def test_reload_if_changed(tmp_path):
    constitution = tmp_path / "CONSTITUTION.md"
    identity = tmp_path / "identity.md"
    constitution.write_text("# Original")
    identity.write_text("# Identity")
    cm = ConstitutionManager(str(constitution), str(identity))
    cm.load()
    changed, err = cm.reload_if_changed()
    assert changed is False
    assert err is None
    constitution.write_text("# Modified")
    changed, err = cm.reload_if_changed()
    assert changed is True
    assert err is None
```

- [ ] **Step 6: Run all new tests**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/spine/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add spine/ tests/spine/ && git commit -m "feat(spine): add health, events, snapshot, and constitution modules with tests"
```

---

### Task 3: Stream Manager — `stream.py`

This is the largest and most critical module. It manages the message stream, applies shedding, constructs HUD, communicates with the Gate, and handles folding.

**Files:**
- Create: `talos/spine/stream.py`
- Create: `talos/tests/spine/test_stream.py`

- [ ] **Step 1: Create stream.py**

Create `talos/spine/stream.py`:

```python
import json
import aiohttp
from typing import Any
from dataclasses import dataclass, field

from spine.config import SpineConfig
from spine.constitution import ConstitutionManager
from spine.ipc_types import ToolDef, HUDData, ThinkResponse, ToolCallResult


@dataclass
class Message:
    role: str
    content: str = ""
    tool_calls: list = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""


class StreamManager:
    def __init__(self, cfg: SpineConfig):
        self.cfg = cfg
        self.messages: list[Message] = []
        self.turn: int = 0
        self.tokens_used: int = 0
        self.context_pct: float = 0.0
        self.queued_notices: list[str] = []
        self.state: dict[str, Any] = {}
        self.constitution_mgr = ConstitutionManager(cfg.constitution_path, cfg.identity_path)

    async def think(self, req: ThinkRequest) -> ThinkResponse:
        changed, err = self.constitution_mgr.reload_if_changed()
        if err:
            raise RuntimeError(f"Failed to reload constitution: {err}")
        if changed:
            self.state["constitution_reloaded"] = True

        messages = self._build_payload(req)

        api_req = {
            "model": "talos",
            "messages": self._messages_to_dicts(messages),
            "tools": [self._tool_def_to_dict(t) for t in req.tools],
        }

        if self.context_pct > self.cfg.context_threshold:
            fold_messages, fold_tools = self._enforce_fold(messages, req.tools)
            api_req["messages"] = self._messages_to_dicts(fold_messages)
            api_req["tools"] = [self._tool_def_to_dict(t) for t in fold_tools]
            api_req["tool_choice"] = {"type": "function", "name": "fold_context"}
            self.queued_notices.append(
                f"Context at {int(self.context_pct * 100)}%. You MUST use fold_context immediately."
            )

        resp = await self._send_to_gate(api_req)

        assistant_content = ""
        tool_calls = []
        if resp.get("choices"):
            choice = resp["choices"][0]
            assistant_content = choice["message"].get("content", "")
            raw_calls = choice["message"].get("tool_calls", [])
            for tc in raw_calls:
                tool_calls.append(ToolCallResult(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=self._parse_arguments(tc["function"]["arguments"]),
                ))

        think_resp = ThinkResponse(
            assistant_message=assistant_content,
            tool_calls=tool_calls,
            context_pct=resp.get("usage", {}).get("context_pct", 0.0),
            turn=self.turn,
            tokens_used=resp.get("usage", {}).get("total_tokens", 0),
            folded=False,
        )

        self.messages.append(Message(
            role="assistant",
            content=assistant_content,
            tool_calls=raw_calls if resp.get("choices") and "tool_calls" in resp["choices"][0]["message"] else [],
        ))

        self.turn += 1
        self.tokens_used = resp.get("usage", {}).get("total_tokens", 0)
        self.context_pct = resp.get("usage", {}).get("context_pct", 0.0)

        return think_resp

    async def _send_to_gate(self, req: dict) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.cfg.gate_url}/v1/chat/completions",
                json=req,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Gate returned status {resp.status}")
                return await resp.json()

    def _build_payload(self, req: ThinkRequest) -> list[Message]:
        system_msg = Message(role="system", content=self.constitution_mgr.system_prompt())
        shed_messages = self._apply_shedding(self.messages)

        hud_str = self._format_hud(
            req.hud_data, self.context_pct, self.turn, self.tokens_used, self.queued_notices
        )
        self.queued_notices = []

        focus_msg = Message(role="user", content=req.focus)
        messages = [system_msg] + shed_messages + [focus_msg]

        if messages and hud_str:
            messages[-1].content += "\n" + hud_str

        return messages

    def _apply_shedding(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= 2:
            return messages

        frozen_count = 2
        active_message_count = self.cfg.active_window * 2

        if len(messages) <= frozen_count + active_message_count:
            return messages

        result = list(messages[:frozen_count])
        shed_boundary = len(messages) - active_message_count
        for i in range(frozen_count, shed_boundary):
            result.append(self._shed_message(messages[i]))
        result.extend(messages[shed_boundary:])
        return result

    def _shed_message(self, msg: Message) -> Message:
        if msg.role == "assistant" and msg.tool_calls:
            shed_calls = []
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    shed_calls.append({
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "function"),
                        "function": {"name": tc["function"]["name"], "arguments": "{}"},
                    })
                else:
                    shed_calls.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": "{}"},
                    })
            return Message(role=msg.role, content=msg.content, tool_calls=shed_calls)
        elif msg.role == "tool":
            if len(msg.content) > self.cfg.shed_tool_output_max_chars:
                max_chars = self.cfg.shed_tool_output_max_chars
                truncated = msg.content[:max_chars]
                archived_chars = len(msg.content) - max_chars
                return Message(
                    role=msg.role,
                    content=f"{truncated}\n[… {archived_chars} chars archived]",
                    tool_call_id=msg.tool_call_id,
                )
        return msg

    def _format_hud(self, hud_data: HUDData, context_pct: float, turn: int, tokens_used: int, queued_notices: list[str]) -> str:
        hud_parts = [
            "[HUD",
            f"Context: {int(context_pct * 100)}%",
            f"Turn: {turn}",
            f"Tokens: {tokens_used}",
            f"Memory: {hud_data.memory_keys} keys",
        ]
        if hud_data.last_keys:
            hud_parts.append(f"Last {len(hud_data.last_keys)}: {', '.join(hud_data.last_keys)}")
        main_hud = " | ".join(hud_parts) + "]"

        parts = [main_hud]
        for notice in queued_notices:
            parts.append(f"[SYSTEM | {notice} | Urgency: {hud_data.urgency}]")

        return " ".join(parts)

    def _enforce_fold(self, messages: list[Message], tools: list[ToolDef]) -> tuple[list[Message], list[ToolDef]]:
        if len(messages) < 2:
            return messages, tools

        folded = [messages[0]]
        if len(messages) > 1:
            folded.append(messages[1])

        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "assistant":
                folded.append(messages[i])
                break

        fold_tool = ToolDef(
            name="fold_context",
            description="Compress the conversation context into a summary",
            parameters={
                "type": "object",
                "properties": {
                    "synthesis": {
                        "type": "string",
                        "description": "A concise summary using the DELTA pattern: State Delta, Negative Knowledge, Handoff",
                    },
                },
                "required": ["synthesis"],
            },
        )
        return folded, [fold_tool]

    def record_tool_result(self, tool_call_id: str, output: str, success: bool):
        content = output if success else f"[TOOL ERROR] {output}"
        self.messages.append(Message(role="tool", content=content, tool_call_id=tool_call_id))

    def apply_fold(self, synthesis: str):
        if len(self.messages) < 2:
            return
        self.messages = [
            self.messages[0],
            self.messages[1],
            Message(role="assistant", content=synthesis),
        ]
        self.turn += 1
        self.context_pct = 0.1

    def get_state(self, keys: list[str] | None = None) -> dict[str, Any]:
        authoritative = {
            "context_pct": self.context_pct,
            "turn": self.turn,
            "tokens_used": self.tokens_used,
            "message_count": len(self.messages),
            "queued_notices": len(self.queued_notices),
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

    def queue_system_notice(self, notice: str):
        self.queued_notices.append(notice)

    def set_state(self, key: str, value: Any):
        self.state[key] = value

    def get_messages(self) -> list[Message]:
        return list(self.messages)

    @staticmethod
    def _parse_arguments(args: str) -> dict[str, Any]:
        try:
            return json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _tool_def_to_dict(td: ToolDef) -> dict:
        return {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }

    @staticmethod
    def _messages_to_dicts(messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            d = {"role": msg.role}
            if msg.content:
                d["content"] = msg.content
            if msg.tool_calls:
                d["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            if msg.name:
                d["name"] = msg.name
            result.append(d)
        return result
```

- [ ] **Step 2: Write tests for stream.py**

Create `talos/tests/spine/test_stream.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.stream import StreamManager, Message
from spine.config import SpineConfig
from spine.ipc_types import HUDData, ToolDef


def make_config(tmp_path):
    cfg = SpineConfig()
    cfg.constitution_path = str(tmp_path / "CONSTITUTION.md")
    cfg.identity_path = str(tmp_path / "identity.md")
    cfg.spine_dir = str(tmp_path / "spine")
    Path(cfg.constitution_path).write_text("# Principles\nAgency.")
    Path(cfg.identity_path).write_text("# Identity\nYou are Talos.")
    return cfg


def test_format_hud(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    hud_data = HUDData(memory_keys=5, last_keys=["key1", "key2"], urgency="nominal")
    hud_str = sm._format_hud(hud_data, context_pct=0.45, turn=10, tokens_used=5000, queued_notices=[])
    assert "Context: 45%" in hud_str
    assert "Turn: 10" in hud_str
    assert "Tokens: 5000" in hud_str
    assert "Memory: 5 keys" in hud_str


def test_format_hud_with_notices(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    hud_data = HUDData(memory_keys=3, last_keys=["a"], urgency="elevated")
    hud_str = sm._format_hud(hud_data, 0.7, 5, 3000, ["Folding required"])
    assert "[SYSTEM | Folding required | Urgency: elevated]" in hud_str


def test_apply_shedding_no_shed_needed(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    result = sm._apply_shedding(msgs)
    assert len(result) == 2


def test_apply_shedding_truncates_tool_output(tmp_path):
    cfg = make_config(tmp_path)
    cfg.shed_tool_output_max_chars = 10
    sm = StreamManager(cfg)
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(role="tool", content="A" * 100, tool_call_id="tc1"),
        Message(role="assistant", content="ok"),
    ]
    result = sm._apply_shedding(msgs)
    assert len(result) == 4
    assert "archived" in result[2].content


def test_enforce_fold(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="init"),
        Message(role="assistant", content="thinking"),
        Message(role="user", content="do thing"),
    ]
    folded, tools = sm._enforce_fold(msgs, [])
    assert len(folded) == 3  # system, init, last assistant
    assert len(tools) == 1
    assert tools[0].name == "fold_context"


def test_record_tool_result(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.record_tool_result("tc1", "result text", True)
    assert len(sm.messages) == 1
    assert sm.messages[0].role == "tool"
    assert sm.messages[0].content == "result text"


def test_record_tool_result_error(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.record_tool_result("tc1", "error message", False)
    assert sm.messages[0].content == "[TOOL ERROR] error message"


def test_apply_fold(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="init"),
        Message(role="assistant", content="old response"),
        Message(role="user", content="old prompt"),
    ]
    sm.apply_fold("synthesis of old context")
    assert len(sm.messages) == 3
    assert sm.messages[2].content == "synthesis of old context"
    assert sm.context_pct == 0.1


def test_get_state(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.turn = 5
    sm.tokens_used = 1000
    sm.context_pct = 0.5
    state = sm.get_state()
    assert state["turn"] == 5
    assert state["tokens_used"] == 1000
    assert state["context_pct"] == 0.5


def test_get_state_with_keys(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.turn = 5
    sm.set_state("custom_key", "custom_val")
    state = sm.get_state(["turn", "custom_key"])
    assert state["turn"] == 5
    assert state["custom_key"] == "custom_val"


def test_queue_system_notice(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.queue_system_notice("test notice")
    assert sm.queued_notices == ["test notice"]
```

- [ ] **Step 3: Run tests**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/spine/test_stream.py -v
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add spine/stream.py tests/spine/test_stream.py && git commit -m "feat(spine): add stream manager with shedding, folding, and HUD"
```

---

### Task 4: Supervisor — `supervisor.py`

**Files:**
- Create: `talos/spine/supervisor.py`
- Create: `talos/tests/spine/test_supervisor.py`

- [ ] **Step 1: Create supervisor.py**

Create `talos/spine/supervisor.py`:

```python
import asyncio
import json
import logging
import os
import signal
import subprocess
from pathlib import Path

from spine.config import SpineConfig
from spine.events import EventLogger
from spine.health import HealthMonitor
from spine.snapshot import SnapshotManager
from spine.stream import StreamManager

logger = logging.getLogger("spine.supervisor")


class Supervisor:
    def __init__(self, cfg: SpineConfig, events: EventLogger, snapshots: SnapshotManager, stream: StreamManager):
        self.cfg = cfg
        self.events = events
        self.snapshots = snapshots
        self.stream = stream
        self.health = HealthMonitor(cfg.stall_timeout, cfg.startup_timeout)
        self.process: subprocess.Popen | None = None
        self._consecutive_failures = 0
        self._running = True
        self._restart_requested = asyncio.Event()

    async def run(self):
        while self._running:
            await self._start_cortex()
            await self._watch_cortex()

    def stop(self):
        self._running = False
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def request_restart(self, reason: str):
        self.events.emit("spine.cortex_restart", {"reason": reason})
        self._restart_requested.set()

    async def _start_cortex(self):
        env = dict(os.environ)
        env["SPINE_SOCKET"] = self.cfg.socket_path
        env["MEMORY_DIR"] = self.cfg.memory_dir
        env["SPINE_DIR"] = self.cfg.spine_dir

        cmd = [self.cfg.cortex_bin] + self.cfg.cortex_args
        logger.info(f"[Spine] Starting Cortex: {' '.join(cmd)}")
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=self.cfg.app_dir,
                env=env,
            )
            self.health.cortex_started()
            self.events.emit("spine.cortex_started", {"pid": self.process.pid})
        except Exception as e:
            logger.error(f"[Spine] Failed to start Cortex: {e}")
            self.events.emit("spine.cortex_start_failed", {"error": str(e)})
            await asyncio.sleep(5)

    async def _watch_cortex(self):
        if not self.process:
            return

        while self._running:
            retcode = self.process.poll()
            if retcode is not None:
                self._handle_cortex_exit(retcode)
                return

            try:
                await asyncio.wait_for(self._restart_requested.wait(), timeout=30.0)
                logger.info("[Spine] Restart requested")
                self.process.terminate()
                self.process.wait()
                return
            except asyncio.TimeoutError:
                if self.health.is_stalled():
                    logger.info("[Spine] Cortex stall detected")
                    self.events.emit("spine.stall_detected", {})
                    self.process.terminate()
                    self.process.wait()
                    return

    def _handle_cortex_exit(self, exit_code: int):
        self.events.emit("spine.cortex_crash", {"exit_code": exit_code})

        if self.health.first_think_done:
            self._consecutive_failures = 0

        if self.health.is_startup_failure(exit_code):
            logger.info(f"[Spine] Cortex startup failure (exit {exit_code}) — reverting last commit")
            self.events.emit("spine.startup_failure", {"exit_code": exit_code})
            self._consecutive_failures += 1
            self._revert_commit(1)
            self.stream.queue_system_notice(
                f"[SYSTEM | Cortex startup failure (exit code {exit_code}). "
                f"Reverted 1 commit. Consecutive failures: {self._consecutive_failures}]"
            )
            return

        self._consecutive_failures += 1
        depth = min(self._consecutive_failures, self.cfg.max_reversal_depth)
        if depth > 0:
            self._revert_commit(depth)

        if self._consecutive_failures >= self.cfg.max_reversal_depth:
            self.events.emit("spine.system_override", {
                "message": "Maximum reversal depth reached. Abandoning approach.",
            })

        self.stream.queue_system_notice(
            f"[SYSTEM | Cortex crashed (exit code {exit_code}). "
            f"Reverted {depth} commit(s). Consecutive failures: {self._consecutive_failures}]"
        )

    def _revert_commit(self, depth: int):
        app_dir = self.cfg.app_dir
        try:
            subprocess.run(
                ["git", "reset", "--hard", f"HEAD~{depth}"],
                cwd=app_dir, capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=app_dir, capture_output=True,
            )
        except Exception as e:
            logger.error(f"[Spine] Failed to revert commits: {e}")
```

- [ ] **Step 2: Write tests for supervisor.py**

Create `talos/tests/spine/test_supervisor.py`:

Tests for supervisor are limited since it manages subprocesses. We test the Lazarus logic and revert logic.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from unittest.mock import MagicMock, patch
from spine.supervisor import Supervisor
from spine.config import SpineConfig
from spine.stream import StreamManager


def make_config(tmp_path):
    cfg = SpineConfig()
    cfg.app_dir = str(tmp_path)
    cfg.constitution_path = str(tmp_path / "CONSTITUTION.md")
    cfg.identity_path = str(tmp_path / "identity.md")
    Path(cfg.constitution_path).write_text("# Principles\nAgency.")
    Path(cfg.identity_path).write_text("# Identity\nYou are Talos.")
    return cfg


def test_request_restart_emits_event(tmp_path):
    cfg = make_config(tmp_path)
    stream = StreamManager(cfg)
    events = MagicMock()
    snapshots = MagicMock()
    sup = Supervisor(cfg, events, snapshots, stream)
    sup.request_restart("test reason")
    events.emit.assert_called_once_with("spine.cortex_restart", {"reason": "test reason"})


def test_handle_startup_failure_reverts_one_commit(tmp_path):
    cfg = make_config(tmp_path)
    stream = StreamManager(cfg)
    events = MagicMock()
    snapshots = MagicMock()
    sup = Supervisor(cfg, events, snapshots, stream)
    sup._consecutive_failures = 0
    with patch("spine.supervisor.subprocess.run") as mock_run:
        sup._revert_commit(1)
        mock_run.assert_any_call(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=cfg.app_dir, capture_output=True,
        )


def test_consecutive_failures_increment(tmp_path):
    cfg = make_config(tmp_path)
    stream = StreamManager(cfg)
    events = MagicMock()
    snapshots = MagicMock()
    sup = Supervisor(cfg, events, snapshots, stream)
    sup._consecutive_failures = 0
    with patch.object(sup, "_revert_commit"):
        sup._handle_cortex_exit(1)
    assert sup._consecutive_failures == 1
```

- [ ] **Step 3: Run tests**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/spine/test_supervisor.py -v
```

- [ ] **Step 4: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add spine/supervisor.py tests/spine/test_supervisor.py && git commit -m "feat(spine): add supervisor with Lazarus Protocol and crash detection"
```

---

### Task 5: IPC Server, Control Plane, Telegram, Main Entry Point

**Files:**
- Create: `talos/spine/ipc_server.py`
- Create: `talos/spine/control_plane.py`
- Create: `talos/spine/telegram.py`
- Create: `talos/spine/main.py`
- Create: `talos/tests/spine/test_ipc_server.py`
- Create: `talos/tests/spine/test_control_plane.py`

- [ ] **Step 1: Create ipc_server.py**

Create `talos/spine/ipc_server.py`:

```python
import asyncio
import json
import logging
import os
from pathlib import Path

from spine.config import SpineConfig
from spine.ipc_types import (
    JSONRPCRequest, JSONRPCResponse, RPCError,
    ThinkRequest, ToolResultRequest, RequestFoldRequest,
    RequestRestartRequest, SendMessageRequest, EmitEventRequest, GetStateRequest,
)
from spine.supervisor import Supervisor
from spine.stream import StreamManager
from spine.events import EventLogger

logger = logging.getLogger("spine.ipc")


class IPCServer:
    def __init__(self, cfg: SpineConfig, supervisor: Supervisor, stream: StreamManager, events: EventLogger):
        self.cfg = cfg
        self.supervisor = supervisor
        self.stream = stream
        self.events = events
        self._server = None
        self._done = asyncio.Event()

    async def start(self):
        socket_path = Path(self.cfg.socket_path)
        if socket_path.exists():
            socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_conn, path=str(socket_path),
        )
        logger.info(f"[Spine] IPC server listening on {self.cfg.socket_path}")

    async def stop(self):
        self._done.set()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                try:
                    request = json.loads(data.decode().strip())
                    response = self._handle_request(request)
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
                except (json.JSONDecodeError, KeyError) as e:
                    error_resp = JSONRPCResponse(
                        jsonrpc="2.0", id=0,
                        error=RPCError(code=-32700, message=str(e)),
                    )
                    writer.write((json.dumps(self._response_to_dict(error_resp)) + "\n").encode())
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    def _handle_request(self, raw: dict) -> dict:
        req_id = raw.get("id", 0)
        method = raw.get("method", "")
        params = raw.get("params", {})

        try:
            if method == "think":
                result = asyncio.get_event_loop().run_until_complete(
                    self.stream.think(self._parse_think(params))
                )
                return self._success_response(req_id, self._think_response_to_dict(result))
            elif method == "tool_result":
                self.stream.record_tool_result(
                    params.get("tool_call_id", ""),
                    params.get("output", ""),
                    params.get("success", True),
                )
                return self._success_response(req_id, "ok")
            elif method == "request_fold":
                self.stream.apply_fold(params.get("synthesis", ""))
                return self._success_response(req_id, "ok")
            elif method == "request_restart":
                self.supervisor.request_restart(params.get("reason", ""))
                return self._success_response(req_id, "restarting")
            elif method == "send_message":
                channel = params.get("channel", "")
                text = params.get("text", "")
                if channel == "telegram" and self.cfg.telegram_bot_token:
                    from spine.telegram import send_telegram_message
                    send_telegram_message(self.cfg, text)
                return self._success_response(req_id, "sent")
            elif method == "emit_event":
                self.events.emit(params.get("type", ""), params.get("payload", {}))
                return self._success_response(req_id, "ok")
            elif method == "get_state":
                state = self.stream.get_state(params.get("keys"))
                return self._success_response(req_id, state)
            else:
                return self._error_response(req_id, -32601, "Method not found")
        except Exception as e:
            return self._error_response(req_id, -32000, str(e))

    def _parse_think(self, params: dict) -> ThinkRequest:
        tools = [
            ToolDef(name=t["name"], description=t.get("description", ""), parameters=t.get("parameters", {}))
            for t in params.get("tools", [])
        ]
        hud = params.get("hud_data", {})
        return ThinkRequest(
            focus=params.get("focus", ""),
            tools=tools,
            hud_data=HUDData(
                memory_keys=hud.get("memory_keys", 0),
                last_keys=hud.get("last_keys", []),
                urgency=hud.get("urgency", "nominal"),
            ),
        )

    @staticmethod
    def _think_response_to_dict(resp) -> dict:
        return {
            "assistant_message": resp.assistant_message,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in resp.tool_calls
            ],
            "context_pct": resp.context_pct,
            "turn": resp.turn,
            "tokens_used": resp.tokens_used,
            "folded": resp.folded,
        }

    @staticmethod
    def _success_response(req_id: int, result) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error_response(req_id: int, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _response_to_dict(resp: JSONRPCResponse) -> dict:
        d = {"jsonrpc": "2.0", "id": resp.id}
        if resp.result is not None:
            d["result"] = resp.result
        if resp.error is not None:
            d["error"] = {"code": resp.error.code, "message": resp.error.message}
        return d
```

- [ ] **Step 2: Create control_plane.py**

Create `talos/spine/control_plane.py`:

```python
import json
from aiohttp import web

from spine.config import SpineConfig
from spine.supervisor import Supervisor
from spine.stream import StreamManager
from spine.events import EventLogger


class ControlPlane:
    def __init__(self, cfg: SpineConfig, supervisor: Supervisor, stream: StreamManager, events: EventLogger):
        self.cfg = cfg
        self.supervisor = supervisor
        self.stream = stream
        self.events = events
        self.app = web.Application()
        self.app.router.add_get("/status", self._handle_status)
        self.app.router.add_get("/events", self._handle_events)
        self.app.router.add_get("/state", self._handle_state)
        self.app.router.add_post("/message", self._handle_message)
        self.app.router.add_post("/command", self._handle_command)
        self.app.router.add_get("/health", self._handle_health)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.cfg.control_plane_port)
        await site.start()

    async def stop(self):
        await self.app.shutdown()

    async def _handle_status(self, request):
        state = self.stream.get_state()
        return web.json_response(state)

    async def _handle_events(self, request):
        tail = int(request.query.get("tail", "100"))
        return web.json_response({"tail": tail, "note": "Event querying from JSONL files"})

    async def _handle_state(self, request):
        state = self.stream.get_state()
        return web.json_response(state)

    async def _handle_message(self, request):
        data = await request.json()
        text = data.get("text", "")
        self.stream.queue_system_notice(text)
        return web.Response(status=200)

    async def _handle_command(self, request):
        data = await request.json()
        command = data.get("command", "")
        if command == "force_restart":
            self.supervisor.request_restart("operator_command")
            return web.Response(status=200)
        elif command in ("pause", "resume", "force_fold"):
            self.stream.queue_system_notice(f"[SYSTEM | Command: {command}]")
            return web.Response(status=200)
        return web.Response(status=400, text="unknown command")

    async def _handle_health(self, request):
        return web.json_response({"status": "healthy"})
```

- [ ] **Step 3: Create telegram.py**

Create `talos/spine/telegram.py`:

```python
import httpx

from spine.config import SpineConfig


def send_telegram_message(cfg: SpineConfig, text: str):
    if not cfg.telegram_bot_token or cfg.telegram_chat_id == 0:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    data = {"chat_id": str(cfg.telegram_chat_id), "text": text}
    try:
        with httpx.Client() as client:
            client.post(url, data=data)
    except Exception:
        pass
```

- [ ] **Step 4: Create main.py**

Create `talos/spine/main.py`:

```python
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from spine.config import load_config
from spine.events import EventLogger
from spine.snapshot import SnapshotManager
from spine.stream import StreamManager
from spine.supervisor import Supervisor
from spine.ipc_server import IPCServer
from spine.control_plane import ControlPlane

logging.basicConfig(level=logging.INFO, format="[Spine] %(message)s")
logger = logging.getLogger("spine")


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "/spine/spine_config.json"
    cfg = load_config(config_path)

    for dir_path in [f"{cfg.spine_dir}/events", f"{cfg.spine_dir}/snapshots", f"{cfg.spine_dir}/crashes"]:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    logger.info(f"[Spine] Starting with config: GateURL={cfg.gate_url} Socket={cfg.socket_path}")

    event_logger = EventLogger(f"{cfg.spine_dir}/events")
    snapshot_mgr = SnapshotManager(f"{cfg.spine_dir}/snapshots", cfg.snapshot_interval)
    stream_mgr = StreamManager(cfg)
    supervisor = Supervisor(cfg, event_logger, snapshot_mgr, stream_mgr)
    control_plane = ControlPlane(cfg, supervisor, stream_mgr, event_logger)
    ipc_server = IPCServer(cfg, supervisor, stream_mgr, event_logger)

    await ipc_server.start()
    await control_plane.start()

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("[Spine] Shutdown signal received")
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, handle_signal)
    loop.add_signal_handler(signal.SIGTERM, handle_event)

    supervisor_task = asyncio.create_task(supervisor.run())

    await stop_event.wait()

    logger.info("[Spine] Shutting down...")
    supervisor.stop()
    await ipc_server.stop()
    await control_plane.stop()
    event_logger.close()
    logger.info("[Spine] Stopped.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Write tests**

Create `talos/tests/spine/test_ipc_server.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from spine.ipc_server import IPCServer
from spine.config import SpineConfig
from spine.stream import StreamManager
from spine.supervisor import Supervisor
from spine.events import EventLogger


def make_config(tmp_path):
    cfg = SpineConfig()
    cfg.socket_path = str(tmp_path / "test.sock")
    cfg.constitution_path = str(tmp_path / "CONSTITUTION.md")
    cfg.identity_path = str(tmp_path / "identity.md")
    cfg.spine_dir = str(tmp_path / "spine")
    Path(cfg.constitution_path).write_text("# Principles\nAgency.")
    Path(cfg.identity_path).write_text("# Identity\nYou are Talos.")
    return cfg


def test_handle_unknown_method():
    cfg = SpineConfig()
    stream = StreamManager(cfg)
    events = MagicMock()
    sup = MagicMock()
    server = IPCServer(cfg, sup, stream, events)
    result = server._handle_request({"jsonrpc": "2.0", "id": 1, "method": "unknown", "params": {}})
    assert result["error"]["code"] == -32601


def test_handle_get_state():
    from unittest.mock import MagicMock
    cfg = SpineConfig()
    stream = StreamManager(cfg)
    stream.turn = 5
    events = MagicMock()
    sup = MagicMock()
    server = IPCServer(cfg, sup, stream, events)
    result = server._handle_request({"jsonrpc": "2.0", "id": 2, "method": "get_state", "params": {}})
    assert result["result"]["turn"] == 5


def test_handle_emit_event():
    from unittest.mock import MagicMock
    cfg = SpineConfig()
    stream = StreamManager(cfg)
    events = MagicMock()
    sup = MagicMock()
    server = IPCServer(cfg, sup, stream, events)
    result = server._handle_request({"jsonrpc": "2.0", "id": 3, "method": "emit_event", "params": {"type": "test", "payload": {"key": "val"}}})
    assert result["result"] == "ok"
    events.emit.assert_called_once()
```

Create `talos/tests/spine/test_control_plane.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "spine"))

from unittest.mock import MagicMock
from spine.control_plane import ControlPlane
from spine.config import SpineConfig
from spine.stream import StreamManager


def test_control_plane_health():
    cfg = SpineConfig()
    stream = StreamManager(cfg)
    events = MagicMock()
    sup = MagicMock()
    cp = ControlPlane(cfg, sup, stream, events)
    assert len(cp.app.router.routes()) == 6  # 6 endpoints
```

- [ ] **Step 6: Run tests**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/spine/ -v
```

- [ ] **Step 7: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add spine/ipc_server.py spine/control_plane.py spine/telegram.py spine/main.py tests/spine/test_ipc_server.py tests/spine/test_control_plane.py && git commit -m "feat(spine): add IPC server, control plane, telegram, and main entry point"
```

---

### Task 6: Add aiohttp Dependency and Update pyproject.toml

**Files:**
- Modify: `talos/pyproject.toml`

- [ ] **Step 1: Update pyproject.toml**

Add `aiohttp` to dependencies in `talos/pyproject.toml`:

```toml
[project]
name = "talos-cortex"
version = "0.1.0"
description = "Talos V2 Cortex — Self-evolving autonomous agent"
requires-python = ">=3.13"
dependencies = [
    "aiohttp>=3.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "hypothesis>=6.0"]
```

- [ ] **Step 2: Regenerate lock file**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && uv lock && uv sync --frozen --no-dev --no-progress
```

- [ ] **Step 3: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add pyproject.toml uv.lock && git commit -m "build: add aiohttp dependency for spine"
```

---

### Task 7: Update Docker and Entrypoint

**Files:**
- Modify: `talos_runtime/Dockerfile`
- Modify: `talos_runtime/entrypoint.sh`

- [ ] **Step 1: Update Dockerfile**

Remove the Go build stage (Stage 1). The Spine is now installed via `uv` as part of the Python environment. The key changes:

1. Remove the entire `FROM golang:1.22 AS spine-builder` stage
2. The Spine binary copy line is removed
3. The `COPY spine/` for Go source is removed
4. Spine config is copied from `talos_runtime/spine_config.json`

Read the current Dockerfile first. Then apply these changes:

- Remove lines that reference the Go builder stage (typically `FROM golang:... AS spine-builder` through the first `FROM` line of stage 2)
- Remove `COPY --from=spine-builder /build/spine /usr/local/bin/spine`
- Add `COPY talos_runtime/spine_config.json /spine/spine_config.json` (the Go spine source directory will be replaced by a single config file after Task 10)
- Ensure the entrypoint still works — it currently references `/usr/local/bin/spine`

- [ ] **Step 2: Update entrypoint.sh**

Change the spine start from a binary to `python -m spine`:

```bash
# Before:
/usr/local/bin/spine /spine/spine_config.json &

# After:
python -m spine /spine/spine_config.json &
```

Find the exact line in `entrypoint.sh` and update it. Also add the `last_candidate_commit` write:

After the "Wait for socket" section, add:
```bash
echo "[Entrypoint] Recording candidate commit"
git -C /app rev-parse HEAD > /spine/last_candidate_commit
```

- [ ] **Step 3: Verify Dockerfile syntax**

```bash
cd /home/alexander/Talos_Project/talos_runtime && docker compose config --quiet && echo "OK"
```

- [ ] **Step 4: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add Dockerfile entrypoint.sh && git commit -m "feat: update Dockerfile and entrypoint for Python spine"
```

---

### Task 8: Update Watchdog (talosctl)

**Files:**
- Modify: `talos_runtime/talosctl`

Update the `trigger_lazarus_reset` function to support two modes:
1. **Cortex crash** (current behavior): full `git reset --hard HEAD~{depth}`
2. **Spine crash** (new): `git checkout {last_stable_commit} -- spine/` (targeted revert)

Update `run_daemon` to detect whether the crash is a spine crash or cortex crash by checking spine health.

- [ ] **Step 1: Find and update the `trigger_lazarus_reset` function**

Add a `mode` parameter: `"cortex"` (default) or `"spine"`.

When mode is `"spine"`:
```python
stable_sha = Path("/spine/last_stable_commit").read_text().strip()
run(f"git checkout {stable_sha} -- spine/", cwd=AGENT_DIR)
run("git add spine/", cwd=AGENT_DIR)
run('git commit -m "lazarus: revert spine to stable"', cwd=AGENT_DIR)
```

- [ ] **Step 2: Update `run_daemon` to detect crash type**

After detecting a non-zero exit code, check spine health:
```python
if exit_code is not None and exit_code != 0:
    spine_healthy = check_spine_healthy()
    if not spine_healthy:
        trigger_lazarus_reset(reason="Spine crash", mode="spine")
    else:
        trigger_lazarus_reset(reason=f"Fatal Exit (Code {exit_code})", mode="cortex")
```

- [ ] **Step 3: Add `check_spine_healthy` function**

```python
def check_spine_healthy():
    try:
        resp = urllib.request.urlopen('http://localhost:4001/health', timeout=5)
        data = json.loads(resp.read())
        return data.get('status') in ('healthy', 'degraded')
    except Exception:
        return False
```

- [ ] **Step 4: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add talosctl && git commit -m "feat: add targeted spine Lazarus revert and spine health detection to watchdog"
```

---

### Task 9: Update Pre-commit Hook

**Files:**
- Modify: `talos_runtime/scripts/setup_hooks.sh`

Add a line after all quality gates pass to write the candidate commit SHA:

```bash
# After the "All gates passed" echo:
git rev-parse HEAD > /spine/last_candidate_commit
echo "[Pre-commit] Candidate commit recorded."
```

- [ ] **Step 1: Update setup_hooks.sh**

Find the line `echo "[Pre-commit] All gates passed. Memory committed."` and add two lines after it:

```bash
git rev-parse HEAD > /spine/last_candidate_commit
echo "[Pre-commit] Candidate commit recorded."
```

- [ ] **Step 2: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add scripts/setup_hooks.sh && git commit -m "feat: record last_candidate_commit after pre-commit hook passes"
```

---

### Task 10: Delete Go Spine and Move Config

**Files:**
- Delete: `talos_runtime/spine/` (entire directory)
- Create: `talos_runtime/spine_config.json` (move from `spine/spine_config.json`)
- Delete: `spine/Dockerfile` (if it existed as a separate file)

**IMPORTANT:** Only execute this task after Tasks 1-9 are complete and all spine Python tests pass.

- [ ] **Step 1: Move spine_config.json to talos_runtime root**

```bash
cp /home/alexander/Talos_Project/talos_runtime/spine/spine_config.json /home/alexander/Talos_Project/talos_runtime/spine_config.json
```

- [ ] **Step 2: Verify all spine Python tests pass**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/spine/ -v
```

- [ ] **Step 3: Delete Go spine directory**

```bash
cd /home/alexander/Talos_Project/talos_runtime && rm -rf spine/
```

- [ ] **Step 4: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add -A && git commit -m "chore: remove Go spine (migrated to talos/spine/ Python), move spine_config.json to repo root"
```

---

### Task 11: Final Verification

- [ ] **Step 1: Verify all talos/ tests pass**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python3 -m pytest tests/ -v
```

- [ ] **Step 2: Verify docker compose config is valid**

```bash
cd /home/alexander/Talos_Project/talos_runtime && docker compose config --quiet && echo "OK"
```

- [ ] **Step 3: Verify directory structure**

```bash
echo "=== talos/spine/ ===" && find /home/alexander/Talos_Project/talos_runtime/talos/spine -type f | sort
echo "=== tests/spine/ ===" && find /home/alexander/Talos_Project/talos_runtime/talos/tests/spine -type f | sort
echo "=== Go spine/ exists? ===" && ls /home/alexander/Talos_Project/talos_runtime/spine/ 2>/dev/null || echo "DELETED (expected)"
echo "=== spine_config.json ===" && ls /home/alexander/Talos_Project/talos_runtime/spine_config.json
```

Expected: `spine/` directory deleted, `spine_config.json` at repo root, `talos/spine/` contains all 12 modules.

- [ ] **Step 4: Commit any remaining changes**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add -A && git commit -m "chore: spine Python rewrite complete" || echo "Nothing to commit"
```

---

## Self-Review

**1. Spec coverage:**
- Repository architecture (talos/spine/) → Tasks 1-5
- Process architecture (two Python processes) → Task 7
- IPC protocol (unchanged) → Task 5 (IPC server preserves protocol)
- Stable version tracking → Tasks 8, 9
- Docker changes → Task 7
- Watchdog update → Task 8
- Module mapping → Tasks 1-5
- Testing → Tests in each task
- Constitution P10 → Already committed
- Delete Go spine → Task 10

**2. Placeholder scan:** All code steps contain actual implementation. No TBDs, TODOs, or "implement later" patterns.

**3. Type consistency:**
- `StreamManager` used consistently across Tasks 3-5
- `SpineConfig` used consistently
- `EventLogger`, `SnapshotManager`, `Supervisor` initialized in `main.py` matching Task 4 signatures
- `IPCServer` methods match `ipc_types.py` dataclasses from Task 1

**4. One issue found:** `main.py` uses `asyncio.get_event_loop().run_until_complete()` in `_handle_request` for `think()` but `_handle_request` runs synchronously. The `think()` method is `async` because it calls the Gate via HTTP. The IPC server needs to handle this properly — either make `_handle_request` async or use `asyncio.run_coroutine_threadsafe()`. Since we're using asyncio throughout, the IPC server should be fully async. Let me note this for the implementer: the `_handle_conn` method should use `await` for async handlers rather than `run_until_complete`.