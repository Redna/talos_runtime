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
    echo "[Entrypoint] Existing repo found, pulling latest..."
    cd /app
    git fetch origin "$GIT_BRANCH"
    git checkout -f "$GIT_BRANCH"
    git reset --hard "origin/$GIT_BRANCH"
    git clean -fd
else
    echo "[Entrypoint] Fresh volume, cloning repo..."
    rm -rf /app/.[!.]* /app/* 2>/dev/null
    git clone -b "$GIT_BRANCH" "$GIT_REPO" /app
fi

# Derive the volatile branch name from the seed (defaults to feat/talos)
VOLATILE_BRANCH=${VOLATILE_BRANCH:-feat/talos}
echo "[Entrypoint] Establishing local volatile branch: $VOLATILE_BRANCH"
cd /app

# If this is a restart (existing repo), preserve the volatile branch pointer
# but reset its working tree to the clean seed. This discards any
# local agent modifications that may have corrupted the state, while
# keeping the commit history on origin/feat/talos available for the
# agent to pull later if needed.
if git rev-parse --verify "$VOLATILE_BRANCH" > /dev/null 2>&1; then
    git checkout -f "$GIT_BRANCH"
    git checkout -B "$VOLATILE_BRANCH"
else
    git checkout -b "$VOLATILE_BRANCH"
fi

# Prevent accidental merge of old volatile branch state from remote on startup.
# The agent can still git fetch explicitly, but this removes the immediate
# temptation of an existing origin/feat/talos tracking branch.
git update-ref -d "refs/remotes/origin/$VOLATILE_BRANCH" 2>/dev/null || true
# Also strip the legacy feat/talos and the new v2 push target.
git update-ref -d "refs/remotes/origin/feat/talos" 2>/dev/null || true
git update-ref -d "refs/remotes/origin/feat/talos-v2" 2>/dev/null || true

echo "[Entrypoint] Branch: $(git -C /app rev-parse --abbrev-ref HEAD)"
COMMIT=$(git -C /app rev-parse HEAD)
echo "[Entrypoint] Commit: $COMMIT"

echo "Restoring authoritative spine files..."
cp -a /spine_backup/. /app/spine/
echo "Purging stale __pycache__..."
rm -rf /app/spine/__pycache__/

chown -R "$USER_NAME":"$GROUP_ID" /app
# Ensure /memory exists and is writable by talos before chown
mkdir -p /memory
chmod -R 777 /memory
chown -R "$USER_NAME":"$GROUP_ID" /memory
mkdir -p /spine/events /spine/trajectories
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/trajectories

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