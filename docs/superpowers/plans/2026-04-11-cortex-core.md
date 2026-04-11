# Cortex Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Cortex — a Python agent that communicates with the Spine via IPC, manages its own tools, memory, and state, and can self-modify.

**Architecture:** The Cortex is a Python process managed by the Spine. It runs the ReAct loop (observe → think via `spine.think()` → act via tools → observe), owns the tool registry, reads/writes `/memory/` directly, and sends all LLM calls through the Spine. It communicates via Unix domain socket using JSON-RPC.

**Tech Stack:** Python 3.13, `unixsocket` or standard library `socket` for IPC, `json` for JSON-RPC, existing tool patterns from Ouroboros adapted for the Spine protocol.

**Depends on:** Spine Core Plan (must be completed first for integration testing, but Cortex code can be developed independently).

---

## File Structure

```
cortex/
  seed_agent.py           # Main ReAct loop entry point
  spine_client.py         # IPC client for communicating with Spine
  spine_client_test.py    # Tests for Spine client
  tool_registry.py        # Tool decorator and registry
  tool_registry_test.py   # Registry tests
  tools/
    __init__.py
    executive.py           # set_focus, resolve_focus, fold_context, reflect
    code_surgery.py        # generate_repo_map, replace_symbol, write_file, read_file, patch_file
    memory.py              # store_fact, recall_fact, list_memory_keys, search_memory
    physical.py            # bash_command, send_message (via Spine), request_restart
    git_operations.py      # git_commit, git_push, git_diff
  state.py                # Focus, task queue, cognitive params
  state_test.py           # State management tests
  memory_store.py          # KV store operations on /memory/
  memory_store_test.py     # Memory store tests
  hud_builder.py           # Builds hud_data for each think() call
  hud_builder_test.py     # HUD builder tests
  requirements.txt        # Python dependencies
```

---

### Task 1: Spine IPC Client

**Files:**
- Create: `cortex/spine_client.py`
- Create: `cortex/spine_client_test.py`

The IPC client is the Cortex's connection to the Spine. It wraps the JSON-RPC protocol into a clean Python API.

- [ ] **Step 1: Write spine_client.py**

Create `cortex/spine_client.py`:

```python
"""
Spine IPC Client — Unix domain socket JSON-RPC client for Cortex ↔ Spine communication.
"""
import json
import socket
from typing import Any, Optional


class SpineClient:
    """Client for communicating with the Spine via Unix domain socket."""

    def __init__(self, socket_path: str = "/tmp/spine.sock"):
        self.socket_path = socket_path
        self._request_id = 0

    def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and return the response."""
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        try:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response_data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                response_data += chunk
                if b"\n" in response_data:
                    break
            response = json.loads(response_data.decode("utf-8").strip())
        finally:
            sock.close()

        if "error" in response:
            raise SpineError(response["error"]["code"], response["error"]["message"])
        return response.get("result", {})

    def think(self, focus: str, tools: list[dict], hud_data: dict) -> dict:
        """Call the LLM with current stream and tool definitions.

        Args:
            focus: Current task focus string.
            tools: List of tool definitions in OpenAI JSON Schema format.
            hud_data: Dict with memory_keys, last_keys, urgency for HUD.

        Returns:
            ThinkResponse with assistant_message, tool_calls, context_pct, turn, etc.
        """
        return self._send_request("think", {
            "focus": focus,
            "tools": tools,
            "hud_data": hud_data,
        })

    def tool_result(self, tool_call_id: str, output: str, success: bool) -> dict:
        """Return tool execution result to the Spine."""
        return self._send_request("tool_result", {
            "tool_call_id": tool_call_id,
            "output": output,
            "success": success,
        })

    def request_fold(self, synthesis: str) -> dict:
        """Request a context fold with a synthesis."""
        return self._send_request("request_fold", {"synthesis": synthesis})

    def request_restart(self, reason: str) -> dict:
        """Request a clean restart of the Cortex process."""
        return self._send_request("request_restart", {"reason": reason})

    def send_message(self, channel: str, text: str) -> dict:
        """Send a message to the creator via Spine-owned channels."""
        return self._send_request("send_message", {"channel": channel, "text": text})

    def emit_event(self, event_type: str, payload: dict) -> dict:
        """Log a custom event."""
        return self._send_request("emit_event", {"type": event_type, "payload": payload})

    def get_state(self, keys: list[str]) -> dict:
        """Query the Spine's authoritative view of agent state."""
        return self._send_request("get_state", {"keys": keys})


class SpineError(Exception):
    """Error returned by the Spine."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Spine error {code}: {message}")
```

