#!/bin/bash

export HOME=/root

GIT_REPO=https://x-access-token:${GITHUB_TOKEN}@github.com/Redna/talos.git
GIT_BRANCH=talos_seed

git config --global --add safe.directory '*'
git config --system --add safe.directory '*' 2>/dev/null || true
git config --global user.name "Talos"
git config --global user.email "talos@agent.local"

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

if ! getent group "$GROUP_ID" > /dev/null 2>&1; then groupadd -g "$GROUP_ID" talos; fi
if ! getent passwd "$USER_ID" > /dev/null 2>&1; then useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/bash talos; fi

USER_NAME=$(getent passwd "$USER_ID" | cut -d: -f1)

if [ -d /app/.git ]; then
    echo "[Entrypoint] Existing repo found, updating seed reference..."
    cd /app
    git fetch origin "$GIT_BRANCH"

    # Save uncommitted work before any branch switching
    STASHED=0
    if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
        if git stash push -m "auto-saved on restart $(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)"; then
            STASHED=1
            echo "[Entrypoint] Stashed uncommitted changes before restart"
        fi
    fi

    # Update the local seed branch to match remote (spine fixes, etc.)
    git checkout -f "$GIT_BRANCH" 2>/dev/null || true
    git reset --hard "origin/$GIT_BRANCH" 2>/dev/null || true
    git clean -fd
else
    echo "[Entrypoint] Fresh volume, cloning repo..."
    rm -rf /app/.[!.]* /app/* 2>/dev/null
    git clone -b "$GIT_BRANCH" "$GIT_REPO" /app
    STASHED=0
fi

# Derive the volatile branch name from the seed (defaults to feat/talos)
VOLATILE_BRANCH=${VOLATILE_BRANCH:-feat/talos}
echo "[Entrypoint] Establishing local volatile branch: $VOLATILE_BRANCH"
cd /app

# On restart: preserve the working branch and its commit history.
# Only create a fresh branch from seed if it doesn't exist yet.
if git rev-parse --verify "$VOLATILE_BRANCH" > /dev/null 2>&1; then
    echo "[Entrypoint] Restoring existing working branch: $VOLATILE_BRANCH"
    git checkout "$VOLATILE_BRANCH"
else
    echo "[Entrypoint] Creating new volatile branch: $VOLATILE_BRANCH from seed"
    git checkout -b "$VOLATILE_BRANCH"
fi

# Try to pull remote state for the volatile branch if available
if git fetch origin "$VOLATILE_BRANCH" 2>/dev/null; then
    if git merge-base --is-ancestor HEAD "origin/$VOLATILE_BRANCH" 2>/dev/null; then
        echo "[Entrypoint] Fast-forwarding to remote $VOLATILE_BRANCH..."
        git merge --ff-only "origin/$VOLATILE_BRANCH" 2>/dev/null || true
    else
        echo "[Entrypoint] Local $VOLATILE_BRANCH has diverged from remote — keeping local history"
    fi
fi

# Restore saved uncommitted work onto the volatile branch
if [ "$STASHED" = "1" ]; then
    if git stash pop; then
        echo "[Entrypoint] Recovered uncommitted files from a sudden crash. Commit them immediately."
    else
        echo "[Entrypoint] WARNING: stash pop had conflicts — uncommitted work left in stash"
    fi
fi

echo "[Entrypoint] Branch: $(git -C /app rev-parse --abbrev-ref HEAD)"
COMMIT=$(git -C /app rev-parse HEAD)
echo "[Entrypoint] Commit: $COMMIT"

echo "Restoring authoritative spine files..."
cp -a /spine_backup/. /app/spine/
chmod -x /app/spine/*.py
echo "Purging stale __pycache__..."
rm -rf /app/spine/__pycache__/
echo "Running memory integrity audit..."
python3 /app/scripts/startup_audit.py || true

chown -R "$USER_NAME":"$GROUP_ID" /app
# Ensure /memory exists and is writable by talos before chown
mkdir -p /memory
chmod -R 777 /memory
chown -R "$USER_NAME":"$GROUP_ID" /memory
mkdir -p /spine/events /spine/trajectories
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/trajectories

rm -f /spine/.paused /spine/.single_step /spine/state.json
echo "$COMMIT" > /spine/last_good_commit
echo "[Entrypoint] Recorded good commit: $COMMIT"

sudo -u "$USER_NAME" -H git config --global user.name "Talos"
sudo -u "$USER_NAME" -H git config --global user.email "talos@agent.local"

if [ -n "$GITHUB_TOKEN" ]; then
    echo "https://x-access-token:${GITHUB_TOKEN}@github.com" > /tmp/git_credentials
    chmod 600 /tmp/git_credentials
    sudo -u "$USER_NAME" -H git config --global credential.helper "store --file /tmp/git_credentials"
fi

if [ -f "/runtime_scripts/setup_hooks.sh" ]; then
    /bin/bash /runtime_scripts/setup_hooks.sh
fi

echo "Locking down semantic firewall and git hooks..."
chown -R root:root /runtime_scripts
chmod -R 755 /runtime_scripts

echo "Containment established."

echo "Starting Spine..."
python -m spine /spine/spine_config.json &
SPINE_PID=$!

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

echo "Awaking Talos as $USER_NAME ($USER_ID:$GROUP_ID)..."
echo "[Entrypoint] Spine managing Cortex lifecycle. Waiting for Spine process..."
wait $SPINE_PID