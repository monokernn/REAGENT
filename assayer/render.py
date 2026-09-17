"""Dossier to text. One screen, two minutes, no verdicts.

Sections are fixed and separate. ``not_found``, ``not_checked`` and ``rejected``
get one section each and never share one: the first is a fact about the asset,
the second a fact about this tool's failure to look, the third a fact about the
model having been caught. Collapsing them would be the single most misleading
thing this program could do, so :mod:`assayer.verify.language` checks the
rendered text and fails the run if they ever merge.
"""

from __future__ import annotations

from . import diff as diff_mod
from .model import (
    COIN_SLOTS, E_ABSENCE, E_DOCUMENT, E_ONCHAIN, FILLED, LEGAL_SLOTS,
    NOT_CHECKED, NOT_FOUND, ONCHAIN_SLOTS, PROFILE_COIN, REJECTED, Dossier,
    Evidence, Slot,
)

H_TITLE = "ASSAY"
H_WHAT = "WHAT THIS IS"
H_CHANGES = "CHANGES"
H_POWERS = "WHAT THE ISSUER CAN DO"
H_ALERTS = "THRESHOLDS SET IN THE ASSET FILE"
H_NOT_FOUND = "NOT FOUND IN THE DOCUMENTS"
H_NOT_CHECKED = "NOT CHECKED"
H_REJECTED = "REJECTED BY THE GATE"
H_CHALLENGE = "HOW THIS CAN END FOR THE HOLDER"
H_SCARS = "SIMILAR CASES"
H_INJECTIONS = "TEXT FOUND THAT SPEAKS TO THE AGENT"
H_SOURCES = "SUGGESTED SOURCES"
H_CONTEXT = "CONTEXT (NOT EVIDENCE)"
H_FOOTER = "COMPLETENESS"
# The coin sheet's own name for the powers section. A memecoin has a deployer
# rather than an issuer, and the sentence under the header is about what the
# deployed code still permits, so the header says that.
H_POWERS_COIN = "WHAT THE CONTRACT STILL ALLOWS"

SECTION_HEADERS = (H_WHAT, H_CHANGES, H_POWERS, H_POWERS_COIN, H_ALERTS,
                   H_NOT_FOUND, H_NOT_CHECKED, H_REJECTED, H_CHALLENGE, H_SCARS,
                   H_INJECTIONS, H_SOURCES, H_CONTEXT)

# Which section each state is allowed to appear in.
STATE_SECTION = {
    NOT_CHECKED: H_NOT_CHECKED,
    REJECTED: H_REJECTED,
}
LEGAL_NOT_FOUND_SECTION = H_NOT_FOUND
ONCHAIN_NOT_FOUND_SECTION = H_POWERS

SLOT_LABEL = {
    "instrument_type": "instrument",
    "issuer": "issuer",
    "holder_rights": "holder rights",
    "insolvency": "issuer insolvency",
    "custodian": "custodian",
    "corporate_actions": "corporate actions",
    "redemption": "redemption",
    "restrictions": "restrictions",
    "mint_authority": "minting",
    "burn_authority": "burning",
    "pause": "halting transfers",
    "upgradeable": "logic upgrade",
    "freeze": "address freeze",
    "oracle": "price source",
    "supply_vs_reserves": "supply against collateral",
    "attestation_freshness": "report freshness",
    "ownership": "ownership",
    "trading_switch": "the transfers switch",
    "transfer_tax": "a cut on transfer",
    "transfer_limits": "caps on a transfer",
    "owner_holdings": "the owner's own balance",
    "burned_supply": "supply at a dead address",
    "liquidity": "the declared pool",
}

WIDTH = 96


def _cite(slot: Slot) -> str:
    ev = slot.evidence
    if ev is None:
        return ""
    if ev.kind == E_DOCUMENT:
        page = ev.locator.page if ev.locator else None
        where = f", p.{page}" if page else ""
        mark = " ~OCR" if ev.degraded else ""
        return f"[{ev.doc_id}{where}{mark}]"
    if ev.kind == E_ONCHAIN:
        return f"[chain, block {ev.block}]"
    if ev.kind == E_ABSENCE:
        if ev.checked_docs:
            return f"[absence, documents: {len(ev.checked_docs)}]"
        return f"[absence, block {ev.block}]"
    return ""


