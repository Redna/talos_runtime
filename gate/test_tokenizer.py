from tokenizer import TokenizerManager


class TestTokenizerModelResolution:
    def test_gemma_maps_to_sentencepiece(self):
        tk = TokenizerManager(
            model="gemma4:31b-cloud",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        # Should gracefully degrade when model file is missing
        assert not tk.available

    def test_qwen_maps_to_tiktoken(self):
        tk = TokenizerManager(
            model="Qwen3.5-27B",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        # tiktoken should be available (pure Python, no model file needed)
        assert tk.available

    def test_llama_maps_to_tiktoken(self):
        tk = TokenizerManager(
            model="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        assert tk.available

    def test_unknown_model_is_unavailable(self):
        tk = TokenizerManager(
            model="bogus-model",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        assert not tk.available


class TestTokenizerContextPct:
    def test_unavailable_returns_none(self):
        tk = TokenizerManager(
            model="bogus-model",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        assert tk.context_pct([{"role": "user", "content": "hello"}]) is None

    def test_empty_messages(self):
        tk = TokenizerManager(
            model="Qwen3.5-27B",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        pct = tk.context_pct([])
        assert pct is not None
        assert pct == 0.0

    def test_clamped_to_one(self):
        tk = TokenizerManager(
            model="Qwen3.5-27B",
            tokenizer_model_path="/nonexistent/path",
            context_window=100,  # tiny window to force clamp
        )
        # A few messages should exceed 100 tokens easily
        msgs = [
            {"role": "system", "content": "x" * 5000},
            {"role": "user", "content": "y" * 5000},
        ]
        pct = tk.context_pct(msgs)
        assert pct is not None
        assert pct == 1.0

    def test_small_messages_low_pct(self):
        tk = TokenizerManager(
            model="Qwen3.5-27B",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        msgs = [{"role": "user", "content": "hello"}]
        pct = tk.context_pct(msgs)
        assert pct is not None
        assert 0.0 < pct < 0.01


class TestTokenizerCountTokens:
    def test_returns_int(self):
        tk = TokenizerManager(
            model="Qwen3.5-27B",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        tokens = tk.count_tokens([{"role": "user", "content": "hello world"}])
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_unavailable_returns_zero(self):
        tk = TokenizerManager(
            model="bogus-model",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        assert tk.count_tokens([{"role": "user", "content": "hello"}]) == 0

    def test_more_content_more_tokens(self):
        tk = TokenizerManager(
            model="Qwen3.5-27B",
            tokenizer_model_path="/nonexistent/path",
            context_window=71680,
        )
        short = tk.count_tokens([{"role": "user", "content": "hi"}])
        long = tk.count_tokens(
            [{"role": "user", "content": "a much longer message with more words"}]
        )
        assert long > short
