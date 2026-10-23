"""What a Solana wallet holds, and what each mint in it still permits.

The wallet's token accounts are read from both token programs, then the mint
accounts behind them in batches of a hundred. For each mint the same authorities
the coin reader looks for are read straight off the account: who can still mint,
freeze, move or burn a holder's balance as a permanent delegate, pause transfers,
run a transfer hook, or change a transfer fee. Every power names the field it was
read from.

Tokens with live powers come first. Nothing is priced and nothing is graded: the
reading says what each mint permits, and `read_token` reads any one of them in full.
"""

from __future__ import annotations

from typing import Any

from .solana import TOKEN_2022_PROGRAM, TOKEN_PROGRAM, _ext

BATCH = 100
MAX_MINTS = 300

# extension -> (field holding the authority, the power it gives)
EXTENSION_POWERS = (
    ("permanentDelegate", "delegate", "permanent_delegate"),
    ("pausableConfig", "authority", "pause"),
    ("transferHook", "authority", "transfer_hook"),
    ("transferFeeConfig", "transferFeeConfigAuthority", "transfer_tax"),
)


def holdings(rpc, owner: str) -> dict[str, dict[str, Any]]:
    """Mint -> amount held and the token program, from every non-empty account."""
    out: dict[str, dict[str, Any]] = {}
    for program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        result = rpc.call("getTokenAccountsByOwner",
                          [owner, {"programId": program}, {"encoding": "jsonParsed"}])
        for acc in (result or {}).get("value") or []:
            info = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            mint = info.get("mint")
            amount = (info.get("tokenAmount") or {}).get("uiAmountString")
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                continue
            if mint and amount > 0:
                held = out.setdefault(mint, {"amount": 0.0, "program": program})
                held["amount"] += amount
    return out


def powers_of(info: dict) -> list[dict[str, str]]:
    """Every live authority on one parsed mint account, with the field it sits in."""
    found = []
    if info.get("mintAuthority"):
        found.append({"power": "mint", "holder": info["mintAuthority"],
                      "field": "getMultipleAccounts(mint).mintAuthority"})
    if info.get("freezeAuthority"):
        found.append({"power": "freeze", "holder": info["freezeAuthority"],
                      "field": "getMultipleAccounts(mint).freezeAuthority"})
    ext = _ext(info)
    for name, key, power in EXTENSION_POWERS:
        holder = (ext.get(name) or {}).get(key)
        if holder:
            found.append({"power": power, "holder": holder,
                          "field": f"getMultipleAccounts(mint).{name}.{key}"})
    return found


def read_wallet(owner: str, rpc) -> dict[str, Any]:
    held = holdings(rpc, owner)
    mints = sorted(held, key=lambda m: -held[m]["amount"])[:MAX_MINTS]
    items, slot = [], None
    for i in range(0, len(mints), BATCH):
        chunk = mints[i:i + BATCH]
        result = rpc.call("getMultipleAccounts", [chunk, {"encoding": "jsonParsed"}]) or {}
        slot = slot or (result.get("context") or {}).get("slot")
        for mint, account in zip(chunk, result.get("value") or []):
            info = (((account or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            ext = _ext(info)
            meta = ext.get("tokenMetadata") or {}
            powers = powers_of(info)
            items.append({
                "mint": mint,
                "amount": held[mint]["amount"],
                "program": "token-2022" if held[mint]["program"] == TOKEN_2022_PROGRAM else "spl-token",
                "symbol": meta.get("symbol"),
                "name": meta.get("name"),
                "powers_live": [p["power"] for p in powers],
                "powers": powers,
            })
    items.sort(key=lambda t: (-len(t["powers_live"]), t["mint"]))
    return {
        "owner": owner,
        "slot": slot,
        "tokens": len(held),
        "read": len(items),
        "with_powers": sum(1 for t in items if t["powers_live"]),
        "items": items,
    }
