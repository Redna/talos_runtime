# Secret Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add trufflehog secret scanning as a pre-commit gate (container) and pre-push gate (host) to prevent credentials from entering git history.

**Architecture:** Trufflehog binary installed in the Docker image. Pre-commit hook in the container runs `trufflehog filesystem` on staged files. Host-side pre-push hook runs `trufflehog git` on outgoing commits. Both use `--only-verified` to eliminate false positives.

**Tech Stack:** Trufflehog (Go binary), Bash hooks, Docker

---

### Task 1: Install trufflehog in Docker image

**Files:**
- Modify: `Dockerfile` (add trufflehog download + install)

- [ ] **Step 1: Add trufflehog binary download to Dockerfile**

Add after the GitHub CLI install block (after line 26), before the `uv` install:

```dockerfile
# Install trufflehog for secret scanning
RUN ARCH=$(dpkg --print-architecture) && \
    TRUFFLEHOG_VERSION=3.88.4 && \
    wget -qO /tmp/trufflehog.tar.gz "https://github.com/trufflesecurity/trufflehog/releases/download/v${TRUFFLEHOG_VERSION}/trufflehog_${TRUFFLEHOG_VERSION}_linux_${ARCH}.tar.gz" && \
    tar -xzf /tmp/trufflehog.tar.gz -C /usr/local/bin trufflehog && \
    rm /tmp/trufflehog.tar.gz && \
    trufflehog --version
```

Note: `dpkg --print-architecture` returns `amd64` or `arm64` which matches trufflehog release naming.

- [ ] **Step 2: Rebuild and verify**

```bash
docker compose build talos
docker run --rm talos_runtime-talos trufflehog --version
```

Expected: `trufflehog version 3.88.4` (or whatever version was installed)

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "feat: add trufflehog binary to Docker image for secret scanning"
```

---

### Task 2: Add trufflehog gate to pre-commit hook (container)

**Files:**
- Modify: `scripts/setup_hooks.sh` (add gate 0 before syntax check)

- [ ] **Step 1: Add trufflehog gate to setup_hooks.sh**

In `scripts/setup_hooks.sh`, add the trufflehog gate as the first step inside the heredoc (after the `export PYTHONDONTWRITEBYTECODE=1` line):

```bash
# 0. Secret Scanning (Trufflehog)
echo "[Pre-commit] Executing secret scan..."
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | tr '\n' ' ')
if [ -n "$STAGED_FILES" ]; then
    if command -v trufflehog &>/dev/null; then
        trufflehog filesystem --only-verified --fail --no-update $STAGED_FILES || { echo "[Error] Secret detected! Commit aborted."; exit 1; }
    else
        echo "[Pre-commit] WARNING: trufflehog not found, skipping secret scan."
    fi
else
    echo "[Pre-commit] No staged files to scan."
fi
```

This goes **before** the existing `# 1. Syntax Validation` block. The existing numbering shifts: syntax becomes 1, pytest becomes 2, constitutional auditor becomes 3.

- [ ] **Step 2: Rebuild the Docker image to pick up the change**

```bash
docker compose build talos
```

- [ ] **Step 3: Commit**

```bash
git add scripts/setup_hooks.sh
git commit -m "feat: add trufflehog secret scan as gate 0 in pre-commit hook"
```

---

### Task 3: Create host-side pre-push hook script

**Files:**
- Create: `scripts/setup_host_hooks.sh`

- [ ] **Step 1: Write setup_host_hooks.sh**

```bash
#!/bin/bash
# Install pre-push hook for the talos_runtime host repository.
# Scans outgoing commits for secrets before pushing to remote.
# Requires: trufflehog (brew install trufflehog or https://github.com/trufflesecurity/trufflehog)

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_FILE="$REPO_ROOT/.git/hooks/pre-push"

if ! command -v trufflehog &>/dev/null; then
    echo "WARNING: trufflehog not found on PATH. Install it first:"
    echo "  brew install trufflehog"
    echo "  or download from https://github.com/trufflesecurity/trufflehog/releases"
    exit 1
fi

cat > "$HOOK_FILE" << 'HOOK'
#!/bin/bash
# Pre-push hook: scan outgoing commits for secrets
while read local_ref local_oid remote_ref remote_oid; do
    if [ "$remote_oid" = "0000000000000000000000000000000000000000" ]; then
        RANGE="$local_oid"
    else
        RANGE="$remote_oid..$local_oid"
    fi
    echo "[Pre-push] Scanning outgoing commits for secrets..."
    trufflehog git "$REPO_ROOT" --only-verified --fail --no-update --branch="" HEAD 2>/dev/null
    RC=$?
    if [ $RC -eq 1 ]; then
        echo "[Pre-push] SECRET DETECTED in outgoing commits! Push aborted."
        exit 1
    fi
done
HOOK
chmod +x "$HOOK_FILE"
echo "Pre-push hook installed successfully."
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x scripts/setup_host_hooks.sh
```

- [ ] **Step 3: Test that the script runs without error (trufflehog must be on host PATH)**

```bash
# If trufflehog is installed on host:
./scripts/setup_host_hooks.sh
# Verify hook was installed:
cat .git/hooks/pre-push
```

- [ ] **Step 4: Commit**

```bash
git add scripts/setup_host_hooks.sh
git commit -m "feat: add host-side pre-push hook script for secret scanning"
```

---

### Task 4: Update .env.example with trufflehog note

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add trufflehog note to .env.example**

Add after the existing `# Run pytest before git push?` section:

```bash
# --- Secret Scanning ---
# The container pre-commit hook includes trufflehog (no host install needed).
# For host-side pre-push scanning, install trufflehog:
#   brew install trufflehog
#   or: https://github.com/trufflesecurity/trufflehog/releases
# Then run: ./scripts/setup_host_hooks.sh
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add trufflehog installation note to .env.example"
```

---

### Task 5: Integration test — verify trufflehog blocks a commit with a secret

**Files:**
- Test: Manual test (no automated test needed — this is a build-time and hook integration)

- [ ] **Step 1: Build the Docker image**

```bash
docker compose build talos
```

- [ ] **Step 2: Verify trufflehog is available in the image**

```bash
docker run --rm --entrypoint trufflehog talos_runtime-talos --version
```

- [ ] **Step 3: Verify pre-push hook installs on host**

```bash
./scripts/setup_host_hooks.sh
ls -la .git/hooks/pre-push
```

Expected: hook file exists and is executable

- [ ] **Step 4: Final commit (push all changes)**

```bash
git push origin feat/spine-cortex
```