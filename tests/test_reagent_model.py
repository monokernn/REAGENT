"""REAGENT's own model: selected by [model], reached over the chat API, kept out of files."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assayer.extract import agent as agent_mod


class FakeModel:
    """A chat-completions endpoint that records what it was sent."""

    def __init__(self, reply="{}", status=200, usage=None):
        self.reply, self.status, self.usage = reply, status, usage or {}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):                          # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length) or b"{}"),
                })
                payload = json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": outer.reply}}],
                    "usage": outer.usage,
                }).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_model():
    model = FakeModel(reply='{"slots": []}',
                      usage={"prompt_tokens": 1000, "completion_tokens": 200})
    yield model
    model.close()


def reagent_config(**model):
    return {"model": {"provider": "reagent", **model},
            "agent": {"command": "no-such-agent-cli", "args": []}}


def test_the_reagent_model_answers_when_its_endpoint_is_set(monkeypatch, fake_model):
    monkeypatch.setenv("REAGENT_MODEL_URL", fake_model.url)
    monkeypatch.setenv("REAGENT_MODEL_KEY", "sk-test-secret")
    monkeypatch.setenv("REAGENT_MODEL", "reagent-test")
    config = reagent_config(usd_per_mtok_in=1.0, usd_per_mtok_out=5.0)

    available, detail = agent_mod.agent_available(config)
    assert available and "reagent-test" in detail and "secret" not in detail

    call = agent_mod.call_agent(config, "read this token")
    assert call.response == '{"slots": []}'
    assert call.cost_usd == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1e6)
    assert call.command == ["reagent", "reagent-test"]

    sent = fake_model.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["auth"] == "Bearer sk-test-secret"
    assert sent["body"]["model"] == "reagent-test"
    assert sent["body"]["messages"] == [{"role": "user", "content": "read this token"}]
    assert agent_mod.model_meta(config) == {"provider": "reagent", "model": "reagent-test"}


def test_without_an_endpoint_the_agent_cli_takes_over(monkeypatch):
    config = reagent_config()
    assert agent_mod.model_settings(config) is None
    available, detail = agent_mod.agent_available(config)
    assert not available and "no-such-agent-cli" in detail
    assert agent_mod.model_meta(config)["command"] == "no-such-agent-cli"


def test_a_failing_model_never_prints_its_key_or_path(monkeypatch):
    model = FakeModel(status=500)
    try:
        monkeypatch.setenv("REAGENT_MODEL_URL", model.url.replace("/v1", "/private-path/v1"))
        monkeypatch.setenv("REAGENT_MODEL_KEY", "sk-test-secret")
        with pytest.raises(agent_mod.AgentFailed) as caught:
            agent_mod.call_agent(reagent_config(), "prompt")
        message = str(caught.value)
        assert "HTTP 500" in message
        assert "secret" not in message and "private-path" not in message
    finally:
        model.close()


def test_an_unreachable_model_is_reported_unavailable(monkeypatch):
    monkeypatch.setenv("REAGENT_MODEL_URL", "http://127.0.0.1:9/v1")
    with pytest.raises(agent_mod.AgentUnavailable):
        agent_mod.call_agent(reagent_config(timeout_s=1), "prompt")
