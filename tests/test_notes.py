"""Notes on a coin: optional prose, held to the readings underneath it.

The sheet is evidence and the notes are commentary, so the commentary is the
part that has to prove itself. These tests hold the two properties that keep it
from becoming a second, softer source of truth: a sentence may not contain a
figure the readings did not produce, and the vocabulary the tool forbids itself
is forbidden to the model writing for it.

They also hold the money: the sheet stays free, the second request for an
unchanged contract does not call anything, and a call that was made is recorded
whether or not a sentence survived it.
"""

from __future__ import annotations

import json

import pytest

from assayer import model, notes as notes_mod, util
from assayer.cli import ERROR, NO_AGENT, NOTHING_TO_DO, OK, RED_LINE, main
from assayer.extract.agent import AgentCall
from assayer.verify import gate as gate_mod
from conftest import write_asset
from test_coin import coin_asset, coin_rpc, sheet_of


@pytest.fixture
def sheet(paths, config):
    """A real coin dossier, the thing the notes must stay inside."""
    asset = coin_asset(pool="0x4444444444444444444444444444444444444444")
    util.atomic_write_text(paths.assets / f"{asset.name}.toml",
                           model.asset_to_toml(asset))
    _result, gate = sheet_of(paths, config, asset, coin_rpc())
    gate_mod.write_dossier(paths, asset, gate)   # the CLI reads it back off disk
    return asset, gate.dossier.to_json()


def _stub(payload, cost=0.02):
    import json as _json
    return lambda _config, _prompt: AgentCall(
        response=_json.dumps(payload), cost_usd=cost, raw_stdout="",
        duration_s=0.1, command=["stub"])


# ------------------------------------------------------------- the figure gate

def test_a_figure_the_readings_never_produced_deletes_the_sentence(sheet):
    _asset, dossier = sheet
    result = notes_mod.gate({
        "headline": "The owner address holds 5.0% of supply.",
        "summary": "A partnership with a 40000000 dollar treasury is planned.",
    }, dossier)

    assert "headline" in result.notes, "a grounded sentence must survive"
    assert "summary" not in result.notes, "an invented figure must not"
    assert result.rejected[0]["where"] == "summary"
    assert "40000000" in result.rejected[0]["figures"]


def test_an_invented_address_is_treated_as_an_invented_figure(sheet):
    _asset, dossier = sheet
    result = notes_mod.gate(
        {"catch": "Liquidity sits at 0xdeadbeefcafe0000000000000000000000000000."},
        dossier)

    assert result.notes == {}
    assert result.rejected[0]["figures"] == \
        ["0xdeadbeefcafe0000000000000000000000000000"]


def test_a_percentage_rounded_honestly_is_still_the_reading(sheet):
    _asset, dossier = sheet
    allowed = notes_mod.allowed_figures(dossier)
    assert "96.0" in allowed, "the fixture's parked LP share should be readable"

    assert notes_mod.unverified("96% of the pool's LP is parked.", allowed) == []
    assert notes_mod.unverified("97% of the pool's LP is parked.", allowed)


def test_each_sentence_is_judged_on_its_own(sheet):
    _asset, dossier = sheet
    result = notes_mod.gate({
        "watch": ["whether ownership is given up",
                  "whether the 12345678901 tokens move"],
        "notes": {"clasp": "A fee is taken on transfer.",
                  "gauge": "The pool holds 88.8% of supply."},
    }, dossier)

    assert result.notes["watch"] == ["whether ownership is given up"]
    assert result.notes["notes"] == {"clasp": "A fee is taken on transfer."}
    assert {r["where"] for r in result.rejected} == {"watch[1]", "notes.gauge"}



def test_a_standard_named_in_a_sentence_is_not_a_claim_about_quantity(sheet):
    """ERC-20 is a name. Reading it as a figure would delete honest prose."""
    _asset, dossier = sheet
    result = notes_mod.gate(
        {"summary": "A deployed ERC-20 behind an EIP-1967 proxy, whose "
                    "token0() answers."}, dossier)

    assert "summary" in result.notes
    assert result.rejected == []