- [ ] **Step 2: Write spine_client_test.py**

Create `cortex/spine_client_test.py`:

```python
"""Tests for Spine IPC client."""
import json
import socket
import threading
import pytest
from spine_client import SpineClient, SpineError


class MockSpineServer:
    """A mock Spine server for testing the IPC client."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.responses = {}  # method -> response
        self.received_requests = []

    def start(self):
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(self.socket_path)
        self.server_socket.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            conn, _ = self.server_socket.accept()
            data = conn.recv(65536)
            if data:
                request = json.loads(data.decode("utf-8").strip())
                self.received_requests.append(request)
                method = request["method"]
                response = self.responses.get(method, {"result": "ok"})
                conn.sendall((json.dumps({
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    **response,
                }) + "\n").encode("utf-8"))
            conn.close()

    def stop(self):
        self.server_socket.close()
        import os
        os.unlink(self.socket_path)


def test_think_request(tmp_path):
    """Test that think() sends the correct JSON-RPC request."""
    socket_path = str(tmp_path / "test.sock")
    mock = MockSpineServer(socket_path)
    mock.responses["think"] = {
        "result": {
            "assistant_message": "I will read the file.",
            "tool_calls": [],
            "context_pct": 0.45,
            "turn": 1,
            "tokens_used": 500,
        }
    }
    mock.start()

    client = SpineClient(socket_path)
    result = client.think(
        focus="fix_auth_bug",
        tools=[{"name": "read_file", "description": "Read a file", "parameters": {"type": "object"}}],
        hud_data={"memory_keys": 5, "last_keys": ["key1"], "urgency": "nominal"},
    )

    assert result["turn"] == 1
    assert result["context_pct"] == 0.45
    assert len(mock.received_requests) == 1
    req = mock.received_requests[0]
    assert req["method"] == "think"
    assert req["params"]["focus"] == "fix_auth_bug"
    assert req["params"]["hud_data"]["memory_keys"] == 5
    mock.stop()


def test_spine_error(tmp_path):
    """Test that Spine errors are raised as SpineError."""
    socket_path = str(tmp_path / "test.sock")
    mock = MockSpineServer(socket_path)
    mock.responses["request_fold"] = {
        "error": {"code": -32000, "message": "No fold needed"}
    }
    mock.start()

    client = SpineClient(socket_path)
    with pytest.raises(SpineError) as exc_info:
        client.request_fold("synthesis")
    assert exc_info.value.code == -32000
    mock.stop()
```

- [ ] **Step 3: Run tests**

```bash
cd cortex && python -m pytest spine_client_test.py -v
```

- [ ] **Step 4: Commit**

```bash
git add cortex/ && git commit -m "feat(cortex): Spine IPC client with JSON-RPC protocol"
```

---

### Task 2: Tool Registry

**Files:**
- Create: `cortex/tool_registry.py`
- Create: `cortex/tool_registry_test.py`

The tool registry uses decorators to register tools and generates OpenAI JSON Schema for the Spine.

- [ ] **Step 1: Write tool_registry.py**

