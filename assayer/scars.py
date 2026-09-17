"""Registry of known failures, matched by signal intersection.

``state/scars.jsonl`` holds cases where holders of some tokenised asset lost
access, value or recourse, decomposed into the same signal names the snapshot
readers produce. Matching is set intersection — no model, no similarity score,
nothing to tune.

It is empty on a fresh install and useless for the first month. That is honest
and expected: the registry is a notebook, not a database shipped with the tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import util
from .model import FILLED, NOT_FOUND, Dossier, Slot, instrument_category

MIN_SHARED = 2

# The only signal names a snapshot produces. A registry entry outside this set
# can never match anything, so the writer is told so at the moment of writing.
SIGNAL_NAMES: tuple[str, ...] = (
    "upgradeable_logic",
    "upgrade_no_timelock",
    "immutable_logic",
    "issuer_can_freeze_address",
    "issuer_can_burn_foreign_balance",
    "issuer_can_halt_transfers",
    "issuer_can_mint",
    "single_key_control",
    "proxy_deployment",
    "attestation_overdue",
    "no_attestation_period",
    "self_attested_reserves",
    "tokens_exceed_declared_collateral",
    "no_custodian_named",
    "no_insolvency_section",
    "no_redemption_terms",
    "debt_claim_not_ownership",
    "offshore_issuer",
    # produced only under the coin profile
    "owner_not_given_up",
    "ownership_given_up",
    "transfers_can_be_switched",
    "transfer_carries_a_cut",
    "transfer_cut_is_movable",
    "transfer_caps_present",
    "owner_holds_a_large_share",
    "lp_mostly_parked",
    "lp_still_movable",
)

SIGNAL_MEANING: dict[str, str] = {
    "upgradeable_logic": "contract logic can be replaced in place",
    "upgrade_no_timelock": "upgrades take effect with no waiting period",
    "immutable_logic": "no known way to replace the logic",
    "issuer_can_freeze_address": "the issuer can block an address from "
                                 "moving its balance",
    "issuer_can_burn_foreign_balance": "the issuer can reduce a balance it "
                                       "does not own",
    "issuer_can_halt_transfers": "transfers can be halted",
    "issuer_can_mint": "supply can be increased",
    "single_key_control": "a material power is held by one key, not a multisig",
    "proxy_deployment": "the token is deployed behind a proxy",
    "attestation_overdue": "the reserve report is older than the promised period",
    "no_attestation_period": "no promised reporting period is declared",
    "self_attested_reserves": "reserves are reported with no named "
                              "independent attestor",
    "tokens_exceed_declared_collateral": "more tokens exist than declared "
                                         "collateral",
    "no_custodian_named": "no custodian is named in the documents",
    "no_insolvency_section": "the documents say nothing about issuer insolvency",
    "no_redemption_terms": "the documents say nothing about redemption terms",
    "debt_claim_not_ownership": "the holder has a claim on the issuer, not "
                                "a share of the asset",
    "offshore_issuer": "the issuer is incorporated in an offshore jurisdiction",
    "owner_not_given_up": "the contract still answers to a live owner",
    "ownership_given_up": "the owner position was handed to an address with "
                          "no known key",
    "transfers_can_be_switched": "transfers can be turned on or off after "
                                 "deployment",
    "transfer_carries_a_cut": "a fee is taken out of a transfer",
    "transfer_cut_is_movable": "that fee can be changed after deployment",
    "transfer_caps_present": "a per-transaction or per-wallet cap is kept",
    "owner_holds_a_large_share": "the owner address holds five percent of "
                                 "supply or more",
    "lp_mostly_parked": "most of the pool's LP token sits at an address with "
                        "no known key",
    "lp_still_movable": "most of the pool's LP token can still be moved",
}


def unknown_signals(signals: Iterable[str]) -> list[str]:
    """Names that no snapshot produces, and which therefore cannot match."""
    return sorted({str(s) for s in signals} - set(SIGNAL_NAMES))


OFFSHORE = (
    "jersey", "guernsey", "isle of man", "cayman", "bvi", "british virgin",
    "bermuda", "bahamas", "panama", "seychelles", "belize", "liechtenstein",
    "marshall islands", "gibraltar", "джерси", "кайман", "сейшел", "панам",
    "бермуд", "гибралтар", "лихтенштейн",
)


@dataclass
class Scar:
    id: str
    date: str
    what: str
    signals: list[str] = field(default_factory=list)
    source: str | None = None

    def to_json(self) -> dict:
        out = {"id": self.id, "date": self.date, "what": self.what,
               "signals": sorted(self.signals)}
        if self.source:
            out["source"] = self.source
        return out


def load(path: Path) -> list[Scar]:
    if not Path(path).exists():
        return []
    out: list[Scar] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(Scar(id=str(raw.get("id")), date=str(raw.get("date", "")),
                        what=str(raw.get("what", "")),
                        signals=[str(s) for s in raw.get("signals", [])],
                        source=raw.get("source")))
    return out


def append(path: Path, scar: Scar) -> None:
    line = json.dumps(scar.to_json(), sort_keys=True, ensure_ascii=False) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with util.FileLock(Path(str(path) + ".lock")):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


def next_id(path: Path) -> str:
    existing = load(path)
    numbers = [int(s.id) for s in existing if s.id.isdigit()]
    return f"{(max(numbers) + 1) if numbers else 1:03d}"


# ---------------------------------------------------------------- signals

def signals_of(legal: Iterable[Slot], onchain: Iterable[Slot],
               facts: dict | None = None) -> list[str]:
    """Signal names derived from slot states and from the structured readings.

    Deliberately not derived from the rendered sentences: prose is translated,
    reworded and reordered, and a registry that matched on prose would quietly
    stop matching one day. Everything that can come from ``facts`` comes from
    ``facts``.
    """
    facts = facts or {}
    out: set[str] = set()
    by_name = {s.slot: s for s in list(legal) + list(onchain)}
    controllers = facts.get("controllers") or {}

    upgrade = by_name.get("upgradeable")
    upgrade_facts = facts.get("upgradeable") or {}
    if upgrade is not None and upgrade.state == FILLED:
        out.add("upgradeable_logic")
        if not upgrade_facts.get("timelock_delay_s"):
            out.add("upgrade_no_timelock")
    if upgrade is not None and upgrade.state == NOT_FOUND:
        out.add("immutable_logic")

    freeze = by_name.get("freeze")
    if freeze is not None and freeze.state == FILLED:
        out.add("issuer_can_freeze_address")

    burn = by_name.get("burn_authority")
    if burn is not None and burn.state == FILLED and burn.material:
        out.add("issuer_can_burn_foreign_balance")

    pause = by_name.get("pause")
    if pause is not None and pause.state == FILLED:
        out.add("issuer_can_halt_transfers")

    mint = by_name.get("mint_authority")
    if mint is not None and mint.state == FILLED:
        out.add("issuer_can_mint")

    # A power held by one key, whichever power it is.
    holders = [(facts.get(key) or {}).get("owner")
               for key in ("mint", "burn", "pause", "freeze")]
    holders.append(upgrade_facts.get("controller") or upgrade_facts.get("admin"))
    for address in holders:
        if address and (controllers.get(address) or {}).get("kind") == "eoa":
            out.add("single_key_control")
            break

    attest = by_name.get("attestation_freshness")
    attest_facts = ((facts.get("reserves") or {}).get("attestation") or {})
    if attest is not None:
        if attest.state == FILLED and attest_facts.get("overdue"):
            out.add("attestation_overdue")
        if attest.state != FILLED:
            out.add("no_attestation_period")
        if attest.state == FILLED and not attest_facts.get("attestor"):
            # A reserve report exists and no independent attestor is declared.
            out.add("self_attested_reserves")

    supply_facts = ((facts.get("reserves") or {}).get("supply_vs_reserves") or {})
    if supply_facts.get("delta_sign") == 1:
        out.add("tokens_exceed_declared_collateral")

    custodian = by_name.get("custodian")
    if custodian is not None and custodian.state == NOT_FOUND:
        out.add("no_custodian_named")

    insolvency = by_name.get("insolvency")
    if insolvency is not None and insolvency.state == NOT_FOUND:
        out.add("no_insolvency_section")

    redemption = by_name.get("redemption")
    if redemption is not None and redemption.state == NOT_FOUND:
        out.add("no_redemption_terms")

    instrument = by_name.get("instrument_type")
    if instrument is not None and instrument.state == FILLED:
        category = instrument_category(instrument.value)
        if category in ("debt_security", "synthetic"):
            out.add("debt_claim_not_ownership")

    issuer = by_name.get("issuer")
    if issuer is not None and issuer.state == FILLED:
        lowered = (issuer.value or "").lower()
        if any(place in lowered for place in OFFSHORE):
            out.add("offshore_issuer")

    if facts.get("implementation"):
        out.add("proxy_deployment")

    # ---- coin-only signals ---------------------------------------------
    # Named for what was read, not for what it is taken to mean, so a case
    # written into the registry a year from now still intersects with them.
    ownership = by_name.get("ownership")
    if ownership is not None:
        if ownership.state == FILLED and ownership.material:
            out.add("owner_not_given_up")
        if ownership.state == FILLED and not ownership.material:
            out.add("ownership_given_up")

    switch = by_name.get("trading_switch")
    if switch is not None and switch.state == FILLED and switch.material:
        out.add("transfers_can_be_switched")

    tax = by_name.get("transfer_tax")
    if tax is not None and tax.state == FILLED:
        out.add("transfer_carries_a_cut")
        if (facts.get("transfer_tax") or {}).get("setters"):
            out.add("transfer_cut_is_movable")

    limits = by_name.get("transfer_limits")
    if limits is not None and limits.state == FILLED:
        out.add("transfer_caps_present")

    holdings = facts.get("owner_holdings") or {}
    share = holdings.get("share_pct")
    if share is not None and share >= 5.0:
        # Five percent is not a judgement about the coin: it is the line the
        # registry's own cases were decomposed against, and it is stated here
        # rather than hidden in a threshold file.
        out.add("owner_holds_a_large_share")

    liquidity = facts.get("liquidity") or {}
    burnt = liquidity.get("lp_burnt_pct")
    if burnt is not None:
        out.add("lp_mostly_parked" if burnt >= 50.0 else "lp_still_movable")

    return sorted(out)


def match(signals: Iterable[str], scars: Iterable[Scar],
          min_shared: int = MIN_SHARED) -> list[dict]:
    """Group scars by the exact signal set they share with this asset."""
    signals = set(signals)
    buckets: dict[tuple[str, ...], list[Scar]] = {}
    for scar in scars:
        shared = tuple(sorted(signals & set(scar.signals)))
        if len(shared) < min_shared:
            continue
        buckets.setdefault(shared, []).append(scar)
    out = []
    for shared in sorted(buckets, key=lambda k: (-len(k), k)):
        group = sorted(buckets[shared], key=lambda s: s.id)
        out.append({"shared_signals": list(shared),
                    "ids": [s.id for s in group],
                    "count": len(group)})
    return out


def match_dossier(dossier: Dossier, path: Path,
                  min_shared: int = MIN_SHARED) -> tuple[list[str], list[dict]]:
    signals = signals_of(dossier.legal, dossier.onchain)
    return signals, match(signals, load(path), min_shared)
