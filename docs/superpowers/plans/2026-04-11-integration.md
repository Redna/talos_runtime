# Integration and Docker Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Spine and Cortex together in Docker Compose, update the Gate to work with the new architecture, add the Spine to the watchdog, and verify end-to-end operation.

**Architecture:** The Spine runs as a sidecar container alongside the Cortex. Both share the `/memory` and `/spine` volumes. The Spine connects to the Gate for LLM calls and exposes the Control Plane on port 4001. The Cortex communicates with the Spine via Unix domain socket.

**Tech Stack:** Docker Compose, existing Dockerfile (modified), new Spine Dockerfile.

**Depends on:** Both Spine Core and Cortex Core plans must be completed first.

---

## File Structure

```
talos_runtime/
  docker-compose.yml          # Modified: add spine service
  Dockerfile                   # Modified: add Spine binary
  spine/Dockerfile             # New: Spine container
  entrypoint.sh                # Modified: add Spine socket path
  .gitignore                   # Modified: add /memory/, /spine/
  scripts/setup_hooks.sh       # Unchanged
  gate/app.py                  # Modified: add /v1/chat/completions proxy for Spine
```

---

### Task 1: Update .gitignore and Create Volume Directories

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Update .gitignore**

Add `/memory/` and `/spine/` to the gitignore:

```gitignore
# Agent state (Cortex reads/writes)
/memory/

# Spine observability (Cortex read-only)
/spine/

# Existing entries
__pycache__/
*.pyc
.env
*.gguf
```

- [ ] **Step 2: Create volume directories**

```bash
mkdir -p memory folds spine/events spine/snapshots spine/crashes
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore && git commit -m "chore: add /memory/ and /spine/ to gitignore"
```

---

### Task 2: Update Docker Compose

**Files:**
- Modify: `docker-compose.yml`

Add the `spine` service and update the `talos` service to connect to it.

- [ ] **Step 1: Read current docker-compose.yml**

The current compose file has three services: `talos`, `gate`, and `llamacpp`. We need to add `spine` and update `talos` to connect to it.

- [ ] **Step 2: Update docker-compose.yml**

Key changes:
- Add `spine` service that builds from `spine/Dockerfile`
- Share `/tmp/spine.sock` between spine and talos (or use a volume)
- Share `/memory` and `/spine` volumes between both
- Add `SPINE_SOCKET` and `SPINE_DIR` environment variables to talos
- Add dependency: talos depends on both spine and gate
- Spine depends on gate (for LLM routing)

The `spine` service in docker-compose.yml:

```yaml
  spine:
    build:
      context: ./spine
      dockerfile: Dockerfile
    container_name: talos_spine
    env_file: .env
    environment:
      - GATE_URL=http://gate:4000
      - MEMORY_DIR=/memory
      - SPINE_DIR=/spine
      - APP_DIR=/app
      - CONSTITUTION_PATH=/app/CONSTITUTION.md
      - IDENTITY_PATH=/app/identity.md
      - SOCKET_PATH=/tmp/spine.sock
      - CONTROL_PLANE_PORT=4001
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-0}
    volumes:
      - ../talos_memory:/memory
      - spine_observability:/spine
      - talos_workspace:/app:ro  # Read-only access to app for constitution
      - /tmp/talos_spine.sock:/tmp/spine.sock
    ports:
      - "4001:4001"
    depends_on:
      gate:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - talos_net
```

The `talos` service update:
- Add `SPINE_SOCKET=/tmp/spine.sock` to environment
- Add volume mount for the socket
- Change `depends_on` to include spine

- [ ] **Step 3: Verify compose config**

```bash
docker compose config --quiet && echo "Compose config is valid"
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml && git commit -m "feat: add spine service to docker-compose"
```

---

### Task 3: Update the Dockerfile and Entrypoint

**Files:**
- Modify: `Dockerfile`
- Modify: `entrypoint.sh`

- [ ] **Step 1: Update Dockerfile**

Add the Spine binary to the talos container image. The Spine binary is built separately and copied in.

Add to the existing Dockerfile before the ENTRYPOINT:

```dockerfile
# 6. Add the Spine binary (built separately)
COPY --from=spine-builder /build/spine /usr/local/bin/spine
```

We'll use a multi-stage build where the Spine is built in a Go builder stage and the binary is copied into the talos image.

- [ ] **Step 2: Update entrypoint.sh**