```python
"""
Tool Registry — Decorator-based tool registration with OpenAI JSON Schema generation.
"""
from typing import Callable, Any
import inspect


class ToolRegistry:
    """Registry for agent tools. Generates OpenAI function-calling schemas."""

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict] = []

    def tool(self, description: str, parameters: dict[str, Any]):
        """Decorator to register a tool function.

        Args:
            description: Human-readable description of what the tool does.
            parameters: JSON Schema object describing the tool's parameters.
        """
        def decorator(func: Callable) -> Callable:
            name = func.__name__
            self._tools[name] = func
            self._schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                }
            })
            return func
        return decorator

    def get_schemas(self) -> list[dict]:
        """Return all tool schemas in OpenAI function-calling format."""
        return list(self._schemas)

    def execute(self, name: str, kwargs: dict[str, Any]) -> str:
        """Execute a tool by name with the given arguments.

        Returns:
            String result of the tool execution.
        """
        if name not in self._tools:
            return f"[ERROR] Unknown tool: {name}"
        try:
            result = self._tools[name](**kwargs)
            return str(result)
        except Exception as e:
            return f"[ERROR] Tool {name} failed: {e}"

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())
```

- [ ] **Step 2: Write tool_registry_test.py**

```python
"""Tests for tool registry."""
from tool_registry import ToolRegistry


def test_register_and_execute():
    registry = ToolRegistry()

    @registry.tool(description="Add two numbers", parameters={
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First number"},
            "b": {"type": "integer", "description": "Second number"},
        },
        "required": ["a", "b"],
    })
    def add(a: int, b: int) -> str:
        return str(a + b)

    assert registry.has_tool("add")
    assert registry.execute("add", {"a": 2, "b": 3}) == "5"


def test_get_schemas():
    registry = ToolRegistry()

    @registry.tool(description="Read a file", parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
        },
        "required": ["path"],
    })
    def read_file(path: str) -> str:
        return "content"

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "read_file"
    assert schemas[0]["function"]["description"] == "Read a file"


def test_unknown_tool():
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert "[ERROR]" in result
    assert "Unknown tool" in result


def test_tool_error_handling():
    registry = ToolRegistry()

    @registry.tool(description="Always fails", parameters={"type": "object", "properties": {}})
    def failing_tool() -> str:
        raise ValueError("intentional failure")

    result = registry.execute("failing_tool", {})
    assert "[ERROR]" in result
    assert "intentional failure" in result
```

- [ ] **Step 3: Run tests**

```bash
cd cortex && python -m pytest tool_registry_test.py -v
```

- [ ] **Step 4: Commit**

```bash
git add cortex/ && git commit -m "feat(cortex): tool registry with decorator-based registration"
```

---

### Task 3: State Management and HUD Builder

**Files:**
- Create: `cortex/state.py`
- Create: `cortex/state_test.py`
- Create: `cortex/memory_store.py`
- Create: `cortex/memory_store_test.py`
- Create: `cortex/hud_builder.py`
- Create: `cortex/hud_builder_test.py`

- [ ] **Step 1: Write state.py**

Focus tracking, task queue management, and cognitive parameters.

```python
"""
State Management — Focus, task queue, and cognitive parameters.
"""
import json
from pathlib import Path
from typing import Optional


class AgentState:
    """Manages the agent's current state: focus, queue, and cognitive params."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.current_focus: Optional[str] = None
        self.error_streak: int = 0
        self.total_tokens_consumed: int = 0
        self._load_state()

    def _load_state(self):
        """Load state from disk."""
        state_file = self.memory_dir / ".agent_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                self.current_focus = data.get("current_focus")
                self.error_streak = data.get("error_streak", 0)
                self.total_tokens_consumed = data.get("total_tokens_consumed", 0)
            except (json.JSONDecodeError, KeyError):
                pass

    def save(self):
        """Save state to disk."""
        state_file = self.memory_dir / ".agent_state.json"
        data = {
            "current_focus": self.current_focus,
            "error_streak": self.error_streak,
            "total_tokens_consumed": self.total_tokens_consumed,
        }
        state_file.write_text(json.dumps(data, indent=2))

    def set_focus(self, objective: str):
        """Set current focus."""
        old = self.current_focus
        self.current_focus = objective
        self.save()
        return old

    def resolve_focus(self, synthesis: str):
        """Clear current focus with a synthesis."""
        old = self.current_focus
        self.current_focus = None
        self.save()
        return old
```

- [ ] **Step 2: Write memory_store.py**

KV store operations on `/memory/`.

