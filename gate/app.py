import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, AsyncGenerator
import re
import httpx
from fastapi import (
    FastAPI,
    Request,
    Response,
    BackgroundTasks,
    HTTPException,
    File,
    UploadFile,
    Form,
)
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv
from tokenizer import TokenizerManager

# Talos Gate - v1.1
load_dotenv()

app = FastAPI(title="Talos Gate")

# Configuration
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", "/memory"))
LOG_DIR = Path(os.getenv("RUNTIME_LOG_DIR", "/runtime_logs"))
LEDGER_FILE = MEMORY_DIR / "financial_ledger.json"
TOGETHERAI_API_KEY = os.getenv("TOGETHERAI_API_KEY", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
DAILY_BUDGET_LIMIT = float(os.getenv("DAILY_BUDGET_LIMIT", "5.00"))
LOCAL_CONTEXT_WINDOW = int(os.getenv("TALOS_CONTEXT_WINDOW", "71680"))
AUDIO_API_URL = os.getenv(
    "AUDIO_API_URL", "https://api.together.xyz/v1/audio/transcriptions"
)
AUDIO_API_KEY = os.getenv("AUDIO_API_KEY", TOGETHERAI_API_KEY)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "host.docker.internal:11434")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

# Tokenizer for accurate context_pct computation
_tokenizer = TokenizerManager(
    model=os.getenv("DEFAULT_MODEL", ""),
    tokenizer_model_path=os.getenv(
        "TOKENIZER_MODEL_PATH",
        "/usr/local/share/talos/tokenizers/gemma_tokenizer.model",
    ),
    context_window=LOCAL_CONTEXT_WINDOW,
)

# State
PRICING_CACHE: Dict[str, Dict[str, float]] = {}


