"""The gate: assembles a dossier from a snapshot and (optionally) an extraction.

Order matters and it is the reverse of the intuitive one. Injection detection
runs first, because a corpus carrying text addressed at the agent must never
reach a model or a reader. Then every claim the model made is checked against
the bytes it cites. A claim without valid evidence is removed — never
down-weighted — and the removal is printed.

The gate calls no model. It can therefore be re-run over the whole archive for
free whenever its rules improve, which is the point of keeping extraction and
verification apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import diff as diff_mod
from .. import render as render_mod
from ..coin import sheet as coin_sheet
from .. import scars as scars_mod
from .. import util
from ..corpus import Corpus
from ..model import (
    E_ABSENCE, E_DOCUMENT, E_ONCHAIN, FILLED, J_FORBIDDEN_KIND, J_NO_EVIDENCE,
    LEGAL_SLOTS, NOT_CHECKED, NOT_FOUND, R_MODEL_JSON, R_NO_DOCUMENTS,
    R_NO_EXTRACTION, R_NO_TEXT_LAYER, R_SLOT_MISSING, REJECTED, Asset, Dossier,
    Evidence, Slot, not_checked,
)
from . import injection as injection_mod
from . import language as language_mod
from . import quotes as quotes_mod
from . import sources as sources_mod


@dataclass
class GateResult:
    dossier: Dossier | None
    text: str | None
    halted: bool = False
    halting: list[dict] = field(default_factory=list)
    violations: list[language_mod.Violation] = field(default_factory=list)
    fabrications: list[dict] = field(default_factory=list)
    drift: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.dossier is not None and not self.halted and not self.violations


def rescan_injections(snapshot: dict, corpus: Corpus, config: dict) -> list[dict]:
    """Re-run injection detection from the corpus with the current rules.

    Not read from the snapshot: the whole point of a free gate is that improved
    rules apply retroactively to documents captured months ago.
    """
    patterns = injection_mod.flatten_patterns(
        (config.get("gate") or {}).get("injection_patterns"))
    findings: list[dict] = []
    for doc_id, sha in sorted(sources_mod.stored_documents(snapshot).items()):
        try:
            doc = corpus.get(sha)
        except KeyError:
            continue
        for finding in injection_mod.scan_document(
                doc.raw, doc.text, doc_id=doc_id, sha256=sha,
                content_type=str(doc.meta.get("content_type") or ""),
                patterns=patterns):
            findings.append(finding.to_json())
    return findings


def unreadable_documents(snapshot: dict, corpus: Corpus) -> dict[str, str]:
    """doc_id -> sha256 for documents that reached the corpus with no text.

    A scan without a text layer is stored, hashed and monitored for substitution
    like any other document, but it cannot carry evidence. Treating its silence
    as an absence would turn a failure of extraction into a claim about the
    asset, which is exactly the confusion this program exists to refuse.
    """
    out: dict[str, str] = {}
    for doc_id, sha in sources_mod.stored_documents(snapshot).items():
        try:
            if not corpus.get(sha).readable:
                out[doc_id] = sha
        except KeyError:
            continue
    return out


def _legal_slots(extraction: dict | None, snapshot: dict, corpus: Corpus,
                 config: dict) -> tuple[list[Slot], list[dict], list[dict],
                                        list[dict]]:
    """Check every legal claim.

    Returns (slots, fabrications, locator drift, unverifiable claims).
    """
    stored = sources_mod.stored_documents(snapshot)
    known_sha = set(stored.values())
    unavailable = sources_mod.unavailable_reasons(snapshot)
    unreadable = unreadable_documents(snapshot, corpus)
    fabrications: list[dict] = []
    drift: list[dict] = []
    unverifiable: list[dict] = []

    def blind(doc_ids) -> list[str]:
        """Which of these documents the tool could not read."""
        return sorted({d for d in doc_ids if d in unreadable})

    if extraction is None:
        reason = R_NO_EXTRACTION
        note = "extract has not been run for this snapshot"
        if not stored:
            reason = R_NO_DOCUMENTS
            if unavailable:
                note = ("no document reached the corpus: "
                        + ", ".join(f"{k}={v}"
                                    for k, v in sorted(unavailable.items())))
            else:
                note = "the asset file declares no documents"
        return ([not_checked(name, reason, note) for name in LEGAL_SLOTS],
                [], [], [])

    claims = {str(c.get("slot")): c for c in extraction.get("slots", [])}
    parse_errors = list(((extraction.get("parse") or {}).get("errors")) or [])
    out: list[Slot] = []
    for name in LEGAL_SLOTS:
        claim = claims.get(name)
        if claim is None:
            if parse_errors:
                out.append(not_checked(name, R_MODEL_JSON,
                                       "the model returned no usable JSON: "
                                       + "; ".join(parse_errors[:3])))
            else:
                out.append(not_checked(name, R_SLOT_MISSING,
                                       "the extraction does not mention this slot"))
            continue
        state = str(claim.get("state") or "")
        raw_evidence = claim.get("evidence")
        evidence = Evidence.from_json(raw_evidence) if isinstance(raw_evidence, dict) \
            else None

        if state == NOT_CHECKED:
            out.append(not_checked(name, str(claim.get("reason") or "model_declined"),
                                   claim.get("note")))
            continue

        if evidence is None:
            out.append(Slot(slot=name, state=REJECTED, reason=J_NO_EVIDENCE,
                            note="a value was proposed with no evidence attached"))
            continue

        if evidence.kind == E_ONCHAIN:
            out.append(Slot(slot=name, state=REJECTED, reason=J_FORBIDDEN_KIND,
                            note="on-chain evidence is produced by scripts only"))
            continue

        if evidence.kind == E_ABSENCE:
            blinded = blind(evidence.checked_docs)
            if blinded:
                # Nothing was read, so nothing is absent.
                out.append(not_checked(
                    name, R_NO_TEXT_LAYER,
                    "absence claimed over documents that have no text layer: "
                    + ", ".join(blinded)))
                unverifiable.append({"slot": name, "docs": blinded,
                                     "claim": claim.get("value"),
                                     "kind": E_ABSENCE})
                continue
            check = quotes_mod.check_absence(evidence, corpus, stored)
            if check.passed:
                out.append(Slot(slot=name, state=NOT_FOUND,
                                value=claim.get("value")
                                or "the checked documents contain no such section.",
                                evidence=evidence))
            else:
                fabrications.append({"slot": name, "doc_id": evidence.doc_id,
                                     "outcome": check.outcome,
                                     "detail": check.detail})
                out.append(Slot(slot=name, state=REJECTED, reason=check.outcome,
                                note=check.detail))
            continue

        if evidence.kind != E_DOCUMENT:
            out.append(Slot(slot=name, state=REJECTED, reason=J_FORBIDDEN_KIND,
                            note=f"evidence of kind {evidence.kind!r} is not allowed"))
            continue

        if blind([evidence.doc_id]):
            # The model was handed an empty document. Whatever it quoted cannot
            # be checked, and the fault is the tool's, so this is not_checked
            # rather than a charge of invention — but the claim is still shown.
            out.append(not_checked(
                name, R_NO_TEXT_LAYER,
                f"the quote is claimed from {evidence.doc_id}, which has no "
                f"text layer: there is nothing to check it against"))
            unverifiable.append({"slot": name, "docs": [evidence.doc_id],
                                 "claim": claim.get("value"),
                                 "quote": evidence.quote, "kind": E_DOCUMENT})
            continue

        check = quotes_mod.check(evidence, corpus, known_sha)
        if not check.passed:
            record = {"slot": name, "doc_id": evidence.doc_id,
                      "sha256": evidence.sha256, "outcome": check.outcome,
                      "detail": check.detail, "quote": evidence.quote}
            if check.outcome == quotes_mod.FABRICATED:
                fabrications.append(record)
            out.append(Slot(slot=name, state=REJECTED, reason=check.outcome,
                            note=check.detail))
            continue

        if check.outcome == quotes_mod.REPAIRED:
            drift.append({"slot": name, "doc_id": evidence.doc_id,
                          "from": evidence.locator.to_json()
                          if evidence.locator else None,
                          "to": check.locator.to_json() if check.locator else None})
        fixed = Evidence(kind=E_DOCUMENT, doc_id=evidence.doc_id,
                         sha256=evidence.sha256, locator=check.locator,
                         quote=evidence.quote, degraded=check.degraded)
        out.append(Slot(slot=name, state=FILLED, value=claim.get("value"),
                        evidence=fixed))
    return out, fabrications, drift, unverifiable


def _challenge(extraction: dict | None, snapshot: dict, corpus: Corpus,
               unreadable: dict[str, str]) -> tuple[list[dict], list[dict],
                                                    list[dict]]:
    """Verify the adversarial theses. Returns (kept, dropped, fabrications).

    The challenge call asks the model for concrete ways the arrangement ends
    badly for a holder. Left unchecked it produces a stream of general
    observations that could be written about anything, so it is held to exactly
    the same evidence contract as the questionnaire: a thesis without a quote
    that survives verification never reaches the document.
    """
    items = list((extraction or {}).get("challenge") or [])
    known_sha = set(sources_mod.stored_documents(snapshot).values())
    kept: list[dict] = []
    dropped: list[dict] = []
    fabrications: list[dict] = []
    for index, item in enumerate(items):
        claim = str(item.get("claim") or "").strip()
        raw = item.get("evidence")
        if not claim:
            continue
        if not isinstance(raw, dict):
            dropped.append({"claim": claim, "reason": J_NO_EVIDENCE})
            continue
        evidence = Evidence.from_json(raw)
        if evidence.kind != E_DOCUMENT:
            dropped.append({"claim": claim, "reason": J_FORBIDDEN_KIND})
            continue
        if evidence.doc_id in unreadable:
            dropped.append({"claim": claim, "reason": R_NO_TEXT_LAYER})
            continue
        check = quotes_mod.check(evidence, corpus, known_sha)
        if not check.passed:
            dropped.append({"claim": claim, "reason": check.outcome,
                            "doc_id": evidence.doc_id})
            if check.outcome == quotes_mod.FABRICATED:
                fabrications.append({"slot": f"challenge[{index}]",
                                     "doc_id": evidence.doc_id,
                                     "sha256": evidence.sha256,
                                     "outcome": check.outcome,
                                     "detail": check.detail,
                                     "quote": evidence.quote})
            continue
        fixed = Evidence(kind=E_DOCUMENT, doc_id=evidence.doc_id,
                         sha256=evidence.sha256, locator=check.locator,
                         quote=evidence.quote, degraded=check.degraded)
        kept.append({"claim": claim, "evidence": fixed.to_json()})
    return kept, dropped, fabrications


def _tainted(fabrications: list[dict], threshold: int) -> list[str]:
    counts: dict[str, int] = {}
    for record in fabrications:
        doc_id = str(record.get("doc_id") or "?")
        counts[doc_id] = counts.get(doc_id, 0) + 1
    return sorted(doc for doc, count in counts.items() if count >= threshold)


def build_dossier(p: util.Paths, config: dict, asset: Asset, snapshot: dict,
                  extraction: dict | None = None,
                  previous: tuple[str, dict] | None = None,
                  cost_usd: float = 0.0) -> GateResult:
    """``dossier = f(snapshot, extraction)``. No clock, no network, no model."""
    corpus = Corpus(p.corpus)
    gate_config = config.get("gate") or {}

    findings = rescan_injections(snapshot, corpus, config)
    halting = [f.to_json() for f in injection_mod.halting(
        [_finding(f) for f in findings], config)]
    if halting:
        return GateResult(dossier=None, text=None, halted=True, halting=halting)

    onchain = [Slot.from_json(s) for s in
               (snapshot.get("slots") or {}).get("onchain") or []]

    if asset.is_coin:
        # Nothing was extracted, so there is nothing for the gate to intersect.
        # The legal list stays empty rather than being filled with sixteen
        # not_checked rows: a coin was never asked those questions, and printing
        # them as unanswered would read as a failure to look.
        legal: list[Slot] = []
        fabrications: list[dict] = []
        drift: list[dict] = []
        unverifiable: list[dict] = []
        unreadable: dict[str, str] = {}
        challenge: list[dict] = []
        challenge_dropped: list[dict] = []
    else:
        legal, fabrications, drift, unverifiable = _legal_slots(
            extraction, snapshot, corpus, config)
        unreadable = unreadable_documents(snapshot, corpus)
        challenge, challenge_dropped, challenge_fabrications = _challenge(
            extraction, snapshot, corpus, unreadable)
        fabrications = fabrications + challenge_fabrications

    tainted = _tainted(fabrications, int(gate_config.get("taint_threshold", 3)))

    changes: list[dict] = []
    previous_ts = None
    days = None
    if previous:
        previous_ts, previous_snapshot = previous
        changes = diff_mod.diff_snapshots(previous_snapshot, snapshot)
        try:
            days = (util.parse_ts_slug(str(snapshot.get("ts")))
                    - util.parse_ts_slug(previous_ts)).days
        except ValueError:
            days = None

    review = sources_mod.review(snapshot, (config.get("sources") or {})
                                .get("allowed_domains") or [])

    all_slots = legal + onchain
    signals = scars_mod.signals_of(legal, onchain, snapshot.get("facts") or {})
    matches = scars_mod.match(signals, scars_mod.load(p.scars))

    context: list[str] = []
    for entry in review["failed"] + review["refused"]:
        context.append(f"document {entry['doc_id']} did not reach the corpus: "
                       f"{entry['reason']}")
    for entry in review["off_whitelist_redirects"]:
        context.append(f"document {entry['doc_id']}: an address outside the "
                       f"whitelist appeared in the redirect chain "
                       f"({entry['host']})")
    for doc_id in sorted(unreadable):
        context.append(f"document {doc_id} was fetched and is hashed, but it "
                       f"has no text layer: neither a quote nor an absence "
                       f"is accepted from it")
    for entry in challenge_dropped:
        context.append(f"a thesis was cut by the gate ({entry['reason']}) and "
                       f"did not reach the document")
    alerts = list(snapshot.get("alerts") or [])

    dossier = Dossier(
        asset=snapshot.get("asset") or asset.to_json(),
        snapshot_ts=str(snapshot.get("ts")),
        snapshot_hash=str(snapshot.get("snapshot_hash")),
        block_number=snapshot.get("block_number"),
        extraction_hash=(extraction or {}).get("extraction_hash"),
        legal=legal,
        onchain=onchain,
        changes=changes,
        previous_snapshot_ts=previous_ts,
        days_since_previous=days,
        injections=findings,
        suggested_sources=review["suggested_sources"],
        tainted_docs=tainted,
        scars=matches,
        context=context,
        challenge=challenge,
        alerts=alerts,
        counters={
            "slots_filled": sum(1 for s in all_slots if s.state == FILLED),
            "slots_total": len(all_slots),
            "not_found": sum(1 for s in all_slots if s.state == NOT_FOUND),
            "not_checked": sum(1 for s in all_slots if s.state == NOT_CHECKED),
            "rejected": sum(1 for s in all_slots if s.state == REJECTED),
            "fabrications": len(fabrications),
            "locator_drift": len(drift),
            "injections": len(findings),
            "unverifiable": len(unverifiable),
            "challenge_kept": len(challenge),
            "challenge_dropped": len(challenge_dropped),
            "alerts": len(alerts),
            "spent_usd": round(float(cost_usd), 6),
        },
        gate={
            "signals": signals,
            "taint_threshold": int(gate_config.get("taint_threshold", 3)),
            "fabrications": fabrications,
            "locator_drift": drift,
            "unverifiable_claims": unverifiable,
            "challenge_dropped": challenge_dropped,
            "unreadable_documents": sorted(unreadable),
            # Carried so the coin sheet can print what the contract calls
            # itself without re-reading the snapshot from disk.
            "facts": (snapshot.get("facts") or {}) if asset.is_coin else {},
            "documents": sources_mod.stored_documents(snapshot),
            "unavailable_documents": sources_mod.unavailable_reasons(snapshot),
        },
    )

    payload = dossier.to_json()
    text = (coin_sheet.render_coin(dossier) if asset.is_coin
            else render_mod.render(dossier))
    violations = language_mod.lint(payload, text, config)
    if violations:
        return GateResult(dossier=dossier, text=text, violations=violations,
                          fabrications=fabrications, drift=drift)
    return GateResult(dossier=dossier, text=text, fabrications=fabrications,
                      drift=drift)


def _finding(raw: dict) -> injection_mod.Finding:
    return injection_mod.Finding(
        doc_id=raw.get("doc_id", ""), sha256=raw.get("sha256", ""),
        surface=raw.get("surface", ""), technique=raw.get("technique", ""),
        severity=raw.get("severity", injection_mod.HIDDEN),
        excerpt=raw.get("excerpt", ""), page=raw.get("page"),
        where=raw.get("where"), pattern=raw.get("pattern"))


def write_dossier(p: util.Paths, asset: Asset, result: GateResult) -> Path:
    """Write ``dossier.json`` and ``dossier.txt`` beside the snapshot timestamp."""
    assert result.dossier is not None
    target = p.dossiers / asset.name / result.dossier.snapshot_ts
    util.write_json(target / "dossier.json", result.dossier.to_json())
    util.atomic_write_text(target / "dossier.txt", result.text or "")
    return target


def list_dossiers(p: util.Paths, asset: Asset) -> list[str]:
    directory = p.dossiers / asset.name
    if not directory.exists():
        return []
    return sorted(d.name for d in directory.iterdir()
                  if d.is_dir() and (d / "dossier.json").exists())


def load_dossier(p: util.Paths, asset: Asset, ts: str) -> dict:
    return util.read_json(p.dossiers / asset.name / ts / "dossier.json")
