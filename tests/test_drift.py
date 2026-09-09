"""Drift quality: what counts as a change, and what is merely a clock ticking.

A monitor that reports something every run teaches its reader to ignore it. Two
values move on their own — the supply and the age of a reserve report — so their
digits are folded out of both the identity hash and the diff, while a change in
the *shape* of the sentence still reports.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assayer import diff as diff_mod
from assayer import scars as scars_mod
from assayer.model import FILLED
from assayer.verify import gate as gate_mod
from conftest import CUSTODY_URL, PROSPECTUS_URL, take_snapshot, write_asset
from fixtures import pages, pdfs

T1 = datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc)
CUSTODY_BODY = pages.CLEAN_HTML.encode("utf-8")


def reserve_asset(paths, **reserves):
    declared = {"claim_doc": "custody", "claimed_units": 1_200_000,
                "unit": "share", "period_days": 30, "attested_at": "2026-01-31"}
    declared.update(reserves)
    return write_asset(
        paths,
        [("prospectus", PROSPECTUS_URL, "2026-01"), ("custody", CUSTODY_URL, None)],
        reserves=declared)


def routes():
    return {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus()),
            CUSTODY_URL: ("text/html", CUSTODY_BODY)}


def test_a_reserve_report_getting_older_is_not_drift(paths, config, rpc):
    asset = reserve_asset(paths)
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    later = take_snapshot(paths, config, asset, rpc, routes(),
                          now=T1 + timedelta(days=5))

    assert first.snapshot_hash == later.snapshot_hash, \
        "five quiet days must not look like a change"
    changes = diff_mod.diff_snapshots(first.snapshot, later.snapshot)
    subjects = {c["subject"] for c in changes}
    assert "attestation_freshness" not in subjects
    assert changes == [], changes

    # The age itself is still on the sheet; it is simply not reported as drift.
    slot = next(s for s in later.snapshot["slots"]["onchain"]
                if s["slot"] == "attestation_freshness")
    assert slot["state"] == FILLED
    assert "9 days old" in slot["value"]


def test_the_report_falling_overdue_is_drift(paths, config, rpc):
    asset = reserve_asset(paths)
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    late = take_snapshot(paths, config, asset, rpc, routes(),
                         now=T1 + timedelta(days=40))

    assert first.snapshot_hash != late.snapshot_hash
    changes = diff_mod.diff_snapshots(first.snapshot, late.snapshot)
    assert any(c["subject"] == "attestation_freshness" for c in changes), changes
    slot = next(s for s in late.snapshot["slots"]["onchain"]
                if s["slot"] == "attestation_freshness")
    assert "overdue" in slot["value"]
    assert slot["material"] is True

    result = gate_mod.build_dossier(paths, config, asset, late.snapshot)
    assert "attestation_overdue" in result.dossier.gate["signals"]


def test_supply_moving_within_the_claim_is_not_an_identity_change(paths, config,
                                                                rpc):
    asset = reserve_asset(paths)
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    rpc.set_uint(asset.address, "totalSupply()", 1_100_000 * 10 ** 18)
    second = take_snapshot(paths, config, asset, rpc, routes(),
                           now=T1 + timedelta(hours=1))
    assert first.snapshot_hash == second.snapshot_hash
    subjects = {c["subject"] for c in
                diff_mod.diff_snapshots(first.snapshot, second.snapshot)}
    assert subjects == {"totalSupply"}, subjects


def test_supply_overtaking_the_claim_changes_the_identity(paths, config, rpc):
    asset = reserve_asset(paths)
    first = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    rpc.set_uint(asset.address, "totalSupply()", 1_400_000 * 10 ** 18)
    second = take_snapshot(paths, config, asset, rpc, routes(),
                           now=T1 + timedelta(hours=1))
    assert first.snapshot_hash != second.snapshot_hash
    slot = next(s for s in second.snapshot["slots"]["onchain"]
                if s["slot"] == "supply_vs_reserves")
    assert "more tokens than declared collateral" in slot["value"]

    result = gate_mod.build_dossier(paths, config, asset, second.snapshot)
    signals = result.dossier.gate["signals"]
    assert "tokens_exceed_declared_collateral" in signals


def test_a_declared_attestor_removes_the_self_attested_signal(paths, config, rpc):
    plain = reserve_asset(paths)
    snap = take_snapshot(paths, config, plain, rpc, routes(), now=T1)
    result = gate_mod.build_dossier(paths, config, plain, snap.snapshot)
    assert "self_attested_reserves" in result.dossier.gate["signals"]

    named = reserve_asset(paths, attestor="Independent Attestor LLP")
    snap2 = take_snapshot(paths, config, named, rpc, routes(),
                          now=T1 + timedelta(hours=1))
    result2 = gate_mod.build_dossier(paths, config, named, snap2.snapshot)
    assert "self_attested_reserves" not in result2.dossier.gate["signals"]


def test_an_unconfirmed_attestation_date_is_not_checked(paths, config, rpc):
    """The asset file says 31 January; the document does not. That is admitted."""
    asset = reserve_asset(paths, attested_at="2026-03-15")
    snap = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    slot = next(s for s in snap.snapshot["slots"]["onchain"]
                if s["slot"] == "attestation_freshness")
    assert slot["state"] == "not_checked"
    assert slot["reason"] == "attestation_date_not_found_in_document"


def test_scars_match_by_signal_intersection(paths, config, rpc):
    asset = reserve_asset(paths)
    snap = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    scars_mod.append(paths.scars, scars_mod.Scar(
        id="001", date="2025-01-01", what="a case about a single key",
        signals=["single_key_control", "issuer_can_freeze_address",
                 "self_attested_reserves"]))
    scars_mod.append(paths.scars, scars_mod.Scar(
        id="002", date="2025-02-01", what="a case with no overlap",
        signals=["oracle_stale", "no_custodian_named"]))

    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot)
    matched = {tuple(m["shared_signals"]): m for m in result.dossier.scars}
    assert any("issuer_can_freeze_address" in key for key in matched), matched
    assert all(len(key) >= 2 for key in matched), "one shared signal is not a match"
    ids = {i for m in result.dossier.scars for i in m["ids"]}
    assert ids == {"001"}


def test_instrument_category_survives_wording(paths):
    from assayer.extract.schema import parse
    from assayer.model import instrument_category

    assert instrument_category("долговая нота эмитента") == "debt_security"
    assert instrument_category("equity in the operating company") == "equity"
    assert instrument_category("depositary receipt over shares") == \
        "depositary_receipt"
    assert instrument_category("something unclassifiable") is None

    parsed = parse('{"slots": [{"slot": "instrument_type", "state": "filled",'
                   ' "value": "долговая нота эмитента",'
                   ' "evidence": {"kind": "document", "doc_id": "p",'
                   ' "sha256": "ab", "quote": "debt securities"}}]}')
    assert parsed.ok
    assert parsed.slots[0]["value"].startswith("debt_security, ")


def test_a_declared_date_may_be_written_the_way_the_report_prints_it(paths, config,
                                                                    rpc):
    """Reading a date and finding it again must accept the same shapes.

    A holder copies `attested_at` out of the custody statement, where it reads
    "31 January 2026". If only the ISO form parses, the slot goes to not_checked
    over the tool's own narrowness, while that very string is one of the forms it
    renders when it goes looking for the date in the document.
    """
    from assayer import util
    from assayer.snapshot import reserves as reserves_mod

    assert util.DATE_FORMS is reserves_mod._DATE_FORMS
    parsed = {util.parse_date(text) for text in
              ("2026-01-31", "31 January 2026", "January 31, 2026",
               "31 Jan 2026", "31.01.2026", "31/01/2026", "  2026-01-31 ")}
    assert len(parsed) == 1 and None not in parsed
    assert util.parse_date("not a date") is None
    assert util.parse_date("2026-13-45") is None

    # End to end: the slot fills from a date written the long way.
    asset = reserve_asset(paths, attested_at="31 January 2026")
    result = take_snapshot(paths, config, asset, rpc, routes(), now=T1)
    slot = next(s for s in result.snapshot["slots"]["onchain"]
                if s["slot"] == "attestation_freshness")
    assert slot["state"] == FILLED, slot
    assert "2026-01-31" in slot["value"]
