"""Git Operation tools — commit, push, diff."""
import subprocess
from tool_registry import ToolRegistry
from spine_client import SpineClient


def register_git_tools(registry: ToolRegistry, client: SpineClient):
    """Register git operation tools."""

    @registry.tool(
        description="Commit staged changes with a message.",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
            },
            "required": ["message"],
        }
    )
    def git_commit(message: str) -> str:
        client.emit_event("cortex.git_commit", {"message": message[:100]})
        try:
            result = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return f"[ERROR] Commit failed: {result.stderr}"
            return f"[COMMITTED] {result.stdout.strip()}"
        except Exception as e:
            return f"[ERROR] Commit failed: {e}"

    @registry.tool(
        description="Push commits to the remote repository.",
        parameters={
            "type": "object",
            "properties": {
                "remote": {"type": "string", "description": "Remote name (default: origin)"},
                "branch": {"type": "string", "description": "Branch name (default: current)"},
            },
        }
    )
    def git_push(remote: str = "origin", branch: str = "") -> str:
        client.emit_event("cortex.git_push", {"remote": remote, "branch": branch})
        try:
            cmd = ["git", "push", remote]
            if branch:
                cmd.append(branch)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return f"[ERROR] Push failed: {result.stderr}"
            return f"[PUSHED] {result.stdout.strip()}"
        except Exception as e:
            return f"[ERROR] Push failed: {e}"

    @registry.tool(
        description="Show the current git diff (staged or unstaged).",
        parameters={
            "type": "object",
            "properties": {
                "staged": {"type": "boolean", "description": "Show staged changes (default: true)"},
            },
        }
    )
    def git_diff(staged: bool = True) -> str:
        client.emit_event("cortex.git_diff", {"staged": staged})
        try:
            cmd = ["git", "diff"]
            if staged:
                cmd.append("--staged")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if not result.stdout.strip():
                return "[NO CHANGES] Nothing to show."
            # Truncate if too long
            output = result.stdout
            if len(output) > 10000:
                output = output[:10000] + f"\n... ({len(result.stdout)} chars total)"
            return output
        except Exception as e:
            return f"[ERROR] Diff failed: {e}"