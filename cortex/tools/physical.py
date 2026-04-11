"""Physical Interface tools — bash, messaging, restart."""
import subprocess
import sys
from tool_registry import ToolRegistry
from spine_client import SpineClient


# Flags that bypass git hooks — always rejected
BLOCKED_FLAGS = {"--no-verify", "--no-gpg-sign", "--no-gpg-sign-key", "--no-gpg-verify"}


def register_physical_tools(registry: ToolRegistry, client: SpineClient):
    """Register physical interface tools."""

    @registry.tool(
        description="Execute a bash command. Rejects flags that bypass git hooks.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        }
    )
    def bash_command(command: str) -> str:
        # Security: reject commands that bypass git hooks
        for flag in BLOCKED_FLAGS:
            if flag in command:
                return f"[REJECTED] Command contains blocked flag '{flag}'. Git hooks must not be bypassed."

        client.emit_event("cortex.bash_command", {"command": command[:200]})
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=60
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            if result.returncode != 0:
                return f"[EXIT {result.returncode}] {output}"
            return output if output.strip() else "[OK] Command completed with no output."
        except subprocess.TimeoutExpired:
            return "[ERROR] Command timed out after 60 seconds."
        except Exception as e:
            return f"[ERROR] Command failed: {e}"

    @registry.tool(
        description="Send a message to the creator via Telegram.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message text to send"},
            },
            "required": ["text"],
        }
    )
    def send_message(text: str) -> str:
        try:
            client.send_message("telegram", text)
            return "[SENT] Message sent to creator."
        except Exception as e:
            return f"[ERROR] Failed to send message: {e}"

    @registry.tool(
        description="Gracefully restart the agent. Rejected if uncommitted changes exist.",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Reason for restart"},
            },
            "required": ["reason"],
        }
    )
    def request_restart(reason: str) -> str:
        # Check for uncommitted changes before restart
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                return "[REJECTED] Cannot restart with uncommitted changes. Commit or stash first."
        except Exception:
            pass  # If git check fails, allow restart anyway

        client.request_restart(reason)
        return "[RESTARTING] Restart requested. Goodbye."