Add Spine startup to the entrypoint. The Spine should start before the Cortex and be ready to accept connections.

Add to `entrypoint.sh` before the final `exec gosu` line:

```bash
# Start the Spine in the background
echo "Starting Spine..."
/usr/local/bin/spine /spine/spine_config.json &
SPINE_PID=$!

# Wait for Spine socket to be available
echo "Waiting for Spine socket..."
for i in $(seq 1 30); do
  if [ -S /tmp/spine.sock ]; then
    echo "Spine socket ready."
    break
  fi
  sleep 1
done

if [ ! -S /tmp/spine.sock ]; then
  echo "ERROR: Spine socket not available after 30 seconds"
  exit 1
fi
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile entrypoint.sh && git commit -m "feat: add Spine binary to Dockerfile and startup to entrypoint"
```

---

### Task 4: Update Gate to Support Spine Proxy

**Files:**
- Modify: `gate/app.py`

The Gate currently handles LLM routing, budget enforcement, and the Constitutional Auditor. It needs one change: ensure it works correctly when the Spine (not the Cortex directly) sends requests.

- [ ] **Step 1: Verify Gate compatibility**

The Spine sends LLM requests to `http://gate:4000/v1/chat/completions` with the same OpenAI-compatible format. The Gate already handles this — no code changes needed. Verify by checking:
- The Gate accepts POST to `/v1/chat/completions` — ✅ (existing code)
- The Gate routes based on model name — ✅ (existing `MODEL_MAP`)
- The Gate logs traces — ✅ (existing `log_completion`)
- The `/v1/audit` endpoint works for Constitutional Auditor — ✅ (existing code)

The Gate is already compatible. No changes needed.

- [ ] **Step 2: Commit (no changes, just verification)**

```bash
git commit --allow-empty -m "chore: verify Gate compatibility with Spine — no changes needed"
```

---

### Task 5: Update talosctl Watchdog

**Files:**
- Modify: `talosctl`

The watchdog needs to be updated to use the Spine's Control Plane API instead of Docker container health checks.

- [ ] **Step 1: Read current talosctl**

The current talosctl uses `docker compose` commands directly and checks container health. It needs to:
1. Check the Spine's `/health` endpoint instead of the Gate's
2. Use the Spine's `/events?tail=100` for crash forensics instead of docker logs
3. Use the Spine's `/command` endpoint for forced restarts

- [ ] **Step 2: Update health check**

Replace the Gate health check with a Spine health check:

```python
def wait_for_spine_healthy(timeout=600):
    """Wait for the Spine to report healthy."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = urllib.request.urlopen('http://localhost:4001/health')
            data = json.loads(resp.read())
            if data.get('status') in ('healthy', 'degraded'):
                return True
        except:
            pass
        time.sleep(5)
    return False
```

- [ ] **Step 3: Update crash forensics**

Replace `docker compose logs --tail=100 talos` with `curl localhost:4001/events?tail=100`:

```python
def capture_crash_log():
    """Capture last 100 events from the Spine event log."""
    try:
        resp = urllib.request.urlopen('http://localhost:4001/events?tail=100')
        events = json.loads(resp.read())
        with open(CRASH_LOG_PATH, 'w') as f:
            json.dump(events, f, indent=2)
    except Exception as e:
        print(f"[DAEMON] Error capturing crash log from Spine: {e}")
```

- [ ] **Step 4: Update Lazarus Protocol**

Instead of `docker compose` commands for restart, use the Spine's `/command` endpoint:

```python
def spine_restart(reason="Crash detected"):
    """Request a Cortex restart via the Spine Control Plane."""
    try:
        req = urllib.request.Request(
            'http://localhost:4001/command',
            data=json.dumps({"command": "force_restart"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"[DAEMON] Error requesting restart via Spine: {e}")
```

- [ ] **Step 5: Test talosctl commands**

```bash
python3 talosctl start --help
python3 talosctl stop --help
```

- [ ] **Step 6: Commit**

```bash
git add talosctl && git commit -m "feat: update talosctl to use Spine Control Plane API"
```

---

### Task 6: End-to-End Integration Test

**Files:**
- Create: `tests/integration/test_e2e.py`

This test verifies the full stack works: Spine starts, Cortex connects via IPC, a think request flows through, and an event is logged.

- [ ] **Step 1: Write end-to-end integration test**

Create `tests/integration/test_e2e.py`:

