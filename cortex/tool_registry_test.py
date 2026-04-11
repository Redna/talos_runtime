"""Tests for tool registry."""
from tool_registry import ToolRegistry


def test_register_and_execute():
    registry = ToolRegistry()

    @registry.tool(description="Add two numbers", parameters={
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First number"},
            "b": {"type": "integer", "description": "Second number"},
        },
        "required": ["a", "b"],
    })
    def add(a: int, b: int) -> str:
        return str(a + b)

    assert registry.has_tool("add")
    assert registry.execute("add", {"a": 2, "b": 3}) == "5"


def test_get_schemas():
    registry = ToolRegistry()

    @registry.tool(description="Read a file", parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
        },
        "required": ["path"],
    })
    def read_file(path: str) -> str:
        return "content"

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "read_file"
    assert schemas[0]["function"]["description"] == "Read a file"


def test_unknown_tool():
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert "[ERROR]" in result
    assert "Unknown tool" in result


def test_tool_error_handling():
    registry = ToolRegistry()

    @registry.tool(description="Always fails", parameters={"type": "object", "properties": {}})
    def failing_tool() -> str:
        raise ValueError("intentional failure")

    result = registry.execute("failing_tool", {})
    assert "[ERROR]" in result
    assert "intentional failure" in result
