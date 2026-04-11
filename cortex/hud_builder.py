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
