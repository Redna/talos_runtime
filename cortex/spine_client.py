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
        """Call the LLM with current stream and tool definitions."""
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