class MessageTraceWriter:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir) / "messages"
        self._written_fingerprints: set[str] = set()
        self._trace_turn = 0
        self._current_date = ""
        self._file = None

    def _ensure_file(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._file = open(self.data_dir / f"{today}.jsonl", "a", encoding="utf-8")
            self._current_date = today
            self._written_fingerprints.clear()

    @staticmethod
    def _fingerprint(msg: dict) -> str:
        # Dedup key based on role, content, and tool calls (ignoring IDs which vary per generation)
        tool_sigs = []
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            tool_sigs.append(
                {
                    "name": func.get("name", ""),
                    "args": func.get("arguments", ""),
                }
            )
        parts = [
            msg.get("role", ""),
            msg.get("content", ""),
            msg.get("tool_call_id", ""),
            msg.get("reasoning", ""),
            json.dumps(tool_sigs, sort_keys=True),
        ]
        return json.dumps(parts, sort_keys=True)

    def _normalize_content(self, message: dict) -> dict:
        content = message.get("content", "")
        if not content:
            return message
        content = re.sub(r"<\|channel\|>.*?<\|channel\|>", "", content, flags=re.DOTALL)
        content = re.sub(r"<\|[^|]*\|>", "", content)
        content = content.strip()
        if content != message.get("content", ""):
            message = {**message, "content": content}
        return message

    def _normalize_tool_calls(self, message: dict) -> dict:
        content = message.get("content", "")
        if not content or "<|tool_call" not in content:
            return message
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            return message
        pattern = r"<\|tool_call\|>call:(\w+)\{(.+?)\}<\|tool_call\|>"
        matches = re.findall(pattern, content, re.DOTALL)
        if not matches:
            pattern2 = r"call:(\w+)\{(.+?)\}"
            matches = re.findall(pattern2, content, re.DOTALL)
        for i, (name, args_raw) in enumerate(matches):
            args_raw = args_raw.replace('<|"|>', '"')
            try:
                args = json.loads(args_raw) if args_raw.strip().startswith("{") else {}
            except (json.JSONDecodeError, ValueError):
                args = {}
            tool_calls.append(
                {
                    "id": f"call_parsed_{i}_{int(time.time())}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
        clean = re.sub(
            r"<\|tool_call\|>.*?<\|tool_call\|>", "", content, flags=re.DOTALL
        ).strip()
        message = {**message, "content": clean or "", "tool_calls": tool_calls}
        return message

    def write_messages(
        self,
        request_messages: list[dict],
        response_message: dict,
        turn: int | None = None,
    ):
        self._ensure_file()
        ts = datetime.now(timezone.utc).isoformat()
        if turn is not None:
            self._trace_turn = turn
        else:
            self._trace_turn += 1
            turn = self._trace_turn

        for msg in request_messages:
            if "_turn" in msg:
                continue
            fp = self._fingerprint(msg)
            if fp in self._written_fingerprints:
                continue
            line = {**msg, "_ts": ts, "_turn": turn}
            self._file.write(json.dumps(line, default=str) + "\n")
            self._written_fingerprints.add(fp)

        resp_fp = self._fingerprint(response_message)
        if resp_fp not in self._written_fingerprints:
            normalized = self._normalize_content(response_message)
            normalized = self._normalize_tool_calls(normalized)
            line = {**normalized, "_ts": ts, "_turn": turn}
            if "role" not in line:
                line["role"] = "assistant"
            self._file.write(json.dumps(line, default=str) + "\n")
            self._written_fingerprints.add(resp_fp)
        self._file.flush()

    def reset(self):
        self._written_fingerprints.clear()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None
            self._current_date = ""


_trace_writer = MessageTraceWriter(DATA_DIR)


# Routing configuration
BACKENDS = {
    "local": "http://llamacpp:8080/v1/chat/completions",
    "ollama": f"http://{OLLAMA_HOST}/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
    "together_images": "https://api.together.xyz/v1/images/generations",
    "together_audio": "https://api.together.xyz/v1/audio/transcriptions",
    "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions",
}


THINKING_MODELS: set[str] = set()


# Model name remapping for backends that need different model names
MODEL_MAP = {
    "talos": "ollama",
    "gemma4:31b-cloud": "ollama",
    "gemma4": "ollama",
    "minimax-m2.7:cloud": "ollama",
    "glm-5.1:cloud": "ollama",
}

MODEL_REMAP = {
    "talos": "gemma4:31b-cloud",
    "gemma4": "gemma4:31b-cloud",
}


async def _detect_thinking_models():
    global THINKING_MODELS
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"http://{OLLAMA_HOST}/api/tags")
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    name = m.get("name", "")
                    try:
                        sr = await client.post(
                            f"http://{OLLAMA_HOST}/api/show",
                            json={"name": name},
                            timeout=10.0,
                        )
                        if sr.status_code == 200:
                            caps = sr.json().get("capabilities", [])
                            if "thinking" in caps:
                                THINKING_MODELS.add(name)
                    except Exception:
                        pass
        print(f"[Talos Gate] Thinking-capable models: {THINKING_MODELS}")
    except Exception as e:
        print(f"[Talos Gate] Could not detect thinking models: {e}")


async def refresh_pricing():
    """Fetches the latest pricing from Together AI."""
    global PRICING_CACHE
    if not TOGETHERAI_API_KEY:
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.together.xyz/v1/models",
                headers={"Authorization": f"Bearer {TOGETHERAI_API_KEY}"},
            )
            if resp.status_code == 200:
                models = resp.json()
                new_cache = {}
                for model in models:
                    pid = model.get("id")
                    pricing = model.get("pricing", {})
                    if pid and pricing:
                        new_cache[pid] = {
                            "input": pricing.get("input", 0.0),
                            "output": pricing.get("output", 0.0),
                            "base": pricing.get(
                                "base", 0.0
                            ),  # Used for fixed price models like images
                        }
                PRICING_CACHE = new_cache
                print(
                    f"[Talos Gate] Refreshed pricing for {len(PRICING_CACHE)} models."
                )
    except Exception as e:
        print(f"[Talos Gate] Failed to refresh pricing: {e}")


@app.on_event("startup")
async def startup_event():
    await refresh_pricing()


def get_current_spend() -> float:
    if not LEDGER_FILE.exists():
        return 0.0
    try:
        data = json.loads(LEDGER_FILE.read_text())
        today = time.strftime("%Y-%m-%d")
        return data.get(today, 0.0)
    except:
        return 0.0


def update_spend(cost: float):
    if cost <= 0:
        return
    try:
        data = {}
        if LEDGER_FILE.exists():
            try:
                data = json.loads(LEDGER_FILE.read_text())
            except:
                pass
        today = time.strftime("%Y-%m-%d")
        data[today] = data.get(today, 0.0) + cost
        LEDGER_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[Talos Gate] Error updating ledger: {e}")


def calculate_cost(backend_key: str, model_id: str, usage: Dict[str, Any]) -> float:
    if backend_key == "local":
        return 0.0

    # Strip our internal prefix if present
    clean_model_id = model_id.replace("together_ai/", "")
    # Default to $1.0 if not found in cache
    pricing = PRICING_CACHE.get(
        clean_model_id, {"input": 1.0, "output": 1.0, "base": 0.0}
    )

    # For fixed price models (like images)
    if pricing.get("base", 0.0) > 0 and not usage.get("total_tokens"):
        return pricing["base"]

    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    cost = (input_tokens / 1_000_000 * pricing["input"]) + (
        output_tokens / 1_000_000 * pricing["output"]
    )
    return cost


def log_completion(
    request_body: Dict[str, Any],
    response_body: Any,
    backend_key: str,
    is_stream: bool = False,
    cost_override: float = 0.0,
):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp_str = time.strftime("%Y%m%d-%H%M%S")
        log_file = LOG_DIR / f"call-{timestamp_str}-{int(time.time())}.json"

        cost = cost_override
        if cost == 0.0 and not is_stream and isinstance(response_body, dict):
            usage = response_body.get("usage", {})
            model_id = request_body.get("model", "unknown")
            cost = calculate_cost(backend_key, model_id, usage)
            update_spend(cost)

        log_data = {
            "timestamp": timestamp_str,
            "model": request_body.get("model", "unknown"),
            "backend": backend_key,
            "messages": request_body.get("messages", []),
            "response": response_body,
            "cost": cost,
            "is_stream": is_stream,
        }
        log_file.write_text(
            json.dumps(log_data, indent=2, default=str), encoding="utf-8"
        )
    except Exception as e:
        print(f"[Talos Gate] Error logging to memory: {e}")


@app.on_event("startup")
async def startup_event():
    await refresh_pricing()
    await _detect_thinking_models()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    model = str(body.get("model", ""))
    is_streaming = body.get("stream", False)
    skip_trace = request.headers.get("X-Talos-Skip-Trace") == "true"

    backend_key = "local"
    if "together" in model.lower():
        backend_key = "together"
    elif model.startswith("nvidia/"):
        backend_key = "nvidia"
    else:
        backend_key = MODEL_MAP.get(model, "local")

    if model in MODEL_REMAP:
        body["model"] = MODEL_REMAP[model]

    if backend_key == "together" and get_current_spend() >= DAILY_BUDGET_LIMIT:
        return Response(
            content=json.dumps(
                {
                    "id": "mock-error",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "error-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "SYSTEM ERROR: Daily budget limit exceeded. Switching to local LLM is required.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                }
            ),
            status_code=200,
            media_type="application/json",
        )

    url = BACKENDS.get(backend_key, BACKENDS["local"])
    headers = {"Content-Type": "application/json"}
    if backend_key == "together" and TOGETHERAI_API_KEY:
        headers["Authorization"] = f"Bearer {TOGETHERAI_API_KEY}"
    if backend_key == "nvidia" and NVIDIA_API_KEY:
        headers["Authorization"] = f"Bearer {NVIDIA_API_KEY}"

    if backend_key == "together" and model.startswith("together_ai/"):
        body["model"] = model.replace("together_ai/", "")
    if backend_key == "nvidia" and model.startswith("nvidia/"):
        body["model"] = model.replace("nvidia/", "", 1)

    resolved_model = body.get("model", model)
    if backend_key == "ollama":
        body.setdefault("options", {})["num_ctx"] = LOCAL_CONTEXT_WINDOW
        if resolved_model in THINKING_MODELS:
            body["reasoning_effort"] = "high"

    # Filter out non-standard parameters for strict backends (NVIDIA, etc.)
    # Also remove None values which some strict APIs reject
    forward_body = {
        k: v for k, v in body.items() 
        if k not in ["turn"] and v is not None
    }

    print(
        f"[Gate] Forwarding to {backend_key}: model={forward_body.get('model')} keys={list(forward_body.keys())}"
    )

    # Health check for Ollama before forwarding to prevent 500->503->deadlock cascade
    if backend_key == "ollama":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                health_check = await client.get(f"http://{OLLAMA_HOST}/api/tags")
                if health_check.status_code != 200:
                    return Response(
                        content=json.dumps({
                            "error": {
                                "message": "Ollama health check failed. The service is reachable but returned an error. It may be overloaded or the model may be unloading.",
                                "type": "server_error",
                                "code": "ollama_unhealthy"
                            }
                        }),
                        status_code=503,
                        media_type="application/json",
                    )
                # Optional: check if the specific model is loaded if we wanted to be more granular
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return Response(
                content=json.dumps({
                    "error": {
                        "message": f"Ollama is unreachable. Service might be down or restarting. Details: {str(e)}",
                        "type": "server_error",
                        "code": "ollama_offline"
                    }
                }),
                status_code=503,
                media_type="application/json",
            )

    if is_streaming:

        async def stream_proxy() -> AsyncGenerator[bytes, None]:
            _accumulated_content = ""
            _accumulated_reasoning = ""
            _accumulated_tool_calls = []
            try:
                for m in forward_body.get("messages", []):
                    if m.get("content") is None:
                        m["content"] = ""
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    async with client.stream(
                        "POST", url, json=forward_body, headers=headers
                    ) as resp:
                        print(f"[Gate] Ollama response status: {resp.status_code}")
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                yield line.encode("utf-8") + b"\n\n"
                                continue
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                yield b"data: [DONE]\n\n"
                                break
                            try:
                                chunk_data = json.loads(payload)
                                delta = chunk_data.get("choices", [{}])[0].get(
                                    "delta", {}
                                )
                                content = delta.get("content", "")
                                reasoning = delta.get("reasoning", "")
                                tool_calls = delta.get("tool_calls")
                                if reasoning:
                                    _accumulated_reasoning += reasoning
                                if content:
                                    filtered = re.sub(
                                        r"<\|channel\|>.*?<\|channel\|>",
                                        "",
                                        content,
                                        flags=re.DOTALL,
                                    )
                                    filtered = re.sub(r"<\|[^|]*\|>", "", filtered)
                                    if filtered:
                                        _accumulated_content += filtered
                                        delta["content"] = filtered
                                    else:
                                        delta.pop("content", None)
                                    payload = json.dumps(chunk_data)
                                if tool_calls:
                                    for tc in tool_calls:
                                        if tc.get("function", {}).get("name"):
                                            _accumulated_tool_calls.append(tc)
                            except json.JSONDecodeError:
                                pass
                            yield f"data: {payload}\n\n".encode("utf-8")
                if not skip_trace:
                    _trace_writer.write_messages(
                        body.get("messages", []),
                        _trace_writer._normalize_content(
                            _trace_writer._normalize_tool_calls(
                                {
                                    "role": "assistant",
                                    "content": _accumulated_content,
                                    "reasoning": _accumulated_reasoning,
                                    "tool_calls": _accumulated_tool_calls,
                                }
                            )
                        ),
                        turn=body.get("turn"),
                    )
                background_tasks.add_task(
                    log_completion,
                    body,
                    {"status": "stream_completed"},
                    backend_key,
                    True,
                )
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.HTTPStatusError,
            ) as e:
                error_payload = {
                    "error": {
                        "message": f"Gateway Error: Model '{model}' is currently unreachable or offline. Please check available models or fallback to the local engine. Details: {str(e)}",
                        "type": "server_error",
                        "code": "model_offline",
                    }
                }
                yield json.dumps(error_payload).encode("utf-8")

        return StreamingResponse(stream_proxy(), media_type="text/event-stream")

    else:
        try:
            for m in forward_body.get("messages", []):
                if m.get("content") is None:
                    m["content"] = ""
            async with httpx.AsyncClient(timeout=1800.0) as client:
                print(f"[Gate] NON-STREAM POSTing to {url}")
                resp = await client.post(url, json=forward_body, headers=headers)
                print(
                    f"[Gate] NON-STREAM Ollama response: {resp.status_code} body: {resp.text[:200]}"
                )
                resp.raise_for_status()

                resp_json = resp.json()

                # Compute context_pct from the actual tokenizer on the
                # request messages. This is deterministic and far more
                # accurate than relying on Ollama's prompt_tokens which
                # can swing by 7K+ tokens and report bogus values >100%.
                request_messages = body.get("messages", [])
                request_pct = _tokenizer.context_pct(request_messages, body.get("tools"))
                if request_pct is not None:
                    resp_json.setdefault("usage", {})["context_pct"] = round(
                        request_pct, 4
                    )
                elif "context_pct" not in resp_json.get("usage", {}):
                    prompt_tokens = resp_json.get("usage", {}).get(
                        "prompt_tokens", 0
                    )
                    resp_json.setdefault("usage", {})["context_pct"] = (
                        round(prompt_tokens / LOCAL_CONTEXT_WINDOW, 4)
                        if prompt_tokens and LOCAL_CONTEXT_WINDOW
                        else 0.0
                    )

                # Normalize Gemma control tokens out of the response content
                for choice in resp_json.get("choices", []):
                    msg = choice.get("message", {})
                    normalized = _trace_writer._normalize_content(msg)
                    normalized = _trace_writer._normalize_tool_calls(normalized)
                    choice["message"] = normalized

                background_tasks.add_task(log_completion, body, resp_json, backend_key)
                if not skip_trace:
                    _trace_writer.write_messages(
                        body.get("messages", []),
                        resp_json.get("choices", [{}])[0].get("message", {}),
                        turn=body.get("turn"),
                    )
                return resp_json
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            error_msg = str(e)
            is_context_overflow = "prompt is too long" in error_msg.lower()
            if isinstance(e, httpx.HTTPStatusError):
                try:
                    body_text = e.response.text
                    if "prompt is too long" in body_text.lower():
                        is_context_overflow = True
                        # Extract the token counts from Ollama's error
                        error_msg = body_text[:300]
                except Exception:
                    pass
            if is_context_overflow:
                return Response(
                    content=json.dumps(
                        {
                            "error": {
                                "message": error_msg,
                                "type": "context_overflow",
                                "code": "context_overflow",
                            }
                        }
                    ),
                    status_code=400,
                    media_type="application/json",
                )
            return Response(
                content=json.dumps(
                    {
                        "error": {
                            "message": f"Gateway Error: Model '{model}' is currently unreachable or offline. Please check available models or fallback to the local engine. Details: {error_msg}",
                            "type": "server_error",
                            "code": "model_offline",
                        }
                    }
                ),
                status_code=503,
                media_type="application/json",
            )
        except Exception as e:
            return Response(
                content=json.dumps(
                    {
                        "error": {
                            "message": f"Gateway Critical Error: {str(e)}",
                            "type": "server_error",
                            "code": "internal_error",
                        }
                    }
                ),
                status_code=500,
                media_type="application/json",
            )


