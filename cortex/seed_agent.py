"""
Talos V2 Cortex — Self-evolving autonomous agent.

Main entry point. Runs the ReAct loop:
  1. Load state and memory
  2. Build HUD data
  3. Call spine.think() with focus, tools, and HUD
  4. Route tool calls
  5. Return tool results
  6. Repeat
"""
import os
import sys
import json
import time
from pathlib import Path

from spine_client import SpineClient, SpineError
from tool_registry import ToolRegistry
from state import AgentState
from memory_store import MemoryStore
from hud_builder import build_hud_data

# Import tool registration functions
from tools.executive import register_executive_tools
from tools.code_surgery import register_code_surgery_tools
from tools.memory import register_memory_tools
from tools.physical import register_physical_tools
from tools.git_operations import register_git_tools


MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", "/memory"))
SPINE_SOCKET = os.environ.get("SPINE_SOCKET", "/tmp/spine.sock")


def main():
    """Main agent loop."""
    # Initialize components
    client = SpineClient(SPINE_SOCKET)
    registry = ToolRegistry()
    state = AgentState(MEMORY_DIR)
    memory = MemoryStore(MEMORY_DIR)

    # Register all tools
    register_executive_tools(registry, client, state)
    register_code_surgery_tools(registry, client)
    register_memory_tools(registry, memory)
    register_physical_tools(registry, client)
    register_git_tools(registry, client)

    # Main loop
    while True:
        try:
            # Build HUD data
            urgency = "nominal"
            if state.error_streak >= 3:
                urgency = "elevated"
            if state.error_streak >= 5:
                urgency = "critical"
            hud_data = build_hud_data(state, memory, urgency)

            # Call Spine to think
            try:
                response = client.think(
                    focus=state.current_focus or "No focus set",
                    tools=registry.get_schemas(),
                    hud_data=hud_data,
                )
            except SpineError as e:
                # If the Spine forces a fold, it will return with fold_context only
                print(f"[Cortex] Spine error: {e}")
                state.error_streak += 1
                state.save()
                continue

            # Update state from response
            state.total_tokens_consumed += response.get("tokens_used", 0)
            state.save()

            # Reset error streak on successful think
            state.error_streak = 0
            state.save()

            # Route tool calls
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                # No tool call — the agent just produced text
                # This shouldn't normally happen with tool_choice=required
                continue

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("arguments", {})

                # Emit event for observability
                client.emit_event("cortex.tool_call", {
                    "tool": tool_name,
                    "args_summary": json.dumps(tool_args)[:200],
                })

                # Execute the tool
                start_time = time.time()
                result = registry.execute(tool_name, tool_args)
                duration_ms = int((time.time() - start_time) * 1000)

                # Return result to Spine
                success = not result.startswith("[ERROR]")
                client.tool_result(tc["id"], result, success)

                # Emit result event
                client.emit_event("cortex.tool_result", {
                    "tool": tool_name,
                    "success": success,
                    "duration_ms": duration_ms,
                    "output_chars": len(result),
                })

                # Check for restart signal
                if tool_name == "request_restart":
                    print("[Cortex] Restart requested. Exiting.")
                    sys.exit(0)

                if not success:
                    state.error_streak += 1
                    state.save()

        except KeyboardInterrupt:
            print("[Cortex] Interrupted. Exiting gracefully.")
            sys.exit(0)
        except Exception as e:
            print(f"[Cortex] Fatal error: {e}")
            state.error_streak += 1
            state.save()
            sys.exit(1)


if __name__ == "__main__":
    main()