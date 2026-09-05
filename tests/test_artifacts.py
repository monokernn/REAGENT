"""Tests 12-16: the three artefacts and the properties that fall out of them.

``dossier = f(snapshot, extraction)`` is the whole architecture in one line. If
it holds, re-verification is free, drift is subtraction, and a snapshot that has
not changed cannot cost anything.
"""

from __future__ import annotations

from datetime import datetime, timezone

from assayer import util
from assayer.cli import NOTHING_TO_DO, OK, main
from assayer.corpus import Corpus, Extracted
from assayer.render import H_CHANGES
from assayer.snapshot import build as build_mod
from assayer.snapshot import docs as docs_mod
from assayer.verify import gate as gate_mod
from assayer.verify import quotes as quotes_mod
from conftest import (
    CUSTODY_URL, PROSPECTUS_URL, document_evidence, take_snapshot,
    write_extraction,
)
from fixtures import pages, pdfs

T1 = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 2, 8, 12, 0, tzinfo=timezone.utc)
REAL_QUOTE = "tokenised debt securities issued by Assay Issuer Limited"


def routes(pdf: bytes | None = None) -> dict:
    return {PROSPECTUS_URL: ("application/pdf", pdf or pdfs.clean_prospectus())}


def _slots(snapshot: dict) -> list[dict]:
    return [{"slot": "instrument_type", "state": "filled",
             "value": "debt note of the issuer",
             "evidence": document_evidence(snapshot, "prospectus", REAL_QUOTE)},
            {"slot": "insolvency", "state": "not_found",
             "value": "the documents say nothing about the order on insolvency.",
             "evidence": {"kind": "absence", "checked_docs": ["prospectus"],
                          "patterns": ["bankruptcy remote", "segregated"]}}]


def test_12_verify_is_deterministic(paths, config, asset, rpc):
    """Two runs over the same inputs produce byte-identical JSON."""
    snap = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    snapshot = snap.snapshot
    extraction = write_extraction(paths, snapshot, _slots(snapshot))

    first = gate_mod.build_dossier(paths, config, asset, snapshot, extraction)
    second = gate_mod.build_dossier(paths, config, asset, snapshot, extraction)
    assert first.ok and second.ok
    left = util.canonical_json(first.dossier.to_json())
    right = util.canonical_json(second.dossier.to_json())
    assert left == right
    assert first.text == second.text

    path_a = gate_mod.write_dossier(paths, asset, first) / "dossier.json"
    bytes_a = path_a.read_bytes()
    gate_mod.write_dossier(paths, asset, second)
    assert path_a.read_bytes() == bytes_a

    assert "created_at" not in left, "a dossier may not carry a wall clock"
    assert "generated" not in left


def test_13_verify_all_calls_no_model(paths, config, asset, rpc, monkeypatch):
    """Improve the gate, re-run the archive, pay nothing."""
    for moment in (T1, T2):
        snap = take_snapshot(paths, config, asset, rpc, routes(), now=moment)
        write_extraction(paths, snap.snapshot, _slots(snap.snapshot))

    from assayer.extract import agent as agent_mod

    def explode(*args, **kwargs):
        raise AssertionError("verify called a model")

    monkeypatch.setattr(agent_mod.subprocess, "run", explode)
    monkeypatch.setattr(agent_mod, "call_agent", explode)

    code = main(["--home", str(paths.root), "verify", "--all"])
    assert code == OK
    assert len(gate_mod.list_dossiers(paths, asset)) == 2

    budget = util.read_json(paths.budget) if paths.budget.exists() else {}
    assert float(budget.get("total_usd", 0.0)) == 0.0


def test_14_an_unchanged_snapshot_hash_prevents_extract(paths, config, asset, rpc,
                                                       monkeypatch, capsys):
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    second = take_snapshot(paths, config, asset, rpc, routes(), now=T2)
    assert first.snapshot_hash == second.snapshot_hash
    assert second.changed is False

    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda config: (True, "fake"))

    def explode(*args, **kwargs):
        raise AssertionError("the model was called for an unchanged snapshot")

    monkeypatch.setattr(agent_mod, "run", explode)
    monkeypatch.setattr(agent_mod, "call_agent", explode)

    code = main(["--home", str(paths.root), "extract", asset.name])
    assert code == NOTHING_TO_DO
    assert "has not changed" in capsys.readouterr().out


def test_14b_the_hash_ignores_the_block_and_the_clock(paths, config, asset, rpc):
    """Otherwise every new block would look like a change and cost money."""
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    rpc.block += 5000
    rpc.set_uint("0x1111111111111111111111111111111111111111",
                 "totalSupply()", 2_000_000 * 10 ** 18)
    second = take_snapshot(paths, config, asset, rpc, routes(), now=T2)
    assert first.snapshot_hash == second.snapshot_hash
    assert second.snapshot["block_number"] != first.snapshot["block_number"]

    # The supply move is still reported; it is simply not an identity change.
    from assayer import diff as diff_mod

    changes = [c for c in diff_mod.diff_snapshots(first.snapshot, second.snapshot)
               if c["subject"] == "totalSupply"]
    assert changes, "a supply change must still show up in the diff"


