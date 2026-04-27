#!/bin/bash

# Prevent accidental installation on the host machine
if [ ! -f /.dockerenv ]; then
    echo "Warning: Not running in Docker. Skipping hook installation to protect host environment."
    exit 0
fi

HOOK_DIR=$(cd /app && git rev-parse --git-path hooks)
PRE_COMMIT_FILE="$HOOK_DIR/pre-commit"
POST_COMMIT_FILE="$HOOK_DIR/post-commit"

cat > "$PRE_COMMIT_FILE" << 'EOF'
#!/bin/bash
export UV_PROJECT_ENVIRONMENT=/venv
export UV_CACHE_DIR=/tmp/.uv-cache
export PYTHONDONTWRITEBYTECODE=1

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

# 1. Syntax Validation (Fast-fail)
echo "[Pre-commit] Executing syntax check..."
for f in $(git diff --cached --name-only --diff-filter=ACM | grep '\.py$'); do
    python3 -m py_compile "$f" || { echo "[Error] Syntax error in $f! Commit aborted."; exit 1; }
done

# 2. Logic Verification (Pytest)
echo "[Pre-commit] Executing pytest..."
if command -v uv &>/dev/null && uv run pytest --version &>/dev/null; then
    uv run pytest tests/ || { echo "[Error] Tests failed! Commit aborted."; exit 1; }
else
    echo "[Pre-commit] WARNING: pytest not available in container, skipping."
fi

# 3. Sentinel Quality Gate (Constitutional Audit)
echo "[Pre-commit] Executing Sentinel Quality Gate..."
python3 /runtime_scripts/constitutional_auditor.py || { echo "[Audit] FAIL: Constitution violation detected. Commit blocked."; exit 1; }

echo "[Pre-commit] All gates passed. Memory committed."
git rev-parse HEAD > /spine/last_candidate_commit
echo "[Pre-commit] Candidate commit recorded."
EOF

cat > "$POST_COMMIT_FILE" << 'EOF'
#!/bin/bash
# Post-commit hook: automatically push to origin/feat/talos

GIT_REMOTE=${GIT_REMOTE:-origin}
GIT_BRANCH=${GIT_BRANCH:-feat/talos-v2}

echo "[Post-commit] Pushing to $GIT_REMOTE/$GIT_BRANCH..."
git push "$GIT_REMOTE" "$GIT_BRANCH" 2>&1 || {
    echo "[Post-commit] WARNING: Push failed. Agent will receive a system notice on next turn."
    exit 0  # Never block commits due to push failure
}
EOF

chmod +x "$PRE_COMMIT_FILE"
chmod +x "$POST_COMMIT_FILE"
echo "Git hooks installed successfully."