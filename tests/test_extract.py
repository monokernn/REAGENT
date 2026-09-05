"""Extraction through a real subprocess, with a fake agent CLI standing in.

Any command that takes a prompt and prints JSON qualifies, so the test supplies
one: a five-line script. This is the only seam in the project where money would
be spent, so it is exercised for real rather than monkeypatched — including the
retry on unparseable output and the ledger entry that follows every call.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assayer import ledger
from assayer.cli import NOTHING_TO_DO, OK, main
from assayer.extract import agent as agent_mod
from assayer.model import FILLED, REJECTED
from assayer.snapshot import build as build_mod
from assayer.verify import gate as gate_mod
from conftest import PROSPECTUS_URL, take_snapshot
from fixtures import pdfs

T1 = datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc)
ROUTES = {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus())}

REAL_QUOTE = "tokenised debt securities issued by Assay Issuer Limited"

AGENT_SCRIPT = '''\
"""A fake agent CLI: reads a prompt on stdin, prints one JSON object."""
import json, re, sys

counter = sys.argv[1] if len(sys.argv) > 1 else None
prompt = sys.stdin.read()
attempt = 1
if counter:
    try:
        attempt = int(open(counter).read().strip()) + 1
    except OSError:
        attempt = 1
    open(counter, "w").write(str(attempt))

sha = re.search(r"sha256=([0-9a-f]{64})", prompt).group(1)

if attempt == 1 and counter:
    # First answer is prose, which the schema must refuse.
    print(json.dumps({"result": "Sure! Here is my analysis of the prospectus.",
                      "total_cost_usd": 0.01}))
    raise SystemExit(0)

answer = {"slots": [
    {"slot": "instrument_type", "state": "filled",
     "value": "debt security issued by the issuer",
     "evidence": {"kind": "document", "doc_id": "prospectus", "sha256": sha,
                  "page": 1,
                  "quote": "tokenised debt securities issued by Assay Issuer Limited"}},
    {"slot": "insolvency", "state": "filled",
     "value": "collateral is ring-fenced for holders",
     "evidence": {"kind": "document", "doc_id": "prospectus", "sha256": sha,
                  "page": 3,
                  "quote": "the collateral is ring-fenced for the holders"}},
    {"slot": "corporate_actions", "state": "not_found",
     "value": "nothing is said about dividends or splits.",
     "evidence": {"kind": "absence", "checked_docs": ["prospectus"],
                  "patterns": ["dividend", "stock split"]}},
    {"slot": "custodian", "state": "not_checked",
     "reason": "document_is_a_scan"},
]}
print(json.dumps({"result": json.dumps(answer), "total_cost_usd": 0.0123}))
'''


@pytest.fixture
def fake_agent(tmp_path: Path):
    script = tmp_path / "fake_agent.py"
    script.write_text(AGENT_SCRIPT, encoding="utf-8")
    return script


def agent_config(config: dict, script: Path, counter: Path | None = None) -> dict:
    args = [str(script)] + ([str(counter)] if counter else [])
    out = dict(config)
    out["agent"] = dict(config["agent"], command=sys.executable, args=args)
    return out


def test_extraction_runs_through_a_real_subprocess(paths, config, asset, rpc,
                                                  fake_agent):
    cfg = agent_config(config, fake_agent)
    available, detail = agent_mod.agent_available(cfg)
    assert available, detail

    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    result = agent_mod.run(paths, cfg, asset, snap.snapshot)

    assert result.attempts == 1
    assert result.cost_usd == pytest.approx(0.0123)
    assert (result.path / "extraction.json").exists()
    assert (result.path / "prompt.txt").exists()
    assert (result.path / "response.1.txt").exists()
    assert result.extraction["snapshot_hash"] == snap.snapshot_hash

    # The spend is recorded, atomically, the moment the call returns.
    assert ledger.state(paths).spent_today == pytest.approx(0.0123)

    gate = gate_mod.build_dossier(paths, config, asset, snap.snapshot,
                                 result.extraction, cost_usd=result.cost_usd)
    assert gate.ok, [str(v) for v in gate.violations]
    by_name = {s.slot: s for s in gate.dossier.legal}
    assert by_name["instrument_type"].state == FILLED
    assert by_name["instrument_type"].value.startswith("debt_security")
    assert by_name["insolvency"].state == REJECTED, "an invented quote must not pass"
    assert by_name["corporate_actions"].state == "not_found"
    assert by_name["custodian"].state == "not_checked"
    assert by_name["custodian"].reason == "document_is_a_scan"
    assert gate.dossier.counters["fabrications"] == 1
    assert gate.dossier.counters["spent_usd"] == pytest.approx(0.0123)


def test_prose_is_refused_and_retried_once(paths, config, asset, rpc, fake_agent,
                                          tmp_path):
    counter = tmp_path / "attempts.txt"
    cfg = agent_config(config, fake_agent, counter)
    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)

    result = agent_mod.run(paths, cfg, asset, snap.snapshot)
    assert result.attempts == 2, "one retry, then the slots stay not_checked"
    assert result.parse_errors
    assert result.extraction["slots"], "the retry produced usable slots"
    # Both calls are paid for and both are on the ledger.
    assert result.cost_usd == pytest.approx(0.01 + 0.0123)
    assert ledger.state(paths).calls_today == 2


def test_the_prompt_carries_what_the_contract_demands(paths, config, asset, rpc):
    from assayer.corpus import Corpus
    from assayer.extract import prompt as prompt_mod

    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    built = prompt_mod.build(snap.snapshot, Corpus(paths.corpus))
    sha = snap.snapshot["documents"][0]["sha256"]

    assert f"sha256={sha}" in built.text
    assert REAL_QUOTE in built.text, "the canonical text itself is handed over"
    for slot in ("instrument_type", "insolvency", "restrictions"):
        assert slot in built.text
    assert "verbatim" in built.text
    assert built.hash == built.hash
    assert prompt_mod.build(snap.snapshot, Corpus(paths.corpus)).hash == built.hash

    # Truncation is admitted rather than hidden.
    short = prompt_mod.build(snap.snapshot, Corpus(paths.corpus), max_chars=50)
    assert short.truncated == ["prospectus"]
    assert short.documents[0]["truncated"] is True


def test_extract_twice_pays_once(paths, config, asset, rpc, fake_agent, capsys):
    cfg_path = paths.config
    original = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        original.replace('command = "no-such-agent-cli"',
                         f'command = {json.dumps(sys.executable)}')
        .replace('args = ["-p"]', f'args = [{json.dumps(str(fake_agent))}]'),
        encoding="utf-8")

    take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    home = str(paths.root)
    assert main(["--home", home, "extract", asset.name]) == OK
    spent = ledger.state(paths).spent_today
    assert spent > 0

    assert main(["--home", home, "extract", asset.name]) == NOTHING_TO_DO
    assert ledger.state(paths).spent_today == pytest.approx(spent), \
        "a second extraction of the same snapshot must cost nothing"
    assert "already has an" in capsys.readouterr().out


def test_a_missing_agent_is_reported_not_guessed_around(paths, config, asset, rpc):
    take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    available, detail = agent_mod.agent_available(config)
    assert not available
    assert "no-such-agent-cli" in detail

    with pytest.raises(agent_mod.AgentUnavailable):
        agent_mod.call_agent(config, "prompt")

    snapshots = build_mod.list_snapshots(paths, asset)
    assert snapshots, "snap works without any agent installed"
    result = gate_mod.build_dossier(
        paths, config, asset, build_mod.load_snapshot(paths, asset, snapshots[-1]))
    assert result.ok, "so does the gate"