```python
"""
Memory Store — Key-value store operations on /memory/.
"""
import json
from pathlib import Path
from typing import Optional


MAX_MEMORY_SLOTS = 50


class MemoryStore:
    """Key-value store backed by agent_memory.json in /memory/."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.store_file = memory_dir / "agent_memory.json"
        self._data: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.store_file.exists():
            try:
                self._data = json.loads(self.store_file.read_text())
            except (json.JSONDecodeError, KeyError):
                self._data = {}

    def _save(self):
        self.store_file.write_text(json.dumps(self._data, indent=2))

    def store(self, key: str, value: str) -> str:
        """Store a key-value pair. Returns confirmation message."""
        if len(key) > 100:
            return f"[ERROR] Key too long (max 100 chars): {key[:50]}..."
        if len(self._data) >= MAX_MEMORY_SLOTS and key not in self._data:
            return f"[ERROR] Memory full ({MAX_MEMORY_SLOTS} slots). Use forget_memory to free slots."
        self._data[key] = value
        self._save()
        return f"[STORED] {key}"

    def recall(self, key: str) -> str:
        """Retrieve value by exact or partial key match."""
        if key in self._data:
            return self._data[key]
        # Partial match
        for k, v in self._data.items():
            if key.lower() in k.lower():
                return v
        return f"[NOT FOUND] No memory matching '{key}'"

    def forget(self, key: str) -> str:
        """Delete a memory entry."""
        if key in self._data:
            del self._data[key]
            self._save()
            return f"[FORGOTTEN] {key}"
        return f"[NOT FOUND] No memory matching '{key}'"

    def list_keys(self) -> list[str]:
        """Return all memory keys."""
        return list(self._data.keys())

    @property
    def count(self) -> int:
        return len(self._data)
```

- [ ] **Step 3: Write hud_builder.py**

Builds the `hud_data` dict for each `think()` call.

```python
"""
HUD Builder — Constructs the HUD data payload for spine.think() calls.
"""
from state import AgentState
from memory_store import MemoryStore


def build_hud_data(state: AgentState, memory: MemoryStore, urgency: str = "nominal") -> dict:
    """Build the HUD data dict for the current think() call.

    Returns:
        Dict with memory_keys, last_keys, and urgency.
    """
    keys = memory.list_keys()
    last_keys = keys[-3:] if len(keys) >= 3 else keys

    return {
        "memory_keys": memory.count,
        "last_keys": last_keys,
        "urgency": urgency,
    }
```

- [ ] **Step 4: Write tests for state, memory, and HUD**

Create `cortex/state_test.py`, `cortex/memory_store_test.py`, and `cortex/hud_builder_test.py` with unit tests for:
- Setting and resolving focus
- Storing, recalling, and forgetting memory
- HUD data construction
- State persistence across load/save

- [ ] **Step 5: Run tests**

```bash
cd cortex && python -m pytest state_test.py memory_store_test.py hud_builder_test.py -v
```

- [ ] **Step 6: Commit**

```bash
git add cortex/ && git commit -m "feat(cortex): state management, memory store, and HUD builder"
```

---

### Task 4: Core Tools Implementation

**Files:**
- Create: `cortex/tools/__init__.py`
- Create: `cortex/tools/executive.py`
- Create: `cortex/tools/code_surgery.py`
- Create: `cortex/tools/memory.py`
- Create: `cortex/tools/physical.py`
- Create: `cortex/tools/git_operations.py`

These are the core tools the agent uses. Each tool is registered via the `@registry.tool()` decorator.

- [ ] **Step 1: Create tools/__init__.py**

Empty file to make `tools` a package.

- [ ] **Step 2: Write tools/executive.py**

