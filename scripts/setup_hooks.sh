#!/bin/bash

# Prevent accidental installation on the host machine
if [ ! -f /.dockerenv ]; then
    echo "Warning: Not running in Docker. Skipping hook installation to protect host environment."
    exit 0
fi

HOOK_FILE=".git/hooks/pre-commit"

cat > "$HOOK_FILE" << 'EOF'
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
uv run pytest tests/ || { echo "[Error] Tests failed! Commit aborted."; exit 1; }

# 3. Sentinel Quality Gate (Constitutional Audit)
echo "[Pre-commit] Executing Sentinel Quality Gate..."
python3 /runtime_scripts/constitutional_auditor.py || { echo "[Audit] FAIL: Constitution violation detected. Commit blocked."; exit 1; }

echo "[Pre-commit] All gates passed. Memory committed."
git rev-parse HEAD > /spine/last_candidate_commit
echo "[Pre-commit] Candidate commit recorded."
EOF

chmod +x "$HOOK_FILE"
echo "Git pre-commit hook installed successfully."