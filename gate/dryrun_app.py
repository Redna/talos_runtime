"""
Dry-run LLM gateway.

Same FastAPI shape as ``gate/app.py`` and the same
``/v1/chat/completions`` contract — but the response is read from a
script file on disk instead of forwarding to a real model backend.

Two response forms are supported:

  1. ``scripted`` — a JSON file containing a list of pre-canned
     OpenAI ``chat.completion`` payloads.  Each request consumes the
     next entry (round-robin or in order).  When the list is
     exhausted the gateway falls back to a default ``bash_command``
     tool call.  This is how the happy / crash / stall scenarios are
     driven.

  2. ``default`` — a built-in handler that always returns a single
     ``bash_command`` tool call printing ``dry-run cycle N`` where
     N is the per-session request counter.  This is used by
     ad-hoc smoke tests that do not pre-author a script.

Why a separate gateway
----------------------
The dry-run's whole point is to remove the *only* non-deterministic
component of the architecture — the external LLM.  Everything else
(Spine, Supervisor, TalosSandbox, nono CLI, Cortex) is exercised
exactly as it is in production.  By keeping the gate the *only* mock
we can be sure the test is honest.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SCRIPT_PATH = Path(os.environ.get("DRYRUN_SCRIPT", "/gate/dryrun_script.json"))
SCENARIO_NAME = os.environ.get("DRYRUN_SCENARIO", "default")
LOG_PATH = Path(os.environ.get("DRYRUN_LOG", "/gate/dryrun_log.jsonl"))
SCRIPTED_RESPONSES: List[dict] = []
SCRIPT_INDEX = 0
SESSION_REQUESTS = 0
SESSION_ID = f"dryrun-{os.getpid()}-{int(time.time())}"

app = FastAPI(title="Talos Dry-Run Gate")


# --------------------------------------------------------------------------
# Logging — every request/response is appended to a JSONL audit file so the
# metrics collector can correlate gate behaviour with spine events.
# --------------------------------------------------------------------------
def _log(event_type: str, payload: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": event_type,
                "ts": time.time(),
                "session": SESSION_ID,
                "scenario": SCENARIO_NAME,
                **payload,
            }) + "\n")
    except Exception:
        pass  # never crash the gate on log failure


# --------------------------------------------------------------------------
# Script loading
# --------------------------------------------------------------------------
def _load_script(path: Path) -> List[dict]:
    """Load scripted responses from disk.

    Each entry is the *raw* body returned by ``/v1/chat/completions``
    (i.e. an OpenAI chat completion payload with choices, usage, etc.).
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "responses" in data:
        return data["responses"]
    return []


def _initial_response(req_seq: int) -> dict:
    """Default response — a single bash_command tool call."""
    return {
        "id": f"dryrun-{req_seq}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "dryrun-scripted",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"dry-run cycle {req_seq}",
                    "tool_calls": [
                        {
                            "id": f"call_{req_seq:04d}",
                            "type": "function",
                            "function": {
                                "name": "bash_command",
                                "arguments": json.dumps({
                                    "command": f"echo 'dry-run cycle {req_seq}'"
                                }),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_call",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "context_pct": 0.0,
        },
    }


@app.on_event("startup")
async def startup_event():
    global SCRIPTED_RESPONSES
    SCRIPTED_RESPONSES = _load_script(SCRIPT_PATH)
    _log("gate.startup", {
        "script_path": str(SCRIPT_PATH),
        "scripted_count": len(SCRIPTED_RESPONSES),
        "scenario": SCENARIO_NAME,
    })


# --------------------------------------------------------------------------
# Health endpoints (so the real gate's docker-compose healthcheck pattern
# still works).
# --------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "healthy", "scenario": SCENARIO_NAME, "session": SESSION_ID}


@app.get("/v1/environment")
async def environment():
    return {
        "scenario": SCENARIO_NAME,
        "scripted_responses_remaining": max(0, len(SCRIPTED_RESPONSES) - SCRIPT_INDEX),
        "session_requests": SESSION_REQUESTS,
    }


# --------------------------------------------------------------------------
# The main endpoint — return the next scripted response, or fall through
# to the default.
# --------------------------------------------------------------------------
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    global SCRIPT_INDEX, SESSION_REQUESTS

    try:
        body = await request.json()
    except Exception:
        body = {}

    SESSION_REQUESTS += 1

    if SCRIPT_INDEX < len(SCRIPTED_RESPONSES):
        resp = SCRIPTED_RESPONSES[SCRIPT_INDEX]
        SCRIPT_INDEX += 1
        # Stamp the request with the session so it is traceable in logs.
        resp_id = resp.get("id", f"scripted-{SCRIPT_INDEX}")
        _log("gate.request", {
            "seq": SESSION_REQUESTS,
            "scripted_index": SCRIPT_INDEX,
            "response_id": resp_id,
        })
        return Response(
            content=json.dumps(resp),
            status_code=200,
            media_type="application/json",
        )

    # Fall-through: default tool call.
    resp = _initial_response(SESSION_REQUESTS)
    _log("gate.request.default", {
        "seq": SESSION_REQUESTS,
        "tool": "bash_command",
    })
    return Response(
        content=json.dumps(resp),
        status_code=200,
        media_type="application/json",
    )


@app.post("/v1/xray/reset-trace")
async def reset_trace():
    """No-op for the dry-run — the spine may call this between folds."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "4000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
