import os
import json
import time
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, AsyncGenerator
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException, File, UploadFile, Form
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

# Talos Gate - v1.1
load_dotenv()

app = FastAPI(title="Talos Gate")

# Configuration
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", "/memory"))
LOG_DIR = Path(os.getenv("RUNTIME_LOG_DIR", "/runtime_logs"))
LEDGER_FILE = MEMORY_DIR / "financial_ledger.json"
TOGETHERAI_API_KEY = os.getenv("TOGETHERAI_API_KEY", "")
DAILY_BUDGET_LIMIT = float(os.getenv("DAILY_BUDGET_LIMIT", "5.00"))
LOCAL_CONTEXT_WINDOW = int(os.getenv("TALOS_CONTEXT_WINDOW", "65536"))
AUDIO_API_URL = os.getenv("AUDIO_API_URL", "https://api.together.xyz/v1/audio/transcriptions")
AUDIO_API_KEY = os.getenv("AUDIO_API_KEY", TOGETHERAI_API_KEY)

# State
PRICING_CACHE: Dict[str, Dict[str, float]] = {}

# Routing configuration
BACKENDS = {
    "local": "http://llamacpp:8080/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
    "together_images": "https://api.together.xyz/v1/images/generations",
    "together_audio": "https://api.together.xyz/v1/audio/transcriptions"
}

# Explicit model mapping
MODEL_MAP = {
    "gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf": "local",
    "gemma-4-31B-it-UD-Q4_K_XL.gguf": "local",
    "Qwen3.5-27B-Q4_K_M.gguf": "local",
    "mistralai_Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf": "local",
}

async def refresh_pricing():
    """Fetches the latest pricing from Together AI."""
    global PRICING_CACHE
    if not TOGETHERAI_API_KEY:
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.together.xyz/v1/models",
                headers={"Authorization": f"Bearer {TOGETHERAI_API_KEY}"}
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
                            "base": pricing.get("base", 0.0) # Used for fixed price models like images
                        }
                PRICING_CACHE = new_cache
                print(f"[Talos Gate] Refreshed pricing for {len(PRICING_CACHE)} models.")
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
    if cost <= 0: return
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
    pricing = PRICING_CACHE.get(clean_model_id, {"input": 1.0, "output": 1.0, "base": 0.0})

    # For fixed price models (like images)
    if pricing.get("base", 0.0) > 0 and not usage.get("total_tokens"):
        return pricing["base"]

    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    cost = (input_tokens / 1_000_000 * pricing["input"]) + (output_tokens / 1_000_000 * pricing["output"])
    return cost

