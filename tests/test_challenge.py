"""The adversarial second mandate, held to the same evidence contract.

Asked "how does this end badly for a holder", a model will happily produce a
page of observations that could be written about any asset ever issued. The only
filter that works on that is the one already built: a thesis without a verbatim
quote from a named document does not reach the reader.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from assayer.extract import agent as agent_mod
from assayer.render import H_CHALLENGE, H_CONTEXT
from assayer.verify import gate as gate_mod
from conftest import PROSPECTUS_URL, take_snapshot, write_extraction
from fixtures import pdfs

T1 = datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc)
ROUTES = {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus())}

GROUNDED_QUOTE = "The Issuer may decline any redemption request"
GROUNDED_CLAIM = ("getting out depends on the issuer: it may decline a "
                  "redemption request at its discretion")
UNGROUNDED_CLAIM = "the instrument may not scale as volumes grow"

AGENT_WITH_CHALLENGE = '''\
import json, re, sys
prompt = sys.stdin.read()
sha = re.search(r"sha256=([0-9a-f]{64})", prompt).group(1)
answer = {
  "slots": [
    {"slot": "redemption", "state": "filled",
     "value": "redemption in kind is not available",
     "evidence": {"kind": "document", "doc_id": "prospectus", "sha256": sha,
                  "quote": "Redemption in kind is not available"}}
  ],
  "challenge": [
    {"claim": "EXIT_DEPENDS",
     "evidence": {"kind": "document", "doc_id": "prospectus", "sha256": sha,
                  "quote": "The Issuer may decline any redemption request"}},
    {"claim": "NO_BASIS"},
    {"claim": "INVENTED_QUOTE",
     "evidence": {"kind": "document", "doc_id": "prospectus", "sha256": sha,
                  "quote": "the Issuer guarantees repayment in full at all times"}}
  ]
}
print(json.dumps({"result": json.dumps(answer), "total_cost_usd": 0.02}))
'''


def evidence_for(snapshot, quote):
    from conftest import document_evidence

    return document_evidence(snapshot, "prospectus", quote)


def test_a_thesis_without_evidence_never_reaches_the_sheet(paths, config, asset,
                                                          rpc):
    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    extraction = write_extraction(paths, snap.snapshot, [])
    extraction["challenge"] = [
        {"claim": GROUNDED_CLAIM,
         "evidence": evidence_for(snap.snapshot, GROUNDED_QUOTE)},
        {"claim": UNGROUNDED_CLAIM},
        {"claim": "the collateral is segregated, so losses are impossible",
         "evidence": evidence_for(snap.snapshot,
                                  "the collateral is fully segregated")},
    ]

    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot, extraction)
    assert result.ok, [str(v) for v in result.violations]
    kept = [item["claim"] for item in result.dossier.challenge]
    assert kept == [GROUNDED_CLAIM]
    assert result.dossier.counters["challenge_kept"] == 1
    assert result.dossier.counters["challenge_dropped"] == 2

    reasons = {d["reason"] for d in result.dossier.gate["challenge_dropped"]}
    assert reasons == {"claim_without_evidence", "fabricated_quote"}
    # An invented quote in a thesis counts as an invention like any other.
    assert result.dossier.counters["fabrications"] == 1

    text = result.text or ""
    assert H_CHALLENGE in text
    section = text.split(H_CHALLENGE, 1)[1]
    assert GROUNDED_CLAIM.split(":")[0] in section
    assert "prospectus" in section
    assert UNGROUNDED_CLAIM not in text, "a general observation is not printed"
    assert "a thesis was cut by the gate" in text.split(H_CONTEXT, 1)[1]


def test_no_challenge_no_section(paths, config, asset, rpc):
    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot,
                                   write_extraction(paths, snap.snapshot, []))
    assert result.dossier.challenge == []
    assert H_CHALLENGE not in (result.text or "")


def test_challenge_survives_a_real_agent_round_trip(paths, config, asset, rpc,
                                                    tmp_path):
    script = tmp_path / "challenging_agent.py"
    script.write_text(AGENT_WITH_CHALLENGE, encoding="utf-8")
    cfg = dict(config)
    cfg["agent"] = dict(config["agent"], command=sys.executable,
                        args=[str(script)])
    cfg["extract"] = {"challenge": True}

    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    run = agent_mod.run(paths, cfg, asset, snap.snapshot, challenge=True)
    assert len(run.extraction["challenge"]) == 3, "parsing keeps what was offered"

    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot,
                                   run.extraction, cost_usd=run.cost_usd)
    assert result.ok, [str(v) for v in result.violations]
    kept = [item["claim"] for item in result.dossier.challenge]
    assert kept == ["EXIT_DEPENDS"]
    assert result.dossier.counters["challenge_dropped"] == 2
    assert result.dossier.counters["fabrications"] == 1


def test_the_challenge_prompt_demands_a_quote(paths, config, asset, rpc):
    from assayer.corpus import Corpus
    from assayer.extract import prompt as prompt_mod

    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    plain = prompt_mod.build(snap.snapshot, Corpus(paths.corpus))
    assert "CHALLENGE" not in plain.text

    with_challenge = prompt_mod.build(snap.snapshot, Corpus(paths.corpus),
                                      challenge=True)
    assert "CHALLENGE" in with_challenge.text
    assert "verbatim quote" in with_challenge.text
    assert "discarded" in with_challenge.text
    assert with_challenge.hash != plain.hash


def test_a_thesis_quoting_an_unreadable_document_is_dropped(paths, config, asset,
                                                            rpc):
    snap = take_snapshot(paths, config, asset, rpc,
                        {PROSPECTUS_URL: ("application/pdf",
                                          pdfs.scanned_prospectus())}, now=T1)
    sha = snap.snapshot["documents"][0]["sha256"]
    extraction = write_extraction(paths, snap.snapshot, [])
    extraction["challenge"] = [{
        "claim": "the issuer may decline a redemption request",
        "evidence": {"kind": "document", "doc_id": "prospectus", "sha256": sha,
                     "quote": "The Issuer may decline any redemption request"},
    }]
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot, extraction)
    assert result.dossier.challenge == []
    assert result.dossier.gate["challenge_dropped"][0]["reason"] == \
        "document_has_no_text_layer"
    assert result.dossier.counters["fabrications"] == 0
