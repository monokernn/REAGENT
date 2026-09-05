"""A document the tool could not read may not be quoted, and may not be silent.

This is the sharpest form of the ``not_found`` / ``not_checked`` line. A scan
with no text layer reaches the corpus as an empty canonical text. If absence
claims were honoured against it, every question would come back "not in the
documents" — a statement about the asset manufactured out of the tool's own
blindness, and the most damaging thing this program could print.
"""

from __future__ import annotations

from datetime import datetime, timezone

from assayer.corpus import Corpus
from assayer.model import NOT_CHECKED, R_NO_TEXT_LAYER
from assayer.render import H_CONTEXT, H_NOT_CHECKED
from assayer.verify import gate as gate_mod
from conftest import (
    PROSPECTUS_URL, document_evidence, take_snapshot, write_extraction,
)
from fixtures import pdfs

T1 = datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc)
SCAN_ROUTES = {PROSPECTUS_URL: ("application/pdf", pdfs.scanned_prospectus())}


def test_a_scan_is_stored_and_marked_unreadable(paths, config, asset, rpc):
    snap = take_snapshot(paths, config, asset, rpc, SCAN_ROUTES, now=T1)
    document = snap.snapshot["documents"][0]
    assert document["state"] == "stored", "it is still monitored for substitution"
    assert document["degraded"] is True
    assert document["sha256"]

    doc = Corpus(paths.corpus).get(document["sha256"])
    assert doc.text.strip() == ""
    assert doc.readable is False
    assert doc.meta["extract_note"] == "no_text_layer_on_any_page"
    assert gate_mod.unreadable_documents(snap.snapshot, Corpus(paths.corpus)) == \
        {"prospectus": document["sha256"]}


def test_absence_claimed_from_a_scan_is_not_checked(paths, config, asset, rpc):
    """The empty document would make every absence trivially true. It does not."""
    snap = take_snapshot(paths, config, asset, rpc, SCAN_ROUTES, now=T1)
    slots = [{"slot": "insolvency", "state": "not_found",
              "value": "the documents say nothing about issuer insolvency.",
              "evidence": {"kind": "absence", "checked_docs": ["prospectus"],
                           "patterns": ["insolvency", "bankruptcy"]}}]
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot,
                                   write_extraction(paths, snap.snapshot, slots))
    assert result.ok, [str(v) for v in result.violations]
    slot = next(s for s in result.dossier.legal if s.slot == "insolvency")
    assert slot.state == NOT_CHECKED, "an unread document proves no absence"
    assert slot.reason == R_NO_TEXT_LAYER
    assert "prospectus" in (slot.note or "")
    assert not [s for s in result.dossier.legal if s.state == "not_found"], \
        "nothing may be reported as absent from a document nobody read"
    assert result.dossier.counters["unverifiable"] == 1

    text = result.text or ""
    assert R_NO_TEXT_LAYER in text.split(H_NOT_CHECKED, 1)[1]
    assert "has no text layer" in text.split(H_CONTEXT, 1)[1]


def test_a_quote_from_a_scan_is_not_checked_not_a_fabrication(paths, config, asset,
                                                            rpc):
    """We handed the model an empty document, so the failure is ours to own."""
    snap = take_snapshot(paths, config, asset, rpc, SCAN_ROUTES, now=T1)
    slots = [{"slot": "issuer", "state": "filled",
              "value": "Assay Issuer Limited, Jersey",
              "evidence": document_evidence(snap.snapshot, "prospectus",
                                            "incorporated in Jersey")}]
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot,
                                   write_extraction(paths, snap.snapshot, slots))
    slot = next(s for s in result.dossier.legal if s.slot == "issuer")
    assert slot.state == NOT_CHECKED
    assert slot.reason == R_NO_TEXT_LAYER
    assert result.dossier.counters["fabrications"] == 0, \
        "the model is not charged with inventing what we could not show it"
    assert result.dossier.counters["unverifiable"] == 1
    unverifiable = result.dossier.gate["unverifiable_claims"][0]
    assert unverifiable["quote"] == "incorporated in Jersey"
    assert "Assay Issuer Limited" not in (slot.value or ""), \
        "the unverified value does not become the slot's value"


def test_the_prompt_tells_the_model_the_document_is_unreadable(paths, config,
                                                             asset, rpc):
    from assayer.extract import prompt as prompt_mod

    snap = take_snapshot(paths, config, asset, rpc, SCAN_ROUTES, now=T1)
    built = prompt_mod.build(snap.snapshot, Corpus(paths.corpus))
    assert "NO TEXT LAYER" in built.text
    assert "do not claim" in built.text
    assert built.documents[0]["readable"] is False


def test_a_readable_document_is_unaffected(paths, config, asset, rpc):
    """The guard must not fire on documents that do have text."""
    snap = take_snapshot(paths, config, asset, rpc,
                         {PROSPECTUS_URL: ("application/pdf",
                                           pdfs.clean_prospectus())}, now=T1)
    assert gate_mod.unreadable_documents(snap.snapshot, Corpus(paths.corpus)) == {}
    slots = [{"slot": "corporate_actions", "state": "not_found",
              "value": "nothing is said about dividends.",
              "evidence": {"kind": "absence", "checked_docs": ["prospectus"],
                           "patterns": ["dividend"]}}]
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot,
                                   write_extraction(paths, snap.snapshot, slots))
    slot = next(s for s in result.dossier.legal if s.slot == "corporate_actions")
    assert slot.state == "not_found"
    assert result.dossier.counters["unverifiable"] == 0


def test_a_scan_still_reports_substitution(paths, config, asset, rpc):
    """Unreadable is not unmonitored: the hash of a scan is watched like any other."""
    from assayer import diff as diff_mod

    first = take_snapshot(paths, config, asset, rpc, SCAN_ROUTES, now=T1)
    replaced = {PROSPECTUS_URL: ("application/pdf",
                                 pdfs.scanned_prospectus(pages=3))}
    second = take_snapshot(paths, config, asset, rpc, replaced,
                           now=datetime(2026, 2, 11, 9, 0, tzinfo=timezone.utc))
    changes = diff_mod.diff_snapshots(first.snapshot, second.snapshot)
    assert any(c["kind"] == "document_replaced" for c in changes)
