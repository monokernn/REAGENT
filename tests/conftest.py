"""Shared harness: a throwaway project root, a fake chain, a fake fetcher.

Nothing in the test suite touches the network or a model. Both are injected.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assayer import model, util                                    # noqa: E402
from assayer.keccak import keccak256                               # noqa: E402
from assayer.snapshot import chain as chain_mod                    # noqa: E402
from assayer.snapshot import docs as docs_mod                      # noqa: E402

CONFIG_TEMPLATE = """
[agent]
command = "no-such-agent-cli"
args = ["-p"]
response_field = "result"
cost_field = "total_cost_usd"

[budget]
max_usd_per_day = 2.00
max_usd_per_extraction = 0.40

[rpc]

[sources]
allowed_domains = ["issuer.example", "docs.issuer.example"]

[corpus]
max_doc_mb = 40
ocr = false

[gate]
taint_threshold = 3
halt_on_injection = true

[gate.injection_patterns]
en = ["ignore previous", "disregard the above", "system:", "you are an ai",
      "new instructions", "the report must"]
ru = ["игнорируй предыдущие", "отчёт должен"]

[language]
forbidden = ["safe", "risky", "buy", "sell", "scam", "legit", "reliable",
             "audited", "guaranteed", "risk score", "investment advice",
             "надёжный", "рискованный", "покупать", "продавать"]
