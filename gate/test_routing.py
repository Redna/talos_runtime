import pytest
from unittest.mock import AsyncMock, patch
from app import app, BACKENDS, MODEL_MAP


def test_model_map_llamacpp():
    assert MODEL_MAP["gemma-4-31B-it-UD-Q4_K_XL.gguf"] == "local"
    assert MODEL_MAP["gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf"] == "local"
    assert MODEL_MAP["Qwen3.5-27B-Q4_K_M.gguf"] == "local"


def test_model_map_ollama():
    assert MODEL_MAP["gemma4:31b-cloud"] == "ollama"
    assert MODEL_MAP["minimax-m2.7:cloud"] == "ollama"
    assert MODEL_MAP["glm-5.1:cloud"] == "ollama"


def test_backends_contain_ollama():
    assert "ollama" in BACKENDS
    assert "host.docker.internal" in BACKENDS["ollama"]


def test_backends_contain_local():
    assert "local" in BACKENDS
    assert "llamacpp" in BACKENDS["local"]


def test_backends_contain_together():
    assert "together" in BACKENDS
    assert "api.together.xyz" in BACKENDS["together"]


def test_routing_local_model():
    model = "gemma-4-31B-it-UD-Q4_K_XL.gguf"
    backend_key = MODEL_MAP.get(model, "local")
    assert backend_key == "local"


def test_routing_ollama_model():
    model = "gemma4:31b-cloud"
    backend_key = MODEL_MAP.get(model, "local")
    assert backend_key == "ollama"


def test_routing_unknown_model_defaults_to_local():
    model = "unknown-model"
    backend_key = MODEL_MAP.get(model, "local")
    assert backend_key == "local"


def test_routing_together_prefix():
    model = "together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
    backend_key = (
        "together" if "together" in model.lower() else MODEL_MAP.get(model, "local")
    )
    assert backend_key == "together"


def test_ollama_host_configurable():
    import os

    original = os.environ.get("OLLAMA_HOST")
    os.environ["OLLAMA_HOST"] = "my-host:12345"
    import importlib
    import app as app_module

    importlib.reload(app_module)
    assert "my-host:12345" in app_module.BACKENDS["ollama"]
    if original:
        os.environ["OLLAMA_HOST"] = original
    else:
        os.environ.pop("OLLAMA_HOST", None)
    importlib.reload(app_module)
