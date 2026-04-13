# Multi-Backend LLM Routing Design — Ollama + Cloud Models

**Date:** 2026-04-13  
**Scope:** Add Ollama and cloud model backends to the Gate, enabling the agent to use local (llamacpp), local-Ollama, and cloud-proxied models

## Problem

The Gate currently supports two backends: llamacpp (local) and Together AI (cloud API). The `.gguf` model files were lost and need to be re-downloaded. Adding Ollama as a backend provides an alternative local inference engine that pulls models automatically. Cloud models (minimax-m2.7, gemma4:31b-cloud, glm-5.1) are reachable through Ollama's cloud proxy, requiring no local GPU memory.

## Architecture

```
Cortex (seed_agent.py)
    ↓
Spine (IPC: /tmp/spine.sock)
    ↓ 
Gate (http://gate:4000/v1/chat/completions)
    ↓ ↓ ↓
    llamacpp   Ollama   Together AI
    :8080      :11434   api.together.xyz
    (local)    (host)   (cloud)
```

The Spine doesn't know about backends — it sends a `model` field in the request. The Gate routes based on the model name.

## Design

### 1. Gate routing updates (`gate/app.py`)

Add Ollama backend and update model map:

```python
BACKENDS = {
    "local": "http://llamacpp:8080/v1/chat/completions",
    "ollama": "http://host.docker.internal:11434/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
    "together_images": "https://api.together.xyz/v1/images/generations",
    "together_audio": "https://api.together.xyz/v1/audio/transcriptions",
}

MODEL_MAP = {
    # Local llamacpp models
    "gemma-4-31B-it-UD-Q4_K_XL.gguf": "local",
    "gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf": "local",
    "Qwen3.5-27B-Q4_K_M.gguf": "local",
    # Ollama models (local + cloud)
    "gemma4:31b-cloud": "ollama",
    "minimax-m2.7:cloud": "ollama",
    "glm-5.1:cloud": "ollama",
    # Together AI models (prefix-based)
}
```

The routing logic (already exists) checks `MODEL_MAP` first, then falls back to `"together"` prefix matching, then defaults to `"local"`.

For Ollama models, the Gate forwards the `model` field as-is since Ollama uses the same field for model selection (e.g., `gemma4:31b-cloud`). No model name transformation needed.

### 2. Ollama-specific headers

Ollama's OpenAI-compatible endpoint doesn't require auth headers. The Gate adds auth headers only for the `together` backend (existing behavior). For `ollama`, no extra headers — just the JSON body.

### 3. Health check update (`gate/app.py`)

Current: `healthy` only if llamacpp responds.  
New: `healthy` if **any** backend responds.

```python
@app.get("/health")
async def health():
    local_ok = ollama_ok = False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://llamacpp:8080/health", timeout=2.0)
            local_ok = r.status_code == 200
    except: pass
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://host.docker.internal:11434/api/tags", timeout=2.0)
            ollama_ok = r.status_code == 200
    except: pass

    if local_ok or ollama_ok:
        return {"status": "healthy", "local_engine_ready": local_ok, "ollama_ready": ollama_ok, ...}
    return {"status": "unhealthy", "local_engine_ready": False, "ollama_ready": False, ...}
```

### 4. Model listing update (`/v1/models`)

Query Ollama's `/api/tags` endpoint and merge its model list into the `/v1/models` response. Tag Ollama models with cost 0 for local, cost from Ollama model metadata for cloud-proxied ones.

### 5. Docker compose updates

Add `extra_hosts` to the `gate` service so it can reach Ollama on the host:

```yaml
gate:
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

No new compose service for Ollama — it runs directly on the host, not in Docker.

### 6. SpineConfig — `gate_model` field

Add `gate_model` to `SpineConfig` with default `"gemma4:31b-cloud"`:

```python
gate_model: str = "gemma4:31b-cloud"
```

The Spine's `StreamManager._build_payload()` passes `self.cfg.gate_model` as the `model` field in the LLM request.

Update `spine_config.json`:
```json
"gate_model": "gemma4:31b-cloud"
```

### 7. `talosctl` startup changes

The watchdog currently starts llamacpp unconditionally. New logic:

1. If `DEFAULT_MODEL` is set and ends with `.gguf` → start llamacpp via compose overlay (existing behavior)
2. If `DEFAULT_MODEL` contains `:` (Ollama model format, e.g., `gemma4:31b-cloud`) → skip llamacpp startup, check Ollama health instead
3. If both are available → start both

The `wait_for_spine_healthy()` function already falls back to Gate health check, so no change needed there.

### 8. `.env` updates

```bash
# Primary model (used by Spine via Gate model field)
TALOS_MODEL=gemma4:31b-cloud

# If running llamacpp locally, set this too:
# DEFAULT_MODEL=gemma-4-31B-it-UD-Q4_K_XL.gguf
```

`DEFAULT_MODEL` drives the llamacpp compose overlay. `TALOS_MODEL` drives the Spine's `gate_model`. These can differ — e.g., TALOS_MODEL on Ollama while llamacpp is down.

### 9. Startup with gemma4:31b-cloud

On first run with an Ollama model, `talosctl start` will:
1. Check if Ollama model is available: `curl http://localhost:11434/api/tags | jq` for the model name
2. If not found, attempt to pull: `ollama pull gemma4:31b-cloud`
3. Wait for Gate health (which now checks Ollama too)
4. Start the agent container

### Files changed

1. `gate/app.py` — add Ollama backend, update MODEL_MAP, update health check, update /v1/models
2. `docker-compose.yml` — add `extra_hosts` to gate service
3. `talos/spine/config.py` — add `gate_model` field
4. `talos/spine/stream.py` — use `gate_model` in payload
5. `spine_config.json` — add `gate_model`
6. `talosctl` — update startup to handle Ollama models, health check
7. `.env.example` — add TALOS_MODEL, document Ollama setup