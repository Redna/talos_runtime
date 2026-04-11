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
        client.request_fold(synthesis)
        return "[CONTEXT FOLDED] Synthesis saved. Context window refreshed."

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