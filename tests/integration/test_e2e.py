"""
End-to-end integration test for the Spine-Cortex architecture.

Prerequisites:
- Spine binary built and available
- Gate service running (or mocked)
- Test uses a mock Gate server
"""
import json
import os
import socket
import sys

# Add cortex directory to Python path for imports.
# The Cortex source lives in the `talos` git submodule, not at the
# runtime-repo root.  We need both directories on sys.path:
#   - `talos/`        — so `from cortex.state import AgentState` works
#                       (cortex/__init__.py makes `cortex` a package)
#   - `talos/cortex/`  — so `from spine_client import SpineClient` works
#                       (those files use bare module names without the
#                       `cortex.` prefix)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'talos'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'talos', 'cortex'))
import subprocess
import time
import tempfile
import pytest
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockGateHandler(BaseHTTPRequestHandler):
    """Mock Gate server that returns a simple LLM response."""
    def do_POST(self):
        if self.path == '/v1/chat/completions':
            response = {
                "id": "mock-response",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "test-model",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "I will read the auth file.",
                        "tool_calls": [{
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "/app/auth.py"}),
                            }
                        }]
                    },
                    "finish_reason": "tool_call",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "context_pct": 0.25},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        elif self.path == '/v1/audit':
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"verdict": "approve"}).encode())
        elif self.path == '/health':
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress log output


@pytest.fixture
def mock_gate():
    """Start a mock Gate server on port 14000."""
    server = HTTPServer(('localhost', 14000), MockGateHandler)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_mock_gate_responds(mock_gate):
    """Test that the mock Gate server responds correctly."""
    import urllib.request
    resp = urllib.request.urlopen('http://localhost:14000/health')
    data = json.loads(resp.read())
    assert data["status"] == "healthy"


def test_mock_gate_completions(mock_gate):
    """Test that the mock Gate returns LLM completions."""
    import urllib.request
    req = urllib.request.Request(
        'http://localhost:14000/v1/chat/completions',
        data=json.dumps({
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert len(data["choices"][0]["message"]["tool_calls"]) > 0


def test_cortex_spine_client_connects():
    """Test that the Cortex's SpineClient can connect to a Unix socket."""
    # This test verifies the IPC protocol without requiring a running Spine binary
    # A full e2e test requires building the Spine binary, which is done in CI
    from spine_client import SpineClient
    client = SpineClient("/tmp/test_nonexistent.sock")
    # We expect a connection error since there's no server
    with pytest.raises(Exception):
        client.think("test", [], {"memory_keys": 0, "last_keys": [], "urgency": "nominal"})


def test_cortex_tool_registration():
    """Test that the Cortex can register all tools and generate schemas.

    Note: this test reflects the current *clean* tool set: executive,
    file_ops, physical, plus the optional plugins.delegation. The
    agent-built kernels.py module is intentionally absent from the
    clean seed (the agent will recreate it under P2 Self-Creation).
    Earlier deleted modules: code_surgery, memory, git_operations,
    and (now) kernels.
    """
    from tool_registry import ToolRegistry
    from tools.executive import register_executive_tools
    from tools.file_ops import register_file_ops_tools
    from tools.physical import register_physical_tools
    from state import AgentState
    from pathlib import Path

    registry = ToolRegistry()
    state = AgentState(Path("/tmp/test_memory"))

    # Use a mock client (just for registration, not actual IPC)
    class MockClient:
        def emit_event(self, *args, **kwargs): pass
        def send_message(self, *args, **kwargs): pass
        def request_restart(self, *args, **kwargs): pass
        def request_fold(self, *args, **kwargs): pass

    client = MockClient()

    register_executive_tools(registry, client, state)
    register_file_ops_tools(registry, client)
    register_physical_tools(registry, client)
    # Optional: plugins.delegation may not exist in every checkpoint
    try:
        from plugins.delegation import register_delegation_tools
        register_delegation_tools(registry, client)
    except ImportError:
        pass

    schemas = registry.get_schemas()
    # Sanity check: at least one tool is registered and they're all
    # valid OpenAI function-calling format.
    assert len(schemas) > 0, "expected at least one tool to register"
    for schema in schemas:
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]