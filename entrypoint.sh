#!/bin/bash

git config --global --add safe.directory '*'

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

if ! getent group "$GROUP_ID" > /dev/null 2>&1; then groupadd -g "$GROUP_ID" talos; fi
if ! getent passwd "$USER_ID" > /dev/null 2>&1; then useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/bash talos; fi

USER_NAME=$(getent passwd "$USER_ID" | cut -d: -f1)

GIT_REMOTE=origin
GIT_BRANCH=feat/talos

cd /app

echo "gitdir: /runtime_git" > /app/.git
if grep -q 'worktree = ' /runtime_git/config 2>/dev/null; then
    sed -i 's|worktree = .*|worktree = /app|' /runtime_git/config
fi

if git ls-remote --exit-code "$GIT_REMOTE" "$GIT_BRANCH" > /dev/null 2>&1; then
    echo "[Entrypoint] Branch $GIT_BRANCH exists on $GIT_REMOTE, pulling latest..."
    git fetch "$GIT_REMOTE" "$GIT_BRANCH"
    git checkout "$GIT_BRANCH"
    git reset --hard "$GIT_REMOTE/$GIT_BRANCH"
    if [ -n "$(git status --porcelain)" ]; then
        echo "[Entrypoint] Reverting uncommitted changes..."
        git checkout -- .
    fi
else
    echo "[Entrypoint] Branch $GIT_BRANCH does not exist on $GIT_REMOTE, creating..."
    git checkout -b "$GIT_BRANCH"
    git push -u "$GIT_REMOTE" "$GIT_BRANCH" || echo "[Entrypoint] Warning: failed to push $GIT_BRANCH, will retry on next startup"
fi

echo "[Entrypoint] Current branch: $(git rev-parse --abbrev-ref HEAD)"
echo "[Entrypoint] Current commit: $(git rev-parse HEAD)"

echo "Restoring authoritative spine files..."
cp -a /spine_backup/. /app/spine/

chown -R "$USER_NAME":"$GROUP_ID" /app
chown -R "$USER_NAME":"$GROUP_ID" /memory
mkdir -p /spine/events /spine/trajectories
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/trajectories

git config --global --add safe.directory /app
sudo -u "$USER_NAME" -H git config --global user.name "Talos"
sudo -u "$USER_NAME" -H git config --global user.email "talos@agent.local"
sudo -u "$USER_NAME" -H git config --global --add safe.directory /app

if [ -n "$GITHUB_TOKEN" ]; then
    echo "https://x-access-token:${GITHUB_TOKEN}@github.com" > /tmp/git_credentials
    chmod 600 /tmp/git_credentials
    sudo -u "$USER_NAME" -H git config --global credential.helper "store --file /tmp/git_credentials"
fi

if [ -f "/runtime_scripts/setup_hooks.sh" ]; then
    cd /app && /bin/bash /runtime_scripts/setup_hooks.sh
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

if git -C /app rev-parse HEAD > /dev/null 2>&1; then
  git -C /app rev-parse HEAD > /spine/last_candidate_commit
  echo "[Entrypoint] Recorded candidate commit"
fi

echo "Awaking Talos as $USER_NAME ($USER_ID:$GROUP_ID)..."
echo "[Entrypoint] Spine managing Cortex lifecycle. Waiting for Spine process..."
wait $SPINE_PID
