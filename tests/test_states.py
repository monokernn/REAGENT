"""Tests 17-18: a failure of the tool never renders as a fact about the asset.

``not_found`` means the tool looked and the thing is not there. ``not_checked``
means the tool did not manage to look. Collapsing the two would turn an outage at
an RPC provider into a statement that the issuer holds no such power.
"""

from __future__ import annotations

from assayer.model import FILLED, NOT_CHECKED, NOT_FOUND
from assayer.render import H_NOT_CHECKED, H_POWERS
from assayer.snapshot import chain as chain_mod
from assayer.verify import gate as gate_mod
from conftest import OWNER_EOA, TOKEN, FakeRpc, code_with, take_snapshot
from fixtures import pdfs
from conftest import PROSPECTUS_URL

ROUTES = {PROSPECTUS_URL: ("application/pdf", pdfs.clean_prospectus())}


def test_17_a_failed_rpc_gives_not_checked_with_a_reason(paths, config, asset):
    rpc = FakeRpc(fail_on={"eth_blockNumber"})
    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    slots = snap.snapshot["slots"]["onchain"]
    states = {s["slot"]: s for s in slots}

    for name in ("mint_authority", "burn_authority", "pause", "upgradeable",
                 "freeze", "oracle"):
        assert states[name]["state"] == NOT_CHECKED, name
        assert states[name]["reason"] == "rpc_error", name
    assert not any(s["state"] == NOT_FOUND for s in slots), \
        "an outage must not be printed as an absence"

    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot)
    assert result.ok
    text = result.text or ""
    assert H_NOT_CHECKED in text
    section = text.split(H_NOT_CHECKED, 1)[1]
    assert "rpc_error" in section
    assert "freeze" in section


def test_17b_a_missing_endpoint_is_also_not_checked(paths, config, asset):
    config = dict(config)
    config["rpc"] = {}
    snap = take_snapshot(paths, config, asset, None, ROUTES)
    states = {s["slot"]: s for s in snap.snapshot["slots"]["onchain"]}
    assert states["freeze"]["state"] == NOT_CHECKED
    assert states["freeze"]["reason"] == "missing_rpc_endpoint"


def test_17c_a_readable_chain_gives_absence_evidence_not_silence(paths, config,
                                                               asset, rpc):
    """"No pause function in this bytecode" is a finding, and it is evidenced."""
    rpc.code[TOKEN] = code_with("name()", "symbol()", "totalSupply()", "owner()")
    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    states = {s["slot"]: s for s in snap.snapshot["slots"]["onchain"]}
    assert states["pause"]["state"] == NOT_FOUND
    assert states["pause"]["evidence"]["kind"] == "absence"
    assert states["pause"]["evidence"]["checked_selectors"]
    assert states["freeze"]["state"] == NOT_FOUND

    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot)
    section = (result.text or "").split(H_POWERS, 1)[1]
    assert "pause" in section


def test_18_an_unknown_proxy_pattern_is_not_checked(paths, config, asset):
    """Unrecognised delegation is admitted, not guessed at."""
    rpc = FakeRpc()
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()", delegatecall=True,
                                padding=400)
    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    slot = next(s for s in snap.snapshot["slots"]["onchain"]
                if s["slot"] == "upgradeable")
    assert slot["state"] == NOT_CHECKED
    assert slot["reason"] == "unknown_proxy_pattern"


def test_18b_eip1967_is_read_with_its_admin_and_timelock(paths, config, asset):
    rpc = FakeRpc()
    timelock = "0x5555555555555555555555555555555555555555"
    implementation = "0x3333333333333333333333333333333333333333"
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()", delegatecall=True,
                                padding=200)
    rpc.code[implementation] = code_with("upgradeTo(address)", "mint(address,uint256)",
                                        "blacklist(address)")
    rpc.code[timelock] = code_with("getMinDelay()")
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_IMPL, implementation)
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_ADMIN, timelock)
    rpc.set_uint(timelock, "getMinDelay()", 172800)

    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    states = {s["slot"]: s for s in snap.snapshot["slots"]["onchain"]}
    upgrade = states["upgradeable"]
    assert upgrade["state"] == FILLED
    assert implementation in upgrade["value"]
    assert "timelock" in upgrade["value"]
    assert "172800" in upgrade["value"]
    assert upgrade["material"] is True
    facts = snap.snapshot["facts"]["upgradeable"]
    assert facts["timelock_delay_s"] == 172800
    assert facts["admin"] == timelock
    assert snap.snapshot["facts"]["controllers"][timelock]["kind"] == "timelock"

    # Selectors from the implementation count as powers of this token.
    assert states["mint_authority"]["state"] == FILLED
    assert states["freeze"]["state"] == FILLED
    assert snap.snapshot["facts"]["implementation"] == implementation
    assert snap.snapshot["facts"]["implementation_code_hash"]


def test_18c_an_upgrade_without_a_timelock_says_so(paths, config, asset):
    rpc = FakeRpc()
    implementation = "0x3333333333333333333333333333333333333333"
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()", delegatecall=True,
                                padding=200)
    rpc.code[implementation] = code_with("upgradeTo(address)", "proxiableUUID()")
    rpc.code[OWNER_EOA] = b""
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_IMPL, implementation)
    rpc.set_address(TOKEN, "owner()", OWNER_EOA)

    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    upgrade = next(s for s in snap.snapshot["slots"]["onchain"]
                   if s["slot"] == "upgradeable")
    assert upgrade["state"] == FILLED
    assert "UUPS" in upgrade["value"]
    assert "no timelock delay found on the upgrade path" in upgrade["value"]
    assert "EOA" in upgrade["value"]
    facts = snap.snapshot["facts"]
    assert facts["upgradeable"]["timelock_delay_s"] is None
    assert facts["upgradeable"]["controller"] == OWNER_EOA
    assert facts["controllers"][OWNER_EOA]["kind"] == "eoa"

    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot)
    # Signals come from the structured readings, not from the sentence above.
    assert "upgrade_no_timelock" in result.dossier.gate["signals"]
    assert "single_key_control" in result.dossier.gate["signals"]


def test_18d_proxy_storage_that_cannot_be_read_is_not_checked(paths, config, asset):
    rpc = FakeRpc(fail_on={"eth_getStorageAt"})
    rpc.code[TOKEN] = code_with("totalSupply()", "owner()")
    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    upgrade = next(s for s in snap.snapshot["slots"]["onchain"]
                   if s["slot"] == "upgradeable")
    assert upgrade["state"] == NOT_CHECKED
    assert upgrade["reason"] == "rpc_error"


def test_18e_reserve_slots_state_why_they_were_not_checked(paths, config, asset,
                                                          rpc):
    snap = take_snapshot(paths, config, asset, rpc, ROUTES)
    states = {s["slot"]: s for s in snap.snapshot["slots"]["onchain"]}
    assert states["supply_vs_reserves"]["state"] == NOT_CHECKED
    assert states["supply_vs_reserves"]["reason"] == "no_reserve_claim_declared"
    assert states["attestation_freshness"]["state"] == NOT_CHECKED
    assert states["attestation_freshness"]["reason"] == "no_attestation_date_declared"
