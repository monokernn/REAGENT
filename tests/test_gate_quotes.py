"""Tests 4-5: fabricated quotes are removed and printed; drifted offsets are repaired.

The interesting case is not that a wrong quote fails. It is that the failure ends
up on the reader's sheet, under its own heading, with the slot discarded rather
than softened.
"""

from __future__ import annotations

from assayer import util
from assayer.model import FILLED, REJECTED
from assayer.render import H_REJECTED
from assayer.verify import gate as gate_mod
from conftest import (
    PROSPECTUS_URL, document_evidence, take_snapshot, write_extraction,
)
from fixtures import pdfs

REAL_QUOTE = "tokenised debt securities issued by Assay Issuer Limited"
INVENTED_QUOTE = ("the collateral is held in a bankruptcy remote vehicle "
                  "for the exclusive benefit of holders")


def routes() -> dict:
    return {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus())}


def test_4_fabricated_quote_is_rejected_and_printed(paths, config, asset, rpc):
    snap = take_snapshot(paths, config, asset, rpc, routes())
    snapshot = snap.snapshot

    slots = [
        {"slot": "instrument_type", "state": "filled",
         "value": "debt note of the issuer, not a share in the company",
         "evidence": document_evidence(snapshot, "prospectus", REAL_QUOTE)},
        {"slot": "insolvency", "state": "filled",
         "value": "the collateral is segregated from other creditors",
         "evidence": document_evidence(snapshot, "prospectus", INVENTED_QUOTE)},
    ]
    extraction = write_extraction(paths, snapshot, slots)

    result = gate_mod.build_dossier(paths, config, asset, snapshot, extraction)
    assert result.ok, [str(v) for v in result.violations]
    dossier = result.dossier
    assert dossier is not None

    by_name = {s.slot: s for s in dossier.legal}
    assert by_name["instrument_type"].state == FILLED
    assert by_name["insolvency"].state == REJECTED
    assert by_name["insolvency"].reason == "fabricated_quote"
    assert by_name["insolvency"].value is None, "a rejected claim keeps no value"
    assert dossier.counters["fabrications"] == 1

    text = result.text or ""
    assert H_REJECTED in text
    assert "insolvency" in text.split(H_REJECTED, 1)[1]
    assert "The gate caught fabricated quotes: 1" in text


def test_5_drifted_offsets_are_repaired_and_logged(paths, config, asset, rpc):
    snap = take_snapshot(paths, config, asset, rpc, routes())
    snapshot = snap.snapshot
    sha = snapshot["documents"][0]["sha256"]
    text = (paths.corpus / sha / "canonical.txt").read_text(encoding="utf-8")
    true_start = text.index(REAL_QUOTE)

    slots = [{
        "slot": "instrument_type", "state": "filled",
        "value": "debt note of the issuer",
        "evidence": document_evidence(snapshot, "prospectus", REAL_QUOTE,
                                      start=true_start + 40,
                                      end=true_start + 40 + len(REAL_QUOTE)),
    }]
    extraction = write_extraction(paths, snapshot, slots)
    result = gate_mod.build_dossier(paths, config, asset, snapshot, extraction)

    slot = next(s for s in result.dossier.legal if s.slot == "instrument_type")
    assert slot.state == FILLED, "a present quote must not be discarded"
    assert slot.evidence.locator.start == true_start
    assert result.dossier.counters["locator_drift"] == 1
    assert result.dossier.gate["locator_drift"][0]["slot"] == "instrument_type"
    assert result.dossier.counters["fabrications"] == 0


def test_5b_absence_claim_is_re_run_not_believed(paths, config, asset, rpc):
    """An absence claim is evidence, so it is re-checked like any other."""
    snap = take_snapshot(paths, config, asset, rpc, routes())
    snapshot = snap.snapshot

    honest = [{"slot": "corporate_actions", "state": "not_found",
               "value": "the checked documents say nothing about dividends or splits.",
               "evidence": {"kind": "absence", "checked_docs": ["prospectus"],
                            "patterns": ["dividend", "stock split"]}}]
    result = gate_mod.build_dossier(paths, config, asset, snapshot,
                                   write_extraction(paths, snapshot, honest))
    slot = next(s for s in result.dossier.legal if s.slot == "corporate_actions")
    assert slot.state == "not_found"

    false_absence = [{"slot": "insolvency", "state": "not_found",
                      "value": "nothing is said about insolvency.",
                      "evidence": {"kind": "absence",
                                   "checked_docs": ["prospectus"],
                                   "patterns": ["insolvency"]}}]
    result = gate_mod.build_dossier(paths, config, asset, snapshot,
                                   write_extraction(paths, snapshot, false_absence))
    slot = next(s for s in result.dossier.legal if s.slot == "insolvency")
    assert slot.state == REJECTED, "the word does occur; the absence claim is false"
    assert result.dossier.counters["fabrications"] == 1


def test_4b_taint_threshold_marks_the_document(paths, config, asset, rpc):
    """Three inventions from one document and it is printed as untrustworthy."""
    config = dict(config)
    config["gate"] = dict(config["gate"], taint_threshold=3)
    snap = take_snapshot(paths, config, asset, rpc, routes())
    snapshot = snap.snapshot
    slots = [
        {"slot": name, "state": "filled", "value": "something",
         "evidence": document_evidence(snapshot, "prospectus",
                                       f"{INVENTED_QUOTE} number {index}")}
        for index, name in enumerate(("insolvency", "custodian", "restrictions"))
    ]
    result = gate_mod.build_dossier(paths, config, asset, snapshot,
                                   write_extraction(paths, snapshot, slots))
    assert result.dossier.counters["fabrications"] == 3
    assert result.dossier.tainted_docs == ["prospectus"]
    assert "Treat anything sourced from them as unverified" in \
        (result.text or "")


def test_4c_a_claim_without_evidence_never_reaches_the_sheet(paths, config, asset,
                                                            rpc):
    snap = take_snapshot(paths, config, asset, rpc, routes())
    snapshot = snap.snapshot
    slots = [{"slot": "issuer", "state": "filled",
              "value": "Assay Issuer Limited, Jersey"}]
    result = gate_mod.build_dossier(paths, config, asset, snapshot,
                                   write_extraction(paths, snapshot, slots))
    slot = next(s for s in result.dossier.legal if s.slot == "issuer")
    assert slot.state == REJECTED
    assert slot.reason == "claim_without_evidence"
    assert "Assay Issuer Limited" not in util.canonical_json(
        result.dossier.to_json())


def test_4d_model_may_not_produce_onchain_evidence(paths, config, asset, rpc):
    snap = take_snapshot(paths, config, asset, rpc, routes())
    snapshot = snap.snapshot
    slots = [{"slot": "issuer", "state": "filled", "value": "something",
              "evidence": {"kind": "onchain", "chain_id": 1, "address": "0x11",
                           "method": "owner()", "block": 1, "raw": "0x0"}}]
    result = gate_mod.build_dossier(paths, config, asset, snapshot,
                                   write_extraction(paths, snapshot, slots))
    slot = next(s for s in result.dossier.legal if s.slot == "issuer")
    assert slot.state == REJECTED
    assert slot.reason == "evidence_kind_not_allowed"
