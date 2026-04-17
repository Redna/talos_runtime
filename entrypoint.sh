#!/bin/bash
set -e

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

if ! getent group "$GROUP_ID" >/dev/null; then groupadd -g "$GROUP_ID" talos; fi
if ! getent passwd "$USER_ID" >/dev/null; then useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/bash talos; fi

USER_NAME=$(getent passwd "$USER_ID" | cut -d: -f1)

GIT_REMOTE=origin
GIT_BRANCH=feat/talos

if [ -f /app/.git ] && grep -q "gitdir:" /app/.git && [ -d /runtime_git/objects ]; then
    echo "[Entrypoint] Setting up git worktree for submodule..."
    cp -a /runtime_git /tmp/runtime_git
    sed -i "s|worktree = .*|worktree = /app|" /tmp/runtime_git/config
    echo "gitdir: /tmp/runtime_git" > /app/.git
fi

cd /app

if git ls-remote --exit-code "$GIT_REMOTE" "$GIT_BRANCH" > /dev/null 2>&1; then
    echo "[Entrypoint] Branch $GIT_BRANCH exists on $GIT_REMOTE"
    if git rev-parse --verify "$GIT_BRANCH" > /dev/null 2>&1; then
        git checkout "$GIT_BRANCH"
        git pull --rebase "$GIT_REMOTE" "$GIT_BRANCH"
    else
        git checkout -b "$GIT_BRANCH" --track "$GIT_REMOTE/$GIT_BRANCH"
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "[Entrypoint] Reverting uncommitted changes..."
        git checkout -- .
    fi
else
    echo "[Entrypoint] Branch $GIT_BRANCH does not exist on $GIT_REMOTE, creating..."
    git checkout -b "$GIT_BRANCH"
    git push -u "$GIT_REMOTE" "$GIT_BRANCH"
fi

echo "[Entrypoint] Current branch: $(git rev-parse --abbrev-ref HEAD)"
echo "[Entrypoint] Current commit: $(git rev-parse HEAD)"

echo "Restoring pristine spine files..."
cp -a /spine_pristine/. /app/spine/

chown -R "$USER_NAME":"$GROUP_ID" /app
chown -R "$USER_NAME":"$GROUP_ID" /memory
mkdir -p /spine/events /spine/snapshots /spine/crashes
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/snapshots /spine/crashes

git config --global --add safe.directory /app
if [ -d /tmp/runtime_git ]; then
    git config --global --add safe.directory /tmp/runtime_git
fi
sudo -u "$USER_NAME" -H git config --global user.name "Talos"
sudo -u "$USER_NAME" -H git config --global user.email "talos@agent.local"
sudo -u "$USER_NAME" -H git config --global --add safe.directory /app
if [ -d /tmp/runtime_git ]; then
    sudo -u "$USER_NAME" -H git config --global --add safe.directory /tmp/runtime_git
fi

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
GIT_HOOKS_DIR="/app/.git/hooks"
if [ -f /app/.git ]; then
    GIT_HOOKS_DIR="/tmp/runtime_git/hooks"
fi
chown -R root:root "$GIT_HOOKS_DIR"
chmod -R 755 /runtime_scripts
chmod -R 755 "$GIT_HOOKS_DIR"

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
else
  echo "[Entrypoint] WARNING: /app is not a git repository, skipping candidate commit"
fi

echo "Awaking Talos as $USER_NAME ($USER_ID:$GROUP_ID)..."
echo "[Entrypoint] Spine managing Cortex lifecycle. Waiting for Spine process..."
wait $SPINE_PID