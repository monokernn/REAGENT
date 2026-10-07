"""The coin profile on Solana: a mint read from its accounts, never from a page.

No network here either. The RPC is a dictionary of accounts, shaped the way a
node answers ``getAccountInfo`` with ``jsonParsed``, so every slot below is
pinned to the field it was read from.
"""

from __future__ import annotations

import base64
import struct

import pytest

from assayer import model
from assayer.coin import solana as sol
from assayer.snapshot.build import compute_alerts

FEE_MINT = "DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP"
PLAIN_MINT = "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk"
KEY_A = "5KXDF6QnqhBj72hDtJNkkpFaQVUfbFXNybMsp3DiK6tD"
KEY_B = "WLHv2UAZm6z4KyaaELi5pjdbJh6RESMva1Rnn8pJVVh"


def mint_account(program: str, *, mint_authority=None, freeze_authority=None,
                 supply=1_000_000_000_000_000, extensions=()) -> dict:
    info = {"decimals": 6, "isInitialized": True, "supply": str(supply),
            "mintAuthority": mint_authority, "freezeAuthority": freeze_authority}
    if extensions:
        info["extensions"] = list(extensions)
    return {"owner": program, "lamports": 1, "executable": False,
            "data": {"program": "spl-token", "parsed": {"type": "mint", "info": info}}}


def metaplex_bytes(update_authority: str, mint: str, name: str, symbol: str,
                   mutable: bool) -> bytes:
    def string(text: str, width: int) -> bytes:
        raw = text.encode().ljust(width, b"\0")
        return struct.pack("<I", len(raw)) + raw
    return (b"\x04" + sol.b58decode(update_authority) + sol.b58decode(mint)
            + string(name, 32) + string(symbol, 10) + string("https://x", 200)
            + struct.pack("<H", 0) + b"\x00" + b"\x01" + bytes([1 if mutable else 0]))


class FakeSolana:
    def __init__(self, accounts: dict, balances: dict | None = None, slot: int = 1234):
        self.accounts = accounts
        self.balances = balances or {}
        self.slot = slot
        self.calls: list[tuple[str, list]] = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "getAccountInfo":
            return {"context": {"slot": self.slot}, "value": self.accounts.get(params[0])}
        if method == "getTokenAccountsByOwner":
            amount = self.balances.get(params[0], 0)
            value = [] if not amount else [{"account": {"data": {"parsed": {"info": {
                "tokenAmount": {"amount": str(amount)}}}}}}]
            return {"context": {"slot": self.slot}, "value": value}
        raise AssertionError(f"unexpected call {method}")


def slots_of(report) -> dict:
    return {s.slot: s for s in report.slots}


# ---------------------------------------------------------------- primitives

def test_base58_round_trips_and_rejects_what_is_not_an_address():
    assert sol.b58encode(sol.b58decode(FEE_MINT)) == FEE_MINT
    assert sol.is_address(FEE_MINT)
    assert not sol.is_address("0x" + "ab" * 20)
    assert not sol.is_address("not-base58-0OIl")


def test_a_wallet_key_is_on_the_curve_and_a_derived_address_is_not():
    assert sol.on_curve(sol.b58decode("11111111111111111111111111111111"))
    derived, _bump = sol.find_program_address([b"seed"], sol.METADATA_PROGRAM)
    assert not sol.on_curve(sol.b58decode(derived))


def test_metaplex_metadata_is_parsed_down_to_its_mutability():
    raw = metaplex_bytes(KEY_B, PLAIN_MINT, "USELESS COIN", "USELESS", mutable=False)
    meta = sol.parse_metaplex(raw)
    assert meta["update_authority"] == KEY_B
    assert (meta["name"], meta["symbol"], meta["mutable"]) == ("USELESS COIN", "USELESS", False)


def test_a_solana_ref_keeps_its_case():
    chain, address = model.parse_ref(f"solana:{FEE_MINT}")
    assert (chain, address) == ("solana", FEE_MINT)
    with pytest.raises(ValueError):
        model.parse_ref("solana:0xdeadbeef")
    assert model.parse_ref("ethereum:0x" + "AB" * 20)[1] == "0x" + "ab" * 20


# ---------------------------------------------------------------- readings

