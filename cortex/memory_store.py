"""
Memory Store — Key-value store operations on /memory/.
"""
import json
from pathlib import Path
from typing import Optional


MAX_MEMORY_SLOTS = 50


class MemoryStore:
    """Key-value store backed by agent_memory.json in /memory/."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.store_file = memory_dir / "agent_memory.json"
        self._data: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.store_file.exists():
            try:
                self._data = json.loads(self.store_file.read_text())
            except (json.JSONDecodeError, KeyError):
                self._data = {}

    def _save(self):
        self.store_file.write_text(json.dumps(self._data, indent=2))

    def store(self, key: str, value: str) -> str:
        """Store a key-value pair. Returns confirmation message."""
        if len(key) > 100:
            return f"[ERROR] Key too long (max 100 chars): {key[:50]}..."
        if len(self._data) >= MAX_MEMORY_SLOTS and key not in self._data:
            return f"[ERROR] Memory full ({MAX_MEMORY_SLOTS} slots). Use forget_memory to free slots."
        self._data[key] = value
        self._save()
        return f"[STORED] {key}"

    def recall(self, key: str) -> str:
        """Retrieve value by exact or partial key match."""
        if key in self._data:
            return self._data[key]
        # Partial match
        for k, v in self._data.items():
            if key.lower() in k.lower():
                return v
        return f"[NOT FOUND] No memory matching '{key}'"

    def forget(self, key: str) -> str:
        """Delete a memory entry."""
        if key in self._data:
            del self._data[key]
            self._save()
            return f"[FORGOTTEN] {key}"
        return f"[NOT FOUND] No memory matching '{key}'"

    def list_keys(self) -> list[str]:
        """Return all memory keys."""
        return list(self._data.keys())

    @property
    def count(self) -> int:
        return len(self._data)
