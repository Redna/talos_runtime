from __future__ import annotations

import asyncio
import datetime
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx


class XRayClient:
    def __init__(
        self,
        gate_url: str,
        spine_url: str,
        on_event: Callable[[dict], None],
    ):
        self.gate_url = gate_url
        self.spine_url = spine_url
        self.on_event = on_event
        self._running = False
        self._state: dict[str, Any] = {}
        self._events: list[dict] = []
        self._commit: dict[str, Any] = {}
        self._container_status: dict[str, str] = {}
        self._data_dir = Path(os.getenv("XRAY_DATA_DIR", "/data"))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._stats_file = self._data_dir / "token_stats.json"
        self._last_stats_write = 0.0

    async def start(self):
        self._running = True
        tasks = [
            asyncio.create_task(self._subscribe_gate_stream()),
            asyncio.create_task(self._subscribe_gate_state()),
            asyncio.create_task(self._poll_spine_state()),
            asyncio.create_task(self._poll_spine_events()),
            asyncio.create_task(self._poll_health_probes()),
            asyncio.create_task(self._poll_spine_commit()),
            asyncio.create_task(self._persist_token_stats()),
        ]
        await asyncio.gather(*tasks)

    async def stop(self):
        self._running = False

    def get_full_snapshot(self) -> dict:
        events = self._events if isinstance(self._events, list) else []
        return {
            "state": self._state,
            "events": events[-200:],
            "commit": self._commit,
            "container_status": self._container_status,
        }

    async def _subscribe_gate_stream(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    async with client.stream(
                        "GET", f"{self.gate_url}/v1/xray/stream"
                    ) as resp:
                        backoff = 1.0
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if not payload:
                                continue
                            try:
                                event = json.loads(payload)
                                self.on_event(event)
                            except json.JSONDecodeError:
                                pass
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _subscribe_gate_state(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    async with client.stream(
                        "GET", f"{self.gate_url}/v1/xray/state"
                    ) as resp:
                        backoff = 1.0
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                event = json.loads(line[6:])
                                self.on_event(event)
                            except json.JSONDecodeError:
                                pass
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _poll_spine_state(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/state")
                    if resp.status_code == 200:
                        self._state = resp.json()
                    try:
                        health_resp = await client.get(f"{self.spine_url}/health")
                        if health_resp.status_code == 200:
                            health_data = health_resp.json()
                            self._state["spine_status"] = health_data.get(
                                "status", "unknown"
                            )
                            if "consecutive_failures" in health_data:
                                self._state["consecutive_failures"] = health_data[
                                    "consecutive_failures"
                                ]
                    except Exception:
                        self._state["spine_status"] = "offline"
                    self.on_event({"type": "state_update", **self._state})
                    backoff = 1.0
            except Exception:
                backoff = min(backoff * 2, 30.0)
            await asyncio.sleep(3)

    async def _poll_spine_events(self):
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/events?tail=200")
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            self._events = data
            except Exception:
                pass
            await asyncio.sleep(10)

    async def _poll_health_probes(self):
        while self._running:
            status = {}
            for name, url in [
                ("gate", f"{self.gate_url}/health"),
                ("talos", f"{self.spine_url}/health"),
            ]:
                try:
                    async with httpx.AsyncClient(timeout=3.0) as client:
                        resp = await client.get(url)
                        data = resp.json()
                        status[name] = data.get("status", "unknown")
                except Exception:
                    status[name] = "offline"
            try:
                ollama_host = os.environ.get(
                    "OLLAMA_HOST", "host.docker.internal:11434"
                )
                if not ollama_host.startswith("http"):
                    ollama_host = f"http://{ollama_host}"
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(f"{ollama_host}/api/tags")
                    data = resp.json()
                    status["ollama"] = (
                        "healthy"
                        if isinstance(data, dict) and "models" in data
                        else "unhealthy"
                    )
            except Exception:
                status["ollama"] = "offline"
            self._container_status = status
            self.on_event({"type": "container_status", **status})
            await asyncio.sleep(10)

    async def _poll_spine_commit(self):
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/commit")
                    if resp.status_code == 200:
                        self._commit = resp.json()
                        self.on_event({"type": "commit_info", **self._commit})
            except Exception:
                pass
            await asyncio.sleep(30)

    async def _persist_token_stats(self):
        while self._running:
            now = time.time()
            if now - self._last_stats_write >= 300:
                try:
                    today = datetime.date.today().isoformat()
                    existing = []
                    if self._stats_file.exists():
                        try:
                            existing = json.loads(self._stats_file.read_text())
                        except Exception:
                            pass
                    entry = {
                        "date": today,
                        "tokens_in": self._state.get("tokens_used", 0),
                        "tokens_out": 0,
                        "turns": self._state.get("turn", 0),
                        "requests": self._state.get("message_count", 0),
                    }
                    found = False
                    for e in existing:
                        if e["date"] == today:
                            e.update(entry)
                            found = True
                            break
                    if not found:
                        existing.append(entry)
                    self._stats_file.write_text(json.dumps(existing, indent=2))
                    self._last_stats_write = now
                except Exception:
                    pass
            await asyncio.sleep(60)
