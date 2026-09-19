"""The registry: mechanical matching, and the one call where a model helps write.

Decomposing an account of a failure into signal names is the only place the plan
lets a model near the registry, and it happens once, when a human writes an entry
down. Whatever it proposes is filtered against the fixed vocabulary here, so the
model cannot widen the signal set — a name that no snapshot produces could never
match anything, and would sit in the file looking like it might.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from assayer import ledger, scars as scars_mod
from assayer.cli import BUDGET, NO_AGENT, OK, USAGE, main
from assayer.extract import agent as agent_mod

STORY = """\
In late 2025 the issuer suspended redemptions without notice, and the token
traded well below its stated backing. The proxy behind it could be replaced
with no waiting period, the reserve report named no independent attestor, and
the issuing entity was registered in Jersey.
"""

DECOMPOSING_AGENT = '''\
import json, sys
prompt = sys.stdin.read()
assert "SIGNAL NAMES" in prompt, "the vocabulary must be handed over"
assert "upgrade_no_timelock" in prompt
answer = {"what": "Redemptions suspended; collateral partly illiquid",
          "signals": ["upgrade_no_timelock", "self_attested_reserves",
                      "offshore_issuer", "issuer_lied_to_regulator"]}
print(json.dumps({"result": json.dumps(answer), "total_cost_usd": 0.004}))
'''


@pytest.fixture
def decomposer(tmp_path: Path):
    script = tmp_path / "decomposer.py"
    script.write_text(DECOMPOSING_AGENT, encoding="utf-8")
    return script


def with_agent(paths, script: Path) -> None:
    text = paths.config.read_text(encoding="utf-8")
    paths.config.write_text(
        text.replace('command = "no-such-agent-cli"',
                     f"command = {json.dumps(sys.executable)}")
        .replace('args = ["-p"]', f"args = [{json.dumps(str(script))}]"),
        encoding="utf-8")


def test_the_vocabulary_is_one_list(paths):
    """Signals produced, signals documented and signals validated are one set."""
    assert len(set(scars_mod.SIGNAL_NAMES)) == len(scars_mod.SIGNAL_NAMES)
    assert set(scars_mod.SIGNAL_MEANING) == set(scars_mod.SIGNAL_NAMES)
    assert scars_mod.unknown_signals(["offshore_issuer", "nonsense"]) == ["nonsense"]

    # Every name the snapshot readers can emit is in the list.
    from assayer.model import FILLED, NOT_FOUND, Slot

    emitted = set(scars_mod.signals_of(
        [Slot(slot="custodian", state=NOT_FOUND),
         Slot(slot="insolvency", state=NOT_FOUND),
         Slot(slot="redemption", state=NOT_FOUND),
         Slot(slot="instrument_type", state=FILLED, value="debt_security, note"),
         Slot(slot="issuer", state=FILLED, value="Assay Ltd, Jersey")],
        [Slot(slot="upgradeable", state=FILLED, value="proxy", material=True),
         Slot(slot="freeze", state=FILLED, value="blacklist", material=True),
         Slot(slot="burn_authority", state=FILLED, value="burn", material=True),
         Slot(slot="pause", state=FILLED, value="pause", material=True),
         Slot(slot="mint_authority", state=FILLED, value="mint", material=True),
         Slot(slot="attestation_freshness", state=FILLED, value="report")],
        {"implementation": "0x3", "upgradeable": {"timelock_delay_s": 0},
         "freeze": {"owner": "0x2"}, "controllers": {"0x2": {"kind": "eoa"}},
         "reserves": {"attestation": {"overdue": True},
                      "supply_vs_reserves": {"delta_sign": 1}}}))
    assert emitted <= set(scars_mod.SIGNAL_NAMES), \
        emitted - set(scars_mod.SIGNAL_NAMES)
    assert "upgrade_no_timelock" in emitted
    assert "single_key_control" in emitted


def test_manual_entry_rejects_invented_signals(paths, capsys):
    home = str(paths.root)
    assert main([
        "--home", home, "scar", "new",
        "--what", "the issuer suspended redemption",
        "--signals", "upgrade_no_timelock,issuer_was_unkind",
    ]) == OK
    printed = capsys.readouterr().out
    assert "issuer_was_unkind" in printed
    assert "no snapshot produces them" in printed

    stored = scars_mod.load(paths.scars)
    assert len(stored) == 1
    assert stored[0].signals == ["upgrade_no_timelock"]

    # An entry made only of invented signals is refused outright.
    assert main(["--home", home, "scar", "new", "--what", "something",
                 "--signals", "made_up_one,made_up_two"]) == USAGE
    assert len(scars_mod.load(paths.scars)) == 1


def test_signals_subcommand_lists_the_vocabulary(paths, capsys):
    assert main(["--home", str(paths.root), "scar", "signals"]) == OK
    printed = capsys.readouterr().out
    for name in scars_mod.SIGNAL_NAMES:
        assert name in printed


def test_decomposition_filters_what_the_model_proposes(paths, config, decomposer):
    cfg = dict(config)
    cfg["agent"] = dict(config["agent"], command=sys.executable,
                        args=[str(decomposer)])
    what, kept, discarded, cost = agent_mod.decompose_signals(cfg, STORY)
    assert kept == ["upgrade_no_timelock", "self_attested_reserves",
                    "offshore_issuer"]
    assert discarded == ["issuer_lied_to_regulator"]
    assert what.startswith("Redemptions suspended")
    assert cost == pytest.approx(0.004)


def test_scar_new_from_text_end_to_end(paths, config, decomposer, capsys,
                                      tmp_path):
    with_agent(paths, decomposer)
    story_file = tmp_path / "story.txt"
    story_file.write_text(STORY, encoding="utf-8")

    home = str(paths.root)
    assert main(["--home", home, "scar", "new", "--from-text", str(story_file),
                 "--date", "2025-11-03",
                 "--source", "https://example.org/case"]) == OK
    printed = capsys.readouterr().out
    assert "decomposed the account" in printed
    assert "issuer_lied_to_regulator" in printed

    stored = scars_mod.load(paths.scars)
    assert len(stored) == 1
    assert stored[0].id == "001"
    assert stored[0].date == "2025-11-03"
    assert stored[0].signals == ["offshore_issuer", "self_attested_reserves",
                                 "upgrade_no_timelock"]
    assert stored[0].source == "https://example.org/case"
    # The one paid call is on the ledger like any other.
    assert ledger.state(paths).spent_today == pytest.approx(0.004)
    assert '"scar_decompose"' in paths.log.read_text(encoding="utf-8")


def test_from_text_without_an_agent_says_so(paths, capsys):
    assert main(["--home", str(paths.root), "scar", "new",
                 "--from-text", "some account of a failure"]) == NO_AGENT
    assert "listed by hand" in capsys.readouterr().out
    assert scars_mod.load(paths.scars) == []


def test_from_text_respects_the_budget(paths, config, decomposer, capsys):
    with_agent(paths, decomposer)
    ledger.record(paths, 2.00, asset="x", kind="extraction")
    assert main(["--home", str(paths.root), "scar", "new",
                 "--from-text", "some account of a failure"]) == BUDGET
    assert "daily_limit_reached" in capsys.readouterr().out
    assert scars_mod.load(paths.scars) == []


def test_matching_needs_two_shared_signals(paths):
    scars_mod.append(paths.scars, scars_mod.Scar(
        id="001", date="2025-01-01", what="one shared signal",
        signals=["offshore_issuer", "no_custodian_named"]))
    scars_mod.append(paths.scars, scars_mod.Scar(
        id="002", date="2025-02-01", what="two shared signals",
        signals=["offshore_issuer", "upgrade_no_timelock"]))
    loaded = scars_mod.load(paths.scars)

    signals = ["offshore_issuer", "upgrade_no_timelock", "issuer_can_mint"]
    matched = scars_mod.match(signals, loaded)
    assert [m["ids"] for m in matched] == [["002"]]
    assert matched[0]["shared_signals"] == ["offshore_issuer", "upgrade_no_timelock"]

    # Identical signal sets group into one line rather than repeating.
    scars_mod.append(paths.scars, scars_mod.Scar(
        id="003", date="2025-03-01", what="the same pair",
        signals=["upgrade_no_timelock", "offshore_issuer"]))
    matched = scars_mod.match(signals, scars_mod.load(paths.scars))
    assert [m["ids"] for m in matched] == [["002", "003"]]
    assert matched[0]["count"] == 2


def test_ids_increment_and_survive_concurrent_writers(paths):
    import threading

    for _ in range(3):
        scars_mod.append(paths.scars, scars_mod.Scar(
            id=scars_mod.next_id(paths.scars), date="2025-01-01", what="x",
            signals=["offshore_issuer"]))
    assert [s.id for s in scars_mod.load(paths.scars)] == ["001", "002", "003"]

    def writer(index: int) -> None:
        scars_mod.append(paths.scars, scars_mod.Scar(
            id=f"9{index:02d}", date="2025-01-01", what="parallel",
            signals=["offshore_issuer"]))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(scars_mod.load(paths.scars)) == 15