def test_14c_a_changed_document_changes_both_hashes(paths, config, asset, rpc):
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    edited = pdfs.build_pdf([pdfs.visible("The Notes are equity securities.")])
    second = take_snapshot(paths, config, asset, rpc, routes(edited), now=T2)
    assert first.snapshot["docs_hash"] != second.snapshot["docs_hash"]
    assert first.snapshot_hash != second.snapshot_hash
    assert second.changed is True


def test_15_canonical_text_is_extracted_exactly_once(paths, config, asset, rpc):
    """A locator written today still points at the same characters tomorrow."""
    snap = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    sha = snap.snapshot["documents"][0]["sha256"]
    corpus = Corpus(paths.corpus)
    original_text = corpus.get(sha).text
    original_meta = corpus.get(sha).meta

    located = docs_mod.find_quote(original_text, REAL_QUOTE)
    assert located is not None

    raw = corpus.get(sha).raw

    def different_extractor(data: bytes, content_type: str) -> Extracted:
        raise AssertionError("a document already in the corpus was re-extracted")

    corpus.put(raw, url=PROSPECTUS_URL, content_type="application/pdf",
               extract=different_extractor)

    again = corpus.get(sha)
    assert again.text == original_text
    assert again.meta["extractor"] == original_meta["extractor"]
    assert again.meta["extractor_version"] == original_meta["extractor_version"]
    assert again.meta["extracted_at"] == original_meta["extracted_at"]

    # And a second snapshot keeps the locator valid.
    take_snapshot(paths, config, asset, rpc, routes(), now=T2)
    from assayer.model import Evidence, Locator

    evidence = Evidence(kind="document", doc_id="prospectus", sha256=sha,
                        quote=REAL_QUOTE,
                        locator=Locator(type="char_range", start=located[0],
                                        end=located[1]))
    check = quotes_mod.check(evidence, corpus, {sha})
    assert check.outcome == quotes_mod.OK


def test_16_a_replaced_document_with_the_same_version_is_reported(paths, config,
                                                                asset, rpc):
    """The quiet substitution: same declared version, different bytes."""
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    swapped = pdfs.build_pdf([
        pdfs.visible("The Notes are tokenised debt securities issued by "
                     "Assay Issuer Limited."),
        pdfs.visible("The Issuer may redeem the Notes at any time without notice."),
    ])
    second = take_snapshot(paths, config, asset, rpc, routes(swapped), now=T2)

    from assayer import diff as diff_mod

    changes = diff_mod.diff_snapshots(first.snapshot, second.snapshot)
    replaced = [c for c in changes if c["kind"] == "document_replaced"]
    assert len(replaced) == 1
    assert replaced[0]["material"] is True
    assert "2026-01" in str(replaced[0]["detail"]), replaced[0]

    result = gate_mod.build_dossier(
        paths, config, asset, second.snapshot, None,
        build_mod.previous_snapshot(paths, asset, before=second.ts))
    assert result.ok
    text = result.text or ""
    assert H_CHANGES in text
    section = text.split(H_CHANGES, 1)[1]
    assert "substituted" in section
    assert result.dossier.days_since_previous == 7


def test_16b_a_document_that_stops_resolving_is_a_change(paths, config, asset, rpc):
    take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    second = take_snapshot(paths, config, asset, rpc, {}, now=T2)
    from assayer import diff as diff_mod

    previous = build_mod.load_snapshot(paths, asset,
                                      build_mod.list_snapshots(paths, asset)[0])
    changes = diff_mod.diff_snapshots(previous, second.snapshot)
    kinds = {c["kind"] for c in changes}
    assert "document_state" in kinds
    document = second.snapshot["documents"][0]
    assert document["state"] == "error"
    assert document["reason"] == "doc_unavailable"


def test_16c_two_documents_share_one_corpus_entry(paths, config, rpc):
    """Content addressing: the same bytes at two URLs are stored once."""
    from conftest import write_asset

    body = pages.CLEAN_HTML.encode("utf-8")
    asset = write_asset(paths, [("custody", CUSTODY_URL, None),
                                ("custody_mirror",
                                 "https://issuer.example/custody.html", None)])
    snap = take_snapshot(paths, config, asset, rpc, {
        CUSTODY_URL: ("text/html", body),
        "https://issuer.example/custody.html": ("text/html", body),
    }, now=T1)
    shas = {d["sha256"] for d in snap.snapshot["documents"]}
    assert len(shas) == 1
    assert len(Corpus(paths.corpus).list()) == 1
    meta = Corpus(paths.corpus).get(shas.pop()).meta
    assert len(meta["sources"]) == 2
