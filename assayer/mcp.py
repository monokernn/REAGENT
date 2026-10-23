"""REAGENT as an MCP server: any agent can read a token before it trades.

The Model Context Protocol lets an assistant — Claude, an IDE, a trading bot —
call tools a local program offers. This module offers the readings: the same
reader the CLI snapshots with, the same slots, the same evidence, handed over as
JSON-RPC on stdin and stdout so nothing has to be installed beside the package.

Five tools, all read-only:

    read_token       read a token from its contract now; nothing is stored
    holder_clusters  the largest wallets of a Solana token, grouped by who funded them
    wallet_tokens    the tokens a Solana wallet holds, and what each mint still permits
    list_assets      the assets registered in this project
    show_dossier     the latest sheet written for one of them

An agent gets what a person gets: every reading with the call it came from and
the block or slot it was read at, and readings that could not be taken marked
as such with the reason. Nothing is summarised into a verdict, so the agent
cannot be handed one to repeat.

Run it with ``assayer mcp``. Claude Code: ``claude mcp add reagent -- assayer mcp``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import __version__, crew as crew_mod, model, util
from .model import COIN_SLOTS, FILLED, NOT_CHECKED, NOT_FOUND, E_ONCHAIN
from .render import _wrap

PROTOCOL = "2025-06-18"
SERVER = {"name": "reagent", "title": "REAGENT", "version": __version__}
INSTRUCTIONS = (
    "REAGENT reads tokens from their contracts. Every reading names the call it "
    "came from. Pass readings on as they are: they describe what the contract "
    "permits, and the person decides what to do with that.")

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}

TOOLS = [
    {
        "name": "read_token",
        "title": "Read a token from its contract",
        "description": (
            "Read a token straight from the chain: who can still mint it, freeze "
            "it, pause it, upgrade it or tax its transfers, how much the key holder "
            "owns, what is burned, and what sits in the pool. Every reading cites "
            "the RPC call it came from. Readings that could not be taken are "
            "marked not_checked with the reason. Read-only, free, nothing stored."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string",
                            "description": "the mint (Solana) or contract (EVM) address"},
                "chain": {"type": "string", "default": "solana",
                          "description": "solana, or an EVM chain with an [rpc] entry "
                                         "in config.toml: " + ", ".join(model.CHAIN_IDS)},
                "pool": {"type": "string",
                         "description": "optional: the pool the token trades in; "
                                        "without it liquidity reads not_checked"},
            },
            "required": ["address"],
            "additionalProperties": False,
        },
        "annotations": {**READ_ONLY, "openWorldHint": True},
    },
    {
        "name": "holder_clusters",
        "title": "Holder clusters of a Solana token",
        "description": (
            "The 100 largest wallets of a Solana token, grouped into clusters by the "
            "address that first funded each one with SOL, or where one holder funded "
            "another. Each cluster comes with the share of supply it holds and the "
            "funder that joins it. Exchanges and busy hubs are listed apart. Funding "
            "addresses are reported by Jupiter; supply is read from the chain."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "the token's mint address"},
            },
            "required": ["address"],
            "additionalProperties": False,
        },
        "annotations": {**READ_ONLY, "openWorldHint": True},
    },
    {
        "name": "wallet_tokens",
        "title": "What a Solana wallet holds",
        "description": (
            "The tokens a Solana wallet holds, read from the chain, and for each mint "
            "every authority still live over it: mint, freeze, permanent delegate, "
            "pause, transfer hook, transfer fee. Each power names the account field it "
            "was read from. Tokens with live powers come first. Read any one of them in "
            "full with read_token."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "the wallet's address"},
            },
            "required": ["address"],
            "additionalProperties": False,
        },
        "annotations": {**READ_ONLY, "openWorldHint": True},
    },
    {
        "name": "list_assets",
        "title": "Registered assets",
        "description": "The assets registered in this REAGENT project, with the "
                       "number of sheets written for each.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {**READ_ONLY, "openWorldHint": False},
    },
    {
        "name": "show_dossier",
        "title": "Latest sheet for an asset",
        "description": "The latest sheet written for a registered asset, exactly as "
                       "the CLI prints it, with its citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string",
                          "description": "the asset's label, name or <chain>:<address>"},
            },
            "required": ["asset"],
            "additionalProperties": False,
        },
        "annotations": {**READ_ONLY, "openWorldHint": False},
    },
]


class ToolError(Exception):
    """A tool ran and could not answer: reported to the agent, not as a crash."""


# ---------------------------------------------------------------- the tools

def _reading(slot: model.Slot) -> dict:
    owner = crew_mod.owner_of(slot.slot)
    out = {"slot": slot.slot, "agent": owner.name if owner else None,
           "state": slot.state, "power_over_your_balance": bool(slot.material)}
    if slot.value is not None:
        out["value"] = slot.value
    if slot.reason:
        out["reason"] = slot.reason
    if slot.note:
        out["note"] = slot.note
    if slot.evidence is not None:
        out["evidence"] = slot.evidence.to_json()
    return out


def _short(address: str | None) -> str:
    text = str(address or "")
    return f"{text[:4]}…{text[-4:]}" if len(text) > 12 else text


def _cite(slot: model.Slot) -> str:
    """The call a reading came from, on its own line under the reading."""
    ev = slot.evidence
    if ev is None or ev.kind != E_ONCHAIN or not ev.method:
        return ""
    where = f" @ {_short(ev.address)}" if ev.address else ""
    return f"      ↳ {ev.method}{where}"


def _text(chain: str, address: str, report) -> str:
    facts = report.facts or {}
    at = "slot" if chain == model.SOLANA else "block"
    lines = [f"REAGENT  ·  {chain}:{address}  ·  {at} {report.block}"]
    name, symbol = (str(facts.get(k) or "").strip() or None for k in ("name", "symbol"))
    if name or symbol:
        lines.append("  calls itself " + " ".join(p for p in (name, f"({symbol})" if symbol else "") if p))
    if facts.get("total_supply"):
        tail = f", {facts['decimals']} decimals" if facts.get("decimals") is not None else ""
        lines.append(f"  total supply {facts['total_supply']}{tail}")
    live = [s.slot for s in report.slots if s.state == FILLED and s.material]
    lines += ["", f"POWERS LIVE: {len(live)}" + (f"  ({', '.join(live)})" if live else "")]
    order = {n: i for i, n in enumerate(COIN_SLOTS)}
    for slot in sorted(report.slots, key=lambda s: order.get(s.slot, 99)):
        owner = crew_mod.owner_of(slot.slot)
        who = f"{owner.name} · " if owner else ""
        if slot.state == NOT_CHECKED:
            detail = f". {slot.note}" if slot.note else ""
            lines += _wrap(f"? {who}{slot.slot}: not checked, {slot.reason}{detail}", 2)
            continue
        marker = "⚠" if slot.material and slot.state == FILLED else ("·" if slot.state == FILLED else "○")
        body = slot.value or ("none found" if slot.state == NOT_FOUND else "")
        lines += _wrap(f"{marker} {who}{slot.slot}: {body}", 2)
        if _cite(slot):
            lines.append(_cite(slot))
    for error in report.errors or []:
        lines.append(f"  ! {error}")
    lines += ["", "This reading shows what the contract records. The person decides."]
    return "\n".join(lines)


def read_token(config: dict, address: str, chain: str = model.SOLANA,
               pool: str | None = None, rpc=None) -> tuple[str, dict]:
    from .coin import read_coin
    chain = (chain or model.SOLANA).strip().lower()
    if chain != model.SOLANA and chain not in model.CHAIN_IDS:
        raise ToolError(f"unknown chain {chain!r}: use solana or one of "
                        f"{', '.join(model.CHAIN_IDS)}")
    try:
        chain, address = model.parse_ref(f"{chain}:{address}")
        if pool:
            pool = model.parse_ref(f"{chain}:{pool}")[1]
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    report = read_coin(config, chain, address, chain_id=model.CHAIN_IDS.get(chain),
                       pool=pool, rpc=rpc)
    data = {
        "chain": chain, "address": address,
        "block" if chain != model.SOLANA else "slot": report.block,
        "facts": report.facts or {},
        "powers_live": [s.slot for s in report.slots if s.state == FILLED and s.material],
        "readings": [_reading(s) for s in report.slots],
        "errors": list(report.errors or []),
    }
    return _text(chain, address, report), data


def _pct(value: float) -> str:
    return f"{value:.2f}%" if value >= 0.01 else f"{value:.4f}%"


def holder_clusters(config: dict, address: str, rpc=None, holders=None) -> tuple[str, dict]:
    from .coin import clusters as clusters_mod
    from .coin.solana import DEFAULT_RPCS, SolanaRpc, is_address
    if rpc is None:
        configured = (config.get("rpc") or {}).get(model.SOLANA)
        urls = [configured] if isinstance(configured, str) and configured else []
        rpc = SolanaRpc(urls + [u for u in DEFAULT_RPCS if u not in urls])
    address = str(address).strip()
    if not is_address(address):
        raise ToolError(f"not a Solana mint address: {address!r}")
    data = clusters_mod.read_clusters(address, rpc=rpc, holders=holders)
    if not data["sample"]:
        raise ToolError(f"{clusters_mod.SOURCE} returned no holders for {address}")
    lines = [f"REAGENT  ·  holder clusters  ·  solana:{address}",
             f"  the {data['sample']} largest wallets and the address that first funded each, "
             f"reported by {data['source']}", ""]
    found = data["clusters"]
    lines.append(f"CLUSTERS: {len(found)}  ·  {data['clustered_wallets']} wallets"
                 + (f"  ·  largest holds {_pct(data['largest_cluster_pct'])} of supply" if found else ""))
    for i, cluster in enumerate(found, 1):
        via = ", ".join(_short(f) for f in cluster["funded_from"]) or "a holder in the cluster"
        lines.append(f"  {i} · {len(cluster['wallets'])} wallets · {_pct(cluster['share_pct'])} · "
                     f"funded from {via}")
        lines.append("      " + "   ".join(f"{_short(w['address'])} {_pct(w['share_pct'])}"
                                          for w in cluster["wallets"][:6])
                     + ("   …" if len(cluster["wallets"]) > 6 else ""))
    if data["hubs"]:
        lines += ["", "FUNDED THROUGH EXCHANGES AND HUBS"]
        lines += [f"  {h['label']} {_short(h['address'])} · {h['funded_wallets']} wallets"
                  for h in data["hubs"]]
    if data["set_apart_pct"]:
        lines += ["", "SET APART: " + ", ".join(f"{k} {_pct(v)}" for k, v in data["set_apart_pct"].items())]
    return "\n".join(lines), data


def _qty(amount: float) -> str:
    return f"{amount:,.0f}" if amount >= 1000 else f"{amount:.6g}"


def wallet_tokens(config: dict, address: str, rpc=None) -> tuple[str, dict]:
    from .coin import wallet as wallet_mod
    from .coin.solana import DEFAULT_RPCS, SolanaRpc, is_address
    if rpc is None:
        configured = (config.get("rpc") or {}).get(model.SOLANA)
        urls = [configured] if isinstance(configured, str) and configured else []
        rpc = SolanaRpc(urls + [u for u in DEFAULT_RPCS if u not in urls])
    address = str(address).strip()
    if not is_address(address):
        raise ToolError(f"not a Solana wallet address: {address!r}")
    data = wallet_mod.read_wallet(address, rpc)
    lines = [f"REAGENT  ·  wallet  ·  solana:{address}"
             + (f"  ·  slot {data['slot']}" if data["slot"] else ""), ""]
    if not data["tokens"]:
        return "\n".join(lines + ["this wallet holds no tokens"]), data
    lines.append(f"TOKENS: {data['tokens']}  ·  read {data['read']}  ·  with live powers: {data['with_powers']}")
    for t in data["items"]:
        label = " ".join(x for x in (t["symbol"] and f"${t['symbol']}", _short(t["mint"])) if x)
        if t["powers_live"]:
            lines.append(f"  ⚠ {label}  ·  {_qty(t['amount'])} held  ·  live: {', '.join(t['powers_live'])}")
            for p in t["powers"]:
                lines.append(f"      ↳ {p['power']} held by {_short(p['holder'])} · {p['field']}")
        else:
            lines.append(f"  ○ {label}  ·  {_qty(t['amount'])} held  ·  no live power")
    lines += ["", "This reading shows what each mint permits. The person decides."]
    return "\n".join(lines), data


def list_assets(p: util.Paths) -> tuple[str, dict]:
    from .verify import gate as gate_mod
    assets = model.list_assets(p)
    rows = [{"name": a.name, "label": a.label, "chain": a.chain, "address": a.address,
             "profile": a.profile, "sheets": len(gate_mod.list_dossiers(p, a))}
            for a in assets]
    if not rows:
        return "no assets registered: assayer add <chain>:<address> --coin", {"assets": []}
    text = "\n".join(f"{r['label'] or r['name']}  {r['chain']}:{r['address']}  "
                     f"{r['profile']}  sheets: {r['sheets']}" for r in rows)
    return text, {"assets": rows}


def show_dossier(p: util.Paths, asset: str) -> tuple[str, dict]:
    from .verify import gate as gate_mod
    try:
        found = model.load_asset(p, asset)
    except (FileNotFoundError, ValueError) as exc:
        raise ToolError(str(exc)) from None
    stamps = gate_mod.list_dossiers(p, found)
    if not stamps:
        raise ToolError(f"no sheet yet for {found.label or found.name}: assayer snap {found.name}")
    path = p.dossiers / found.name / stamps[-1] / "dossier.txt"
    return path.read_text(encoding="utf-8"), {"asset": found.name, "ts": stamps[-1]}


# ---------------------------------------------------------------- the protocol

class Server:
    """One MCP session over newline-delimited JSON-RPC 2.0."""

    def __init__(self, paths: Callable[[], util.Paths], config: Callable[[], dict], rpc=None,
                 holders=None):
        self._paths, self._config, self.rpc, self.holders = paths, config, rpc, holders

    def handle(self, message: Any) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method, ident = message.get("method"), message.get("id")
        if method is None:                      # a response to us; we send no requests
            return None
        if ident is None:                       # a notification: never answered
            return None
        params = message.get("params") or {}
        if method == "initialize":
            asked = str(params.get("protocolVersion") or PROTOCOL)
            return _result(ident, {
                "protocolVersion": asked if asked <= PROTOCOL else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER,
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(ident, {})
        if method == "tools/list":
            return _result(ident, {"tools": TOOLS})
        if method == "tools/call":
            return self._call(ident, params)
        return _error(ident, METHOD_NOT_FOUND, f"no method {method!r}")

    def _call(self, ident, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(ident, INVALID_PARAMS, "arguments must be an object")
        try:
            if name == "read_token":
                if not args.get("address"):
                    raise ToolError("address is required")
                text, data = read_token(self._config(), str(args["address"]),
                                        str(args.get("chain") or model.SOLANA),
                                        args.get("pool"), rpc=self.rpc)
            elif name == "holder_clusters":
                if not args.get("address"):
                    raise ToolError("address is required")
                text, data = holder_clusters(self._config(), str(args["address"]), rpc=self.rpc,
                                             holders=self.holders)
            elif name == "wallet_tokens":
                if not args.get("address"):
                    raise ToolError("address is required")
                text, data = wallet_tokens(self._config(), str(args["address"]), rpc=self.rpc)
            elif name == "list_assets":
                text, data = list_assets(self._paths())
            elif name == "show_dossier":
                if not args.get("asset"):
                    raise ToolError("asset is required")
                text, data = show_dossier(self._paths(), str(args["asset"]))
            else:
                return _error(ident, INVALID_PARAMS, f"no tool {name!r}")
        except ToolError as exc:
            return _result(ident, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:                # the session outlives one failed read
            return _result(ident, {"content": [{"type": "text",
                                                "text": f"{type(exc).__name__}: {exc}"}],
                                   "isError": True})
        return _result(ident, {"content": [{"type": "text", "text": text}],
                               "structuredContent": data, "isError": False})

    def serve(self, stdin: BinaryIO, stdout: BinaryIO) -> int:
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                reply = _error(None, PARSE_ERROR, "invalid JSON")
            else:
                if isinstance(message, list):   # a batch
                    replies = [r for r in (self.handle(m) for m in message) if r]
                    reply = replies or None
                else:
                    reply = self.handle(message)
            if reply is not None:
                stdout.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
                stdout.flush()
        return 0


def _result(ident, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def serve(home=None) -> int:
    """Run on stdin/stdout. Nothing else may write to stdout while this runs."""
    def paths() -> util.Paths:
        return util.Paths(Path(home).resolve()) if home else util.paths()

    def config() -> dict:
        try:
            return util.load_config(paths())
        except Exception:                       # no project around: public endpoints
            return {}

    return Server(paths, config).serve(sys.stdin.buffer, sys.stdout.buffer)
