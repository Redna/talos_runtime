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


def test_non_streaming_completion_writes_trace(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    msgs = [
        {"role": "system", "content": "You are Talos."},
        {"role": "user", "content": "Begin."},
    ]
    resp_msg = {
        "role": "assistant",
        "content": "I will act.",
        "tool_calls": [],
    }
    writer.write_messages(msgs, resp_msg, turn=0)

    writer.write_messages(
        msgs + [resp_msg, {"role": "tool", "tool_call_id": "x", "content": "ok"}],
        {"role": "assistant", "content": "Step 2."},
        turn=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_file = trace_dir / "messages" / f"{today}.jsonl"
    lines = [json.loads(l) for l in trace_file.read_text().strip().split("\n")]
    assert len(lines) == 6
    assert lines[3]["role"] == "assistant"
    assert lines[3]["content"] == "I will act."
    assert lines[4]["role"] == "tool"
    assert lines[5]["role"] == "assistant"
    assert lines[5]["content"] == "Step 2."


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


def test_normalize_content_strips_channel_tokens(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    msg = {
        "role": "assistant",
        "content": "<|channel|>thought<channel|><|channel|>thought<channel|><|channel|>thought<channel|>Real content here",
    }
    result = writer._normalize_content(msg)
    assert "<|channel|>" not in result["content"]
    assert "Real content here" in result["content"]


def test_normalize_content_strips_generic_control_tokens(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    msg = {
        "role": "assistant",
        "content": "<|start|><|end|>Keep this<|sep|>",
    }
    result = writer._normalize_content(msg)
    assert result["content"] == "Keep this"


def test_normalize_content_preserves_clean_content(trace_dir):
    from app import MessageTraceWriter

    writer = MessageTraceWriter(trace_dir)
    msg = {"role": "assistant", "content": "Normal response with no tokens"}
    result = writer._normalize_content(msg)
    assert result["content"] == "Normal response with no tokens"
