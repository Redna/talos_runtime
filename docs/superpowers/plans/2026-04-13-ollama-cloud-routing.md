# Multi-Backend LLM Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ollama and cloud model backends to the Gate, enabling the agent to route LLM requests to llamacpp, Ollama, or Together AI based on the model name. Start with `gemma4:31b-cloud` as the default.

**Architecture:** The Spine sends `model` in the request. The Gate routes based on MODEL_MAP: `.gguf` names → llamacpp, `:cloud` suffix → Ollama, `together_ai/` prefix → Together AI. Ollama runs on the host and is reachable from Docker via `host.docker.internal`.

**Tech Stack:** Python (FastAPI, httpx), Docker Compose, Ollama

---

### Task 1: Add `gate_model` to SpineConfig and use it in StreamManager

**Files:**
- Modify: `talos/spine/config.py` (add `gate_model` field)
- Modify: `talos/spine/stream.py` (use `self.cfg.gate_model` instead of hardcoded `"talos"`)
- Modify: `spine_config.json` (add `gate_model`)

- [ ] **Step 1: Add `gate_model` to SpineConfig**

In `talos/spine/config.py`, add after line 29 (`shed_tool_output_max_chars`):

```python
    gate_model: str = "gemma4:31b-cloud"
```

- [ ] **Step 2: Use `gate_model` in StreamManager payload**

In `talos/spine/stream.py`, line 45, change:

```python
        api_req = {
            "model": "talos",
```

to:

```python
        api_req = {
            "model": self.cfg.gate_model,
```

- [ ] **Step 3: Add `gate_model` to spine_config.json**

In `spine_config.json`, add after `"shed_tool_output_max_chars"`:

```json
  "gate_model": "gemma4:31b-cloud"
```

So the full file becomes:

```json
{
  "memory_dir": "/memory",
  "spine_dir": "/spine",
  "constitution_path": "/app/CONSTITUTION.md",
  "identity_path": "/app/identity.md",
  "app_dir": "/app",
  "cortex_bin": "/venv/bin/python",
  "cortex_args": ["seed_agent.py"],
  "socket_path": "/tmp/spine.sock",
  "control_plane_port": 4001,
  "context_threshold": 0.85,
  "active_window": 5,
  "max_context_tokens": 71680,
  "gate_url": "http://gate:4000",
  "stall_timeout": 600000000000,
  "snapshot_interval": 10,
  "max_reversal_depth": 5,
  "shed_tool_output_max_chars": 500,
  "gate_model": "gemma4:31b-cloud"
}
```

- [ ] **Step 4: Add `TALOS_MODEL` env var mapping to SpineConfig**

In `talos/spine/config.py`, modify `load_config` to also check the `TALOS_MODEL` environment variable as an override. Change the function to:

```python
def load_config(path: str) -> SpineConfig:
    cfg = SpineConfig()
    config_file = Path(path)
    if config_file.exists():
        data = json.loads(config_file.read_text())
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    env_model = os.environ.get("TALOS_MODEL")
    if env_model:
        cfg.gate_model = env_model
    return cfg
```

Also add `import os` at the top of the file.

- [ ] **Step 5: Commit**

```bash
cd talos && git add spine/config.py spine/stream.py && git commit -m "feat: add gate_model to SpineConfig, use it in StreamManager payload"
cd .. && git add spine_config.json && git commit -m "feat: set default gate_model to gemma4:31b-cloud in spine_config.json"
```

---

### Task 2: Add Ollama backend to Gate routing

**Files:**
- Modify: `gate/app.py` (add ollama backend, update MODEL_MAP, update health check, update /v1/models)

- [ ] **Step 1: Add Ollama backend and model map entries**

In `gate/app.py`, update the `BACKENDS` dict to add Ollama:

```python
BACKENDS = {
    "local": "http://llamacpp:8080/v1/chat/completions",
    "ollama": "http://host.docker.internal:11434/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
    "together_images": "https://api.together.xyz/v1/images/generations",
    "together_audio": "https://api.together.xyz/v1/audio/transcriptions",
}
```

Update `MODEL_MAP` to add Ollama models:

```python
MODEL_MAP = {
    # Local llamacpp models
    "gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf": "local",
    "gemma-4-31B-it-UD-Q4_K_XL.gguf": "local",
    "Qwen3.5-27B-Q4_K_M.gguf": "local",
    "mistralai_Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf": "local",
    # Ollama models
    "gemma4:31b-cloud": "ollama",
    "minimax-m2.7:cloud": "ollama",
    "glm-5.1:cloud": "ollama",
}
```

- [ ] **Step 2: Add Ollama auth header handling**

In the `chat_completions` function, after the existing `together` auth header block (around line 185), add:

```python
    if backend_key == "ollama":
        # Ollama doesn't require auth headers, but needs the model field as-is
        pass  # No extra headers needed
```

This is a no-op but makes the routing explicit. Actually, since `pass` is a no-op, we can omit this entirely. The existing code already works: no auth headers are added for `"local"`, and the same pattern applies to `"ollama"`.

