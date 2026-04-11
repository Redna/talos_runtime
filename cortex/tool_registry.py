"""
Tool Registry — Decorator-based tool registration with OpenAI JSON Schema generation.
"""
from typing import Callable, Any


class ToolRegistry:
    """Registry for agent tools. Generates OpenAI function-calling schemas."""

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict] = []

    def tool(self, description: str, parameters: dict[str, Any]):
        """Decorator to register a tool function.

        Args:
            description: Human-readable description of what the tool does.
            parameters: JSON Schema object describing the tool's parameters.
        """
        def decorator(func: Callable) -> Callable:
            name = func.__name__
            self._tools[name] = func
            self._schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                }
            })
            return func
        return decorator

    def get_schemas(self) -> list[dict]:
        """Return all tool schemas in OpenAI function-calling format."""
        return list(self._schemas)

    def execute(self, name: str, kwargs: dict[str, Any]) -> str:
        """Execute a tool by name with the given arguments.

        Returns:
            String result of the tool execution.
        """
        if name not in self._tools:
            return f"[ERROR] Unknown tool: {name}"
        try:
            result = self._tools[name](**kwargs)
            return str(result)
        except Exception as e:
            return f"[ERROR] Tool {name} failed: {e}"

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())