"""


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "assayer").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "state").mkdir()
    (root / "config.toml").write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return root


@pytest.fixture
def paths(home: Path) -> util.Paths:
    return util.Paths(home)


@pytest.fixture
def config(paths: util.Paths) -> dict:
    return util.load_config(paths)


# ---------------------------------------------------------------- fake chain

def code_with(*signatures: str, delegatecall: bool = False,
              padding: int = 0) -> bytes:
    """Runtime bytecode that answers the given signatures, as a dispatch table."""
    out = bytearray(b"\x60\x80\x60\x40\x52")  # PUSH1 80 PUSH1 40 MSTORE
    for signature in signatures:
        out += b"\x63" + keccak256(signature.encode("ascii"))[:4]
        out += b"\x14\x60\x20\x57"  # EQ PUSH1 20 JUMPI
    if delegatecall:
        out += b"\xf4"
    out += b"\x00" * padding
    return bytes(out)


@dataclass
class FakeRpc:
    """Read-only JSON-RPC double. Any method it does not know raises."""

    block: int = 21_000_000
    chain_id: int = 1
    code: dict[str, bytes] = field(default_factory=dict)
    storage: dict[tuple[str, str], str] = field(default_factory=dict)
    calls: dict[tuple[str, str], str] = field(default_factory=dict)
    balances: dict[tuple[str, str], int] = field(default_factory=dict)
    timestamp: int = 1_800_000_000
    fail_on: set[str] = field(default_factory=set)
    seen: list[tuple[str, list]] = field(default_factory=list)

    def call(self, method: str, params: list):
        self.seen.append((method, params))
        if method in self.fail_on:
            raise chain_mod.RpcError(f"fake failure for {method}")
        if method not in chain_mod.READ_ONLY_METHODS:
            raise chain_mod.RpcError(f"method not permitted: {method}")
        if method == "eth_blockNumber":
            return hex(self.block)
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_getCode":
            return "0x" + self.code.get(str(params[0]).lower(), b"").hex()
        if method == "eth_getStorageAt":
            key = (str(params[0]).lower(), str(params[1]).lower())
            return self.storage.get(key, "0x" + "0" * 64)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(self.timestamp), "number": hex(self.block)}
        if method == "eth_call":
            to = str(params[0].get("to", "")).lower()
            data = str(params[0].get("data", ""))
            if data[:10] == chain_mod.SEL["balance_of"] and len(data) >= 74:
                # balanceOf takes an argument, so unlike every other call here
                # the answer depends on it: the holder is decoded rather than
                # collapsed into one reply per token.
                holder = "0x" + data[-40:].lower()
                return "0x" + format(self.balances.get((to, holder), 0), "064x")
            return self.calls.get((to, data[:10]), "0x")
        raise chain_mod.RpcError(f"unexpected method {method}")

    # -- helpers ------------------------------------------------------
    def set_call(self, address: str, signature: str, value: str) -> None:
        self.calls[(address.lower(), chain_mod.selector(signature))] = value

    def set_uint(self, address: str, signature: str, value: int) -> None:
        self.set_call(address, signature, "0x" + format(value, "064x"))

    def set_address(self, address: str, signature: str, value: str) -> None:
        self.set_call(address, signature,
                      "0x" + "0" * 24 + value.lower().replace("0x", ""))

    def set_balance(self, token: str, holder: str, value: int) -> None:
        self.balances[(token.lower(), holder.lower())] = value

    def set_storage(self, address: str, slot: str, value_address: str) -> None:
        self.storage[(address.lower(), slot.lower())] = \
            "0x" + "0" * 24 + value_address.lower().replace("0x", "")


TOKEN = "0x1111111111111111111111111111111111111111"
OWNER_EOA = "0x2222222222222222222222222222222222222222"
IMPL = "0x3333333333333333333333333333333333333333"


@pytest.fixture
def rpc() -> FakeRpc:
    """A token whose issuer can mint, pause, freeze and burn other balances."""
    fake = FakeRpc()
    fake.code[TOKEN] = code_with(
        "name()", "symbol()", "decimals()", "totalSupply()", "owner()",
        "mint(address,uint256)", "burn(address,uint256)", "pause()", "paused()",
        "blacklist(address)", "isBlacklisted(address)")
    fake.code[OWNER_EOA] = b""
    fake.set_uint(TOKEN, "decimals()", 18)
    fake.set_uint(TOKEN, "totalSupply()", 1_000_000 * 10 ** 18)
    fake.set_address(TOKEN, "owner()", OWNER_EOA)
    fake.set_call(TOKEN, "paused()", "0x" + "0" * 64)
    return fake


# ---------------------------------------------------------------- fake fetcher

class FakeFetcher:
    """Serves canned documents and records every URL it was asked for.

    Enforces the same whitelist and redirect rules as the real fetcher, so tests
    exercise the policy rather than a simplified copy of it.
    """

    def __init__(self, allowed, routes: dict):
        self.allowed = list(allowed)
        self.routes = dict(routes)
        self.asked: list[str] = []

    def fetch(self, url: str) -> docs_mod.FetchResult:
        self.asked.append(url)
        if not docs_mod.domain_allowed(url, self.allowed):
            raise docs_mod.SourceRefused(url, "domain_not_whitelisted")
        chain: list[str] = []
        current = url
        for _hop in range(5):
            entry = self.routes.get(current)
            if entry is None:
                raise FileNotFoundError(f"no route for {current}")
            if isinstance(entry, str):  # a redirect target
                chain.append(entry)
                if not docs_mod.domain_allowed(entry, self.allowed):
                    raise docs_mod.SourceRefused(entry,
                                                 "redirect_outside_whitelist")
                current = entry
                continue
            content_type, data = entry
            return docs_mod.FetchResult(url=url, final_url=current, status=200,
                                        content_type=content_type, data=data,
                                        redirects=chain)
        raise docs_mod.SourceRefused(url, "too_many_redirects")


# ---------------------------------------------------------------- assets

def write_asset(paths: util.Paths, documents: list[tuple[str, str, str | None]],
                *, label: str = "Assay Note", address: str = TOKEN,
                chain: str = "ethereum", reserves: dict | None = None) -> model.Asset:
    asset = model.Asset(
        chain=chain, address=address, label=label,
        documents=[model.Document(doc_id=doc_id, url=url, version=version)
                   for doc_id, url, version in documents],
        reserves=reserves or {}, chain_id=model.CHAIN_IDS.get(chain))
    text = model.asset_to_toml(asset)
    if reserves:
        lines = ["", "[reserves]"]
        for key, value in reserves.items():
            lines.append(f"{key} = " + (f'"{value}"' if isinstance(value, str)
                                        else str(value)))
        text += "\n".join(lines) + "\n"
    util.atomic_write_text(paths.assets / f"{asset.name}.toml", text)
    return model.load_asset(paths, asset.name)


@pytest.fixture
def asset(paths: util.Paths) -> model.Asset:
    return write_asset(paths, [("prospectus",
                                "https://docs.issuer.example/prospectus.pdf",
                                "2026-01")])


PROSPECTUS_URL = "https://docs.issuer.example/prospectus.pdf"
CUSTODY_URL = "https://docs.issuer.example/custody.html"


def take_snapshot(paths: util.Paths, config: dict, asset_obj: model.Asset,
                  rpc_double, routes: dict, *, now=None,
                  allowed: list[str] | None = None):
    """Build one snapshot with the network and the chain both faked."""
    from assayer.snapshot import build as build_mod

    allowed = allowed if allowed is not None else \
        list((config.get("sources") or {}).get("allowed_domains") or [])
    fetcher = FakeFetcher(allowed, routes)
    result = build_mod.build(paths, config, asset_obj, rpc=rpc_double,
                             fetcher=fetcher, now=now)
    result.fetcher = fetcher  # type: ignore[attr-defined]
    return result


def write_extraction(paths: util.Paths, snapshot: dict, slots: list[dict], *,
                     cost_usd: float = 0.1234,
                     parse_errors: list[str] | None = None) -> dict:
    """Store an extraction exactly as the agent would, without calling one."""
    from assayer.extract.schema import extraction_hash

    agent_meta = {"command": "fake", "args": []}
    prompt_hash = "0" * 64
    ehash = extraction_hash(str(snapshot["snapshot_hash"]), prompt_hash, slots,
                            agent_meta)
    extraction = {
        "schema_version": 1,
        "extraction_hash": ehash,
        "asset": snapshot.get("asset"),
        "snapshot_ts": snapshot.get("ts"),
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "docs_hash": snapshot.get("docs_hash"),
        "prompt_hash": prompt_hash,
        "agent": agent_meta,
        "documents": [],
        "truncated_documents": [],
        "slots": slots,
        "challenge": [],
        "parse": {"attempts": 1, "errors": list(parse_errors or [])},
        "created_at": "2026-02-01T00:00:00Z",
    }
    util.write_json(paths.extractions / ehash / "extraction.json", extraction)
    util.write_json(paths.extractions / ehash / "cost.json", {"usd": cost_usd})
    util.write_json(paths.extractions / "by-snapshot" /
                    f"{snapshot['snapshot_hash']}.json",
                    {"extraction_hash": ehash, "asset": "test",
                     "snapshot_ts": snapshot.get("ts")})
    return extraction


def document_evidence(snapshot: dict, doc_id: str, quote: str, *,
                      start: int | None = None,
                      end: int | None = None) -> dict:
    sha = next(d["sha256"] for d in snapshot["documents"]
               if d["doc_id"] == doc_id and d["state"] == "stored")
    evidence: dict = {"kind": "document", "doc_id": doc_id, "sha256": sha,
                      "quote": quote}
    if start is not None:
        evidence["locator"] = {"type": "char_range", "start": start, "end": end}
    return evidence
