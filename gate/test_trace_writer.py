import json
import pytest
from pathlib import Path
from datetime import datetime, timezone


@pytest.fixture
def trace_dir(tmp_path):
    return tmp_path / "data"


def test_write_messages_creates_daily_file(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer.write_messages(
        request_messages=[
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "Begin."},
        ],
        response_message={
            "role": "assistant",
            "content": "I will start.",
            "tool_calls": [],
        },
        turn=0,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    assert trace_file.exists()

    lines = trace_file.read_text().strip().split("\n")
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["role"] == "system"
    assert first["content"] == "You are Talos."
    assert "_ts" in first
    assert first["_turn"] == 0


def test_write_messages_deduplication(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer.write_messages(
        request_messages=[
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "Begin."},
        ],
        response_message={"role": "assistant", "content": "I will start."},
        turn=0,
    )

    writer.write_messages(
        request_messages=[
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "Begin."},
            {"role": "assistant", "content": "I will start."},
            {"role": "tool", "tool_call_id": "c1", "content": "result1"},
        ],
        response_message={"role": "assistant", "content": "Next step."},
        turn=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    lines = trace_file.read_text().strip().split("\n")
    assert len(lines) == 6
    last = json.loads(lines[-1])
    assert last["role"] == "assistant"
    assert last["content"] == "Next step."
    assert last["_turn"] == 1


def test_write_messages_includes_reasoning(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer.write_messages(
        request_messages=[{"role": "user", "content": "Go"}],
        response_message={
            "role": "assistant",
            "content": "I will act.",
            "reasoning": "Let me think about this...",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"/app/main.py"}',
                    },
                }
            ],
        },
        turn=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    lines = trace_file.read_text().strip().split("\n")
    resp = json.loads(lines[-1])
    assert resp["reasoning"] == "Let me think about this..."
    assert resp["tool_calls"][0]["function"]["name"] == "read_file"


def test_day_rollover(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    writer._current_date = "2025-12-31"
    writer.write_messages(
        request_messages=[{"role": "user", "content": "test"}],
        response_message={"role": "assistant", "content": "ok"},
        turn=0,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    assert trace_file.exists()
