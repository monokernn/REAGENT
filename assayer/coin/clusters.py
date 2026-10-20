"""Holder clusters on Solana: the largest wallets, grouped by who funded them.

Jupiter returns the hundred largest holders of a mint, folded by owning wallet,
and with each one the address that first funded the wallet with SOL. Wallets
that share a funder, or where one holder funded another, form a cluster: the
same signal the familiar bubble maps draw their lines from.

The funding addresses are reported by Jupiter, and the result says so. The chain
is asked two things: the token's supply, so shares are of the whole supply, and
how busy a shared funder is. An exchange hot wallet or a busy bot funds wallets
by the thousand, and being paid by one says nothing about who you are, so such a
funder is listed as a hub and joins nobody.

Read-only like everything else here: Jupiter over HTTPS GET, the chain through
:class:`~assayer.coin.solana.SolanaRpc` and its read-only method list.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable

from .solana import INCINERATOR, SolanaRpc, b58decode, on_curve

JUPITER_HOLDERS = "https://datapi.jup.ag/v1/holders/{mint}"
SOURCE = "jupiter"

# Exchange hot wallets that fund wallets by the thousand.
EXCHANGES = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase",
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE": "Coinbase",
    "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD": "OKX",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "Bybit",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5": "Kraken",
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ": "MEXC",
}
HUB_TXS = 1000              # this many transactions …
HUB_WINDOW_S = 24 * 3600    # … inside a day makes an address a hub


def fetch_holders(mint: str, timeout_s: float = 15.0) -> dict:
    """Jupiter's holder list for a mint, as it answers."""
    request = urllib.request.Request(JUPITER_HOLDERS.format(mint=mint),
                                     headers={"User-Agent": "assayer/0.3 (read-only)",
                                              "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def _off_curve(address: str) -> bool:
    try:
        return not on_curve(b58decode(address))
    except Exception:                       # noqa: BLE001 — unreadable stays a wallet
        return False


def _kind(row: dict) -> str:
    """A person's position, a pool, a lock, or burnt supply."""
    address = str(row.get("address") or "")
    names = " ".join(str(t.get("name") or t.get("id") or "") for t in row.get("tags") or []).lower()
    if address == INCINERATOR:
        return "burn"
    if "pool" in names or "amm" in names:
        return "pool"
    if "lock" in names or "vest" in names or _off_curve(address):
        return "locked"
    return "holder"


def _is_hub(rpc, address: str) -> bool:
    if address in EXCHANGES:
        return True
    try:
        sigs = rpc.call("getSignaturesForAddress", [address, {"limit": HUB_TXS}]) or []
    except Exception:                       # noqa: BLE001 — unknown stays a plain funder
        return False
    if len(sigs) < HUB_TXS:
        return False
    newest, oldest = sigs[0].get("blockTime"), sigs[-1].get("blockTime")
    return bool(newest and oldest and newest - oldest < HUB_WINDOW_S)


def read_clusters(mint: str, rpc=None, holders: Callable[[str], dict] | None = None) -> dict[str, Any]:
    """The clusters among a mint's largest wallets, with the share of supply each holds."""
    rpc = rpc or SolanaRpc()
    data = (holders or fetch_holders)(mint)
    rows = [r for r in (data or {}).get("holders") or [] if r.get("address")]
    try:
        supply = float(rpc.call("getTokenSupply", [mint])["value"]["uiAmountString"])
    except Exception:                       # noqa: BLE001 — shares of the sample instead
        supply = None
    total = supply or sum(float(r.get("amount") or 0) for r in rows) or 1.0

    wallets: dict[str, dict] = {}
    for row in rows:
        info = row.get("addressInfo") or {}
        wallets[row["address"]] = {
            "address": row["address"], "kind": _kind(row),
            "share_pct": round(float(row.get("amount") or 0) * 100 / total, 4),
            "funded_by": info.get("fundingAddress"), "funded_at": info.get("fundingBlockTime"),
        }

    funded: dict[str, list[str]] = {}
    for w in wallets.values():
        if w["kind"] == "holder" and w["funded_by"]:
            funded.setdefault(w["funded_by"], []).append(w["address"])

    hubs, links = [], []
    for funder, paid in funded.items():
        if funder in wallets:
            links += [(funder, p) for p in paid]          # one holder funded another
        elif len(paid) >= 2:
            if _is_hub(rpc, funder):
                hubs.append({"address": funder, "label": EXCHANGES.get(funder, "busy hub"),
                             "funded_wallets": len(paid)})
            else:
                links += [(funder, p) for p in paid]

    # wallets joined through a shared funder or a direct link
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for address, w in wallets.items():
        if w["kind"] == "holder" and address in parent:
            groups.setdefault(find(address), []).append(address)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: -wallets[m]["share_pct"])
        via = sorted({a for a, b in links if b in members and a not in wallets})
        clusters.append({
            "share_pct": round(sum(wallets[m]["share_pct"] for m in members), 4),
            "wallets": [{"address": m, "share_pct": wallets[m]["share_pct"]} for m in members],
            "funded_from": via,
        })
    clusters.sort(key=lambda c: -c["share_pct"])

    set_apart = {}
    for kind in ("pool", "locked", "burn"):
        share = sum(w["share_pct"] for w in wallets.values() if w["kind"] == kind)
        if share:
            set_apart[kind] = round(share, 4)
    return {
        "chain": "solana", "mint": mint, "source": SOURCE, "read_at": int(time.time()),
        "supply": supply, "sample": len(wallets),
        "clusters": clusters,
        "clustered_wallets": sum(len(c["wallets"]) for c in clusters),
        "largest_cluster_pct": clusters[0]["share_pct"] if clusters else 0.0,
        "hubs": sorted(hubs, key=lambda h: -h["funded_wallets"]),
        "set_apart_pct": set_apart,
    }
