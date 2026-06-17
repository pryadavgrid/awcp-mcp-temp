"""Unit tests for awcp.laminar.estimator.

These tests run WITHOUT tiktoken installed (FallbackEstimator path) and,
when tiktoken IS installed, verify the BPE path as well.  Both paths must
produce sensible, non-zero estimates so pre_check has a meaningful input.
"""

import json
import pytest

from awcp.laminar.estimator import (
    FallbackEstimator,
    TiktokenEstimator,
    estimate_request,
    _encoding_for,
    _messages_to_text,
)


# ── FallbackEstimator ─────────────────────────────────────────────────────────

class TestFallbackEstimator:
    def setup_method(self):
        self.est = FallbackEstimator()

    def test_empty_string_returns_one(self):
        assert self.est.estimate("") == 1

    def test_four_chars_is_one_token(self):
        assert self.est.estimate("abcd") == 1

    def test_proportional(self):
        short = self.est.estimate("hello")
        long_ = self.est.estimate("hello world this is a longer sentence with many words")
        assert long_ > short

    def test_never_zero(self):
        assert self.est.estimate("x") >= 1


# ── TiktokenEstimator ─────────────────────────────────────────────────────────

class TestTiktokenEstimator:
    def test_falls_back_on_unknown_encoding(self):
        est = TiktokenEstimator("nonexistent_encoding_xyz")
        result = est.estimate("hello world")
        assert result > 0

    def test_gpt2_encoding_reasonable(self):
        try:
            import tiktoken  # noqa: F401
        except ImportError:
            pytest.skip("tiktoken not installed")
        est = TiktokenEstimator("gpt2")
        count = est.estimate("The quick brown fox jumps over the lazy dog")
        assert 8 <= count <= 15  # should be ~10 tokens

    def test_cl100k_base_reasonable(self):
        try:
            import tiktoken  # noqa: F401
        except ImportError:
            pytest.skip("tiktoken not installed")
        est = TiktokenEstimator("cl100k_base")
        count = est.estimate("Hello, world!")
        assert 3 <= count <= 6

    def test_special_tokens_do_not_raise(self):
        try:
            import tiktoken  # noqa: F401
        except ImportError:
            pytest.skip("tiktoken not installed")
        est = TiktokenEstimator("cl100k_base")
        # Special token text that could otherwise cause encode() to raise
        result = est.estimate("<|endoftext|> hello <|fim_prefix|>")
        assert result > 0


# ── _encoding_for routing ─────────────────────────────────────────────────────

class TestEncodingFor:
    def test_gpt4o_gets_o200k(self):
        assert _encoding_for("gpt-4o") == "o200k_base"

    def test_gpt4o_mini_gets_o200k(self):
        assert _encoding_for("gpt-4o-mini") == "o200k_base"

    def test_gpt4_gets_cl100k(self):
        assert _encoding_for("gpt-4-turbo") == "cl100k_base"

    def test_gpt35_gets_cl100k(self):
        assert _encoding_for("gpt-3.5-turbo") == "cl100k_base"

    def test_claude_gets_cl100k(self):
        assert _encoding_for("claude-sonnet-4-6") == "cl100k_base"
        assert _encoding_for("claude-opus-4-8") == "cl100k_base"

    def test_llama_gets_gpt2(self):
        assert _encoding_for("llama3.1:8b") == "gpt2"
        assert _encoding_for("llama3.2-vision") == "gpt2"

    def test_mistral_gets_gpt2(self):
        assert _encoding_for("mistral-7b-instruct") == "gpt2"

    def test_deepseek_gets_gpt2(self):
        assert _encoding_for("deepseek-r1:14b") == "gpt2"

    def test_unknown_model_defaults_to_gpt2(self):
        assert _encoding_for("some-future-model-xyz") == "gpt2"

    def test_case_insensitive(self):
        assert _encoding_for("GPT-4O") == "o200k_base"
        assert _encoding_for("CLAUDE-3-HAIKU") == "cl100k_base"

    def test_o1_gets_o200k(self):
        assert _encoding_for("o1-mini") == "o200k_base"
        assert _encoding_for("o3-pro") == "o200k_base"


# ── _messages_to_text ─────────────────────────────────────────────────────────

class TestMessagesToText:
    def test_basic_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user",   "content": "Hello"},
        ]
        text = _messages_to_text(msgs)
        assert "system" in text
        assert "You are helpful" in text
        assert "Hello" in text

    def test_multimodal_content_extracts_text(self):
        msgs = [{"role": "user", "content": [
            {"type": "text",  "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        ]}]
        text = _messages_to_text(msgs)
        assert "Describe this" in text
        assert "image_url" not in text

    def test_non_dict_entries_skipped(self):
        result = _messages_to_text(["not a dict", None, {"role": "user", "content": "hi"}])
        assert "hi" in result

    def test_empty_messages(self):
        assert _messages_to_text([]) == ""


# ── estimate_request ──────────────────────────────────────────────────────────

class TestEstimateRequest:
    def _body(self, payload: dict) -> bytes:
        return json.dumps(payload).encode()

    def test_ollama_chat_format(self):
        body = self._body({
            "model": "llama3.1",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        })
        result = estimate_request(body)
        assert result > 0

    def test_ollama_generate_format(self):
        body = self._body({
            "model": "llama3.1",
            "prompt": "Tell me a joke about programming.",
        })
        result = estimate_request(body)
        assert result > 0

    def test_openai_chat_format(self):
        body = self._body({
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user",   "content": "Explain quantum entanglement."},
            ],
        })
        result = estimate_request(body)
        assert result > 0

    def test_anthropic_format_with_system(self):
        body = self._body({
            "model": "claude-sonnet-4-6",
            "system": "You are a coding assistant.",
            "messages": [{"role": "user", "content": "Write hello world in Python."}],
        })
        result = estimate_request(body)
        assert result > 0

    def test_embedding_string_input(self):
        body = self._body({"model": "text-embedding-3-small", "input": "embed this text"})
        result = estimate_request(body)
        assert result > 0

    def test_embedding_list_input(self):
        body = self._body({"model": "text-embedding-ada-002", "input": ["hello", "world"]})
        result = estimate_request(body)
        assert result > 0

    def test_empty_body_returns_zero(self):
        assert estimate_request(b"") == 0

    def test_invalid_json_returns_zero(self):
        assert estimate_request(b"not json {{{") == 0

    def test_model_hint_used_when_no_model_in_body(self):
        body = self._body({"messages": [{"role": "user", "content": "hi"}]})
        result = estimate_request(body, model_hint="gpt-4o")
        assert result > 0

    def test_longer_prompt_estimates_more_tokens(self):
        short_body = self._body({"model": "gpt-4", "messages": [
            {"role": "user", "content": "Hi."}
        ]})
        long_body = self._body({"model": "gpt-4", "messages": [
            {"role": "user", "content": "Hi. " * 200}
        ]})
        assert estimate_request(long_body) > estimate_request(short_body)

    def test_multiple_messages_overhead(self):
        one_msg = self._body({"model": "gpt-4", "messages": [
            {"role": "user", "content": "Hello"}
        ]})
        ten_msgs = self._body({"model": "gpt-4", "messages": [
            {"role": "user", "content": "Hello"} for _ in range(10)
        ]})
        assert estimate_request(ten_msgs) > estimate_request(one_msg)
