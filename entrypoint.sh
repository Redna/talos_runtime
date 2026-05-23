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

# Grant sudo permissions to the user and preserve proxy env vars
echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/talos
echo "Defaults env_keep += \"http_proxy https_proxy all_proxy no_proxy\"" >> /etc/sudoers.d/talos
chmod 0440 /etc/sudoers.d/talos

# Wait for Sentinel Proxy to be resolvable and reachable
if [ -n "$http_proxy" ]; then
    PROXY_HOST=$(echo "$http_proxy" | sed -E 's/http:\/\/([^:]+):.*/\1/')
    echo "[Entrypoint] Waiting for Sentinel Proxy ($PROXY_HOST) to be ready..."
    for i in $(seq 1 30); do
        if getent hosts "$PROXY_HOST" > /dev/null 2>&1; then
            echo "[Entrypoint] Sentinel Proxy DNS resolved."
            break
        fi
        sleep 1
    done
fi

# Wait for and install Sentinel Root CA if available
if [ -d /usr/local/share/ca-certificates/sentinel ]; then
    echo "[Entrypoint] Installing Sentinel Root CA..."
    # Copy from the RO volume to the writable system certs dir
    if [ -f /usr/local/share/ca-certificates/sentinel/mitmproxy-ca-cert.pem ]; then
        cp /usr/local/share/ca-certificates/sentinel/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/sentinel-mitmproxy.crt
        update-ca-certificates
    fi
fi

# Derive the volatile branch name from the seed (defaults to experiment)
VOLATILE_BRANCH=${VOLATILE_BRANCH:-experiment}

if [ -d /app/.git ] && [ "${FORCE_FRESH_CLONE:-0}" != "1" ]; then
    echo "[Entrypoint] Existing repo found, preserving state..."
    cd /app
else
    echo "[Entrypoint] Fresh volume or FORCE_FRESH_CLONE=1, (re)cloning repo..."
    rm -rf /app/.[!.]* /app/* 2>/dev/null
    git clone -b "$GIT_BRANCH" "$GIT_REPO" /app
    cd /app
    echo "[Entrypoint] Creating new volatile branch: $VOLATILE_BRANCH from seed"
    git checkout -b "$VOLATILE_BRANCH"
fi

# Try to pull remote state for the volatile branch if available
if git rev-parse --verify "$VOLATILE_BRANCH" > /dev/null 2>&1; then
    echo "[Entrypoint] Working on branch: $VOLATILE_BRANCH"
    if git fetch origin "$VOLATILE_BRANCH" 2>/dev/null; then
        if git merge-base --is-ancestor HEAD "origin/$VOLATILE_BRANCH" 2>/dev/null; then
            echo "[Entrypoint] Fast-forwarding to remote $VOLATILE_BRANCH..."
            git merge --ff-only "origin/$VOLATILE_BRANCH" 2>/dev/null || true
        else
            echo "[Entrypoint] Local $VOLATILE_BRANCH has diverged from remote — keeping local history"
        fi
    fi
fi

echo "[Entrypoint] Branch: $(git -C /app rev-parse --abbrev-ref HEAD)"
COMMIT=$(git -C /app rev-parse HEAD)
echo "[Entrypoint] Commit: $COMMIT"

echo "Restoring authoritative spine files..."
cp -a /spine_backup/. /app/spine/
chmod -x /app/spine/*.py
echo "Locking down spine files (immutable bit)..."
chattr -R +i /app/spine/ || echo "Warning: Could not set immutable bit (chattr not supported on this filesystem?)"
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