@app.post("/v1/images/generations")
async def generate_images(request: Request, background_tasks: BackgroundTasks):
    if not TOGETHERAI_API_KEY:
        raise HTTPException(
            status_code=501, detail="Together AI API Key not configured."
        )

    if get_current_spend() >= DAILY_BUDGET_LIMIT:
        raise HTTPException(status_code=402, detail="Daily budget limit exceeded.")

    body = await request.json()
    model = body.get("model", "stabilityai/stable-diffusion-xl-base-1.0")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOGETHERAI_API_KEY}",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            BACKENDS["together_images"], json=body, headers=headers
        )
        if resp.status_code != 200:
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type="application/json",
            )

        resp_json = resp.json()
        # Together image pricing is often fixed per image. Default to $0.01 if unknown.
        pricing = PRICING_CACHE.get(model, {"base": 0.01})
        cost = pricing.get("base", 0.01)

        update_spend(cost)
        background_tasks.add_task(
            log_completion, body, resp_json, "together_images", cost_override=cost
        )
        return resp_json


@app.post("/v1/audio/transcriptions")
async def proxy_audio_transcription(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str = Form(None),
    prompt: str = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    """
    Proxies audio transcription requests.
    Automatically handles multipart boundary generation for the upstream API.
    """
    if not AUDIO_API_KEY:
        raise HTTPException(
            status_code=500, detail="Audio provider API key is missing."
        )

    if get_current_spend() >= DAILY_BUDGET_LIMIT:
        raise HTTPException(status_code=402, detail="Daily budget limit exceeded.")

    # Await the file stream into memory
    file_bytes = await file.read()

    # Construct the payload exactly as the OpenAI spec requires
    files = {"file": (file.filename, file_bytes, file.content_type)}

    data = {
        "model": model,
        "response_format": response_format,
        "temperature": str(temperature),
    }

    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    headers = {"Authorization": f"Bearer {AUDIO_API_KEY}"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                AUDIO_API_URL, data=data, files=files, headers=headers, timeout=120.0
            )
            response.raise_for_status()

            resp_json = response.json()

            # Audio pricing: Default to $0.005 per request for now.
            cost = 0.005
            update_spend(cost)

            background_tasks.add_task(
                log_completion,
                {"model": model, "tool": "audio_transcription"},
                resp_json,
                "audio_api",
                cost_override=cost,
            )

            return resp_json

        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code, detail=e.response.text
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/models")
async def list_models():
    """Aggregates models from local llama.cpp and Together AI with modality mapping."""
    unified_models = []

    # 1. Add local llama.cpp models
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://llamacpp:8080/v1/models", timeout=5.0)
            if resp.status_code == 200:
                local_models = resp.json().get("data", [])
                for m in local_models:
                    unified_models.append(
                        {
                            "id": m.get("id", "local-model"),
                            "context_window": LOCAL_CONTEXT_WINDOW,
                            "cost_per_m_in": 0.0,
                            "cost_per_m_out": 0.0,
                            "modalities": ["text"],
                        }
                    )
    except (httpx.RequestError, httpx.HTTPStatusError, Exception):
        pass  # Local LLM is offline or disabled, ignore silently.

    # 2. Add Ollama models
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://{OLLAMA_HOST}/api/tags", timeout=5.0)
            if resp.status_code == 200:
                ollama_data = resp.json()
                for m in ollama_data.get("models", []):
                    unified_models.append(
                        {
                            "id": m.get("name", "ollama-model"),
                            "context_window": LOCAL_CONTEXT_WINDOW,
                            "cost_per_m_in": 0.0,
                            "cost_per_m_out": 0.0,
                            "modalities": ["text"],
                        }
                    )
    except (httpx.RequestError, httpx.TimeoutException):
        pass  # Ollama not reachable

    # 3. Add Together AI models
    if TOGETHERAI_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.together.xyz/v1/models",
                    headers={"Authorization": f"Bearer {TOGETHERAI_API_KEY}"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    together_models = resp.json()
                    for m in together_models:
                        m_type = m.get("type", "").lower()
                        m_id = m.get("id", "")

                        modalities = ["text"]
                        if "vision" in m_id.lower() or "vision" in m_type:
                            modalities.append("vision")
                        elif m_type == "image":
                            modalities = ["image_generation"]
                        elif m_type == "audio":
                            modalities = ["audio_transcription"]

                        # Only include relevant types
                        if m_type in ["chat", "language", "image", "audio"]:
                            pricing = m.get("pricing", {})
                            unified_models.append(
                                {
                                    "id": f"together_ai/{m_id}",
                                    "context_window": m.get("context_length", 8192)
                                    if modalities[0] == "text"
                                    else 0,
                                    "cost_per_m_in": pricing.get("input", 1.0),
                                    "cost_per_m_out": pricing.get("output", 1.0),
                                    "modalities": modalities,
                                }
                            )
        except (httpx.RequestError, httpx.HTTPStatusError, Exception):
            pass  # External API unreachable or key invalid

    return {"object": "list", "data": unified_models}


@app.get("/v1/environment")
async def check_environment():
    models_resp = await list_models()
    spend = get_current_spend()
    return {
        "budget": {
            "daily_limit_usd": DAILY_BUDGET_LIMIT,
            "current_spend_usd": spend,
            "remaining_usd": max(0.0, DAILY_BUDGET_LIMIT - spend),
        },
        "models": models_resp["data"],
    }


@app.get("/healthz")
async def healthz():
    return {"status": "alive"}


@app.get("/health")
async def health():
    local_ok = False
    ollama_ok = False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://llamacpp:8080/health", timeout=2.0)
            local_ok = r.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        pass
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://{OLLAMA_HOST}/api/tags", timeout=2.0)
            ollama_ok = r.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        pass

    if local_ok or ollama_ok:
        status = "healthy"
    else:
        status = "degraded"

    return {
        "status": status,
        "engine": "Talos Gate",
        "local_engine_ready": local_ok,
        "ollama_ready": ollama_ok,
        "current_spend": f"{get_current_spend():.4f}/{DAILY_BUDGET_LIMIT:.4f}",
    }


@app.get("/v1/xray/history")
async def xray_history_list(count: int = 50):
    if not LOG_DIR.exists():
        return []
    files = sorted(LOG_DIR.glob("call-*.json"), reverse=True)[:count]
    result = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            result.append(
                {
                    "filename": f.name,
                    "model": data.get("model", "unknown"),
                    "timestamp": data.get("timestamp", ""),
                    "tokens_in": data.get("response", {})
                    .get("usage", {})
                    .get("prompt_tokens", 0),
                    "tokens_out": data.get("response", {})
                    .get("usage", {})
                    .get("completion_tokens", 0),
                    "backend": data.get("backend", "unknown"),
                }
            )
        except:
            pass
    return result


@app.get("/v1/xray/history/{filename}")
async def xray_history_detail(filename: str):
    filepath = LOG_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if not str(filepath).startswith(str(LOG_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    return json.loads(filepath.read_text())


@app.post("/v1/xray/reset-trace")
async def xray_reset_trace():
    _trace_writer.reset()
    return {"status": "ok", "message": "Trace fingerprint cache cleared."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=4000)