```python
"""Executive Control tools — focus, fold, reflect."""
from tool_registry import ToolRegistry
from spine_client import SpineClient


def register_executive_tools(registry: ToolRegistry, client: SpineClient, state):
    """Register executive control tools."""

    @registry.tool(
        description="Set current focus to a new objective.",
        parameters={
            "type": "object",
            "properties": {
                "objective": {"type": "string", "description": "The objective to focus on"},
            },
            "required": ["objective"],
        }
    )
    def set_focus(objective: str) -> str:
        old = state.set_focus(objective)
        client.emit_event("cortex.focus_set", {"from": old, "to": objective})
        return f"[FOCUS SET] Now focusing on: {objective}"

    @registry.tool(
        description="Resolve current focus with a synthesis.",
        parameters={
            "type": "object",
            "properties": {
                "synthesis": {"type": "string", "description": "Summary of what was accomplished"},
            },
            "required": ["synthesis"],
        }
    )
    def resolve_focus(synthesis: str) -> str:
        old = state.resolve_focus(synthesis)
        client.emit_event("cortex.focus_resolved", {"focus": old, "synthesis": synthesis})
        return f"[FOCUS RESOLVED] {old}: {synthesis}"

    @registry.tool(
        description="Fold context to free up space. Use the DELTA pattern: State Delta, Negative Knowledge, Handoff.",
        parameters={
            "type": "object",
            "properties": {
                "synthesis": {"type": "string", "description": "DELTA pattern synthesis of current context"},
            },
            "required": ["synthesis"],
        }
    )
    def fold_context(synthesis: str) -> str:
        result = client.request_fold(synthesis)
        return f"[CONTEXT FOLDED] Synthesis saved. Context window refreshed."

    @registry.tool(
        description="Reflect and pause. Set sleep_duration to rest (1-120 seconds).",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Current status reflection"},
                "sleep_duration": {"type": "integer", "description": "Seconds to pause (1-120)"},
            },
            "required": ["status"],
        }
    )
    def reflect(status: str, sleep_duration: int = 0) -> str:
        import time
        client.emit_event("cortex.reflect", {"status": status, "sleep_duration": sleep_duration})
        if sleep_duration > 0:
            time.sleep(min(sleep_duration, 120))
        return f"[REFLECT] {status}"
```

- [ ] **Step 3: Write tools/code_surgery.py, tools/memory.py, tools/physical.py, tools/git_operations.py**

Implement each tool module following the same pattern as `executive.py`:
- Each function is decorated with `@registry.tool(description, parameters)`
- Each calls `client.emit_event()` for observability
- Error handling returns error strings (never raises exceptions — tools return strings)
- `send_message` in `physical.py` calls `client.send_message("telegram", text)`
- `request_restart` in `physical.py` calls `client.request_restart(reason)`
- `bash_command` in `physical.py` uses `subprocess.run()` with a 60-second timeout and rejects `--no-verify` flags

- [ ] **Step 4: Write tests for tools**

Test each tool with a mock SpineClient and mock state.

- [ ] **Step 5: Run tests**

```bash
cd cortex && python -m pytest tools/ -v
```

- [ ] **Step 6: Commit**

```bash
git add cortex/ && git commit -m "feat(cortex): core tools — executive, code surgery, memory, physical, git"
```

---

### Task 5: Main ReAct Loop

**Files:**
- Create: `cortex/seed_agent.py`
- Create: `cortex/requirements.txt`

The main agent loop. This ties together the Spine client, tool registry, state management, memory, and HUD builder.

- [ ] **Step 1: Write requirements.txt**

```
# Core dependencies — minimal, matching the current Ouroboros stack
# tree-sitter is for AST-aware code editing (replace_symbol)
# httpx is for direct API calls (communication tools)
```

(Note: the current Ouroboros uses `openai`, `tree-sitter`, `trafilatura`, etc. These will be added as needed when porting tools.)

- [ ] **Step 2: Write seed_agent.py**

