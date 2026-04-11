"""Tests for HUD builder."""
from pathlib import Path
from state import AgentState
from memory_store import MemoryStore
from hud_builder import build_hud_data


def test_build_hud_data(tmp_path):
    state = AgentState(tmp_path)
    memory = MemoryStore(tmp_path)
    memory.store("db_schema", "users: id, name")
    memory.store("auth_flow", "OAuth2 flow")

    hud = build_hud_data(state, memory, urgency="elevated")

    assert hud["memory_keys"] == 2
    assert len(hud["last_keys"]) == 2
    assert hud["urgency"] == "elevated"


def test_hud_data_last_keys_capped(tmp_path):
    state = AgentState(tmp_path)
    memory = MemoryStore(tmp_path)
    for i in range(10):
        memory.store(f"key_{i}", f"value_{i}")

    hud = build_hud_data(state, memory)

    assert hud["memory_keys"] == 10
    assert len(hud["last_keys"]) == 3  # capped at 3
