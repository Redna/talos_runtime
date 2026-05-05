import json
import os

DEFAULT_TOKENIZER_PATH = "/usr/local/share/talos/tokenizers/gemma_tokenizer.model"
DEFAULT_CONTEXT_WINDOW = 262144


class TokenizerManager:
    def __init__(
        self,
        model: str = "",
        tokenizer_model_path: str = DEFAULT_TOKENIZER_PATH,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ):
        self.model = model or ""
        self.context_window = context_window
        self._backend = self._resolve_backend()
        self._sp = None
        self._enc = None
        self._init_backend(tokenizer_model_path)

    def _resolve_backend(self) -> str:
        m = self.model.lower()
        if "gemma" in m:
            return "sentencepiece"
        if "qwen" in m or "llama" in m:
            return "tiktoken"
        return "unavailable"

    def _init_backend(self, tokenizer_model_path: str):
        if self._backend == "sentencepiece":
            if not os.path.exists(tokenizer_model_path):
                self._backend = "unavailable"
                return
            try:
                import sentencepiece as spm  # noqa: F811

                self._sp = spm.SentencePieceProcessor(
                    model_file=tokenizer_model_path
                )
            except Exception:
                self._backend = "unavailable"
        elif self._backend == "tiktoken":
            try:
                import tiktoken

                self._enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._backend = "unavailable"

    @property
    def available(self) -> bool:
        return self._backend != "unavailable"

    def count_tokens(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        """Tokenize serialized messages and optional tools, return token count."""
        text = "\n".join(
            json.dumps(m, ensure_ascii=False) for m in messages
        )
        if tools:
            text += "\n" + "\n".join(
                json.dumps(t, ensure_ascii=False) for t in tools
            )
        if self._backend == "sentencepiece" and self._sp is not None:
            return len(self._sp.encode(text))
        if self._backend == "tiktoken" and self._enc is not None:
            return len(self._enc.encode(text))
        return 0

    def context_pct(self, messages: list[dict], tools: list[dict] | None = None) -> float | None:
        """Return context_pct from tokenizing messages+optional tools, or None if unavailable."""
        if not self.available or self.context_window <= 0:
            return None
        tokens = self.count_tokens(messages, tools)
        return max(0.0, min(tokens / self.context_window, 1.0))