```python
"""
Talos V2 Cortex — Self-evolving autonomous agent.

Main entry point. Runs the ReAct loop:
  1. Load state and memory
  2. Build HUD data
  3. Call spine.think() with focus, tools, and HUD
  4. Route tool calls
  5. Return tool results
  6. Repeat
"""
import os
import sys
import json
from pathlib import Path

from spine_client import SpineClient, SpineError
from tool_registry import ToolRegistry
from state import AgentState
from memory_store import MemoryStore
from hud_builder import build_hud_data

# Import tool registration functions
from tools.executive import register_executive_tools
from tools.code_surgery import register_code_surgery_tools
from tools.memory import register_memory_tools
from tools.physical import register_physical_tools
from tools.git_operations import register_git_tools


MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", "/memory"))
SPINE_SOCKET = os.environ.get("SPINE_SOCKET", "/tmp/spine.sock")


def main():
    """Main agent loop."""
    # Initialize components
    client = SpineClient(SPINE_SOCKET)
    registry = ToolRegistry()
    state = AgentState(MEMORY_DIR)
    memory = MemoryStore(MEMORY_DIR)

    # Register all tools
    register_executive_tools(registry, client, state)
    register_code_surgery_tools(registry, client)
    register_memory_tools(registry, memory)
    register_physical_tools(registry, client)
    register_git_tools(registry, client)

    # Main loop
    while True:
        try:
            # Build HUD data
            urgency = "nominal"
            if state.error_streak >= 3:
                urgency = "elevated"
            if state.error_streak >= 5:
                urgency = "critical"
            hud_data = build_hud_data(state, memory, urgency)

            # Call Spine to think
            try:
                response = client.think(
                    focus=state.current_focus or "No focus set",
                    tools=registry.get_schemas(),
                    hud_data=hud_data,
                )
            except SpineError as e:
                # If the Spine forces a fold, it will return with fold_context only
                print(f"[Cortex] Spine error: {e}")
                state.error_streak += 1
                state.save()
                continue

            # Update state from response
            state.total_tokens_consumed += response.get("tokens_used", 0)
            state.save()

            # Reset error streak on successful think
            state.error_streak = 0
            state.save()

            # Route tool calls
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                # No tool call — the agent just produced text
                # This shouldn't normally happen with tool_choice=required
                continue

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("arguments", {})

                # Emit event for observability
                client.emit_event("cortex.tool_call", {
                    "tool": tool_name,
                    "args_summary": json.dumps(tool_args)[:200],
                })

                # Execute the tool
                start_time = __import__("time").time()
                result = registry.execute(tool_name, tool_args)
                duration_ms = int((__import__("time").time() - start_time) * 1000)

                # Return result to Spine
                success = not result.startswith("[ERROR]")
                client.tool_result(tc["id"], result, success)

                # Emit result event
                client.emit_event("cortex.tool_result", {
                    "tool": tool_name,
                    "success": success,
                    "duration_ms": duration_ms,
                    "output_chars": len(result),
                })

                # Check for restart signal
                if tool_name == "request_restart":
                    print("[Cortex] Restart requested. Exiting.")
                    sys.exit(0)

                if not success:
                    state.error_streak += 1
                    state.save()

        except KeyboardInterrupt:
            print("[Cortex] Interrupted. Exiting gracefully.")
            sys.exit(0)
        except Exception as e:
            print(f"[Cortex] Fatal error: {e}")
            state.error_streak += 1
            state.save()
            sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write integration test**

Create `cortex/test_seed_agent.py` with a test that:
1. Creates a mock SpineClient
2. Verifies the main loop processes a think response with a tool call
3. Verifies tool execution and result reporting
4. Verifies error streak tracking

- [ ] **Step 4: Run tests**

```bash
cd cortex && python -m pytest test_seed_agent.py -v
```

- [ ] **Step 5: Commit**

```bash
git add cortex/ && git commit -m "feat(cortex): main ReAct loop with tool routing and error handling"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ IPC client (spine_client.py) → Task 1
- ✅ Tool registry with OpenAI JSON Schema → Task 2
- ✅ State management (focus, error streak) → Task 3
- ✅ Memory store (KV on /memory/) → Task 3
- ✅ HUD builder → Task 3
- ✅ Core tools (executive, code surgery, memory, physical, git) → Task 4
- ✅ Main ReAct loop → Task 5
- ✅ Self-modification pipeline (write_file, git_commit, request_restart) → Task 4

**Placeholder scan:**
- Task 4 has partial implementation notes ("following the same pattern as executive.py") for code_surgery, memory, physical, and git tools. These will need full implementations during execution.

**Type consistency:**
- SpineClient method signatures match the IPC protocol defined in the spec
- ToolDef, HUDData types match the Go IPC types
- Tool registry produces OpenAI JSON Schema compatible with the Spine's validation