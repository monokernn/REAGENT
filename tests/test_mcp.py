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
    assert {t["name"] for t in tools} == {"read_token", "holder_clusters", "list_assets", "show_dossier"}
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


# ---------------------------------------------------------------- holder clusters

MINT = FEE_MINT
# wallets that have signed transactions, so they sit on the curve like any holder
W1 = "9W575thhzDifUfgkwS4UVE1VAnQ7PLR7LLSyot14xVuK"
W2 = "8JdwN6FddHNfdr238TsAERAHWseCTw6mkpvWaBmwC8uC"
W3 = "HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r"
W4 = "DepjZCVRSAxc4mrfQ2e6NYtTpMpHJoWkP6gyfGwJJ73k"
FUNDER = "5KXDF6QnqhBj72hDtJNkkpFaQVUfbFXNybMsp3DiK6tD"
BUSY = "G" * 43 + "g"
BINANCE = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
POOL = "Bbru5XAuLKEY7E3cvVcddwa7imHoAQoX2yEBGgBKKfGo"


class ClusterChain:
    """Supply, and how busy an address is: the two things asked of the chain."""

    def __init__(self, busy=()):
        self.busy = set(busy)
        self.calls = []

    def call(self, method, params):
        self.calls.append(method)
        if method == "getTokenSupply":
            return {"value": {"uiAmountString": "1000"}}
        if method == "getSignaturesForAddress":
            many = params[0] in self.busy
            if many:
                return [{"blockTime": 2000}] + [{"blockTime": 1500}] * 998 + [{"blockTime": 1000}]
            return [{"blockTime": 2000}]
        raise AssertionError(method)


def jupiter(*rows):
    def holders(_mint):
        return {"holders": [{"address": a, "amount": amount, "tags": tags,
                             "addressInfo": {"fundingAddress": funder}} for a, amount, funder, tags in rows]}
    return holders


def clusters_server(tmp_path, holders, busy=()):
    (tmp_path / "assets").mkdir(exist_ok=True)
    return mcp.Server(lambda: util.Paths(tmp_path), lambda: {}, rpc=ClusterChain(busy), holders=holders)


def test_wallets_with_one_funder_are_one_cluster_with_their_share_of_supply(tmp_path):
    holders = jupiter((POOL, 300, None, [{"id": "Pool", "name": "Pumpfun AMM Liquidity Pool"}]),
                      (W1, 60, FUNDER, []), (W2, 40, FUNDER, []), (W3, 10, W2, []), (W4, 5, None, []))
    result = call(clusters_server(tmp_path, holders), "holder_clusters", address=MINT)
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["source"] == "jupiter" and data["supply"] == 1000.0 and data["sample"] == 5
    assert len(data["clusters"]) == 1
    cluster = data["clusters"][0]
    assert [w["address"] for w in cluster["wallets"]] == [W1, W2, W3]
    assert cluster["share_pct"] == 11.0 and cluster["funded_from"] == [FUNDER]
    assert data["set_apart_pct"] == {"pool": 30.0}
    text = result["content"][0]["text"]
    assert "CLUSTERS: 1  ·  3 wallets  ·  largest holds 11.00% of supply" in text
    assert "reported by jupiter" in text


def test_an_exchange_or_a_busy_funder_is_listed_apart_and_joins_nobody(tmp_path):
    holders = jupiter((W1, 50, BINANCE, []), (W2, 40, BINANCE, []), (W3, 30, BUSY, []), (W4, 20, BUSY, []))
    data = call(clusters_server(tmp_path, holders, busy=[BUSY]), "holder_clusters", address=MINT)["structuredContent"]
    assert data["clusters"] == []
    assert {h["address"]: h["label"] for h in data["hubs"]} == {BINANCE: "Binance", BUSY: "busy hub"}


def test_clusters_are_read_on_solana_and_carry_no_verdict(tmp_path):
    srv = clusters_server(tmp_path, jupiter((W1, 60, FUNDER, []), (W2, 40, FUNDER, [])))
    bad = call(srv, "holder_clusters", address="0x" + "ab" * 20)
    assert bad["isError"] is True and "Solana mint" in bad["content"][0]["text"]
    text = call(srv, "holder_clusters", address=MINT)["content"][0]["text"]
    for word in FORBIDDEN:
        assert not re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE), word


def test_the_solana_client_reads_signatures_and_still_refuses_to_send():
    assert "getSignaturesForAddress" in sol.READ_ONLY_METHODS
    client = sol.SolanaRpc(["http://127.0.0.1:1"])
    try:
        client.call("send" + "Transaction", [])
    except Exception as exc:
        assert "not permitted" in str(exc)
    else:
        raise AssertionError("a write method went through")
