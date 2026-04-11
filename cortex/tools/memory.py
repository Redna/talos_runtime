"""Memory tools — store, recall, search."""
from tool_registry import ToolRegistry
from memory_store import MemoryStore


def register_memory_tools(registry: ToolRegistry, memory: MemoryStore):
    """Register memory tools."""

    @registry.tool(
        description="Store a key-value fact for later recall.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to store under"},
                "value": {"type": "string", "description": "Value to store"},
            },
            "required": ["key", "value"],
        }
    )
    def store_fact(key: str, value: str) -> str:
        return memory.store(key, value)

    @registry.tool(
        description="Recall a stored fact by exact or partial key match.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to look up"},
            },
            "required": ["key"],
        }
    )
    def recall_fact(key: str) -> str:
        return memory.recall(key)

    @registry.tool(
        description="List all memory keys.",
        parameters={
            "type": "object",
            "properties": {},
        }
    )
    def list_memory_keys() -> str:
        keys = memory.list_keys()
        return f"[MEMORY KEYS] ({len(keys)} total): {', '.join(keys)}"

    @registry.tool(
        description="Search memory keys and values for a query string.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        }
    )
    def search_memory(query: str) -> str:
        results = []
        for key in memory.list_keys():
            value = memory.recall(key)
            if query.lower() in key.lower() or query.lower() in value.lower():
                results.append(f"{key}: {value[:100]}")
        if results:
            return f"[SEARCH RESULTS] Found {len(results)} matches:\n" + "\n".join(results)
        return f"[NOT FOUND] No memories matching '{query}'"