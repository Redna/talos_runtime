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
