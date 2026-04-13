from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from xray_client import XRayClient

app = FastAPI(title="Talos X-ray")

GATE_URL = os.getenv("GATE_URL", "http://gate:4000")
SPINE_URL = os.getenv("SPINE_URL", "http://talos_agent:4001")

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

_connected_clients: list[WebSocket] = []
_xray_client: XRayClient | None = None


def _broadcast(event: dict):
    dead = []
    for ws in _connected_clients:
        try:
            asyncio.get_event_loop().create_task(ws.send_json(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _connected_clients:
            _connected_clients.remove(ws)


@app.on_event("startup")
async def startup():
    global _xray_client
    _xray_client = XRayClient(GATE_URL, SPINE_URL, _broadcast)
    asyncio.create_task(_xray_client.start())


@app.on_event("shutdown")
async def shutdown():
    if _xray_client:
        await _xray_client.stop()


@app.get("/", response_class=HTMLResponse)
async def index():
    return (static_dir / "index.html").read_text()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connected_clients.append(ws)
    if _xray_client:
        try:
            snapshot = _xray_client.get_full_snapshot()
            await ws.send_json({"type": "full_snapshot", **snapshot})
        except Exception:
            pass
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
        return _xray_client._state
    return {}


@app.post("/api/command")
async def api_command(request: Request):
    data = await request.json()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{SPINE_URL}/command", json=data)
            return JSONResponse(
                content=resp.json() if resp.text else {"status": "ok"},
                status_code=resp.status_code,
            )
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
