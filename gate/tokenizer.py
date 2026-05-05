import json
import os

DEFAULT_TOKENIZER_PATH = "/usr/local/share/talos/tokenizers/gemma_tokenizer.model"
DEFAULT_CONTEXT_WINDOW = 262144

# Heuristic: English + JSON text averages ~3.5 characters per token across
# common tokenizers (Gemma SP, tiktoken cl100k/o200k, Llama BPE).
CHARS_PER_TOKEN = 3.5


class TokenizerManager:
    def __init__(
        self,
        model: str = "",
        tokenizer_model_path: str = DEFAULT_TOKENIZER_PATH,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ):
        self.model = model or ""
        self.context_window = context_window
        self._sp = None
        self._enc = None
        self._init_backends(tokenizer_model_path)

    def _init_backends(self, tokenizer_model_path: str):
        # Try sentencepiece first for gemma models (best accuracy)
        if "gemma" in self.model.lower():
            if os.path.exists(tokenizer_model_path) and os.path.getsize(tokenizer_model_path) > 0:
                try:
                    import sentencepiece as spm
                    self._sp = spm.SentencePieceProcessor(model_file=tokenizer_model_path)
                except Exception:
                    pass

        # Always try tiktoken as primary or fallback — works offline
        try:
            import tiktoken
            self._enc = tiktoken.get_encoding("o200k_base")
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._sp is not None or self._enc is not None

    def count_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        text = "\n".join(
            json.dumps(m, ensure_ascii=False) for m in messages
        )
        if tools:
            text += "\n" + "\n".join(
                json.dumps(t, ensure_ascii=False) for t in tools
            )

        if self._sp is not None:
            return len(self._sp.encode(text))
        if self._enc is not None:
            return len(self._enc.encode(text))
        # Absolute last resort: character-count heuristic
        return max(1, int(len(text) / CHARS_PER_TOKEN))

    def context_pct(self, messages: list[dict], tools: list[dict] | None = None) -> float | None:
        if self.context_window <= 0:
            return None
        tokens = self.count_tokens(messages, tools)
        return max(0.0, min(tokens / self.context_window, 1.0))
