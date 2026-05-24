# Use a lightweight Python base image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/talos \
    TALOS_DRIVE_ROOT=/drive \
    TALOS_REPO_DIR=/app \
    PYTHONPATH=/app:/app/cortex \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Enable BuildKit mount caching for apt and uv
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    git curl gosu sudo wget gnupg patch procps htop ca-certificates e2fsprogs && \
    # Install GitHub CLI
    mkdir -p -m 755 /etc/apt/keyrings && \
    wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install gh -y

# Install trufflehog for secret scanning
RUN ARCH=$(dpkg --print-architecture) && \
    TRUFFLEHOG_VERSION=3.88.4 && \
    wget -qO /tmp/trufflehog.tar.gz "https://github.com/trufflesecurity/trufflehog/releases/download/v${TRUFFLEHOG_VERSION}/trufflehog_${TRUFFLEHOG_VERSION}_linux_${ARCH}.tar.gz" && \
    tar -xzf /tmp/trufflehog.tar.gz -C /usr/local/bin trufflehog && \
    rm /tmp/trufflehog.tar.gz && \
    trufflehog --version

# Install uv for fast package management
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh

# Allow git to work regardless of directory ownership (container runs as different users)
RUN git config --system --add safe.directory '*'

# 1. Cache dependencies (Layer is cached unless pyproject.toml/uv.lock changes)
COPY talos_runtime/talos/pyproject.toml talos_runtime/talos/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-progress --extra dev

# 2. Copy the actual code
COPY talos_runtime/talos/ .

# 3. Final sync to install the local project (fast as deps are cached) with dev deps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-progress --extra dev

# 4. Agent sandbox: install pip + make venv writable so Talos can add packages
RUN uv pip install pip && \
    uv pip install -r requirements.txt && \
    chmod -R 777 /venv

# 4a. Preserve pristine spine files (restored on each startup to prevent cortex corruption)
RUN cp -a /app/spine /spine_backup && \
    chmod -x /spine_backup/*.py

# 4. Add runtime scripts (Hardened)
COPY talos_runtime/scripts/ /runtime_scripts/
RUN chown -R root:root /runtime_scripts && chmod -R 555 /runtime_scripts && \
    chmod +x /runtime_scripts/setup_hooks.sh

# 5. Add the entrypoint script
COPY talos_runtime/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 7. Copy Spine configuration
COPY talos_runtime/spine_config.json /spine/spine_config.json

# The entrypoint launches the seed agent directly
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# Use absolute path to the persistent venv python
CMD ["sleep", "infinity"]