def test_a_fee_mint_reads_its_fee_its_authorities_and_who_holds_what():
    fee = {"extension": "transferFeeConfig", "state": {
        "transferFeeConfigAuthority": KEY_A, "withdrawWithheldAuthority": KEY_A,
        "newerTransferFee": {"epoch": 1, "maximumFee": 10, "transferFeeBasisPoints": 100}}}
    meta = {"extension": "tokenMetadata", "state": {
        "name": "Super Inu", "symbol": "SI", "updateAuthority": KEY_B}}
    rpc = FakeSolana({FEE_MINT: mint_account(sol.TOKEN_2022_PROGRAM, extensions=[fee, meta])},
                     balances={KEY_A: 76_000_000_000_000})
    report = sol.read_solana_coin({}, FEE_MINT, rpc=rpc)
    by = slots_of(report)

    assert report.block == 1234
    assert report.facts["program"] == "token-2022"
    assert (report.facts["name"], report.facts["symbol"]) == ("Super Inu", "SI")
    assert by["transfer_tax"].state == model.FILLED and by["transfer_tax"].material
    assert "100 basis points" in by["transfer_tax"].value
    assert by["ownership"].material and KEY_A in by["ownership"].value
    assert by["owner_holdings"].value.endswith("holds 7.6% of supply")
    assert by["mint_authority"].state == model.NOT_FOUND
    assert by["freeze"].state == model.NOT_FOUND


def test_a_classic_mint_with_every_authority_dropped_reads_renounced():
    meta_address = sol.metadata_address(PLAIN_MINT)
    rpc = FakeSolana({
        PLAIN_MINT: mint_account(sol.TOKEN_PROGRAM),
        meta_address: {"owner": sol.METADATA_PROGRAM, "data": [base64.b64encode(
            metaplex_bytes(KEY_B, PLAIN_MINT, "USELESS COIN", "USELESS", mutable=False)
        ).decode(), "base64"]},
    })
    report = sol.read_solana_coin({}, PLAIN_MINT, rpc=rpc)
    by = slots_of(report)

    assert report.facts["program"] == "spl-token"
    assert report.facts["symbol"] == "USELESS"
    assert report.facts["ownership"]["renounced"] is True
    assert not any(s.material for s in report.slots)
    assert by["liquidity"].state == model.NOT_CHECKED


def test_freeze_mint_delegate_and_a_frozen_default_are_all_live_powers():
    extensions = [
        {"extension": "permanentDelegate", "state": {"delegate": KEY_B}},
        {"extension": "defaultAccountState", "state": {"accountState": "frozen"}},
        {"extension": "transferHook", "state": {"authority": KEY_A, "programId": None}},
    ]
    rpc = FakeSolana({FEE_MINT: mint_account(
        sol.TOKEN_2022_PROGRAM, mint_authority=KEY_A, freeze_authority=KEY_A,
        extensions=extensions)})
    by = slots_of(sol.read_solana_coin({}, FEE_MINT, rpc=rpc))
    for name in ("mint_authority", "freeze", "burn_authority", "trading_switch", "upgradeable"):
        assert by[name].state == model.FILLED and by[name].material, name
    assert "can set one" in by["upgradeable"].value


def test_an_account_that_is_not_a_mint_is_said_to_be_one_and_nothing_else():
    rpc = FakeSolana({})
    report = sol.read_solana_coin({}, FEE_MINT, rpc=rpc)
    assert all(s.state == model.NOT_CHECKED for s in report.slots)
    assert all(s.reason == sol.R_NOT_A_MINT for s in report.slots)


def test_a_declared_pool_is_weighed_by_what_it_owns():
    pool = KEY_B
    rpc = FakeSolana({FEE_MINT: mint_account(sol.TOKEN_PROGRAM)},
                     balances={pool: 250_000_000_000_000})
    by = slots_of(sol.read_solana_coin({}, FEE_MINT, pool=pool, rpc=rpc))
    assert by["liquidity"].state == model.FILLED
    assert by["liquidity"].value.startswith("25.0% of supply")


def test_the_holders_thresholds_read_solana_slots_the_same_way():
    fee = {"extension": "transferFeeConfig", "state": {
        "transferFeeConfigAuthority": KEY_A,
        "newerTransferFee": {"maximumFee": 1, "transferFeeBasisPoints": 50}}}
    rpc = FakeSolana({FEE_MINT: mint_account(sol.TOKEN_2022_PROGRAM, extensions=[fee])})
    report = sol.read_solana_coin({}, FEE_MINT, rpc=rpc)
    asset = model.Asset(chain="solana", address=FEE_MINT, label="$SI",
                        profile=model.PROFILE_COIN,
                        alerts={"require_no_transfer_tax": True, "require_no_freeze": True,
                                "require_renounced_ownership": True})
    crossed = {a["key"]: a["crossed"] for a in compute_alerts(asset, report.facts, report.slots)}
    assert crossed == {"require_no_transfer_tax": True, "require_no_freeze": False,
                       "require_renounced_ownership": True}


def test_the_reader_never_asks_for_anything_but_reads():
    rpc = sol.SolanaRpc(urls=[])
    with pytest.raises(Exception, match="not permitted"):
        rpc.call("sendTransaction", ["…"])
