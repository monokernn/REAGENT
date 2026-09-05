"""Snapshot assembly and content addressing.

A snapshot is the free, trusted artefact: on-chain readings, stored documents,
injection findings, harvested links. It carries two hashes.

``docs_hash``
    over the document set only — what a model would be given to read.

``snapshot_hash``
    over ``docs_hash`` plus the on-chain readings — the semantic identity of the
    asset as observed.

Neither hash covers the block number or any wall clock, and numbers inside the
few genuinely volatile slot values are folded out (see
:data:`VOLATILE_VALUE_SLOTS`). Otherwise every fresh block would produce a fresh
identity, and "pay only when something changed" would degrade into "pay always".
The block number is recorded beside the hash, not inside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .. import util
from ..corpus import Corpus
from ..model import (
    ONCHAIN_SLOTS, SCHEMA_VERSION, VOLATILE_VALUE_SLOTS, Asset, Slot, not_checked,
)
from ..verify import injection as injection_mod
from . import chain as chain_mod
from . import docs as docs_mod
from . import reserves as reserves_mod

# Facts that change without anyone deciding anything. The reserve readings are
# derived from a supply that moves and a report that ages; the meaningful shape
# of both already reaches the hash through the digit-folded slot values.
VOLATILE_FACTS = frozenset({"total_supply", "selectors_seen", "reserves"})

_DIGITS = re.compile(r"\d[\d,._ ]*")


def fold_digits(value: str) -> str:
    """Replace digit runs with ``#``, leaving the shape of the sentence."""
    return _DIGITS.sub("#", value)


def compute_alerts(asset: Asset, facts: dict, slots: list[Slot]) -> list[dict]:
    """Compare the snapshot against the thresholds in the asset file.

    These are the holder's own numbers, not a judgement by this program: it does
    not decide that two days of timelock is enough, it reports that the number
    the holder wrote down was crossed. Every entry names the threshold, the
    reading, and which way it went, so nothing here is a score.
    """
    alerts: list[dict] = []
    declared = asset.alerts or {}
    if not declared:
        return alerts
    by_name = {s.slot: s for s in slots}
    upgrade = facts.get("upgradeable") or {}
    reserves = facts.get("reserves") or {}
    attestation = reserves.get("attestation") or {}
    supply = reserves.get("supply_vs_reserves") or {}

    def add(key: str, threshold, value, crossed: bool, text: str) -> None:
        alerts.append({"key": key, "threshold": threshold, "value": value,
                       "crossed": bool(crossed), "text": text})

    if "max_attestation_age_days" in declared:
        limit = int(declared["max_attestation_age_days"])
        age = attestation.get("age_days")
        if age is None:
            add("max_attestation_age_days", limit, None, False,
                "the age of the reserve report was not read, "
                "threshold not applied")
        else:
            add("max_attestation_age_days", limit, age, age > limit,
                f"the reserve report is {age} days old, threshold is "
                f"{limit} days"
                + (" (threshold crossed)" if age > limit else ""))

    if "min_timelock_delay_s" in declared:
        limit = int(declared["min_timelock_delay_s"])
        slot = by_name.get("upgradeable")
        if slot is not None and slot.state == "not_found":
            add("min_timelock_delay_s", limit, None, False,
                "an upgrade through a known pattern is impossible, "
                "threshold not applied")
        elif slot is not None and slot.state == "not_checked":
            add("min_timelock_delay_s", limit, None, False,
                f"the upgrade pattern was not read ({slot.reason}), "
                f"threshold not applied")
        else:
            delay = upgrade.get("timelock_delay_s")
            actual = int(delay or 0)
            add("min_timelock_delay_s", limit, actual, actual < limit,
                f"the delay on the upgrade path is {actual}s, minimum set "
                f"to {limit}s"
                + (" (threshold crossed)" if actual < limit else ""))

    if "max_supply_over_claim_pct" in declared:
        limit = float(declared["max_supply_over_claim_pct"])
        over = supply.get("over_pct")
        if over is None:
            add("max_supply_over_claim_pct", limit, None, False,
                "supply does not exceed the declared collateral, or the "
                "comparison was not made")
        else:
            add("max_supply_over_claim_pct", limit, over, over > limit,
                f"tokens exceed the declared collateral by {over}%, "
                f"threshold is {limit}%"
                + (" (threshold crossed)" if over > limit else ""))

    checks = (
        ("require_no_freeze", "freeze",
         "the issuer has an address-freeze function",
         "no address-freeze function in the code"),
        ("require_no_foreign_burn", "burn_authority",
         "the issuer can reduce a balance it does not own",
         "no function that reduces someone else's balance"),
        ("require_immutable_logic", "upgradeable",
         "the contract logic can be replaced in place",
         "no known pattern for replacing the logic"),
    )
    for key, slot_name, bad, good in checks:
        if not declared.get(key):
            continue
        slot = by_name.get(slot_name)
        if slot is None or slot.state == "not_checked":
            add(key, True, None, False,
                f"{slot_name} was not read, threshold not applied")
            continue
        if key == "require_no_foreign_burn":
            present = slot.state == "filled" and slot.material
        else:
            present = slot.state == "filled"
        add(key, True, present, present,
            (bad + " (threshold crossed)") if present else good)
    return alerts


