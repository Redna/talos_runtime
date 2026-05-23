FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install mitmproxy requests

WORKDIR /app
COPY mitm_audit.py .

# Use mitmproxy's default port
EXPOSE 8080 8081

ENTRYPOINT ["mitmdump", "-s", "mitm_audit.py", "--set", "block_global=false"]
