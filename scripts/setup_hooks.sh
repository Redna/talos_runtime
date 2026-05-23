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
# Pre-commit hook: minimalist gate. Hard enforcement is handled by the Sentinel Proxy.
echo "[Pre-commit] Recording candidate commit for Spine..."
git rev-parse HEAD > /spine/last_candidate_commit
EOF

cat > "$POST_COMMIT_FILE" << 'EOF'
#!/bin/bash
# Post-commit hook: automatically push to origin/HEAD (current branch)

GIT_REMOTE=${GIT_REMOTE:-origin}

echo "[Post-commit] Pushing to $GIT_REMOTE HEAD..."
git push -u "$GIT_REMOTE" HEAD 2>&1 || {
    echo "[Post-commit] WARNING: Push failed. Agent will receive a system notice on next turn."
    exit 0  # Never block commits due to push failure
}
EOF

chmod +x "$PRE_COMMIT_FILE"
chmod +x "$POST_COMMIT_FILE"
echo "Git hooks installed successfully."