"""Supply against declared collateral, and the age of the reserve report.

Both slots lean on numbers a human wrote into ``assets/<asset>.toml``. That is
not evidence, so the declared figures are only ever *compared* with something
that is: ``totalSupply`` read at a block, and the attestation date as it appears
in the attestation document itself. When the document does not confirm the
declared date, the slot goes to ``not_checked`` rather than borrowing authority
from the config file.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .. import util
from ..corpus import Corpus
from ..model import (
    E_DOCUMENT, FILLED, R_NO_ATTESTATION_DATE, R_NO_PERIOD, R_NO_RESERVE_CLAIM,
    R_RPC_ERROR, Evidence, Locator, Slot, not_checked,
)
from . import docs as docs_mod

R_ATTESTATION_UNCONFIRMED = "attestation_date_not_found_in_document"
R_NO_ATTESTATION_DOC = "no_attestation_document_declared"
R_ATTESTATION_DOC_MISSING = "attestation_document_not_in_snapshot"


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def supply_vs_reserves(reserves: dict, facts: dict, chain_id: int | None,
                       address: str, block: int | None) -> tuple[Slot, dict]:
    claimed = reserves.get("claimed_units")
    if claimed is None:
        return not_checked(
            "supply_vs_reserves", R_NO_RESERVE_CLAIM,
            "no [reserves] claimed_units in the asset file: there is "
            "nothing to compare totalSupply against"), {}
    raw_supply = facts.get("total_supply")
    if raw_supply is None:
        return not_checked("supply_vs_reserves", R_RPC_ERROR,
                           "totalSupply() could not be read"), {}
    decimals = facts.get("decimals")
    scale = Decimal(10) ** int(decimals) if decimals is not None else Decimal(1)
    supply = Decimal(str(raw_supply)) / scale
    claim = Decimal(str(claimed))
    unit = str(reserves.get("unit") or "units")
    delta = supply - claim
    parts = [f"totalSupply {_fmt(supply)} against declared collateral "
             f"{_fmt(claim)} {unit}"]
    if delta == 0:
        parts.append("equal")
    else:
        pct = (abs(delta) / claim * 100) if claim else None
        word = ("more tokens than declared collateral" if delta > 0
                else "more declared collateral than tokens")
        parts.append(f"{_fmt(abs(delta))} {word}"
                     + (f" ({_fmt(pct.quantize(Decimal('0.01')))}%)" if pct else ""))
    if reserves.get("claim_doc"):
        parts.append(f"the collateral figure is declared in the asset file, "
                     f"sourced from {reserves['claim_doc']}")
    over_pct = None
    if claim and delta > 0:
        over_pct = float((delta / claim * 100).quantize(Decimal("0.01")))
    computed = {"supply": _fmt(supply), "claimed": _fmt(claim), "unit": unit,
                "delta_sign": (1 if delta > 0 else (-1 if delta < 0 else 0)),
                "over_pct": over_pct}
    return (Slot(slot="supply_vs_reserves", state=FILLED, value="; ".join(parts),
                 material=delta > 0,
                 evidence=Evidence(kind="onchain", chain_id=chain_id,
                                   address=address, method="totalSupply()",
                                   block=block, raw=str(raw_supply))),
            computed)


_DATE_FORMS = ("%d %B %Y", "%B %d, %Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y",
               "%b %d, %Y", "%d %b %Y")


def _date_strings(date: datetime) -> list[str]:
    out = []
    for form in _DATE_FORMS:
        try:
            rendered = date.strftime(form)
        except ValueError:
            continue
        out.append(rendered)
        stripped = rendered.lstrip("0").replace(" 0", " ")
        if stripped != rendered:
            out.append(stripped)
    return out


def attestation_freshness(reserves: dict, doc_results, corpus: Corpus,
                          as_of: datetime) -> tuple[Slot, dict]:
    attested_raw = reserves.get("attested_at")
    if not attested_raw:
        return not_checked(
            "attestation_freshness", R_NO_ATTESTATION_DATE,
            "no [reserves] attested_at in the asset file"), {}
    attested = util.parse_date(str(attested_raw))
    if attested is None:
        return not_checked("attestation_freshness", R_NO_ATTESTATION_DATE,
                           f"attested_at could not be parsed: "
                           f"{attested_raw!r}"), {}
    period = reserves.get("period_days")
    if period is None:
        return not_checked(
            "attestation_freshness", R_NO_PERIOD,
            "no [reserves] period_days in the asset file: an age without "
            "a promised period says nothing"), {}
    claim_doc = reserves.get("claim_doc")
    if not claim_doc:
        return not_checked("attestation_freshness", R_NO_ATTESTATION_DOC,
                           "no [reserves] claim_doc naming the report "
                           "document"), {}
    stored = {d.doc_id: d for d in doc_results if d.state == "stored"}
    entry = stored.get(str(claim_doc))
    if entry is None or not entry.sha256:
        return not_checked(
            "attestation_freshness", R_ATTESTATION_DOC_MISSING,
            f"document {claim_doc!r} is not in this snapshot"), {}
    doc = corpus.get(entry.sha256)
    text = doc.text
    hit = None
    for candidate in _date_strings(attested):
        found = docs_mod.find_quote(text, candidate)
        if found:
            hit = (candidate, found)
            break
    if hit is None:
        return not_checked(
            "attestation_freshness", R_ATTESTATION_UNCONFIRMED,
            f"the declared date {attested_raw} does not occur in "
            f"{claim_doc}"), {}

    _quote, (start, end) = hit
    age = (as_of - attested).days
    overdue = age - int(period)
    value = (f"reserve report dated {attested.strftime('%Y-%m-%d')}, "
             f"{age} days old at snapshot time; declared period "
             f"{int(period)} days")
    if overdue > 0:
        value += f"; overdue by {overdue} days"
    computed = {"attested_at": attested.strftime("%Y-%m-%d"), "age_days": age,
                "period_days": int(period), "overdue": overdue > 0,
                "attestor": reserves.get("attestor")}
    return (Slot(slot="attestation_freshness", state=FILLED, value=value,
                 material=overdue > 0,
                 evidence=Evidence(kind=E_DOCUMENT, doc_id=str(claim_doc),
                                   sha256=doc.sha256, quote=text[start:end],
                                   degraded=bool(doc.meta.get("degraded")),
                                   locator=Locator(type="char_range", start=start,
                                                   end=end,
                                                   page=doc.page_of(start)))),
            computed)


def read_reserves(asset: Any, facts: dict, doc_results, corpus: Corpus,
                  chain_id: int | None, block: int | None,
                  as_of: datetime) -> tuple[list[Slot], dict]:
    """Both reserve slots, plus the structured readings signals are matched on."""
    supply_slot, supply_facts = supply_vs_reserves(asset.reserves, facts, chain_id,
                                                  asset.address, block)
    age_slot, age_facts = attestation_freshness(asset.reserves, doc_results,
                                               corpus, as_of)
    computed: dict = {}
    if supply_facts:
        computed["supply_vs_reserves"] = supply_facts
    if age_facts:
        computed["attestation"] = age_facts
    if asset.reserves:
        computed["declared"] = {
            "claim_doc": asset.reserves.get("claim_doc"),
            "attestor": asset.reserves.get("attestor"),
        }
    return [supply_slot, age_slot], computed