@dataclass
class SnapResult:
    asset: Asset
    ts: str
    path: Path
    snapshot: dict
    changed: bool
    previous_ts: str | None
    previous_hash: str | None
    halting: list[dict]
    # Carried, not re-read: the previous snapshot as it was before this one landed.
    previous: dict | None = None

    @property
    def snapshot_hash(self) -> str:
        return self.snapshot["snapshot_hash"]

    @property
    def crossed_alerts(self) -> list[dict]:
        return [a for a in (self.snapshot.get("alerts") or []) if a.get("crossed")]


def _hash_view(slot: Slot) -> dict:
    view: dict[str, Any] = {"slot": slot.slot, "state": slot.state}
    if slot.reason:
        view["reason"] = slot.reason
    if slot.value is not None:
        value = slot.value
        if slot.slot in VOLATILE_VALUE_SLOTS:
            value = fold_digits(value)
        view["value"] = value
    return view


def compute_docs_hash(documents: Iterable[dict]) -> str:
    identity = sorted(
        (
            {
                "doc_id": d.get("doc_id"),
                "sha256": d.get("sha256"),
                "state": d.get("state"),
                "reason": d.get("reason"),
                "version": d.get("version"),
            }
            for d in documents
        ),
        key=lambda item: (str(item["doc_id"]), str(item["sha256"])),
    )
    return util.stable_hash(identity)


def compute_snapshot_hash(docs_hash: str, slots: Iterable[Slot], facts: dict,
                          asset: Asset) -> str:
    identity = {
        "asset": {"chain": asset.chain, "address": asset.address},
        "docs_hash": docs_hash,
        "slots": sorted((_hash_view(s) for s in slots),
                        key=lambda v: str(v["slot"])),
        "facts": {k: v for k, v in sorted(facts.items())
                  if k not in VOLATILE_FACTS},
    }
    return util.stable_hash(identity)


def previous_snapshot(p: util.Paths, asset: Asset,
                      before: str | None = None) -> tuple[str, dict] | None:
    directory = p.snapshots / asset.name
    if not directory.exists():
        return None
    for child in sorted((d for d in directory.iterdir() if d.is_dir()),
                        reverse=True):
        if before is not None and child.name >= before:
            continue
        candidate = child / "snapshot.json"
        if candidate.exists():
            return child.name, util.read_json(candidate)
    return None


def list_snapshots(p: util.Paths, asset: Asset) -> list[str]:
    directory = p.snapshots / asset.name
    if not directory.exists():
        return []
    return sorted(d.name for d in directory.iterdir()
                  if d.is_dir() and (d / "snapshot.json").exists())


def load_snapshot(p: util.Paths, asset: Asset, ts: str) -> dict:
    return util.read_json(p.snapshots / asset.name / ts / "snapshot.json")


