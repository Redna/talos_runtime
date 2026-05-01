FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Gemma tokenizer model for accurate context estimation.
# If the download fails (e.g. network restricted), the tokenizer falls back
# to prompt_tokens-based estimation — the Gate still works.
RUN mkdir -p /usr/local/share/talos/tokenizers && \
    wget -qO /usr/local/share/talos/tokenizers/gemma_tokenizer.model \
    "https://huggingface.co/google/gemma-4-31b-it/resolve/main/tokenizer.model" || true

COPY app.py tokenizer.py .

EXPOSE 4000

CMD ["python", "app.py"]