- [ ] **Step 3: Update the health check endpoint**

Replace the existing `/health` endpoint (starting around line 418) with:

```python
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
            r = await client.get("http://host.docker.internal:11434/api/tags", timeout=2.0)
            ollama_ok = r.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        pass

    status = "healthy" if (local_ok or ollama_ok) else "unhealthy"
    return {
        "status": status,
        "engine": "Talos Gate",
        "local_engine_ready": local_ok,
        "ollama_ready": ollama_ok,
        "current_spend": f"{get_current_spend():.4f}/{DAILY_BUDGET_LIMIT:.4f}"
    }
```

- [ ] **Step 4: Update /v1/models to include Ollama models**

In the `list_models` function, add Ollama model listing after the local llamacpp section (after `except` block around line 364) and before the Together AI section:

```python
    # 2. Add Ollama models
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://host.docker.internal:11434/api/tags", timeout=5.0)
            if resp.status_code == 200:
                ollama_data = resp.json()
                for m in ollama_data.get("models", []):
                    unified_models.append({
                        "id": m.get("name", "ollama-model"),
                        "context_window": LOCAL_CONTEXT_WINDOW,
                        "cost_per_m_in": 0.0,
                        "cost_per_m_out": 0.0,
                        "modalities": ["text"]
                    })
    except (httpx.RequestError, httpx.TimeoutException):
        pass  # Ollama not reachable
```

Also update the comment numbering: section 2 was "Together AI models", now it's section 3.

- [ ] **Step 5: Add OLLAMA_HOST env var for configurable endpoint**

In the configuration section near line 25, add:

```python
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "host.docker.internal:11434")
```

Then replace all hardcoded `host.docker.internal:11434` references in BACKENDS, health check, and /v1/models with `OLLAMA_HOST`:

```python
BACKENDS = {
    "local": "http://llamacpp:8080/v1/chat/completions",
    "ollama": f"http://{OLLAMA_HOST}/v1/chat/completions",
    ...
}
```

And in health check and /v1/models:
```python
    r = await client.get(f"http://{OLLAMA_HOST}/api/tags", timeout=2.0)
```

```python
    r = await client.get(f"http://{OLLAMA_HOST}/api/tags", timeout=5.0)
```

- [ ] **Step 6: Commit**

```bash
git add gate/app.py
git commit -m "feat: add Ollama backend routing, health check, and model listing to Gate"
```

---

### Task 3: Update docker-compose.yml for host networking

**Files:**
- Modify: `docker-compose.yml` (add `extra_hosts` to gate service)

- [ ] **Step 1: Add extra_hosts to gate service**

In `docker-compose.yml`, add `extra_hosts` to the `gate` service, after `environment:`:

```yaml
  gate:
    build:
      context: ./gate
      dockerfile: Dockerfile
    container_name: talos_gate
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "4000:4000"
```

- [ ] **Step 2: Validate compose config**

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.rocm.yml config 2>&1 | head -5
```

Expected: No errors, valid config output.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add host.docker.internal to gate service for Ollama access"
```

---

### Task 4: Update talosctl for Ollama-aware startup

**Files:**
- Modify: `talosctl` (add Ollama health check, skip llamacpp if no model, pull model)

- [ ] **Step 1: Add Ollama health check function**

In `talosctl`, after the `check_spine_healthy` function, add:

```python
def check_ollama_healthy():
    """Check if Ollama is reachable and has models available."""
    import socket
    ollama_host = os.environ.get("OLLAMA_HOST", "localhost:11434")
    host, port = ollama_host.rsplit(":", 1)
    try:
        sock = socket.create_connection((host, int(port)), timeout=3)
        sock.close()
        return True
    except:
        return False

def ollama_pull_model(model_name):
    """Try to pull an Ollama model if not already available."""
    try:
        result = subprocess.run(
            ["ollama", "pull", model_name],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

def ollama_has_model(model_name):
    """Check if Ollama has a specific model."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5
        )
        return model_name in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
```

Also add `import socket` near the top of the file if not already present.

- [ ] **Step 2: Modify `run_daemon` to handle Ollama-only starts**

In the `run_daemon` function, after the compose args setup and before starting llamacpp, add logic to skip llamacpp when using an Ollama model:

Replace the llamacpp startup block:

```python
    res = subprocess.run(f"docker compose {compose_args} up -d llamacpp", ...)
```

With:

