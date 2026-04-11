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
        self.responses = {}
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
                response = self.responses.get(method, {"jsonrpc": "2.0", "id": request["id"], "result": "ok"})
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
            conn.close()

    def stop(self):
        self.server_socket.close()
        import os
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass


def test_think_request(tmp_path):
    """Test that think() sends the correct JSON-RPC request."""
    socket_path = str(tmp_path / "test.sock")
    mock = MockSpineServer(socket_path)
    mock.responses["think"] = {
        "jsonrpc": "2.0",
        "id": 1,
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
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "No fold needed"}
    }
    mock.start()

    client = SpineClient(socket_path)
    with pytest.raises(SpineError) as exc_info:
        client.request_fold("synthesis")
    assert exc_info.value.code == -32000
    mock.stop()