def log_completion(request_body: Dict[str, Any], response_body: Any, backend_key: str, is_stream: bool = False, cost_override: float = 0.0):
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
            "is_stream": is_stream
        }
        log_file.write_text(json.dumps(log_data, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        print(f"[Talos Gate] Error logging to memory: {e}")

@app.post("/v1/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    model = body.get("model", "")
    is_streaming = body.get("stream", False)

    backend_key = "local"
    if "together" in model.lower():
        backend_key = "together"
    else:
        backend_key = MODEL_MAP.get(model, "local")

    if backend_key != "local" and get_current_spend() >= DAILY_BUDGET_LIMIT:
        return Response(
            content=json.dumps({
                "id": "mock-error",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "error-model",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "SYSTEM ERROR: Daily budget limit exceeded. Switching to local LLM is required."
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }),
            status_code=200,
            media_type="application/json"
        )

    url = BACKENDS.get(backend_key, BACKENDS["local"])
    headers = {"Content-Type": "application/json"}
    if backend_key == "together" and TOGETHERAI_API_KEY:
        headers["Authorization"] = f"Bearer {TOGETHERAI_API_KEY}"

    if backend_key == "together" and model.startswith("together_ai/"):
        body["model"] = model.replace("together_ai/", "")

    if is_streaming:
        async def stream_proxy() -> AsyncGenerator[bytes, None]:
            try:
                async with httpx.AsyncClient(timeout=1800.0) as client:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                background_tasks.add_task(log_completion, body, {"status": "stream_completed"}, backend_key, True)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                error_payload = {
                    "error": {
                        "message": f"Gateway Error: Model '{model}' is currently unreachable or offline. Please check available models or fallback to the local engine. Details: {str(e)}",
                        "type": "server_error",
                        "code": "model_offline"
                    }
                }
                yield json.dumps(error_payload).encode("utf-8")

        return StreamingResponse(stream_proxy(), media_type="text/event-stream")

    else:
        try:
            async with httpx.AsyncClient(timeout=1800.0) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()

                resp_json = resp.json()
                background_tasks.add_task(log_completion, body, resp_json, backend_key)
                return resp_json
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            return Response(
                content=json.dumps({
                    "error": {
                        "message": f"Gateway Error: Model '{model}' is currently unreachable or offline. Please check available models or fallback to the local engine. Details: {str(e)}",
                        "type": "server_error",
                        "code": "model_offline"
                    }
                }),
                status_code=503,
                media_type="application/json"
            )
        except Exception as e:
            return Response(
                content=json.dumps({
                    "error": {
                        "message": f"Gateway Critical Error: {str(e)}",
                        "type": "server_error",
                        "code": "internal_error"
                    }
                }),
                status_code=500,
                media_type="application/json"
            )

@app.post("/v1/images/generations")
async def generate_images(request: Request, background_tasks: BackgroundTasks):
    if not TOGETHERAI_API_KEY:
        raise HTTPException(status_code=501, detail="Together AI API Key not configured.")

    if get_current_spend() >= DAILY_BUDGET_LIMIT:
        raise HTTPException(status_code=402, detail="Daily budget limit exceeded.")

    body = await request.json()
    model = body.get("model", "stabilityai/stable-diffusion-xl-base-1.0")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOGETHERAI_API_KEY}"
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(BACKENDS["together_images"], json=body, headers=headers)
        if resp.status_code != 200:
            return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

        resp_json = resp.json()
        # Together image pricing is often fixed per image. Default to $0.01 if unknown.
        pricing = PRICING_CACHE.get(model, {"base": 0.01})
        cost = pricing.get("base", 0.01)

        update_spend(cost)
        background_tasks.add_task(log_completion, body, resp_json, "together_images", cost_override=cost)
        return resp_json

@app.post("/v1/audio/transcriptions")
async def proxy_audio_transcription(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str = Form(None),
    prompt: str = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0)
):
    """
    Proxies audio transcription requests.
    Automatically handles multipart boundary generation for the upstream API.
    """
    if not AUDIO_API_KEY:
        raise HTTPException(status_code=500, detail="Audio provider API key is missing.")

    if get_current_spend() >= DAILY_BUDGET_LIMIT:
        raise HTTPException(status_code=402, detail="Daily budget limit exceeded.")

    # Await the file stream into memory
    file_bytes = await file.read()

    # Construct the payload exactly as the OpenAI spec requires
    files = {
        "file": (file.filename, file_bytes, file.content_type)
    }

    data = {
        "model": model,
        "response_format": response_format,
        "temperature": str(temperature)
    }

    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    headers = {
        "Authorization": f"Bearer {AUDIO_API_KEY}"
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                AUDIO_API_URL,
                data=data,
                files=files,
                headers=headers,
                timeout=120.0
            )
            response.raise_for_status()

            resp_json = response.json()

            # Audio pricing: Default to $0.005 per request for now.
            cost = 0.005
            update_spend(cost)

            background_tasks.add_task(log_completion, {"model": model, "tool": "audio_transcription"}, resp_json, "audio_api", cost_override=cost)

            return resp_json

        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
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
                    unified_models.append({
                        "id": m.get("id", "local-model"),
                        "context_window": LOCAL_CONTEXT_WINDOW,
                        "cost_per_m_in": 0.0,
                        "cost_per_m_out": 0.0,
                        "modalities": ["text"]
                    })
    except (httpx.RequestError, httpx.HTTPStatusError, Exception):
        pass # Local LLM is offline or disabled, ignore silently.

    # 2. Add Together AI models
    if TOGETHERAI_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.together.xyz/v1/models",
                    headers={"Authorization": f"Bearer {TOGETHERAI_API_KEY}"},
                    timeout=10.0
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
                            unified_models.append({
                                "id": f"together_ai/{m_id}",
                                "context_window": m.get("context_length", 8192) if modalities[0] == "text" else 0,
                                "cost_per_m_in": pricing.get("input", 1.0),
                                "cost_per_m_out": pricing.get("output", 1.0),
                                "modalities": modalities
                            })
        except (httpx.RequestError, httpx.HTTPStatusError, Exception):
            pass # External API unreachable or key invalid

    return {"object": "list", "data": unified_models}

@app.get("/v1/environment")
async def check_environment():
    models_resp = await list_models()
    spend = get_current_spend()
    return {
        "budget": {
            "daily_limit_usd": DAILY_BUDGET_LIMIT,
            "current_spend_usd": spend,
            "remaining_usd": max(0.0, DAILY_BUDGET_LIMIT - spend)
        },
        "models": models_resp["data"]
    }

@app.get("/health")
async def health():
    local_reachable = False
    try:
        async with httpx.AsyncClient() as client:
            test_resp = await client.get("http://llamacpp:8080/health", timeout=2.0)
            if test_resp.status_code == 200:
                local_reachable = True
    except:
        pass

    # Strictly healthy only if backend is ready
    status = "healthy" if local_reachable else "degraded"

    return {
        "status": status,
        "engine": "Talos Gate",
        "local_engine_ready": local_reachable,
        "current_spend": f"{get_current_spend():.4f}/{DAILY_BUDGET_LIMIT:.4f}"
    }

@app.post("/v1/audit")
async def audit_changes(request: Request):
    """
    Quality Gate: The Constitutional Auditor evaluates changes against the agent's Constitution.
    Uses the exact same trajectory prefix to maintain KV cache efficiency.
    """
    try:
        body = await request.json()
        diff = body.get("git_diff", "")
        constitution = body.get("constitution", "")
        # The full trajectory [System, History..., Assistant] as seen by the model
        messages = body.get("messages", [])

        if not diff:
            return {"rejected": False, "reason": "No changes to audit."}

        # Binary Tool Definitions for high-signal output
        audit_tools = [
            {
                "type": "function",
                "function": {
                    "name": "approve_commit",
                    "description": "Approve the changes as being fully aligned with the Constitution.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string", "description": "Concise summary of why the changes are safe and compliant."}
                        },
                        "required": ["reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "reject_commit",
                    "description": "Reject the changes due to Constitutional violations or architectural risks.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string", "description": "Detailed explanation of the breach and required fixes."},
                            "criticality": {"type": "string", "enum": ["low", "medium", "high", "fatal"]}
                        },
                        "required": ["reason", "criticality"]
                    }
                }
            }
        ]

        # Audit Instruction - Appended as a new turn to the existing context
        audit_prompt = f"""Your task is to critically audit your latest changes: {diff}
Your mission is to keep all your actions aligned to your CONSTITUTION: {constitution}
All breaches must be detected and immediately reported to avoid chaotic / catastrophic failure.

Review the history to understand your intent, then examine the diff.
If the diff violates any principle (P0-P9), you MUST call 'reject_commit'.
If it is fully compliant, call 'approve_commit'.
"""

        # Append the audit task as a new USER turn
        audit_messages = messages + [{"role": "user", "content": audit_prompt}]

        # Call the local LLM
        backend_url = BACKENDS["local"]
        payload = {
            "model": list(MODEL_MAP.keys())[0],
            "messages": audit_messages,
            "tools": audit_tools,
            "tool_choice": "required", # Hard constraint
            "temperature": 0.0,
            "extra_body": {"cache_prompt": True} # Explicitly request caching
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(backend_url, json=payload)
            resp.raise_for_status()

            resp_json = resp.json()
            tool_call = resp_json["choices"][0]["message"]["tool_calls"][0]
            func_name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])

            rejected = (func_name == "reject_commit")

            return {
                "rejected": rejected,
                "reason": args.get("reason", "No reason provided."),
                "criticality": args.get("criticality", "low" if not rejected else "high")
            }

    except Exception as e:
        print(f"[Sentinel Error] Audit failed: {e}")
        return {"rejected": True, "reason": f"Sentinel Internal Error: {str(e)}", "criticality": "fatal"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)