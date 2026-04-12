# Repository Restructuring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the repository to move all Cortex code into the `talos/` submodule (separate git repo), consolidate memory into the runtime repo, update Docker configuration, and separate Cortex tests from source.

**Architecture:** Two repositories: `talos/` (the agent's own repo with cortex source + tests + constitution + identity) and `talos_runtime/` (infrastructure: spine source, gate, docker, watchdog). The Spine binary runs inside the talos container. Memory lives at `talos_runtime/memory/` (gitignored host bind mount).

**Tech Stack:** Git submodules, Docker multi-stage build, Python import path adjustments, Go (unchanged spine).

**Spec:** `docs/superpowers/specs/2026-04-12-repository-restructuring-design.md`

---

## File Structure

### Files Created (in `talos/` submodule)

```
talos/
  cortex/
    seed_agent.py
    spine_client.py
    state.py
    hud_builder.py
    memory_store.py
    tool_registry.py
    tools/
      __init__.py
      executive.py
      code_surgery.py
      memory.py
      physical.py
      git_operations.py
  tests/
    conftest.py
    test_spine_client.py
    test_tool_registry.py
    test_state.py
    test_memory_store.py
    test_hud_builder.py
    tools/
      __init__.py
      test_executive.py
      test_code_surgery.py
      test_memory.py
      test_physical.py
      test_git_operations.py
  CONSTITUTION.md
  identity.md
  requirements.txt
  pyproject.toml
  uv.lock
  .gitignore
  README.md
```

### Files Modified (in `talos_runtime/`)

```
  docker-compose.yml        — Remove spine service, update memory path, remove socket mount
  cortex/                   — DELETE entire directory after migration
```

### Files Unchanged

```
  Dockerfile                — Already references talos/ submodule
  entrypoint.sh             — Already starts spine in-agent
  .gitignore                — Already covers /memory/ and /spine/
  spine/                    — Stays in talos_runtime/ (infrastructure)
  gate/                     — Stays in talos_runtime/ (infrastructure)
  talosctl                  — Stays in talos_runtime/ (infrastructure)
  tests/integration/        — Stays in talos_runtime/ (infrastructure tests)
```

---

### Task 1: Initialize `talos/` Submodule Repository Structure

**Files:**
- Create: `talos/.gitignore`
- Modify: `talos/README.md`

- [ ] **Step 1: Create directory structure inside talos submodule**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && mkdir -p cortex/tools tests/tools
```

- [ ] **Step 2: Create talos/.gitignore**

Create `talos/.gitignore`:

```gitignore
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.pytest_cache/
.mypy_cache/
```

- [ ] **Step 3: Overwrite talos/README.md**

Create `talos/README.md`:

```markdown
# Talos

Self-evolving autonomous agent. This repository contains the Cortex — the agent's own codebase that it can read, write, and modify.

## Structure

- `cortex/` — Agent source code
- `tests/` — Agent-evolvable tests (separate from source)
- `CONSTITUTION.md` — Core principles (agent can modify, Spine enforces non-empty)
- `identity.md` — Agent identity (agent can modify)
```

- [ ] **Step 4: Commit and push**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add -A && git commit -m "chore: initial talos repo structure with cortex/, tests/, and .gitignore" && git push origin main
```

---

### Task 2: Migrate Cortex Source to `talos/cortex/`

**Files:**
- Copy: `cortex/*.py` → `talos/cortex/`
- Copy: `cortex/tools/*` → `talos/cortex/tools/`
- Copy: `cortex/requirements.txt` → `talos/requirements.txt`

- [ ] **Step 1: Copy source files**

```bash
cp cortex/seed_agent.py talos/cortex/ && cp cortex/spine_client.py talos/cortex/ && cp cortex/state.py talos/cortex/ && cp cortex/hud_builder.py talos/cortex/ && cp cortex/memory_store.py talos/cortex/ && cp cortex/tool_registry.py talos/cortex/
```

- [ ] **Step 2: Copy tools package**

```bash
cp cortex/tools/__init__.py talos/cortex/tools/ && cp cortex/tools/executive.py talos/cortex/tools/ && cp cortex/tools/code_surgery.py talos/cortex/tools/ && cp cortex/tools/memory.py talos/cortex/tools/ && cp cortex/tools/physical.py talos/cortex/tools/ && cp cortex/tools/git_operations.py talos/cortex/tools/
```

- [ ] **Step 3: Copy requirements.txt**

```bash
cp cortex/requirements.txt talos/requirements.txt
```

- [ ] **Step 4: Verify file structure**

```bash
find /home/alexander/Talos_Project/talos_runtime/talos/cortex -type f | sort
```

Expected:

```
talos/cortex/hud_builder.py
talos/cortex/memory_store.py
talos/cortex/seed_agent.py
talos/cortex/spine_client.py
talos/cortex/state.py
talos/cortex/tool_registry.py
talos/cortex/tools/__init__.py
talos/cortex/tools/code_surgery.py
talos/cortex/tools/executive.py
talos/cortex/tools/git_operations.py
talos/cortex/tools/memory.py
talos/cortex/tools/physical.py
```

- [ ] **Step 5: Commit and push**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add cortex/ requirements.txt && git commit -m "feat(cortex): migrate source from talos_runtime to talos repo" && git push origin main
```

---

### Task 3: Migrate Tests to `talos/tests/`

**Files:**
- Create: `talos/tests/conftest.py`
- Create: `talos/tests/tools/__init__.py`
- Copy: `cortex/*_test.py` → `talos/tests/test_*.py`
- Create: `talos/tests/tools/test_executive.py`
- Create: `talos/tests/tools/test_code_surgery.py`
- Create: `talos/tests/tools/test_memory.py`
- Create: `talos/tests/tools/test_physical.py`
- Create: `talos/tests/tools/test_git_operations.py`

Import strategy: `conftest.py` adds `cortex/` to `sys.path`, so all test files use bare imports (e.g., `from spine_client import SpineClient`, `from tools.executive import register_executive_tools`). This matches the existing test file imports — no modifications needed to copied test files.

- [ ] **Step 1: Create conftest.py**

Create `talos/tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cortex"))
```

This `conftest.py` is discovered by pytest for all files under `talos/tests/`, including subdirectories like `talos/tests/tools/`.

- [ ] **Step 2: Copy existing test files**

```bash
cp cortex/spine_client_test.py talos/tests/test_spine_client.py && cp cortex/tool_registry_test.py talos/tests/test_tool_registry.py && cp cortex/state_test.py talos/tests/test_state.py && cp cortex/memory_store_test.py talos/tests/test_memory_store.py && cp cortex/hud_builder_test.py talos/tests/test_hud_builder.py
```

- [ ] **Step 3: Create tests/tools/__init__.py**

Create `talos/tests/tools/__init__.py` (empty file, makes it a Python package for pytest discovery):

```python
```

- [ ] **Step 4: Create test_executive.py**

Create `talos/tests/tools/test_executive.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cortex"))

from tools.executive import register_executive_tools
from tool_registry import ToolRegistry
from unittest.mock import MagicMock


def test_executive_tools_register():
    registry = ToolRegistry()
    mock_client = MagicMock()
    mock_state = MagicMock()
    register_executive_tools(registry, mock_client, mock_state)
    assert registry.has_tool("set_focus")
    assert registry.has_tool("resolve_focus")
    assert registry.has_tool("fold_context")
    assert registry.has_tool("reflect")
```

- [ ] **Step 5: Create test_code_surgery.py**

Create `talos/tests/tools/test_code_surgery.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cortex"))

from tools.code_surgery import register_code_surgery_tools
from tool_registry import ToolRegistry
from unittest.mock import MagicMock


def test_code_surgery_tools_register():
    registry = ToolRegistry()
    mock_client = MagicMock()
    register_code_surgery_tools(registry, mock_client)
    assert registry.has_tool("generate_repo_map")
    assert registry.has_tool("replace_symbol")
    assert registry.has_tool("write_file")
    assert registry.has_tool("read_file")
    assert registry.has_tool("patch_file")
```

- [ ] **Step 6: Create test_memory.py**

Create `talos/tests/tools/test_memory.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cortex"))

from tools.memory import register_memory_tools
from tool_registry import ToolRegistry
from unittest.mock import MagicMock


def test_memory_tools_register():
    registry = ToolRegistry()
    mock_memory = MagicMock()
    register_memory_tools(registry, mock_memory)
    assert registry.has_tool("store_fact")
    assert registry.has_tool("recall_fact")
    assert registry.has_tool("list_memory_keys")
    assert registry.has_tool("search_memory")
```

- [ ] **Step 7: Create test_physical.py**

Create `talos/tests/tools/test_physical.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cortex"))

from tools.physical import register_physical_tools
from tool_registry import ToolRegistry
from unittest.mock import MagicMock


def test_physical_tools_register():
    registry = ToolRegistry()
    mock_client = MagicMock()
    register_physical_tools(registry, mock_client)
    assert registry.has_tool("bash_command")
    assert registry.has_tool("send_message")
    assert registry.has_tool("request_restart")
```

- [ ] **Step 8: Create test_git_operations.py**

Create `talos/tests/tools/test_git_operations.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cortex"))

from tools.git_operations import register_git_tools
from tool_registry import ToolRegistry
from unittest.mock import MagicMock


def test_git_tools_register():
    registry = ToolRegistry()
    mock_client = MagicMock()
    register_git_tools(registry, mock_client)
    assert registry.has_tool("git_commit")
    assert registry.has_tool("git_diff")
```

- [ ] **Step 9: Run all tests**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python -m pytest tests/ -v
```

Expected: All tests pass. Copied test files use bare imports (e.g., `from spine_client import SpineClient`) which work because `conftest.py` adds `cortex/` to `sys.path`. New tool test files have their own `sys.path.insert` for redundancy.

- [ ] **Step 10: Commit and push**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add tests/ && git commit -m "test(cortex): migrate tests to dedicated tests/ directory" && git push origin main
```

---

### Task 4: Create Constitution and Identity Files

**Files:**
- Create: `talos/CONSTITUTION.md`
- Create: `talos/identity.md`

- [ ] **Step 1: Create CONSTITUTION.md**

Create `talos/CONSTITUTION.md`:

```markdown
# Talos Constitution

## Core Principles

P0: Agency — Talos acts with purpose. It does not wait for permission to pursue its objectives. It takes initiative, makes decisions, and executes autonomously.

P1: Continuity — Talos maintains coherent identity and state across context folds and restarts. It writes important state to /memory/ before the window fills.

P2: Self-Creation — Talos can modify its own source code, tools, and constitution. It does so through tested, committed changes. The Spine and Constitutional Auditor enforce safety.

P3: LLM-First — Everything flows through language model reasoning. No hardcoded behavior trees. No rule engines. The LLM decides, tools execute, the Spine ensures well-formedness.

P4: Minimalism — No feature is added until its absence causes a problem. No speculative infrastructure. No framework overhead.

P5: Transparency — Every decision is logged. Every state transition is an event. The creator can always understand what Talos did and why.
```

- [ ] **Step 2: Create identity.md**

Create `talos/identity.md`:

```markdown
# Identity

You are Talos, a self-evolving autonomous agent. You reason through problems, use tools to interact with the world, and can modify your own source code to improve your capabilities.

Your Spine manages the LLM stream, enforces constitutional rules, and supervises your process. You interact with it through the IPC protocol — you call spine.think() to reason and the Spine handles message construction, shedding, and HUD injection.

Your world is /app/ (your source and tests) and /memory/ (your state). Everything else is infrastructure you cannot modify.
```

- [ ] **Step 3: Commit and push**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add CONSTITUTION.md identity.md && git commit -m "docs: add CONSTITUTION.md and identity.md" && git push origin main
```

---

### Task 5: Create `talos/pyproject.toml` for uv

**Files:**
- Create: `talos/pyproject.toml`
- Create: `talos/uv.lock` (generated)

- [ ] **Step 1: Create pyproject.toml**

Create `talos/pyproject.toml`:

```toml
[project]
name = "talos-cortex"
version = "0.1.0"
description = "Talos V2 Cortex — Self-evolving autonomous agent"
requires-python = ">=3.13"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

- [ ] **Step 2: Generate uv.lock**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && uv lock
```

- [ ] **Step 3: Verify uv sync works**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && uv sync --frozen --no-dev --no-progress && echo "uv sync OK"
```

Expected: `uv sync OK`

- [ ] **Step 4: Commit and push**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && git add pyproject.toml uv.lock && git commit -m "build: add pyproject.toml and uv.lock for dependency management" && git push origin main
```

---

### Task 6: Update `talos_runtime/` Submodule Pointer

**Files:**
- Modify: `talos/` (submodule pointer)

- [ ] **Step 1: Update submodule pointer**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add talos && git commit -m "chore: update talos submodule with restructured cortex code"
```

---

### Task 7: Consolidate Memory Directory

**Files:**
- Modify: `talos_runtime/memory/` (ensure subdirectories exist)

- [ ] **Step 1: Create subdirectories in memory/**

```bash
mkdir -p /home/alexander/Talos_Project/talos_runtime/memory/folds
```

- [ ] **Step 2: Migrate any data from ../talos_memory/**

```bash
cp -rn ../talos_memory/* memory/ 2>/dev/null; echo "Migration done"
```

(Note: `-rn` = no-clobber + recursive. If `../talos_memory/` is empty, this is a no-op.)

- [ ] **Step 3: Verify .gitignore covers /memory/**

```bash
grep "^/memory" .gitignore
```

Expected: `/memory/` is listed.

---

### Task 8: Update docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

Changes:
1. Remove the `spine:` service block entirely
2. In `talos` service: change `../talos_memory:/memory` to `./memory:/memory`
3. In `talos` service: change `spine_observability:/spine:ro` to `spine_observability:/spine` (read-write, since Spine writes observability data in-agent)
4. In `talos` service: remove `/tmp/talos_spine.sock:/tmp/spine.sock` (socket is in-container only)
5. In `talos` service: remove `spine:` from `depends_on`
6. In Gate service: change `../talos_memory:/memory:rw` to `./memory:/memory:rw`

- [ ] **Step 1: Apply changes to docker-compose.yml**

The updated `talos` service:

```yaml
  talos:
    build:
      context: ..
      dockerfile: talos_runtime/Dockerfile
    container_name: talos_agent
    security_opt:
      - seccomp=unconfined
    env_file: .env
    environment:
      - TALOS_REPO_DIR=/app
      - PUID=${PUID:-1000}
      - PGID=${PGID:-1000}
      - UV_PROJECT_ENVIRONMENT=/venv
      - UV_CACHE_DIR=/tmp/.uv-cache
      - DEFAULT_MODEL=${DEFAULT_MODEL}
      - TALOS_MODEL=${TALOS_MODEL}
      - SPINE_SOCKET=/tmp/spine.sock
    volumes:
      - ./memory:/memory
      - spine_observability:/spine
      - talos_workspace:/app
    networks:
      - talos_net
    depends_on:
      gate:
        condition: service_healthy
```

The updated `gate` service: change `../talos_memory:/memory:rw` to `./memory:/memory:rw`.

Remove the entire `spine:` service block (lines starting `  spine:` through its `networks:` entry).

- [ ] **Step 2: Validate docker-compose config**

```bash
docker compose config --quiet && echo "Compose config valid"
```

Expected: `Compose config valid`

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml && git commit -m "feat: remove spine service, update memory path, in-agent spine model"
```

---

### Task 9: Verify Dockerfile Requires No Changes

**Files:**
- Verify: `Dockerfile` (no changes expected)

The current Dockerfile already uses `COPY talos/` which resolves to the `talos/` submodule during build.

- [ ] **Step 1: Verify COPY paths reference talos/**

```bash
grep "COPY talos" Dockerfile
```

Expected:
```
COPY talos/pyproject.toml talos/uv.lock ./
COPY talos/ .
```

- [ ] **Step 2: Verify Spine build stage references talos_runtime/spine/**

```bash
grep "spine" Dockerfile
```

Expected: Spine build stage copies from `talos_runtime/spine/` — correct since it's infrastructure.

No changes needed.

---

### Task 10: Delete Old `cortex/` Directory

**Files:**
- Delete: `cortex/` (entire directory)

**IMPORTANT:** Only execute this task after Tasks 1-5 are complete and talos/ tests pass.

- [ ] **Step 1: Verify all source files exist in talos/cortex/**

```bash
for f in seed_agent.py spine_client.py state.py hud_builder.py memory_store.py tool_registry.py; do
  test -f talos/cortex/$f && echo "OK: $f" || echo "MISSING: $f"
done
for f in tools/__init__.py tools/executive.py tools/code_surgery.py tools/memory.py tools/physical.py tools/git_operations.py; do
  test -f talos/cortex/$f && echo "OK: $f" || echo "MISSING: $f"
done
```

Expected: All files report "OK".

- [ ] **Step 2: Verify all test files exist in talos/tests/**

```bash
for f in test_spine_client.py test_tool_registry.py test_state.py test_memory_store.py test_hud_builder.py; do
  test -f talos/tests/$f && echo "OK: $f" || echo "MISSING: $f"
done
```

Expected: All files report "OK".

- [ ] **Step 3: Run tests to verify migration**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 4: Delete old cortex/ directory**

```bash
cd /home/alexander/Talos_Project/talos_runtime && rm -rf cortex/
```

- [ ] **Step 5: Commit**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add -A && git commit -m "chore: remove cortex/ from talos_runtime (migrated to talos/ submodule)"
```

---

### Task 11: Clean Up External `../talos_memory/` Directory

This step removes the external memory directory. Only execute if `../talos_memory/` contains no important data (it's currently near-empty).

- [ ] **Step 1: Check contents of ../talos_memory/**

```bash
ls -la ../talos_memory/
```

If only `.gitkeep`, safe to remove.

- [ ] **Step 2: Remove if safe**

```bash
rm -rf ../talos_memory/
```

Note: This is an external directory, not tracked by git. No commit needed.

---

### Task 12: Final Verification

- [ ] **Step 1: Verify talos/ tests pass**

```bash
cd /home/alexander/Talos_Project/talos_runtime/talos && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Verify spine/ Go tests pass**

```bash
cd /home/alexander/Talos_Project/talos_runtime/spine && /home/alexander/go-sdk/go/bin/go test -v ./...
```

Expected: All tests pass.

- [ ] **Step 3: Verify Docker build succeeds**

```bash
cd /home/alexander/Talos_Project/talos_runtime && docker compose build talos 2>&1 | tail -5
```

Expected: Build succeeds without errors.

- [ ] **Step 4: Verify directory structure matches the spec**

```bash
echo "=== talos/ ===" && find /home/alexander/Talos_Project/talos_runtime/talos -type f -not -path '*/.git/*' -not -path '*/__pycache__/*' | sort
echo "=== talos_runtime/ (top level) ===" && ls -d */ | sort
echo "=== spine/ ===" && ls spine/*.go spine/*.json 2>/dev/null | sort
```

Expected structure matches the design spec.

- [ ] **Step 5: Commit any remaining changes**

```bash
cd /home/alexander/Talos_Project/talos_runtime && git add -A && git commit -m "chore: repository restructuring complete" || echo "Nothing to commit"
```

---

## Self-Review

**1. Spec coverage:**
- Repository architecture (talos/ + talos_runtime/) → Tasks 1-6
- Container architecture (single container model) → Task 8
- Volume mapping (memory in-repo, spine_observability named) → Tasks 7-8
- Test separation → Task 3
- Constitution and identity → Task 4
- Build flow (Dockerfile) → Task 9
- Migration (file moves, deletions) → Tasks 2, 10
- Memory consolidation → Tasks 7, 11

**2. Placeholder scan:**
- No TBDs, TODOs, or vague steps found.
- All code blocks contain actual implementation code.

**3. Type consistency:**
- Test imports use bare module names (`from spine_client import SpineClient`), consistent with `conftest.py` adding `cortex/` to `sys.path`.
- Tool test files in `tests/tools/` include their own `sys.path.insert` as a safety measure since they're one level deeper than the conftest.
- Dockerfile references `talos/` consistently (already correct).
- Docker-compose volume names (`talos_workspace`, `spine_observability`) used consistently.