"""Tests for memory store."""
from pathlib import Path
from memory_store import MemoryStore, MAX_MEMORY_SLOTS


def test_store_and_recall(tmp_path):
    mem = MemoryStore(tmp_path)
    assert mem.store("db_schema", "users: id, name, email") == "[STORED] db_schema"
    assert mem.recall("db_schema") == "users: id, name, email"


def test_partial_recall(tmp_path):
    mem = MemoryStore(tmp_path)
    mem.store("database_schema", "users: id, name")
    assert "users" in mem.recall("schema")


def test_forget(tmp_path):
    mem = MemoryStore(tmp_path)
    mem.store("temp_key", "temp_value")
    assert "[FORGOTTEN]" in mem.forget("temp_key")
    assert "[NOT FOUND]" in mem.recall("temp_key")


def test_memory_full(tmp_path):
    mem = MemoryStore(tmp_path)
    for i in range(MAX_MEMORY_SLOTS):
        mem.store(f"key_{i}", f"value_{i}")
    result = mem.store("overflow", "overflow_value")
    assert "[ERROR]" in result
    assert "full" in result.lower()


def test_persistence(tmp_path):
    mem = MemoryStore(tmp_path)
    mem.store("persist_key", "persist_value")

    mem2 = MemoryStore(tmp_path)
    assert mem2.recall("persist_key") == "persist_value"
