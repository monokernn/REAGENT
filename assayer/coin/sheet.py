"""The coin dossier as text. One screen, no verdicts, same grammar.

This is deliberately not a second renderer with its own habits. It imports the
RWA sheet's line wrapper, citation format and section headers, so the two sheets
wrap, cite and separate their sections identically, and
:mod:`assayer.verify.language` lints both against the same rules. What changes
is which sections exist: a coin has no documentary absence and no rejected
claim, because nothing was extracted and no model was asked.

The footer counts what was read and how much of it is a power over somebody
else's balance. It is not a score and there is no arithmetic combining the two:
four live powers on a coin with a burnt pool and four on one with a live owner
are four either way, and the reader is the one who weighs them.
"""

from __future__ import annotations

from ..model import COIN_SLOTS, FILLED, NOT_CHECKED, NOT_FOUND, Dossier
from ..render import (
    H_ALERTS, H_CHANGES, H_CONTEXT, H_FOOTER, H_NOT_CHECKED, H_POWERS_COIN,
    H_SCARS, H_TITLE, H_WHAT, _by_state, _slot_line, _wrap,
)
from .. import diff as diff_mod


def _identity(facts: dict) -> list[str]:
    """What the contract calls itself. Read, not taken from the asset file."""
    lines: list[str] = []
    name = facts.get("name")
    symbol = facts.get("symbol")
    if name or symbol:
        both = " ".join(part for part in (name, f"({symbol})" if symbol else "")
                        if part)
        subject = "the mint" if facts.get("program") else "the contract"
        lines += _wrap(f"{subject} calls itself {both}", 2, "[chain]")
    supply = facts.get("total_supply")
    if supply:
        decimals = facts.get("decimals")
        tail = f", {decimals} decimals" if decimals is not None else ""
        lines += _wrap(f"total supply {supply}{tail}", 2, "[chain]")
    program = facts.get("program")
    if program:
        extensions = ", ".join(facts.get("extensions") or []) or "no extensions"
        lines += _wrap(f"an SPL mint on the {program} program; {extensions}", 2, "[chain]")
    size = facts.get("code_size")
    if size:
        implementation = facts.get("implementation")
        where = (f"; the logic it runs lives at {implementation}"
                 if implementation else "")
        lines += _wrap(f"{size} bytes of deployed code{where}", 2, "[chain]")
    if not lines:
        lines.append("  (the token answered none of the name calls)")
    return lines


def render_coin(dossier: Dossier) -> str:
    out: list[str] = []
    asset = dossier.asset or {}
    facts = (dossier.gate or {}).get("facts") or {}
    date = dossier.snapshot_ts[:8]
    pretty = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date
    out.append(f"{H_TITLE}  ·  COIN  ·  {asset.get('label', '')}  ·  "
               f"{asset.get('chain', '')}:{asset.get('address', '')}  ·  {pretty}")

    material = [s for s in dossier.onchain if s.state == FILLED and s.material]
    if material:
        names = ", ".join(s.slot for s in material)
        out.append("")
        out += _wrap(f"!  Powers over somebody else's balance are live in this "
                     f"contract: {names}.", 0)

    out += ["", H_WHAT]
    out += _identity(facts)

    if dossier.previous_snapshot_ts:
        days = dossier.days_since_previous
        suffix = (f"previous snapshot: {days} days ago" if days is not None
                  else f"previous snapshot: {dossier.previous_snapshot_ts}")
        out += ["", f"{H_CHANGES}  ({suffix})"]
        if dossier.changes:
            for change in dossier.changes:
                marker = "⚠" if change.get("material") else "·"
                out += _wrap(f"{marker} {diff_mod.describe(change)}", 2, "[diff]")
        else:
            out.append("  · no changes")

    out += ["", H_POWERS_COIN]
    order = {name: i for i, name in enumerate(COIN_SLOTS)}
    powers = [s for s in dossier.onchain if s.state in (FILLED, NOT_FOUND)]
    if powers:
        for slot in sorted(powers, key=lambda s: order.get(s.slot, 99)):
            marker = "⚠" if (slot.state == FILLED and slot.material) else \
                ("·" if slot.state == FILLED else "○")
            out += _slot_line(slot, marker)
    else:
        out.append(f"  ? nothing was read, see «{H_NOT_CHECKED}»")

    if dossier.alerts:
        out += ["", H_ALERTS]
        for alert in dossier.alerts:
            marker = "⚠" if alert.get("crossed") else "·"
            out += _wrap(f"{marker} {alert.get('text')}", 2, "[asset file]")

    unchecked = _by_state(dossier.onchain, NOT_CHECKED)
    if unchecked:
        out += ["", H_NOT_CHECKED]
        for slot in unchecked:
            detail = f". {slot.note}" if slot.note else ""
            out += _wrap(f"? {slot.slot}: {slot.reason}{detail}", 2)

    if dossier.scars:
        out += ["", H_SCARS]
        for entry in dossier.scars:
            signals = ", ".join(entry.get("shared_signals", []))
            ids = ", ".join(entry.get("ids", []))
            out += _wrap(f"· {signals}", 2, f"[scars: {ids}]")

    if dossier.context:
        out += ["", H_CONTEXT]
        for line in dossier.context:
            out += _wrap(f"· {line}", 2)

    counters = dossier.counters or {}
    out += ["", (f"{H_FOOTER}: {counters.get('slots_filled', 0)}"
                 f"/{counters.get('slots_total', 0)} slots   "
                 f"POWERS LIVE: {len(material)}   "
                 f"SPENT: ${counters.get('spent_usd', 0):.4f}")]
    out.append(f"snapshot {dossier.snapshot_hash[:12]}  ·  "
               f"no extraction: this profile calls no model  ·  "
               f"block {dossier.block_number}")
    out.append("This sheet shows what the contract records. The person decides.")
    return "\n".join(out) + "\n"
