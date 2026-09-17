"""The coin profile: an asset with no documents, read by scripts alone.

Nothing here is numbered against the build plan. The plan describes an asset
that arrives with a prospectus, and these are the properties that only exist
once that assumption is dropped: that a coin costs nothing and asks nothing of
a model, that a pool nobody declared is never guessed at, and that the two
kinds of movement a coin shows — a balance drifting and a fee being changed —
are told apart rather than both reported as "something changed".
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from assayer import model, util
from assayer.cli import NOTHING_TO_DO, main
from assayer.model import FILLED, NOT_CHECKED, NOT_FOUND, Asset
from assayer.snapshot import build as build_mod
from assayer.snapshot import chain as chain_mod
from assayer.verify import gate as gate_mod
from conftest import FakeRpc, code_with

T1 = datetime(2026, 2, 4, 9, 0, tzinfo=timezone.utc)

TOKEN = "0x1111111111111111111111111111111111111111"
OWNER = "0x2222222222222222222222222222222222222222"
POOL = "0x4444444444444444444444444444444444444444"
OTHER = "0x9999999999999999999999999999999999999999"
DEAD = "0x000000000000000000000000000000000000dead"
SUPPLY = 1_000_000_000 * 10 ** 18


def coin_rpc(*, owner: str = OWNER, fee: int = 5) -> FakeRpc:
    """A coin with a live owner, a fee, a cap and a mostly parked pool."""
    rpc = FakeRpc()
    rpc.code[TOKEN] = code_with(
        "name()", "symbol()", "decimals()", "totalSupply()", "owner()",
        "balanceOf(address)", "transferOwnership(address)",
        "enableTrading()", "tradingOpen()",
        "totalFees()", "setFees(uint256,uint256)",
        "maxWallet()", "setMaxWallet(uint256)")
    rpc.code[OWNER] = b""
    rpc.code[POOL] = code_with("token0()", "token1()", "totalSupply()",
                               "balanceOf(address)")
    rpc.set_call(TOKEN, "name()", "0x" + b"Pons".hex().ljust(64, "0"))
    rpc.set_call(TOKEN, "symbol()", "0x" + b"PONS".hex().ljust(64, "0"))
    rpc.set_uint(TOKEN, "decimals()", 18)
    rpc.set_uint(TOKEN, "totalSupply()", SUPPLY)
    rpc.set_address(TOKEN, "owner()", owner)
    rpc.set_uint(TOKEN, "totalFees()", fee)
    rpc.set_uint(TOKEN, "maxWallet()", SUPPLY // 50)
    rpc.set_call(TOKEN, "tradingOpen()", "0x" + "0" * 63 + "1")
    rpc.set_balance(TOKEN, OWNER, SUPPLY // 20)
    rpc.set_balance(TOKEN, DEAD, SUPPLY // 10)
    rpc.set_balance(TOKEN, POOL, SUPPLY // 4)
    rpc.set_address(POOL, "token0()", TOKEN)
    rpc.set_address(POOL, "token1()", OTHER)
    rpc.set_uint(POOL, "totalSupply()", 1000)
    rpc.set_balance(POOL, DEAD, 960)
    return rpc


def coin_asset(**kwargs) -> Asset:
    return Asset(chain="ethereum", address=TOKEN, label="$PONS", chain_id=1,
                 profile=model.PROFILE_COIN, **kwargs)


def sheet_of(paths, config, asset, rpc, *, now=T1):
    result = build_mod.build(paths, config, asset, rpc=rpc, now=now)
    gate = gate_mod.build_dossier(paths, config, asset, result.snapshot)
    assert not gate.violations, [str(v) for v in gate.violations]
    return result, gate


def slot(gate, name):
    return next(s for s in gate.dossier.onchain if s.slot == name)


# ---------------------------------------------------------------------------

def test_a_coin_is_read_with_no_documents_no_model_and_no_spend(paths, config):
    asset = coin_asset(pool=POOL)
    result, gate = sheet_of(paths, config, asset, coin_rpc())

    assert result.snapshot["documents"] == []
    assert result.snapshot["injections"] == []
    # The legal questionnaire is not asked, so it is not printed as unanswered:
    # sixteen not_checked rows would read as a failure to look.
    assert gate.dossier.legal == []
    assert gate.dossier.counters["spent_usd"] == 0
    assert {s.slot for s in gate.dossier.onchain} == set(model.COIN_SLOTS)

    text = gate.text
    assert "WHAT THE CONTRACT STILL ALLOWS" in text
    assert "no extraction: this profile calls no model" in text
    assert "NOT FOUND IN THE DOCUMENTS" not in text
    assert "REJECTED BY THE GATE" not in text


def test_the_paid_phase_refuses_a_coin_instead_of_asking_a_model_for_nothing(
        paths, config, home):
    asset = coin_asset(pool=POOL)
    util.atomic_write_text(paths.assets / f"{asset.name}.toml",
                           model.asset_to_toml(asset))
    build_mod.build(paths, config, asset, rpc=coin_rpc(), now=T1)

    assert main(["--home", str(home), "extract", asset.name]) == NOTHING_TO_DO


def test_a_given_up_owner_and_a_live_owner_do_not_read_the_same(paths, config):
    _live_result, live = sheet_of(paths, config, coin_asset(), coin_rpc())
    live_slot = slot(live, "ownership")
    assert live_slot.state == FILLED and live_slot.material
    assert "still answers to an owner" in live_slot.value

    _gone_result, gone = sheet_of(paths, config, coin_asset(),
                                  coin_rpc(owner=DEAD))
    gone_slot = slot(gone, "ownership")
    assert gone_slot.state == FILLED and not gone_slot.material
    assert "given up" in gone_slot.value

    # A contract that never had an owner is a third reading again, and it is an
    # absence of a power rather than a power that was handed away.
    bare = FakeRpc()
    bare.code[TOKEN] = code_with("name()", "totalSupply()", "balanceOf(address)")
    bare.set_uint(TOKEN, "totalSupply()", SUPPLY)
    _bare_result, never = sheet_of(paths, config, coin_asset(), bare)
    assert slot(never, "ownership").state == NOT_FOUND

    assert "owner_not_given_up" in live.dossier.gate["signals"]
    assert "ownership_given_up" in gone.dossier.gate["signals"]


def test_a_pool_that_does_not_trade_this_token_is_refused_not_believed(
        paths, config):
    rpc = coin_rpc()
    # The declared pair is a real pool, just not this token's. Its numbers would
    # look perfectly plausible on the sheet, which is exactly the problem.
    rpc.set_address(POOL, "token0()", OTHER)
    rpc.set_address(POOL, "token1()", "0x8888888888888888888888888888888888888888")
    _result, gate = sheet_of(paths, config, coin_asset(pool=POOL), rpc)

    liquidity = slot(gate, "liquidity")
    assert liquidity.state == NOT_CHECKED
    assert liquidity.reason == "declared_pool_is_not_a_pair_for_this_token"
    assert "25.0%" not in gate.text


def test_an_undeclared_pool_is_not_searched_for(paths, config):
    _result, gate = sheet_of(paths, config, coin_asset(), coin_rpc())
    liquidity = slot(gate, "liquidity")
    assert liquidity.state == NOT_CHECKED
    assert liquidity.reason == "no_pool_declared"
    # NOT CHECKED is a fact about this tool, never about the coin.
    assert "no_pool_declared" in gate.text.split("NOT CHECKED")[1]


def test_a_coin_threshold_prints_the_reading_beside_the_number(paths, config):
    asset = coin_asset(pool=POOL, alerts={
        "max_owner_share_pct": 1.0,
        "min_burnt_lp_pct": 95.0,
        "require_renounced_ownership": True,
    })
    _result, gate = sheet_of(paths, config, asset, coin_rpc())
    by_key = {a["key"]: a for a in gate.dossier.alerts}

    assert by_key["max_owner_share_pct"]["crossed"] is True
    assert by_key["max_owner_share_pct"]["value"] == 5.0
    assert "5.0%" in by_key["max_owner_share_pct"]["text"]
    # 96% parked against a 95% minimum: read, and not crossed.
    assert by_key["min_burnt_lp_pct"]["crossed"] is False
    assert by_key["require_renounced_ownership"]["crossed"] is True


def test_a_threshold_whose_reading_is_missing_is_not_reported_as_satisfied(
        paths, config):
    asset = coin_asset(alerts={"min_burnt_lp_pct": 95.0})  # no pool declared
    _result, gate = sheet_of(paths, config, asset, coin_rpc())
    alert = next(a for a in gate.dossier.alerts if a["key"] == "min_burnt_lp_pct")

    assert alert["value"] is None
    assert alert["crossed"] is False
    assert "not applied" in alert["text"]


def test_a_balance_that_drifted_is_not_a_change_but_a_fee_that_moved_is(
        paths, config):
    """The two kinds of movement a coin shows, told apart.

    A balance moves because somebody traded. A fee moves because whoever holds
    the owner key decided it should. Only the second belongs in the snapshot
    hash: if a drifting balance flipped it, ``watch`` would cry drift on every
    block and the holder would learn to ignore it. The size of a balance is
    watched by the thresholds in the asset file instead.
    """
    asset = coin_asset(pool=POOL)
    first = build_mod.build(paths, config, asset, rpc=coin_rpc(), now=T1)

    traded = coin_rpc()
    traded.set_balance(TOKEN, POOL, SUPPLY // 5)
    traded.set_balance(TOKEN, OWNER, SUPPLY // 25)
    second = build_mod.build(paths, config, asset, rpc=traded, now=T1)
    assert second.snapshot_hash == first.snapshot_hash
    assert second.changed is False

    third = build_mod.build(paths, config, asset, rpc=coin_rpc(fee=25), now=T1)
    assert third.snapshot_hash != first.snapshot_hash
    assert third.changed is True


def test_a_coin_file_that_also_lists_documents_is_refused(tmp_path):
    raw = {
        "chain": "ethereum", "address": TOKEN, "label": "$PONS",
        "profile": "coin",
        "documents": [{"doc_id": "prospectus", "url": "https://e.example/p.pdf"}],
    }
    with pytest.raises(ValueError, match="carries no documents"):
        model.asset_from_toml(raw)


# ---------------------------------------------------------------------------
# The mirror in assayer/coin/surface.py has to stay a mirror. It exists so that
# anything reading the same bytecode looks for the same functions this reader
# does, and the way it rots is silently: a signature added to chain.py, not
# copied across, and two sheets that quietly disagree. Each name below is turned
# into bytecode on its own and the slot builder is asked whether it sees it.

SIG_OF = {key: sig for sig, key in chain_mod.SIGNATURES.items()}

BASICS = ("name()", "symbol()", "decimals()", "totalSupply()",
          "balanceOf(address)")


def one_function_coin(key: str) -> FakeRpc:
    rpc = FakeRpc()
    rpc.code[TOKEN] = code_with(*BASICS, SIG_OF[key].split("#")[0])
    rpc.set_uint(TOKEN, "totalSupply()", SUPPLY)
    return rpc


@pytest.mark.parametrize("slot_name,group,expected_material", [
    ("mint_authority", "write", True),
    ("burn_authority", "foreign", True),
    ("burn_authority", "own", False),
    ("pause", "write", True),
    ("freeze", "write", True),
])
def test_every_mirrored_signature_is_seen_by_the_slot_it_is_listed_under(
        paths, config, slot_name, group, expected_material):
    from assayer.coin import surface

    for key in surface.REUSED[slot_name][group]:
        _result, gate = sheet_of(paths, config, coin_asset(),
                                 one_function_coin(key))
        found = slot(gate, slot_name)
        assert found.state == FILLED, f"{slot_name} missed {SIG_OF[key]}"
        assert found.material is expected_material, SIG_OF[key]
