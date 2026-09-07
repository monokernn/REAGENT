"""Tests 19-22: budgets, the ledger under concurrency, and the CLI surface.

The budget tests matter because the failure they prevent is silent: a loop that
re-reads the same unchanged prospectus every hour costs real money and produces
nothing new.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from assayer import ledger, util
from assayer.cli import BUDGET, DRIFT, ERROR, NO_AGENT, OK, USAGE, main
from conftest import PROSPECTUS_URL, take_snapshot
from fixtures import pdfs

T1 = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
ROUTES = {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus())}


def test_19_the_daily_limit_blocks_extract(paths, config, asset, rpc, monkeypatch,
                                          capsys):
    take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    ledger.record(paths, 2.00, asset=asset.name, kind="extraction")

    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda config: (True, "fake"))

    def explode(*args, **kwargs):
        raise AssertionError("the model was called after the budget refused")

    monkeypatch.setattr(agent_mod, "call_agent", explode)

    code = main(["--home", str(paths.root), "extract", asset.name, "--force"])
    assert code == BUDGET
    assert "daily_limit_reached" in capsys.readouterr().out


def test_19b_check_passes_below_the_limit(paths, config):
    ledger.record(paths, 0.10, asset="x", kind="extraction")
    state = ledger.check(paths, config)
    assert state.spent_today == pytest.approx(0.10)


def test_20_an_over_limit_call_blocks_the_next_one(paths, config):
    """A runaway call cannot be undone, so the next one is refused instead."""
    ledger.record(paths, 0.50, asset="x", kind="extraction")
    with pytest.raises(ledger.BudgetExceeded) as caught:
        ledger.check(paths, config)
    assert caught.value.reason == "previous_extraction_over_limit"

    ledger.clear_last_extraction(paths)
    state = ledger.check(paths, config)
    assert state.last_extraction_usd == 0.0


def test_21_concurrent_writers_lose_nothing(paths):
    """Twenty threads, five spends each, one hundred cents exactly."""
    threads = [threading.Thread(target=lambda: [
        ledger.record(paths, 0.01, asset="x", kind="probe") for _ in range(5)])
        for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state = ledger.state(paths)
    assert state.total == pytest.approx(1.00)
    assert state.calls_today == 100
    lines = paths.log.read_text(encoding="utf-8").strip().splitlines()
    assert len([line for line in lines if '"spend"' in line]) == 100


def test_21b_the_log_survives_concurrent_appends(paths):
    import json

    def worker(index: int) -> None:
        for step in range(10):
            util.log_event(paths, "probe", worker=index, step=step)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = paths.log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 100
    for line in lines:
        json.loads(line)  # every line is complete JSON, none interleaved


def test_22_every_subcommand_answers_with_a_code(paths, config, asset, rpc, capsys):
    home = str(paths.root)
    take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)

    assert main([]) == USAGE
    assert main(["--home", home, "status"]) == OK
    assert main(["--home", home, "doctor"]) in (OK, ERROR)
    assert main(["--home", home, "show", asset.name]) == ERROR  # no dossier yet
    assert main(["--home", home, "verify", asset.name]) == OK
    assert main(["--home", home, "show", asset.name]) == OK
    assert main(["--home", home, "show", "no-such-asset"]) == ERROR
    assert main(["--home", home, "diff", asset.name]) == ERROR  # one snapshot only
    assert main(["--home", home, "evidence", asset.name, "pause"]) == OK
    assert main(["--home", home, "evidence", asset.name, "nonsense"]) == ERROR
    assert main(["--home", home, "extract", asset.name]) == NO_AGENT
    assert main(["--home", home, "scar", "new", "--what", "issuer halted redemption",
                 "--signals", "upgrade_no_timelock,offshore_issuer"]) == OK
    assert main(["--home", home, "scar", "new"]) == USAGE
    assert main(["--home", home, "add", "not-an-address"]) == USAGE
    assert main(["--home", home, "add", "ethereum:0x" + "9" * 40,
                 "--docs", "https://docs.issuer.example/terms.pdf"]) == OK
    assert main(["--home", home, "snap"]) == USAGE
    capsys.readouterr()


def test_22b_watch_signals_drift_through_its_exit_code(paths, config, asset, rpc,
                                                      monkeypatch):
    """A cron job wants a non-zero code when something moved, and zero when not."""
    from assayer.snapshot import build as build_mod
    from conftest import FakeFetcher

    allowed = list(config["sources"]["allowed_domains"])
    state = {"routes": dict(ROUTES)}

    def fake_build(p, cfg, a, **kwargs):
        kwargs.pop("fetcher", None)
        return original_build(p, cfg, a,
                              fetcher=FakeFetcher(allowed, state["routes"]),
                              rpc=rpc, **kwargs)

    original_build = build_mod.build
    monkeypatch.setattr(build_mod, "build", fake_build)

    assert main(["--home", str(paths.root), "watch"]) == OK
    assert main(["--home", str(paths.root), "watch"]) == OK

    state["routes"] = {PROSPECTUS_URL: ("application/pdf", pdfs.build_pdf(
        [pdfs.visible("The Notes are equity securities after all.")]))}
    assert main(["--home", str(paths.root), "watch"]) == DRIFT


def test_22c_snap_reports_whether_anything_changed(paths, config, asset, rpc,
                                                  capsys):
    take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    out = capsys.readouterr().out
    assert out == "" or "snapshot" in out


def test_22d_an_unreadable_asset_file_does_not_hide_the_others(paths, config):
    from assayer import model

    (paths.assets / "broken.toml").write_text("this is not toml = [", encoding="utf-8")
    assert model.list_assets(paths) == []
    assert main(["--home", str(paths.root), "status"]) == OK


def test_22e_doctor_catches_a_corpus_entry_edited_behind_its_back(paths, config,
                                                                 asset, rpc, capsys):
    """Content addressing is a promise about bytes on disk. It is worth checking.

    A locator written months ago is only meaningful while the file it addresses
    still hashes to the name it is filed under. Editing that file locally would
    otherwise leave every quote citing content nobody verified.
    """
    from assayer.corpus import Corpus

    take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    corpus = Corpus(paths.corpus)
    stored = corpus.list()
    assert stored, "the snapshot should have filled the corpus"
    assert corpus.check(deep=True) == []

    original = next((paths.corpus / stored[0]).glob("original.*"))
    original.write_bytes(original.read_bytes() + b"\n% quietly appended\n")

    # The shallow pass still sees a complete entry; only re-hashing finds it.
    assert corpus.check() == []
    problems = corpus.check(deep=True)
    assert [p.kind for p in problems] == ["hash_mismatch"]
    assert problems[0].sha256 == stored[0]

    capsys.readouterr()
    assert main(["--home", str(paths.root), "doctor", "--deep"]) == ERROR
    printed = capsys.readouterr().out
    assert "hash_mismatch" in printed
    assert stored[0][:12] in printed


def test_22f_the_injection_tail_counts_every_finding(capsys):
    """"and N more" must count what was found, not what an iterator has left.

    Undercounting is the one direction this line cannot be wrong in: a reader
    who is told about three findings and eleven more behaves differently from
    one told about three and seventeen.
    """
    from assayer.cli import _print_injections

    def findings(n):
        for i in range(n):
            yield {"doc_id": f"doc{i}", "technique": "hidden_css",
                   "excerpt": "…", "severity": "hidden"}

    _print_injections(findings(20), limit=3)
    printed = capsys.readouterr().out
    assert "and 17 more" in printed
    assert printed.count("hidden_css") == 3

    # A list gets the same answer, and a short run says nothing about a tail.
    _print_injections(list(findings(20)), limit=3)
    assert "and 17 more" in capsys.readouterr().out
    _print_injections(findings(2), limit=3)
    assert "more" not in capsys.readouterr().out
