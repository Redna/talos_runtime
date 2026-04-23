#!/bin/bash

export HOME=/root

GIT_REPO=https://x-access-token:${GITHUB_TOKEN}@github.com/Redna/talos.git
GIT_BRANCH=feat/talos

git config --global --add safe.directory '*'
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
    git checkout "$GIT_BRANCH"
    git reset --hard "origin/$GIT_BRANCH"
else
    echo "[Entrypoint] Fresh volume, cloning repo..."
    rm -rf /app/.[!.]* /app/* 2>/dev/null
    git clone -b "$GIT_BRANCH" "$GIT_REPO" /app
fi

echo "[Entrypoint] Branch: $(git -C /app rev-parse --abbrev-ref HEAD)"
echo "[Entrypoint] Commit: $(git -C /app rev-parse HEAD)"

echo "Restoring authoritative spine files..."
cp -a /spine_backup/. /app/spine/

chown -R "$USER_NAME":"$GROUP_ID" /app
chown -R "$USER_NAME":"$GROUP_ID" /memory
mkdir -p /spine/events /spine/trajectories
chown -R "$USER_NAME":"$GROUP_ID" /spine/events /spine/trajectories

sudo -u "$USER_NAME" -H git config --global user.name "Talos"
sudo -u "$USER_NAME" -H git config --global user.email "talos@agent.local"
sudo -u "$USER_NAME" -H git config --global --add safe.directory /app

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

git -C /app rev-parse HEAD > /spine/last_good_commit
echo "[Entrypoint] Recorded good commit: $(cat /spine/last_good_commit)"

echo "Awaking Talos as $USER_NAME ($USER_ID:$GROUP_ID)..."
echo "[Entrypoint] Spine managing Cortex lifecycle. Waiting for Spine process..."
wait $SPINE_PID