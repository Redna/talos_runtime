"""Tests for agent state management."""
import json
from pathlib import Path
from state import AgentState


def test_set_and_resolve_focus(tmp_path):
    state = AgentState(tmp_path)
    assert state.current_focus is None

    old = state.set_focus("fix_auth_bug")
    assert old is None
    assert state.current_focus == "fix_auth_bug"

    old = state.resolve_focus("Fixed the bug")
    assert old == "fix_auth_bug"
    assert state.current_focus is None


def test_state_persistence(tmp_path):
    state = AgentState(tmp_path)
    state.set_focus("build_api")
    state.error_streak = 3
    state.total_tokens_consumed = 5000
    state.save()

    # Load fresh state from disk
    state2 = AgentState(tmp_path)
    assert state2.current_focus == "build_api"
    assert state2.error_streak == 3
    assert state2.total_tokens_consumed == 5000
