"""REAGENT over MCP: an agent asks, the reader answers, nothing is summarised.

No network. The Solana node is the same dictionary of accounts the coin tests
use, so every reading an agent receives is pinned to the field it came from.
"""

from __future__ import annotations

import io
import json
import pathlib
import re

from assayer import mcp, model, util
from assayer.coin import solana as sol

from test_solana import FEE_MINT, KEY_A, KEY_B, FakeSolana, mint_account

# the words the sheets are linted against, from the project's own config
FORBIDDEN = util.load_toml(pathlib.Path(__file__).parents[1] / "config.toml")["language"]["forbidden"]


def fee_node() -> FakeSolana:
    fee = {"extension": "transferFeeConfig", "state": {
        "transferFeeConfigAuthority": KEY_A, "withdrawWithheldAuthority": KEY_A,
        "newerTransferFee": {"epoch": 1, "maximumFee": 10, "transferFeeBasisPoints": 100}}}
    meta = {"extension": "tokenMetadata", "state": {
        "name": "Super Inu", "symbol": "SI", "updateAuthority": KEY_B}}
    return FakeSolana({FEE_MINT: mint_account(sol.TOKEN_2022_PROGRAM, extensions=[fee, meta])},
                      balances={KEY_A: 76_000_000_000_000})


def server(tmp_path, rpc=None) -> mcp.Server:
    (tmp_path / "assets").mkdir(exist_ok=True)
    return mcp.Server(lambda: util.Paths(tmp_path), lambda: {}, rpc=rpc)


def call(srv, name, **arguments):
    reply = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}})
    assert reply["id"] == 7
    return reply["result"]


def test_the_handshake_names_reagent_and_offers_tools(tmp_path):
    srv = server(tmp_path)
    reply = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "0"}}})
    result = reply["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "reagent"
    assert "tools" in result["capabilities"]
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_every_tool_is_declared_read_only(tmp_path):
    tools = server(tmp_path).handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {"read_token", "list_assets", "show_dossier"}
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["inputSchema"]["type"] == "object"


def test_a_token_is_read_with_its_live_powers_and_where_each_came_from(tmp_path):
    result = call(server(tmp_path, fee_node()), "read_token", address=FEE_MINT)
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["slot"] == 1234
    assert data["facts"]["symbol"] == "SI"
    assert {"ownership", "transfer_tax"} <= set(data["powers_live"])
    by = {r["slot"]: r for r in data["readings"]}
    assert len(by) == len(model.COIN_SLOTS)
    tax = by["transfer_tax"]
    assert tax["agent"] == "Clasp" and tax["power_over_your_balance"] is True
    assert tax["evidence"]["method"] and "100 basis points" in tax["value"]
    assert by["mint_authority"]["state"] == model.NOT_FOUND
    assert by["liquidity"]["state"] == model.NOT_CHECKED and by["liquidity"]["reason"]

    text = result["content"][0]["text"]
    assert text.startswith(f"REAGENT  ·  solana:{FEE_MINT}")
    assert f"POWERS LIVE: {len(data['powers_live'])}" in text
    assert "⚠ Clasp · transfer_tax" in text
    assert text.rstrip().endswith("The person decides.")


def test_what_an_agent_is_handed_carries_no_verdict(tmp_path):
    text = call(server(tmp_path, fee_node()), "read_token", address=FEE_MINT)["content"][0]["text"]
    for word in FORBIDDEN:
        assert not re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE), word


def test_a_bad_address_or_chain_is_an_answer_not_a_crash(tmp_path):
    srv = server(tmp_path, fee_node())
    bad = call(srv, "read_token", address="0xdeadbeef")
    assert bad["isError"] is True and "Solana address" in bad["content"][0]["text"]
    chain = call(srv, "read_token", address=FEE_MINT, chain="dogechain")
    assert chain["isError"] is True and "unknown chain" in chain["content"][0]["text"]
    missing = call(srv, "read_token")
    assert missing["isError"] is True


def test_an_empty_project_says_how_to_register(tmp_path):
    result = call(server(tmp_path), "list_assets")
    assert result["structuredContent"] == {"assets": []}
    assert "assayer add" in result["content"][0]["text"]
    none = call(server(tmp_path), "show_dossier", asset="nothing-here")
    assert none["isError"] is True


def test_unknown_methods_and_broken_json_get_protocol_errors(tmp_path):
    srv = server(tmp_path)
    assert srv.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["error"]["code"] == mcp.METHOD_NOT_FOUND
    assert srv.handle({"id": 4, "method": "ping"})["error"]["code"] == mcp.INVALID_REQUEST
    assert srv.handle({"jsonrpc": "2.0", "id": 5, "method": "ping"})["result"] == {}


def test_a_session_runs_over_stdio_one_line_per_message(tmp_path):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "read_token", "arguments": {"address": FEE_MINT}}},
    ]
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines) + b"not json\n")
    stdout = io.BytesIO()
    assert server(tmp_path, fee_node()).serve(stdin, stdout) == 0
    replies = [json.loads(l) for l in stdout.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, None]
    assert replies[1]["result"]["structuredContent"]["facts"]["name"] == "Super Inu"
    assert replies[2]["error"]["code"] == mcp.PARSE_ERROR
