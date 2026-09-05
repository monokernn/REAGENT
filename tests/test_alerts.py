"""Thresholds from the asset file: the holder's numbers, not the tool's opinion.

REAGENT does not decide that two days of timelock is enough. It reports that the
number written in ``[alerts]`` was crossed, names the reading and the threshold,
and leaves the conclusion where it belongs. A threshold that cannot be evaluated
says so instead of counting as satisfied.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assayer.cli import DRIFT, OK, main
from assayer.render import H_ALERTS
from assayer.snapshot import chain as chain_mod
from assayer.verify import gate as gate_mod
from conftest import (
    CUSTODY_URL, PROSPECTUS_URL, FakeRpc, TOKEN, code_with, take_snapshot,
    write_asset,
)
from fixtures import pages, pdfs

T1 = datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc)
ROUTES = {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus()),
          CUSTODY_URL: ("text/html", pages.CLEAN_HTML.encode("utf-8"))}


def asset_with(paths, alerts: dict, **reserves):
    declared = {"claim_doc": "custody", "claimed_units": 1_200_000,
                "unit": "share", "period_days": 30, "attested_at": "2026-01-31"}
    declared.update(reserves)
    asset = write_asset(
        paths,
        [("prospectus", PROSPECTUS_URL, "2026-01"), ("custody", CUSTODY_URL, None)],
        reserves=declared)
    # write_asset does not emit [alerts]; append it and reload.
    path = paths.assets / f"{asset.name}.toml"
    lines = ["", "[alerts]"]
    for key, value in alerts.items():
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        lines.append(f"{key} = {rendered}")
    path.write_text(path.read_text(encoding="utf-8") + "\n".join(lines) + "\n",
                    encoding="utf-8")
    from assayer import model

    return model.load_asset(paths, asset.name)


def by_key(snapshot: dict) -> dict[str, dict]:
    return {a["key"]: a for a in snapshot.get("alerts") or []}


def test_no_alerts_declared_means_no_alerts_section(paths, config, asset, rpc):
    snap = take_snapshot(paths, config, asset, rpc, ROUTES, now=T1)
    assert snap.snapshot["alerts"] == []
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot)
    assert H_ALERTS not in (result.text or "")


def test_attestation_age_threshold(paths, config, rpc):
    target = asset_with(paths, {"max_attestation_age_days": 30})
    snap = take_snapshot(paths, config, target, rpc, ROUTES, now=T1)
    alert = by_key(snap.snapshot)["max_attestation_age_days"]
    assert alert["crossed"] is False
    assert alert["value"] == 4

    late = take_snapshot(paths, config, target, rpc, ROUTES,
                         now=T1 + timedelta(days=40))
    alert = by_key(late.snapshot)["max_attestation_age_days"]
    assert alert["crossed"] is True
    assert alert["value"] == 44
    assert "threshold crossed" in alert["text"]
    assert late.crossed_alerts == [alert]

    result = gate_mod.build_dossier(paths, config, target, late.snapshot)
    assert result.ok
    section = (result.text or "").split(H_ALERTS, 1)[1]
    assert "44" in section and "30" in section


def test_timelock_threshold_reads_the_delay(paths, config):
    target = asset_with(paths, {"min_timelock_delay_s": 172800})
    timelock = "0x5555555555555555555555555555555555555555"
    implementation = "0x3333333333333333333333333333333333333333"

    rpc = FakeRpc()
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()", delegatecall=True,
                                padding=200)
    rpc.code[implementation] = code_with("upgradeTo(address)")
    rpc.code[timelock] = code_with("getMinDelay()")
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_IMPL, implementation)
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_ADMIN, timelock)
    rpc.set_uint(timelock, "getMinDelay()", 3600)

    snap = take_snapshot(paths, config, target, rpc, ROUTES, now=T1)
    alert = by_key(snap.snapshot)["min_timelock_delay_s"]
    assert alert["crossed"] is True
    assert alert["value"] == 3600

    rpc.set_uint(timelock, "getMinDelay()", 172800)
    later = take_snapshot(paths, config, target, rpc, ROUTES,
                          now=T1 + timedelta(hours=1))
    assert by_key(later.snapshot)["min_timelock_delay_s"]["crossed"] is False


def test_an_unreadable_threshold_is_not_treated_as_satisfied(paths, config):
    """A failed RPC must not read as "the timelock is fine"."""
    target = asset_with(paths, {"min_timelock_delay_s": 172800})
    rpc = FakeRpc(fail_on={"eth_getStorageAt"})
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()")
    snap = take_snapshot(paths, config, target, rpc, ROUTES, now=T1)
    alert = by_key(snap.snapshot)["min_timelock_delay_s"]
    assert alert["crossed"] is False
    assert alert["value"] is None
    assert "threshold not applied" in alert["text"]


def test_capability_thresholds(paths, config, rpc):
    target = asset_with(paths, {"require_no_freeze": True,
                                "require_no_foreign_burn": True,
                                "require_immutable_logic": True})
    snap = take_snapshot(paths, config, target, rpc, ROUTES, now=T1)
    alerts = by_key(snap.snapshot)
    # The fixture token can freeze and can burn foreign balances, and is not a proxy.
    assert alerts["require_no_freeze"]["crossed"] is True
    assert alerts["require_no_foreign_burn"]["crossed"] is True
    assert alerts["require_immutable_logic"]["crossed"] is False
    assert "no known pattern for replacing the logic" in \
        alerts["require_immutable_logic"]["text"]


def test_supply_over_claim_threshold(paths, config, rpc):
    target = asset_with(paths, {"max_supply_over_claim_pct": 1.0})
    snap = take_snapshot(paths, config, target, rpc, ROUTES, now=T1)
    # 1_000_000 minted against 1_200_000 declared: nothing in excess.
    assert by_key(snap.snapshot)["max_supply_over_claim_pct"]["crossed"] is False

    rpc.set_uint(TOKEN, "totalSupply()", 1_300_000 * 10 ** 18)
    second = take_snapshot(paths, config, target, rpc, ROUTES,
                           now=T1 + timedelta(hours=1))
    alert = by_key(second.snapshot)["max_supply_over_claim_pct"]
    assert alert["crossed"] is True
    assert alert["value"] == 8.33


def test_watch_exits_with_drift_while_a_threshold_is_crossed(paths, config,
                                                           monkeypatch):
    target = asset_with(paths, {"require_no_freeze": True})
    from assayer.snapshot import build as build_mod
    from conftest import FakeFetcher, FakeRpc as Rpc

    rpc = Rpc()
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()", "blacklist(address)",
                                "isBlacklisted(address)")
    rpc.set_uint(TOKEN, "totalSupply()", 1_000_000 * 10 ** 18)
    original = build_mod.build
    allowed = list(config["sources"]["allowed_domains"])

    def fake_build(p, cfg, a, **kwargs):
        kwargs.pop("fetcher", None)
        kwargs.pop("rpc", None)
        return original(p, cfg, a, fetcher=FakeFetcher(allowed, ROUTES), rpc=rpc,
                        **kwargs)

    monkeypatch.setattr(build_mod, "build", fake_build)
    assert main(["--home", str(paths.root), "watch"]) == DRIFT

    # Remove the capability and the same threshold stops firing.
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()")
    assert main(["--home", str(paths.root), "watch"]) == DRIFT  # the change itself
    assert main(["--home", str(paths.root), "watch"]) == OK
    assert target.alerts == {"require_no_freeze": True}
