# Talos Operating Branch Spec

**Date:** 2026-04-17
**Status:** Draft
**Scope:** Git branch workflow for the autonomous agent inside the Talos container.

---

## 1. Overview

The agent operates exclusively on a dedicated git branch (`feat/talos`). This isolates the agent's work from host infrastructure code (`main`) and provides a clean, incremental history of the agent's evolution.

## 2. Branch Model

| Branch | Owner | Purpose |
|--------|-------|---------|
| `feat/talos` | Agent | Agent's exclusive working branch. All commits, all pushes. |
| `main` | Host | Infrastructure (Docker, Gate, watchdog). Agent never touches it. |

## 3. Container Startup Sequence

On every container start (including restarts), the entrypoint script ensures the container runs on `feat/talos`. The remote for `feat/talos` is assumed to be `origin` (standard convention; if different, update `GIT_REMOTE` in the entrypoint):

```
GIT_REMOTE=${GIT_REMOTE:-origin}
GIT_BRANCH=${GIT_BRANCH:-feat/talos}

1. git checkout $GIT_BRANCH    # Switch to agent branch
   └─ If branch doesn't exist: git checkout -b $GIT_BRANCH
2. git pull --rebase $GIT_REMOTE/$GIT_BRANCH   # Sync remote progress
3. git status --porcelain      # Check for uncommitted files
   └─ If dirty: git checkout -- .   # Revert — never start dirty
4. git rev-parse HEAD > /spine/last_candidate_commit
5. Install post-commit hook: cp /runtime_scripts/post-commit /app/.git/hooks/post-commit
```

**Rule: The container never starts with uncommitted changes.**

## 4. Commit and Push Workflow

### 4.1 Pre-commit Hook

Every `git commit` triggers:
1. `python3 -m py_compile` — syntax validation
2. `pytest tests/` — test suite
3. `constitutional_auditor.py` — LLM audit
4. Record candidate commit: `git rev-parse HEAD > /spine/last_candidate_commit`

### 4.2 Post-commit Hook (automatic)

After a successful commit, a post-commit hook automatically runs:

```
git push origin feat/talos
```

**Rule: Every commit is immediately pushed. No exceptions.**

### 4.3 Push Failure Handling

If `git push` fails (network error, remote rejection):
- The commit succeeds locally
- Agent receives a system notice on the next turn: `[SYSTEM | Push failed to origin/feat/talos. Retry with git_push().]`
- The `git_push()` tool exists for the agent to retry manually
- On next container restart, `git pull` will attempt to sync before starting

## 5. Restart Rules

### 5.1 Graceful Restart (`request_restart`)

The agent calls `spine.request_restart(reason)`. Before terminating:
- Agent must have a **clean working tree** — uncommitted changes are not allowed
- If the agent has uncommitted changes, it must either commit them or revert them before calling `request_restart`
- This is already enforced by the existing pre-commit hook gate

### 5.2 Crash Restart

If the Cortex process crashes unexpectedly:
- On restart, the startup sequence checks `git status --porcelain`
- Any uncommitted files are reverted: `git checkout -- .`
- Agent resumes from the last committed state

**Rule: A restart never begins with a dirty working tree.**

## 6. Stable Version Tracking

| File | Written By | When |
|------|-----------|------|
| `last_candidate_commit` | Pre-commit hook | After commit passes all gates |
| `last_stable_commit` | Spine | After first successful `think()` |

On crash recovery, the Lazarus Protocol reverts to `last_stable_commit` if the crash occurred before first successful think.

## 7. What the Agent Can Never Do

- `git checkout main` — blocked at tool level in `bash_command`
- `git push origin main` — blocked at post-commit hook level
- `git reset --hard` to a commit outside `feat/talos` — blocked
- `bash_command` with `--no-verify` — blocked

## 8. Exceptions

- `git fetch`, `git pull` — allowed (syncing from remote)
- `git log`, `git status`, `git diff` — allowed (reading only)
- `git branch` — allowed for reading, blocked for creating outside `feat/talos`
