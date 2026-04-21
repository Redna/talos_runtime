from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger("xray.client")


class XRayClient:
    def __init__(
        self,
        gate_url: str,
        spine_dir: str,
        on_event: Callable[[dict], None],
    ):
        self.gate_url = gate_url
        self.spine_dir = Path(spine_dir)
        self.on_event = on_event
        self._running = False
        self._state: dict[str, Any] = {}
        self._events: list[dict] = []
        self._commit: dict[str, Any] = {}
        self._container_status: dict[str, str] = {}
        self._data_dir = Path(os.getenv("XRAY_DATA_DIR", "/data"))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._messages: list[dict] = []
        self._max_messages = 500
        self._file_offset = 0
        self._current_trace_path: Path | None = None
        self.is_paused = False
        self._last_state_event: dict = {}

    async def start(self):
        self._running = True
        tasks = [
            asyncio.create_task(self._tail_message_trace()),
            asyncio.create_task(self._poll_spine_state()),
            asyncio.create_task(self._poll_health_probes()),
            asyncio.create_task(self._poll_spine_commit()),
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
            "messages": self._messages[-self._max_messages :],
        }

    async def _tail_message_trace(self):
        while self._running:
            try:
                today = datetime.date.today().isoformat()
                path = self._data_dir / "messages" / f"{today}.jsonl"
                if path.exists():
                    if path != self._current_trace_path:
                        self._current_trace_path = path
                        self._file_offset = 0
                    with open(path, "r", encoding="utf-8") as f:
                        f.seek(self._file_offset)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            msg = json.loads(line)
                            self._messages.append(msg)
                            if len(self._messages) > self._max_messages:
                                self._messages = self._messages[-self._max_messages :]
                            self.on_event({"type": "message", "message": msg})
                        self._file_offset = f.tell()
            except FileNotFoundError:
                pass
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("[XRay] Skipping malformed JSONL line: %s", e)
            except Exception:
                await asyncio.sleep(2)
                continue
            await asyncio.sleep(1)

    async def _poll_spine_state(self):
        backoff = 1.0
        while self._running:
            try:
                state_path = self.spine_dir / "state.json"
                if state_path.exists():
                    with open(state_path, "r", encoding="utf-8") as f:
                        self._state = json.load(f)
                self.is_paused = (self.spine_dir / ".paused").exists()
                self._state["is_paused"] = self.is_paused
                new_event = {
                    "type": "state_update",
                    "is_paused": self.is_paused,
                    **self._state,
                }
                if new_event != self._last_state_event:
                    self._last_state_event = new_event
                    self.on_event(new_event)
                backoff = 1.0
            except Exception:
                backoff = min(backoff * 2, 30.0)
            await asyncio.sleep(3)

    async def _poll_health_probes(self):
        while self._running:
            status = {}
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(f"{self.gate_url}/healthz")
                    data = resp.json()
                    status["gate"] = data.get("status", "unknown")
            except Exception:
                status["gate"] = "offline"
            health_path = self.spine_dir / "health.json"
            try:
                with open(health_path, "r", encoding="utf-8") as f:
                    health_data = json.load(f)
                status["talos"] = health_data.get("status", "unknown")
            except Exception:
                status["talos"] = "offline"
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
                commit_path = self.spine_dir / "commit.json"
                if commit_path.exists():
                    with open(commit_path, "r", encoding="utf-8") as f:
                        self._commit = json.load(f)
                    self.on_event({"type": "commit_info", **self._commit})
            except Exception:
                pass
            await asyncio.sleep(30)