def _wrap(text: str, indent: int, cite: str = "") -> list[str]:
    """Left-aligned body with the citation parked at the right edge."""
    pad = " " * indent
    words = str(text).split()
    lines: list[str] = []
    budget = WIDTH - indent - (len(cite) + 2 if cite else 0)
    budget = max(28, budget)
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= budget:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if not lines:
        lines = [""]
    out = []
    for index, line in enumerate(lines):
        if index == 0 and cite:
            gap = max(1, WIDTH - indent - len(line) - len(cite))
            out.append(pad + line + " " * gap + cite)
        else:
            out.append(pad + line)
    return out


def _slot_line(slot: Slot, marker: str, *, name: bool = True) -> list[str]:
    label = SLOT_LABEL.get(slot.slot, slot.slot)
    head = f"{slot.slot}: " if name else ""
    body = slot.value or ""
    if name:
        body = f"{head}{body}" if body else head.rstrip()
    else:
        body = f"{label}: {body}" if body else label
    return _wrap(f"{marker} {body}".strip(), 2, _cite(slot))


def _by_state(slots: list[Slot], state: str) -> list[Slot]:
    return [s for s in slots if s.state == state]


def render(dossier: Dossier) -> str:
    out: list[str] = []
    asset = dossier.asset or {}
    date = dossier.snapshot_ts[:8]
    pretty_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date
    header = (f"{H_TITLE}  ·  {asset.get('label', '')}  ·  "
              f"{asset.get('chain', '')}:{asset.get('address', '')}  ·  {pretty_date}")
    out.append(header)
    counters = dossier.counters or {}
    if counters.get("fabrications"):
        out.append("")
        out.append(f"!  The gate caught fabricated quotes: "
                   f"{counters['fabrications']}. See \"{H_REJECTED}\" below.")
    if dossier.tainted_docs:
        out.append(f"!  Documents with repeated fabrications: "
                   f"{', '.join(dossier.tainted_docs)}. "
                   f"Treat anything sourced from them as unverified.")
    if dossier.injections:
        out.append(f"!  Text addressed at the agent was found in the documents: "
                   f"{len(dossier.injections)}. See \"{H_INJECTIONS}\" below.")

    # WHAT THIS IS: legal slots that passed the gate
    legal_filled = _by_state(dossier.legal, FILLED)
    out += ["", H_WHAT]
    if legal_filled:
        for slot in sorted(legal_filled, key=lambda s: LEGAL_SLOTS.index(s.slot)
                           if s.slot in LEGAL_SLOTS else 99):
            out += _slot_line(slot, " ", name=False)
    else:
        out.append("  (no legal slot is filled: see the sections below)")

    # CHANGES
    if dossier.previous_snapshot_ts:
        days = dossier.days_since_previous
        suffix = f"previous snapshot: {days} days ago" if days is not None \
            else f"previous snapshot: {dossier.previous_snapshot_ts}"
        out += ["", f"{H_CHANGES}  ({suffix})"]
        if dossier.changes:
            for change in dossier.changes:
                marker = "⚠" if change.get("material") else "·"
                out += _wrap(f"{marker} {diff_mod.describe(change)}", 2, "[diff]")
        else:
            out.append("  · no changes")

    # WHAT THE ISSUER CAN DO: on-chain powers, plus absences of power
    out += ["", H_POWERS]
    onchain_order = {name: i for i, name in enumerate(ONCHAIN_SLOTS)}
    powers = [s for s in dossier.onchain if s.state in (FILLED, NOT_FOUND)]
    if powers:
        for slot in sorted(powers, key=lambda s: onchain_order.get(s.slot, 99)):
            marker = "⚠" if (slot.state == FILLED and slot.material) else \
                ("·" if slot.state == FILLED else "○")
            out += _slot_line(slot, marker)
    else:
        out.append(f"  ? no on-chain power was read, see \"{H_NOT_CHECKED}\"")

    # THRESHOLDS: the holder's own numbers, not the tool's opinion
    if dossier.alerts:
        out += ["", H_ALERTS]
        for alert in dossier.alerts:
            marker = "⚠" if alert.get("crossed") else "·"
            out += _wrap(f"{marker} {alert.get('text')}", 2, "[asset file]")

    # NOT FOUND IN THE DOCUMENTS: documentary absence only
    legal_absent = _by_state(dossier.legal, NOT_FOUND)
    if legal_absent:
        out += ["", H_NOT_FOUND]
        for slot in legal_absent:
            checked = len(slot.evidence.checked_docs) if slot.evidence else 0
            body = slot.value or "no such section"
            out += _wrap(f"✗ {slot.slot}: {body} Documents checked: {checked}.",
                         2, _cite(slot))

    # NOT CHECKED: facts about the tool, never about the asset
    unchecked = [s for s in dossier.slots if s.state == NOT_CHECKED]
    if unchecked:
        out += ["", H_NOT_CHECKED]
        for slot in unchecked:
            detail = f". {slot.note}" if slot.note else ""
            out += _wrap(f"? {slot.slot}: {slot.reason}{detail}", 2)

    # REJECTED BY THE GATE
    rejected = [s for s in dossier.slots if s.state == REJECTED]
    if rejected:
        out += ["", H_REJECTED]
        for slot in rejected:
            detail = f". {slot.note}" if slot.note else ""
            out += _wrap(f"⨯ {slot.slot}: {slot.reason}{detail}", 2)

    # HOW THIS CAN END FOR THE HOLDER: adversarial theses, each one quoted
    if dossier.challenge:
        out += ["", H_CHALLENGE]
        for item in dossier.challenge:
            evidence = Evidence.from_json(item.get("evidence") or {})
            cite = _cite(Slot(slot="challenge", state=FILLED, evidence=evidence))
            out += _wrap(f"! {item.get('claim')}", 2, cite)

    # SIMILAR CASES
    if dossier.scars:
        out += ["", H_SCARS]
        for entry in dossier.scars:
            signals = ", ".join(entry.get("shared_signals", []))
            ids = ", ".join(entry.get("ids", []))
            out += _wrap(f"· {signals}", 2, f"[scars: {ids}]")

    # TEXT FOUND THAT SPEAKS TO THE AGENT
    if dossier.injections:
        out += ["", H_INJECTIONS]
        for finding in dossier.injections:
            page = f", p.{finding['page']}" if finding.get("page") else ""
            where = f", {finding['where']}" if finding.get("where") else ""
            out += _wrap(
                f"⚑ {finding.get('doc_id')}{page}{where}: "
                f"{finding.get('technique')}. {finding.get('excerpt')}", 2)

    # SUGGESTED SOURCES
    if dossier.suggested_sources:
        out += ["", H_SOURCES]
        out.append("  Links inside documents are never followed. Add a domain by "
                   "hand in config.toml.")
        for entry in dossier.suggested_sources:
            out += _wrap(f"· {entry.get('domain')}, from "
                         f"{entry.get('doc_id')}, mentions: "
                         f"{entry.get('count')}", 2)

    # CONTEXT
    if dossier.context:
        out += ["", H_CONTEXT]
        for line in dossier.context:
            out += _wrap(f"· {line}", 2)

    filled = counters.get("slots_filled", 0)
    total = counters.get("slots_total", 0)
    out += ["", (f"{H_FOOTER}: {filled}/{total} slots   "
                 f"FABRICATIONS: {counters.get('fabrications', 0)}   "
                 f"INJECTIONS: {counters.get('injections', 0)}   "
                 f"SPENT: ${counters.get('spent_usd', 0):.4f}")]
    out.append(f"snapshot {dossier.snapshot_hash[:12]}  ·  "
               f"extraction {(dossier.extraction_hash or '-')[:12]}  ·  "
               f"block {dossier.block_number}")
    out.append("This sheet shows what the sources say and what the contract "
               "records. The person decides.")
    return "\n".join(out) + "\n"
