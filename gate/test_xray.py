import asyncio
import pytest

pytest_plugins = ["pytest_asyncio"]
from app import app, _xray_subscribers, _xray_broadcast


@pytest.mark.asyncio(loop_scope="function")
async def test_no_subscribers_no_overhead():
    _xray_subscribers.clear()
    _xray_broadcast({"type": "token", "content": "hello"})
    assert len(_xray_subscribers) == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_subscriber_receives_event():
    _xray_subscribers.clear()
    q = asyncio.Queue()
    _xray_subscribers.append(q)
    _xray_broadcast({"type": "token", "content": "hello"})
    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event["type"] == "token"
    assert event["content"] == "hello"
    _xray_subscribers.clear()
