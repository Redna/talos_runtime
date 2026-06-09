FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /gate

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Gemma tokenizer model for accurate context estimation.
# If the download fails (e.g. network restricted), the tokenizer falls back
# to prompt_tokens-based estimation — the Gate still works.
RUN mkdir -p /usr/local/share/talos/tokenizers && \
    wget -qO /usr/local/share/talos/tokenizers/gemma_tokenizer.model \
    "https://huggingface.co/google/gemma-4-31b-it/resolve/main/tokenizer.model" || true

# Copy both the real gate and the dryrun gate plus all the scenario scripts.
# DRYRUN_MODE=1 (set in docker-compose.dryrun.yml) selects dryrun_app.py.
COPY app.py dryrun_app.py tokenizer.py entrypoint.sh dryrun_entrypoint.sh ./
COPY dryrun_script_happy.json dryrun_script_crash.json dryrun_script_stall.json ./
RUN chmod +x entrypoint.sh dryrun_entrypoint.sh

EXPOSE 4000

# When DRYRUN_MODE=1 the dryrun_entrypoint selects dryrun_app.py and the
# scenario script based on DRYRUN_SCENARIO.  The default entrypoint runs the
# real gate.  Both paths are kept in this image to keep the build cache hot.
ENTRYPOINT ["./entrypoint.sh"]