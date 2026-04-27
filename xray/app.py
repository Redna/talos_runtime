from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from xray_client import XRayClient

GATE_URL = os.getenv("GATE_URL", "http://gate:4000")
SPINE_DIR = os.getenv("SPINE_DIR", "/spine")

static_dir = Path(__file__).parent / "static"

_connected_clients: list[WebSocket] = []
_xray_client: XRayClient | None = None
_xray_task: asyncio.Task | None = None
_broadcast_queue: asyncio.Queue | None = None


def _broadcast(event: dict):
    if _broadcast_queue is None:
        return
    _broadcast_queue.put_nowait(event)


async def _broadcast_loop():
    while True:
        event = await _broadcast_queue.get()
        dead = []
        for ws in _connected_clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _connected_clients:
                _connected_clients.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _xray_client, _xray_task, _broadcast_queue
    print("[Xray] Lifespan starting...", flush=True)
    _broadcast_queue = asyncio.Queue()
    _xray_client = XRayClient(GATE_URL, SPINE_DIR, _broadcast)
    _xray_task = asyncio.create_task(_xray_client.start())
    _broadcast_loop_task = asyncio.create_task(_broadcast_loop())
    print(f"[Xray] Client started, task={_xray_task}", flush=True)
    yield
    if _broadcast_loop_task:
        _broadcast_loop_task.cancel()
    if _xray_task:
        _xray_task.cancel()
    if _xray_client:
        await _xray_client.stop()


app = FastAPI(title="Talos X-ray", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (static_dir / "index.html").read_text()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connected_clients.append(ws)
    client = _xray_client
    if client:
        try:
            snapshot = client.get_full_snapshot()
            await ws.send_json({"type": "full_snapshot", **snapshot})
        except Exception as e:
            print(f"[Xray] snapshot error: {e}", flush=True)
    else:
        print("[Xray] WS connected but client is None", flush=True)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _connected_clients:
            _connected_clients.remove(ws)


@app.get("/api/state")
async def api_state():
    if _xray_client:
        msgs = _xray_client._messages
        # Ensure the window starts at an assistant message so we don't
        # show orphaned tool results without the call that triggered them.
        start = max(0, len(msgs) - 100)
        for i in range(start, len(msgs)):
            if msgs[i].get("role") == "assistant":
                start = i
                break
        return {
            **_xray_client._state,
            "messages": msgs[start:],
        }
    return {}


@app.post("/api/command")
async def api_command(request: Request):
    data = await request.json()
    try:
        command = data.get("command", "")
        spine = Path(SPINE_DIR)
        if command == "pause":
            (spine / ".paused").touch()
        elif command == "resume":
            paused = spine / ".paused"
            if paused.exists():
                paused.unlink()
            (spine / ".wake").touch()
        elif command == "force_restart":
            (spine / ".restart").touch()
        elif command == "step":
            (spine / ".single_step").touch(exist_ok=True)
            wake = spine / ".paused"
            if wake.exists():
                wake.unlink()
        return JSONResponse(content={"status": "ok"}, status_code=200)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=503)


@app.get("/api/history")
async def api_history(count: int = 50):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GATE_URL}/v1/xray/history?count={count}")
            return resp.json()
    except Exception:
        return []


@app.get("/api/history/{filename}")
async def api_history_detail(filename: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GATE_URL}/v1/xray/history/{filename}")
            return resp.json()
    except Exception:
        return {}