# ------------------------------------------------------------- the linter

def test_the_model_writes_under_the_vocabulary_the_tool_forbids_itself(sheet, config):
    _asset, dossier = sheet
    forbidden = (config.get("language") or {}).get("forbidden") or []
    assert forbidden, "the fixture config should carry a forbidden list"

    result = notes_mod.gate({"headline": f"This contract is {forbidden[0]}."},
                            dossier, config)
    assert result.violations, "a forbidden word must be reported, not rewritten"


# ------------------------------------------------------------- the money

def test_the_sheet_itself_never_calls_an_agent(sheet, home, monkeypatch):
    asset, _dossier = sheet
    monkeypatch.setattr(notes_mod, "build_prompt", _forbidden)
    assert main(["--home", str(home), "show", asset.name]) == OK


def test_a_second_request_for_an_unchanged_contract_pays_nothing(
        sheet, home, config, paths, monkeypatch):
    asset, _dossier = sheet
    from assayer.extract import agent as agent_mod

    calls = []

    def once(_config, prompt):
        calls.append(prompt)
        return _stub({"headline": "A fee is taken on transfer."})(None, None)

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent", once)

    assert main(["--home", str(home), "notes", asset.name]) == OK
    assert main(["--home", str(home), "notes", asset.name]) == OK
    assert len(calls) == 1, "the contract did not move, so nothing was owed"


def test_a_call_that_produced_nothing_is_still_recorded(
        sheet, home, paths, monkeypatch):
    asset, _dossier = sheet
    from assayer import ledger
    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent",
                        _stub({"headline": "Supply is 777777777 tokens."}))

    assert main(["--home", str(home), "notes", asset.name]) == OK
    stored = notes_mod.load(paths, asset.name, _dossier_hash(paths, asset))
    assert stored["notes"] == {}, "the invented figure should leave nothing"
    assert ledger.state(paths).spent_today > 0, "the call still cost money"


def test_without_an_agent_cli_the_command_says_so_and_the_sheet_still_prints(
        sheet, home, monkeypatch):
    asset, _dossier = sheet
    from assayer.extract import agent as agent_mod
    monkeypatch.setattr(agent_mod, "agent_available",
                        lambda c: (False, "'claude' is not on PATH"))

    assert main(["--home", str(home), "notes", asset.name]) == NO_AGENT
    assert main(["--home", str(home), "show", asset.name]) == OK


def test_an_asset_with_documents_is_sent_to_extract_instead(home, paths, config):
    asset = write_asset(paths, [("prospectus", "https://docs.issuer.example/p.pdf",
                                 "2026-01")])
    assert main(["--home", str(home), "notes", asset.name]) == NOTHING_TO_DO


def test_a_forbidden_word_fails_the_command(sheet, home, config, monkeypatch):
    asset, _dossier = sheet
    from assayer.extract import agent as agent_mod
    forbidden = (config.get("language") or {}).get("forbidden")[0]

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent",
                        _stub({"headline": f"The contract is {forbidden}."}))

    assert main(["--home", str(home), "notes", asset.name]) == RED_LINE


# ------------------------------------------------- judging it a second time

def test_what_was_proposed_is_kept_beside_what_survived(
        sheet, home, paths, monkeypatch):
    """Without the proposal on disk there is nothing left to judge again."""
    asset, _dossier = sheet
    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent", _stub({
        "headline": "A fee is taken on transfer.",
        "summary": "A partnership with a 40000000 dollar treasury is planned.",
    }))
    assert main(["--home", str(home), "notes", asset.name]) == OK

    stored = notes_mod.load(paths, asset.name, _dossier_hash(paths, asset))
    assert "summary" not in stored["notes"], "the invented figure was deleted"
    assert "40000000" in stored["proposed"]["summary"], \
        "but the sentence that was paid for is still there to be judged again"