```python
    env = load_env(RUNTIME_DIR / ".env")
    model = env.get("DEFAULT_MODEL", "")
    needs_llamacpp = model.endswith(".gguf") or not model

    if needs_llamacpp:
        res = subprocess.run(f"docker compose {compose_args} up -d llamacpp", shell=True, cwd=RUNTIME_DIR, capture_output=True, text=True)
        if res.returncode != 0 and "already in use" in res.stderr:
            print("[DAEMON] Removing stale containers...", flush=True)
            subprocess.run(f"docker compose {compose_args} down", shell=True, cwd=RUNTIME_DIR, capture_output=True)
            res = subprocess.run(f"docker compose {compose_args} up -d llamacpp", shell=True, cwd=RUNTIME_DIR, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"\033[91m[DAEMON] FATAL: docker compose up llamacpp failed:\033[0m", flush=True)
            print(res.stderr.strip(), flush=True)
            HEALTH_FILE.write_text(json.dumps({"status": "failed", "error": "llamacpp startup failed", "ts": time.time()}))
            if PID_FILE.exists(): PID_FILE.unlink()
            sys.exit(1)
    else:
        print("[DAEMON] No .gguf model configured, skipping llamacpp.", flush=True)

    # Check Ollama availability for cloud models
    talos_model = env.get("TALOS_MODEL", "")
    if ":" in talos_model:
        if not check_ollama_healthy():
            print("[DAEMON] WARNING: Ollama not reachable at localhost:11434", flush=True)
        elif not ollama_has_model(talos_model):
            print(f"[DAEMON] Pulling Ollama model: {talos_model}...", flush=True)
            ollama_pull_model(talos_model)
```

Make sure to keep the rest of the daemon logic (HEALTH_FILE writes, wait_for_spine_healthy, etc.) unchanged.

- [ ] **Step 3: Commit**

```bash
git add talosctl
git commit -m "feat: add Ollama-aware startup to talosctl (skip llamacpp, pull models)"
```

---

### Task 5: Update .env.example with Ollama and TALOS_MODEL docs

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add Ollama and model config**

Update the model section to document the new options:

```bash
# Default Model: determines which llamacpp compose overlay to use
# When set to a .gguf filename, llamacpp is started with that model
# When empty or not a .gguf, llamacpp is skipped
DEFAULT_MODEL=

# TALOS_MODEL: the model name sent to the Gate for LLM routing
# Ollama models (colon suffix):  gemma4:31b-cloud, minimax-m2.7:cloud, glm-5.1:cloud
# llamacpp models (.gguf):       gemma-4-31B-it-UD-Q4_K_XL.gguf
# Together AI models:             together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
TALOS_MODEL=gemma4:31b-cloud

# Ollama host (default: localhost:11434 for host access, host.docker.internal:11434 for Docker)
OLLAMA_HOST=host.docker.internal:11434
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add Ollama and TALOS_MODEL config to .env.example"
```

---

### Task 6: Write tests for Gate routing

**Files:**
- Create: `gate/test_routing.py`

- [ ] **Step 1: Write routing tests**

Create `gate/test_routing.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app import app, BACKENDS, MODEL_MAP


def test_model_map_llamacpp():
    assert MODEL_MAP["gemma-4-31B-it-UD-Q4_K_XL.gguf"] == "local"
    assert MODEL_MAP["gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf"] == "local"
    assert MODEL_MAP["Qwen3.5-27B-Q4_K_M.gguf"] == "local"


def test_model_map_ollama():
    assert MODEL_MAP["gemma4:31b-cloud"] == "ollama"
    assert MODEL_MAP["minimax-m2.7:cloud"] == "ollama"
    assert MODEL_MAP["glm-5.1:cloud"] == "ollama"


def test_backends_contain_ollama():
    assert "ollama" in BACKENDS
    assert "host.docker.internal" in BACKENDS["ollama"]


def test_routing_logic():
    """Test the routing decision in chat_completions."""
    # Local models
    assert MODEL_MAP.get("gemma-4-31B-it-UD-Q4_K_XL.gguf", "local") == "local"
    # Ollama models
    assert MODEL_MAP.get("gemma4:31b-cloud", "local") == "ollama"
    # Unknown model defaults to local
    assert MODEL_MAP.get("unknown-model", "local") == "local"
    # Together prefix
    model = "together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
    backend = "together" if "together" in model.lower() else MODEL_MAP.get(model, "local")
    assert backend == "together"
```

- [ ] **Step 2: Run tests**

```bash
cd gate && python3 -m pytest test_routing.py -v
```

Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add gate/test_routing.py
git commit -m "test: add Gate routing tests for llamacpp, Ollama, and Together AI"
```

---

### Task 7: Integration test — verify Ollama endpoint is reachable from Docker

**Files:**
- No new test files — manual verification

- [ ] **Step 1: Ensure Ollama is running on the host**

```bash
curl http://localhost:11434/api/tags
```

Expected: JSON response with model list

- [ ] **Step 2: Rebuild gate and test connectivity**

```bash
docker compose build gate
docker compose up -d gate
# Wait for healthy
sleep 5
curl http://localhost:4000/health
```

Expected: JSON with `"ollama_ready": true` (or `false` if Ollama isn't running — that's OK for now)

- [ ] **Step 3: Verify Gate model listing includes Ollama**

```bash
curl http://localhost:4000/v1/models
```

Expected: JSON with `data` array including Ollama model names if Ollama is running

- [ ] **Step 4: Verify talosctl startup detects Ollama model**

```bash
# Set TALOS_MODEL in .env
./talosctl status
```

Expected: Shows watchdog status, no errors about missing llamacpp when using Ollama model

- [ ] **Step 5: Push all changes**

```bash
git push origin feat/spine-cortex
cd talos && git push origin main
```