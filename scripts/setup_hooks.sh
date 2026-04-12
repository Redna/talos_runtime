#!/bin/bash

# Prevent accidental installation on the host machine
if [ ! -f /.dockerenv ]; then
    echo "Warning: Not running in Docker. Skipping hook installation to protect host environment."
    exit 0
fi

HOOK_FILE=".git/hooks/pre-commit"

cat > "$HOOK_FILE" << 'EOF'
#!/bin/bash
export UV_PROJECT_ENVIRONMENT=/tmp/talos-preflight-venv
export UV_CACHE_DIR=/tmp/.uv-cache
export PYTHONDONTWRITEBYTECODE=1

# 1. Static Type Checking (Mypy)
echo "[Pre-commit] Executing mypy..."
uv run mypy seed_agent.py || { echo "[Error] Type check failed! Commit aborted."; exit 1; }

# 2. Syntax Validation (Fast-fail)
echo "[Pre-commit] Executing syntax check..."
python3 -m py_compile seed_agent.py || { echo "[Error] Syntax error detected! Commit aborted."; exit 1; }

# 3. Logic Verification (Pytest)
echo "[Pre-commit] Executing pytest..."
uv run pytest tests/ || { echo "[Error] Tests failed! Commit aborted."; exit 1; }

# 4. Sentinel Quality Gate (Constitutional Audit)
# We run the auditor as a standalone python script outside the agent's main trajectory.
echo "[Pre-commit] Executing Sentinel Quality Gate..."
python3 /runtime_scripts/constitutional_auditor.py || { echo "[Audit] FAIL: Constitution violation detected. Commit blocked."; exit 1; }

echo "[Pre-commit] All gates passed. Memory committed."
EOF

chmod +x "$HOOK_FILE"
echo "Git pre-commit hook installed successfully."