def test_a_recheck_judges_the_proposal_again_rather_than_the_verdict(sheet):
    _asset, dossier = sheet
    first = notes_mod.gate({
        "headline": "A fee is taken on transfer.",
        "summary": "Supply is 777777777 tokens.",
    }, dossier)
    first.cost_usd = 0.03
    assert "summary" not in first.notes

    again = notes_mod.regate(first.to_json(), dossier)

    assert again.notes == first.notes
    assert [r["where"] for r in again.rejected] == ["summary"], \
        "the deleted sentence is judged again, not silently forgotten"
    assert again.cost_usd == 0.03, "the money was spent once and stays recorded"


def test_a_recheck_calls_nothing_and_buys_nothing(
        sheet, home, paths, monkeypatch):
    asset, _dossier = sheet
    from assayer import ledger
    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent",
                        _stub({"headline": "A fee is taken on transfer."}, 0.03))
    assert main(["--home", str(home), "notes", asset.name]) == OK
    spent = ledger.state(paths).spent_today

    def never(*_a, **_k):
        raise AssertionError("a recheck must not reach the agent CLI")

    monkeypatch.setattr(agent_mod, "call_agent", never)
    monkeypatch.setattr(notes_mod, "build_prompt", never)
    assert main(["--home", str(home), "notes", asset.name, "--recheck"]) == OK

    assert ledger.state(paths).spent_today == spent, "a recheck buys nothing"
    stored = notes_mod.load(paths, asset.name, _dossier_hash(paths, asset))
    assert stored["cost_usd"] == 0.03, "and does not erase what was spent"


def test_a_word_added_to_the_forbidden_list_catches_a_note_already_written(
        sheet, home, paths, monkeypatch):
    """The gate's vocabulary is ours, so tightening it reaches the archive."""
    asset, _dossier = sheet
    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent",
                        _stub({"headline": "The deployment is straightforward."}))
    assert main(["--home", str(home), "notes", asset.name]) == OK

    config_file = home / "config.toml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            'forbidden = ["safe"', 'forbidden = ["straightforward", "safe"'),
        encoding="utf-8")

    assert main(["--home", str(home), "notes", asset.name, "--recheck"]) == RED_LINE
    stored = notes_mod.load(paths, asset.name, _dossier_hash(paths, asset))
    assert stored["violations"], "the word added today must be reported today"


def test_notes_written_before_the_proposal_was_kept_say_so(
        sheet, home, paths, monkeypatch):
    asset, _dossier = sheet
    from assayer.extract import agent as agent_mod

    monkeypatch.setattr(agent_mod, "agent_available", lambda c: (True, "stub"))
    monkeypatch.setattr(agent_mod, "call_agent",
                        _stub({"headline": "A fee is taken on transfer."}))
    assert main(["--home", str(home), "notes", asset.name]) == OK

    # An archive entry from before the proposal was carried out of the gate.
    target = notes_mod.path_for(paths, asset.name, _dossier_hash(paths, asset))
    old = json.loads(target.read_text(encoding="utf-8"))
    old.pop("proposed")
    target.write_text(json.dumps(old), encoding="utf-8")

    assert main(["--home", str(home), "notes", asset.name, "--recheck"]) == ERROR


def test_a_recheck_of_an_asset_nobody_has_written_about_says_what_to_run(
        sheet, home):
    asset, _dossier = sheet
    assert main(["--home", str(home), "notes", asset.name, "--recheck"]) == ERROR


# ------------------------------------------------------------- helpers

def _forbidden(*_a, **_k):
    raise AssertionError("show must not build a prompt")


def _dossier_hash(paths, asset):
    ts = gate_mod.list_dossiers(paths, asset)[-1]
    return str(gate_mod.load_dossier(paths, asset, ts).get("snapshot_hash") or "")