def build(p: util.Paths, config: dict, asset: Asset, *,
          rpc: chain_mod.Rpc | None = None,
          fetcher: Any | None = None,
          now: datetime | None = None) -> SnapResult:
    """Take one snapshot. No model, no signing, no writes outside ``state/``."""
    now = now or util.utc_now()
    corpus = Corpus(p.corpus)
    allowed = list((config.get("sources") or {}).get("allowed_domains") or [])
    max_bytes = int(float((config.get("corpus") or {}).get("max_doc_mb", 40))
                    * 1024 * 1024)
    if fetcher is None:
        fetcher = docs_mod.UrllibFetcher(allowed, max_bytes)

    report = chain_mod.read_chain(config, asset.chain, asset.address,
                                  chain_id=asset.chain_id, rpc=rpc)
    doc_results = docs_mod.collect(asset.documents, fetcher=fetcher,
                                   corpus=corpus, allowed=allowed)

    patterns = injection_mod.flatten_patterns(
        (config.get("gate") or {}).get("injection_patterns"))
    findings: list[dict] = []
    suggested: list[dict] = []
    for result in doc_results:
        if result.state != "stored" or not result.sha256:
            continue
        doc = corpus.get(result.sha256)
        for finding in injection_mod.scan_document(
                doc.raw, doc.text, doc_id=result.doc_id, sha256=result.sha256,
                content_type=result.content_type or "", patterns=patterns):
            findings.append(finding.to_json())
        suggested += docs_mod.harvest_links(doc, result.doc_id, allowed)

    onchain_slots = list(report.slots)
    reserve_slots, reserve_facts = reserves_mod.read_reserves(
        asset, report.facts, doc_results, corpus, report.chain_id, report.block,
        now)
    onchain_slots += reserve_slots
    if reserve_facts:
        report.facts["reserves"] = reserve_facts
    known = {s.slot for s in onchain_slots}
    for name in ONCHAIN_SLOTS:
        if name not in known:
            onchain_slots.append(not_checked(name, "slot_not_produced",
                                            "no reader produced this slot"))
    onchain_slots.sort(key=lambda s: ONCHAIN_SLOTS.index(s.slot)
                       if s.slot in ONCHAIN_SLOTS else 99)

    documents = [r.to_json() for r in doc_results]
    docs_hash = compute_docs_hash(documents)
    snapshot_hash = compute_snapshot_hash(docs_hash, onchain_slots, report.facts,
                                          asset)

    merged_sources: dict[str, dict] = {}
    for entry in suggested:
        key = f"{entry['domain']}|{entry['doc_id']}"
        merged_sources[key] = entry

    previous = previous_snapshot(p, asset)
    ts = _free_ts(p, asset, now)
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "ts": ts,
        "created_at": util.iso(now),
        "asset": asset.to_json(),
        "block_number": report.block,
        "chain_id": report.chain_id,
        "facts": report.facts,
        "rpc_errors": report.errors,
        "documents": documents,
        "slots": {"onchain": [s.to_json() for s in onchain_slots]},
        "injections": findings,
        "suggested_sources": [merged_sources[k] for k in sorted(merged_sources)],
        "docs_hash": docs_hash,
        "snapshot_hash": snapshot_hash,
        # Derived from the readings above, so deliberately outside both hashes:
        # a threshold the holder edits is not a change in the asset.
        "alerts": compute_alerts(asset, report.facts, onchain_slots),
    }

    previous_ts = previous[0] if previous else None
    previous_hash = previous[1].get("snapshot_hash") if previous else None
    changed = previous_hash != snapshot_hash

    target = p.snapshots / asset.name / ts / "snapshot.json"
    util.write_json(target, snapshot)

    halting = [f.to_json() for f in injection_mod.halting(
        [_finding_from_json(f) for f in findings], config)]

    util.log_event(p, "snapshot", asset=asset.name, ts=ts,
                   snapshot_hash=snapshot_hash, docs_hash=docs_hash,
                   changed=changed, block=report.block,
                   documents_stored=sum(1 for r in doc_results
                                        if r.state == "stored"),
                   injections=len(findings), halting=len(halting))

    return SnapResult(asset=asset, ts=ts, path=target, snapshot=snapshot,
                      changed=changed, previous_ts=previous_ts,
                      previous_hash=previous_hash, halting=halting,
                      previous=previous[1] if previous else None)


def _free_ts(p: util.Paths, asset: Asset, now: datetime) -> str:
    """A timestamp slug not already taken.

    Two snapshots inside one second would otherwise land in the same directory
    and the older one would vanish, taking the diff with it. The slug moves
    forward a second at a time instead.
    """
    from datetime import timedelta

    directory = p.snapshots / asset.name
    moment = now
    for _ in range(120):
        ts = util.ts_slug(moment)
        if not (directory / ts / "snapshot.json").exists():
            return ts
        moment = moment + timedelta(seconds=1)
    raise RuntimeError(f"no free snapshot timestamp for {asset.name}")


def _finding_from_json(raw: dict) -> injection_mod.Finding:
    return injection_mod.Finding(
        doc_id=raw.get("doc_id", ""), sha256=raw.get("sha256", ""),
        surface=raw.get("surface", ""), technique=raw.get("technique", ""),
        severity=raw.get("severity", injection_mod.HIDDEN),
        excerpt=raw.get("excerpt", ""), page=raw.get("page"),
        where=raw.get("where"), pattern=raw.get("pattern"))
