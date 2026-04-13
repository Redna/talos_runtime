# Secret Scanning Design — Trufflehog Integration

**Date:** 2026-04-13  
**Scope:** Pre-commit and pre-push secret scanning for both the agent container and the host repo

## Problem

The Talos agent can commit code inside the `talos_workspace` named volume (via git inside the container). The developer commits code on the host in `talos_runtime/`. Neither location scans for leaked credentials (API keys, tokens, private keys) before they enter git history.

## Design

### Container-side (talos named volume /app)

Add `trufflehog` as **gate 0** in the pre-commit hook, before syntax checking:

```
0. trufflehog filesystem --no-verify --only-verified --fail  (scan staged files)
1. py_compile   (syntax)
2. pytest       (logic)
3. constitutional_auditor  (principles)
```

**Installation:** Download trufflehog binary from GitHub releases into `/usr/local/bin/` in the Dockerfile. The binary is ~50MB.

**Scanning scope:** Only staged changes. The hook runs:

```bash
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM)
if [ -n "$STAGED_FILES" ]; then
    trufflehog filesystem --no-verify --only-verified --fail $(echo "$STAGED_FILES" | tr '\n' ' ')
fi
```

Key flags:
- `--only-verified`: Only report secrets that can be verified (e.g., a live API key check). Eliminates false positives.
- `--fail`: Exit code 1 if secrets found (blocks the commit)
- `--no-update`: Skip checking for trufflehog updates (avoids network dependency)

**Bypass prevention:** The agent's `bash_command` tool already rejects `--no-verify`. The hook file is owned by root (`chmod 755`), so the agent cannot modify or delete it.

### Host-side (talos_runtime/)

Create `scripts/setup_host_hooks.sh` that installs a `pre-push` hook into `.git/hooks/pre-push`. This scans commits being pushed against the remote, catching secrets that may have slipped through:

```bash
#!/bin/bash
# .git/hooks/pre-push — runs trufflehog on outgoing commits
while read local_ref local_oid remote_ref remote_oid; do
    trufflehog git "https://github.com/Redna/talos_runtime.git" \
        --only-verified --fail --branch="${remote_ref##refs/heads/}" \
        --from="${remote_oid}" --to="${local_oid}"
done
```

**Installation:** Run once on the host: `./scripts/setup_host_hooks.sh`

**Host prerequisite:** Trufflehog must be installed on the host (`brew install trufflehog`, or download from GitHub releases).

### Hook invocation matrix

| Location | Hook | When | What it scans |
|---|---|---|---|
| Container `/app` | pre-commit | Every commit | Staged file diff |
| Host `talos_runtime/` | pre-push | `git push` | Outgoing commits vs remote |

### Fail behavior

- If trufflehog finds verified secrets: exit 1, commit/push blocked, clear message showing which file and secret type
- If trufflehog binary is missing: warn but **do not block** (fail-open for missing binary, fail-closed for found secrets)
- No update checks (`--no-update`) — prevents network-dependent failures in air-gapped or offline environments

### Files changed

1. `Dockerfile` — add trufflehog binary download
2. `scripts/setup_hooks.sh` — add gate 0 (trufflehog) to pre-commit hook
3. `scripts/setup_host_hooks.sh` — new file, installs host pre-push hook
4. `.env.example` — document trufflehog requirement for host development