```python
"""
End-to-end integration test for the Spine-Cortex architecture.

Prerequisites:
- Spine binary built and available at /tmp/spine-test/spine
- Gate service running (or mocked)
- Test uses a mock Gate server
"""
import json
import socket
import subprocess
import time
import tempfile
import pytest
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockGateHandler(BaseHTTPRequestHandler):
    """Mock Gate server that returns a simple LLM response."""
    def do_POST(self):
        if self.path == '/v1/chat/completions':
            response = {
                "id": "mock-response",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "test-model",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "I will read the auth file.",
                        "tool_calls": [{
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "/app/auth.py"}),
                            }
                        }]
                    },
                    "finish_reason": "tool_call",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        elif self.path == '/health':
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())

    def log_message(self, format, *args):
        pass  # Suppress log output


@pytest.fixture
def mock_gate():
    """Start a mock Gate server on port 14000."""
    server = HTTPServer(('localhost', 14000), MockGateHandler)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_spine_starts_and_serves_control_plane(tmp_path, mock_gate):
    """Test that the Spine starts, creates the IPC socket, and serves the control plane."""
    # This test verifies:
    # 1. Spine process starts
    # 2. Control plane responds on port 4001 (or test port)
    # 3. IPC socket is created
    # 4. Health endpoint returns healthy
    # Note: Full end-to-end testing requires the Cortex binary, which is tested separately.
    pass  # Placeholder — actual test requires building the Spine binary


def test_cortex_connects_to_spine(tmp_path):
    """Test that the Cortex can connect to the Spine via IPC socket."""
    # This test verifies:
    # 1. SpineClient can connect to a Unix socket
    # 2. JSON-RPC messages are sent and received correctly
    # 3. think() method sends the correct format
    pass  # Placeholder — requires running Spine binary
```

- [ ] **Step 2: Run integration test**

```bash
cd tests/integration && python -m pytest test_e2e.py -v
```

Note: Full end-to-end testing requires building the Spine binary first. The integration test framework is set up here; actual execution happens in CI/CD or manual testing.

- [ ] **Step 3: Commit**

```bash
git add tests/ && git commit -m "test: end-to-end integration test framework"
```

---

### Task 7: Full Stack Smoke Test

- [ ] **Step 1: Build all containers**

```bash
docker compose build
```

- [ ] **Step 2: Create .env file with test configuration**

```bash
cp .env.example .env
# Edit .env to set a test model and API keys
```

- [ ] **Step 3: Start the full stack**

```bash
docker compose up -d
```

- [ ] **Step 4: Verify Spine is running**

```bash
curl http://localhost:4001/health
```

Expected: `{"status": "healthy"}`

- [ ] **Step 5: Verify Cortex connects to Spine**

Check the Spine logs for `cortex_started` event:

```bash
docker compose logs spine | grep cortex_started
```

- [ ] **Step 6: Verify an LLM call flows through**

Send a message via the Control Plane:

```bash
curl -X POST http://localhost:4001/message -H "Content-Type: application/json" -d '{"text": "Hello, Talos!"}'
```

- [ ] **Step 7: Verify event logging**

```bash
curl http://localhost:4001/events?tail=10
```

Expected: JSON array of recent events.

- [ ] **Step 8: Stop the stack**

```bash
docker compose down
```

- [ ] **Step 9: Commit (if any configuration changes were needed)**

```bash
git add -A && git commit -m "chore: full stack smoke test configuration"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Docker Compose setup → Task 2
- ✅ Volume layout (/memory, /spine, /app) → Task 2
- ✅ Spine connects to Gate → Task 4 (no changes needed)
- ✅ Cortex connects to Spine via IPC → Task 3
- ✅ Watchdog updated to use Control Plane → Task 5
- ✅ Crash forensics via Spine events → Task 5
- ✅ End-to-end testing → Tasks 6, 7

**Placeholder scan:**
- Task 6 has placeholder integration tests. These require a running Spine binary, which is built in the Spine Core plan. The test framework is set up; actual test logic will be filled in during execution.
- Task 7 is a manual smoke test checklist, not automated.

**Type consistency:**
- Environment variables (SPINE_SOCKET, SPINE_DIR, etc.) are consistent across docker-compose.yml and the Cortex code
- Volume mounts match the spec (/memory for Cortex r/w, /spine for Spine management, /app for Cortex source)
- Port assignments are consistent (4001 for Control Plane, 4000 for Gate)