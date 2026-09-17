"""Twelve readings of a coin's contract, filled by scripts alone.

Six of them are the RWA profile's own power slots, reused verbatim rather than
reimplemented: whether supply can grow, whether a balance somebody else holds
can be reduced, whether transfers can be halted, whether an address can be
blocked, and whether the logic can be replaced are the same questions about the
same bytecode. What a coin adds is the part a prospectus would otherwise carry:
who still owns the contract, whether transfers can be switched off after the
fact, what a transfer pays, what a wallet may hold, where the supply sits, and
how much of it is parked at an address nobody has a key to.

Two rules the whole file obeys.

**Nothing is discovered.** The pool a coin trades in is read only when the asset
file names it, and it is checked to be a pair for this token before a single
number from it is believed. A pool nobody declared is a pool this tool does not
know about, and it says so under NOT CHECKED rather than searching for one.

**A unit the contract does not declare is not invented.** A fee getter answers
an integer. Whether that integer is a percent, a tenth of a percent or a basis
point is a convention of the source this program never saw, so the reading is
printed with the function it came from and left alone. Amount caps are the
exception and the reason is arithmetic, not taste: a cap is denominated in the
token's own units, so dividing it by ``totalSupply`` is exact.
"""

from __future__ import annotations

from ..model import (
    COIN_SLOTS, FILLED, NOT_FOUND, R_NO_RPC, R_RPC_ERROR, Slot, not_checked,
)
from ..keccak import keccak_hex
from .surface import OWN
from ..snapshot.chain import (
    SEL, ZERO_ADDRESS, ChainReader, ChainReport, JsonRpc, Rpc, RpcError,
    _sig_of, find_selectors, has_any, to_address, to_int, word,
)

# Addresses with no known key. Value sent here leaves circulation, which is why
# a burn is read as a balance at one of them rather than as a supply figure.
DEAD_ADDRESSES = (
    ZERO_ADDRESS,
    "0x000000000000000000000000000000000000dead",
)

R_NO_POOL = "no_pool_declared"
R_POOL_MISMATCH = "declared_pool_is_not_a_pair_for_this_token"
R_NO_BALANCE_FN = "contract_answers_no_balanceOf"
R_NO_SUPPLY = "total_supply_not_read"
R_NO_OWNER_READ = "owner_address_not_read"


def _pct(part: int, whole: int) -> float | None:
    """A share of supply, rounded where a dossier can be compared to itself."""
    if not whole or part < 0:
        return None
    return round(part * 100.0 / whole, 4)


class CoinReader(ChainReader):
    """Fills the twelve coin slots for one address at one block."""

    def __init__(self, rpc: Rpc, chain: str, address: str,
                 chain_id: int | None = None, pool: str | None = None):
        super().__init__(rpc, chain, address, chain_id=chain_id)
        self.pool = pool.lower() if pool else None

    # -- primitives ------------------------------------------------------
    def balance_of(self, holder: str, token: str | None = None) -> int | None:
        """``balanceOf(holder)`` on this token, or on the pool's own LP token."""
        data = SEL["balance_of"] + "000000000000000000000000" + holder[2:].lower()
        raw = self._eth_call(token or self.address, data)
        if raw and len(raw) >= 32:
            return to_int(word(raw, 0))
        return None

    def supply_of(self, token: str) -> int | None:
        raw = self.call_key("total_supply", to=token)
        if raw and len(raw) >= 32:
            return to_int(word(raw, 0))
        return None

    def _supply(self) -> int | None:
        value = self.facts.get("total_supply")
        return int(value) if value not in (None, "") else None

    def _owner_reading(self) -> tuple[str | None, str | None, bool]:
        """(address, method, the contract answers an owner function at all).

        :meth:`ChainReader.owner_address` deliberately treats the zero address
        as "no owner", which is right for an issuer and wrong here: for a coin
        the difference between a contract that never had an owner and one whose
        owner was renounced is the whole reading.
        """
        for key, method in (("owner", "owner()"), ("get_owner", "getOwner()"),
                            ("admin", "admin()"), ("get_admin", "getAdmin()")):
            raw = self.call_key(key)
            if raw and len(raw) >= 32:
                address = to_address(word(raw, 0))
                if address in DEAD_ADDRESSES:
                    return None, method, True
                return address, method, True
        return None, None, False

    # -- entry point -----------------------------------------------------
    def read(self) -> ChainReport:
        try:
            block_hex = self.rpc.call("eth_blockNumber", [])
            self.block = int(block_hex, 16)
            self.block_tag = hex(self.block)
        except (RpcError, ValueError, TypeError) as exc:
            return ChainReport(
                block=None, chain_id=self.chain_id,
                slots=[not_checked(name, R_RPC_ERROR, str(exc))
                       for name in COIN_SLOTS],
                facts={}, errors=[f"eth_blockNumber -> {exc}"])

        if self.chain_id is None:
            try:
                self.chain_id = int(self.rpc.call("eth_chainId", []), 16)
            except (RpcError, ValueError, TypeError):
                pass

        code = self.code(self.address)
        self.facts["code_size"] = len(code)
        self.facts["code_hash"] = keccak_hex(code) if code else None
        self.facts["profile"] = "coin"

        if not code:
            # An address with no code is not a token with nothing to say; it is
            # an address this chain has never seen deployed, and every slot is
            # a fact about that rather than about a coin.
            slots = [not_checked(name, R_RPC_ERROR, "no code at this address")
                     for name in COIN_SLOTS]
            return ChainReport(block=self.block, chain_id=self.chain_id,
                               slots=slots, facts=self.facts, errors=self.errors)

        self._read_token_basics()
        proxy_slot, implementation, selectors = self._read_proxy(
            code, find_selectors(code))
        self.facts["selectors_seen"] = len(selectors)
        if implementation:
            self.facts["implementation"] = implementation

        slots = [
            self._slot_ownership(selectors),
            self._slot_mint(selectors),
            self._slot_burn(selectors),
            self._slot_pause(selectors),
            self._slot_freeze(selectors),
            proxy_slot,
            self._slot_trading_switch(selectors),
            self._slot_transfer_tax(selectors),
            self._slot_transfer_limits(selectors),
            self._slot_owner_holdings(selectors),
            self._slot_burned_supply(selectors),
            self._slot_liquidity(selectors),
        ]
        order = {name: i for i, name in enumerate(COIN_SLOTS)}
        slots.sort(key=lambda s: order.get(s.slot, 99))
        return ChainReport(block=self.block, chain_id=self.chain_id, slots=slots,
                           facts=self.facts, errors=self.errors)

    # -- ownership -------------------------------------------------------
    def _slot_ownership(self, selectors: set[str]) -> Slot:
        keys = OWN["ownership"]["read"]
        present = has_any(selectors, *keys)
        if not present:
            self.facts["ownership"] = {"functions": [], "owner": None}
            return Slot(slot="ownership", state=NOT_FOUND,
                        value="the contract answers no owner function: there is "
                              "no owner to hand the powers below to",
                        evidence=self.absence(SEL[k] for k in keys))
        owner, method, _answers = self._owner_reading()
        can_transfer = has_any(selectors, *OWN["ownership"]["write"])
        if owner is None:
            self.facts["ownership"] = {"functions": present, "owner": None,
                                       "renounced": True}
            return Slot(
                slot="ownership", state=FILLED, material=False,
                value=f"ownership is given up: {method} answers an address with "
                      f"no known key",
                evidence=self.evidence(method or "owner()", ZERO_ADDRESS))
        described = self.describe_controller(owner)
        self.facts["ownership"] = {"functions": present, "owner": owner,
                                   "renounced": False}
        value = f"the contract still answers to an owner: {described}"
        if can_transfer:
            value += "; the position can be passed on: transferOwnership(address)"
        return Slot(slot="ownership", state=FILLED, material=True, value=value,
                    evidence=self.evidence(method or "owner()", owner))

    # -- the switch ------------------------------------------------------
    def _slot_trading_switch(self, selectors: set[str]) -> Slot:
        setters = OWN["trading_switch"]["write"]
        getters = OWN["trading_switch"]["read"]
        present_set = has_any(selectors, *setters)
        present_get = has_any(selectors, *getters)
        if not present_set and not present_get:
            self.facts["trading_switch"] = None
            return Slot(slot="trading_switch", state=NOT_FOUND,
                        value="no function in the deployed code turns transfers "
                              "on or off",
                        evidence=self.absence(SEL[k] for k in setters + getters))
        state_now: bool | None = None
        for key in present_get:
            raw = self.call_key(key)
            if raw and len(raw) >= 32:
                state_now = bool(to_int(word(raw, 0)))
                break
        owner, method, _ = self._owner_reading()
        self.facts["trading_switch"] = {"functions": present_set + present_get,
                                        "open": state_now, "owner": owner}
        if present_set:
            value = ("transfers can be switched on or off after deployment: "
                     + ", ".join(_sig_of(k) for k in present_set))
        else:
            value = ("the contract keeps a transfers-open flag: "
                     + ", ".join(_sig_of(k) for k in present_get))
        if state_now is not None:
            value += ("; the flag reads open at this block" if state_now
                      else "; the flag reads shut at this block: transfers are "
                           "not open to everyone")
        if owner and present_set:
            value += "; controlled by " + self.describe_controller(owner)
        return Slot(slot="trading_switch", state=FILLED,
                    material=bool(present_set), value=value,
                    evidence=self.evidence(
                        _sig_of((present_get or present_set)[0]),
                        state_now if state_now is not None else owner))

    # -- what a transfer pays --------------------------------------------
    def _slot_transfer_tax(self, selectors: set[str]) -> Slot:
        getters = OWN["transfer_tax"]["read"]
        setters = OWN["transfer_tax"]["write"]
        present_get = has_any(selectors, *getters)
        present_set = has_any(selectors, *setters)
        if not present_get and not present_set:
            self.facts["transfer_tax"] = None
            return Slot(slot="transfer_tax", state=NOT_FOUND,
                        value="no fee function in the deployed code: a transfer "
                              "moves what it says it moves",
                        evidence=self.absence(SEL[k] for k in getters + setters))
        readings: dict[str, int] = {}
        for key in present_get:
            raw = self.call_key(key)
            if raw and len(raw) >= 32:
                readings[_sig_of(key)] = to_int(word(raw, 0))
        owner, _method, _ = self._owner_reading()
        self.facts["transfer_tax"] = {"getters": present_get,
                                      "setters": present_set,
                                      "readings": readings, "owner": owner}
        parts = []
        if readings:
            parts.append("a cut is taken on transfer: "
                         + ", ".join(f"{sig} reads {value}"
                                     for sig, value in sorted(readings.items())))
        elif present_get:
            parts.append("the contract keeps a fee figure: "
                         + ", ".join(_sig_of(k) for k in present_get))
        if present_set:
            parts.append("the figure can be moved after deployment: "
                         + ", ".join(_sig_of(k) for k in present_set))
        if readings:
            # The unit is a convention of a source this program never read.
            parts.append("the contract does not declare the unit of these "
                         "figures, so they are printed as read")
        if owner and present_set:
            parts.append("controlled by " + self.describe_controller(owner))
        return Slot(slot="transfer_tax", state=FILLED,
                    material=bool(present_set), value="; ".join(parts),
                    evidence=self.evidence(
                        _sig_of((present_get or present_set)[0]),
                        sorted(readings.items())[0][1] if readings else owner))

    # -- what a wallet may hold ------------------------------------------
    def _slot_transfer_limits(self, selectors: set[str]) -> Slot:
        tx_keys = OWN["transfer_limits"]["per_transfer"]
        wallet_keys = OWN["transfer_limits"]["per_wallet"]
        setters = OWN["transfer_limits"]["write"]
        flags = OWN["transfer_limits"]["flag"]
        present_tx = has_any(selectors, *tx_keys)
        present_wallet = has_any(selectors, *wallet_keys)
        present_set = has_any(selectors, *setters)
        cooldown = has_any(selectors, *flags)
        every = tx_keys + wallet_keys + setters + flags
        if not (present_tx or present_wallet or present_set or cooldown):
            self.facts["transfer_limits"] = None
            return Slot(slot="transfer_limits", state=NOT_FOUND,
                        value="no per-transaction or per-wallet cap in the "
                              "deployed code",
                        evidence=self.absence(SEL[k] for k in every))
        supply = self._supply()
        readings: dict[str, dict] = {}
        for key in present_tx + present_wallet:
            raw = self.call_key(key)
            if raw and len(raw) >= 32:
                amount = to_int(word(raw, 0))
                readings[_sig_of(key)] = {"amount": str(amount),
                                          "share_pct": _pct(amount, supply or 0)}
        owner, _method, _ = self._owner_reading()
        self.facts["transfer_limits"] = {"getters": present_tx + present_wallet,
                                         "setters": present_set,
                                         "cooldown": bool(cooldown),
                                         "readings": readings, "owner": owner}
        parts = []
        for sig, entry in sorted(readings.items()):
            share = entry["share_pct"]
            # A cap is denominated in the token's own units, so this division is
            # exact: no convention is being assumed here.
            parts.append(f"{sig} caps one move at {entry['amount']}"
                         + (f", {share}% of supply" if share is not None else ""))
        if not readings and (present_tx or present_wallet):
            parts.append("the contract keeps a cap: "
                         + ", ".join(_sig_of(k)
                                     for k in present_tx + present_wallet))
        if cooldown:
            parts.append("a per-address cooldown is kept: cooldownEnabled()")
        if present_set:
            parts.append("the caps can be moved or dropped after deployment: "
                         + ", ".join(_sig_of(k) for k in present_set))
        if owner and present_set:
            parts.append("controlled by " + self.describe_controller(owner))
        return Slot(slot="transfer_limits", state=FILLED,
                    material=bool(present_set), value="; ".join(parts),
                    evidence=self.evidence(
                        _sig_of((present_tx + present_wallet + present_set)[0]),
                        len(readings) or None))

    # -- where the supply sits -------------------------------------------
    def _slot_owner_holdings(self, selectors: set[str]) -> Slot:
        if not has_any(selectors, "balance_of"):
            return not_checked("owner_holdings", R_NO_BALANCE_FN,
                               "no balance can be read from this contract")
        supply = self._supply()
        if not supply:
            return not_checked("owner_holdings", R_NO_SUPPLY,
                               "totalSupply() did not answer, so a share of it "
                               "cannot be computed")
        owner, _method, answers = self._owner_reading()
        if owner is None:
            reason = ("the owner position is given up, so there is no owner "
                      "balance to read" if answers else
                      "the contract answers no owner function")
            return not_checked("owner_holdings", R_NO_OWNER_READ, reason)
        balance = self.balance_of(owner)
        if balance is None:
            return not_checked("owner_holdings", R_NO_BALANCE_FN,
                               f"balanceOf({owner}) did not answer")
        share = _pct(balance, supply)
        self.facts["owner_holdings"] = {"owner": owner, "balance": str(balance),
                                        "share_pct": share}
        if balance == 0:
            return Slot(slot="owner_holdings", state=FILLED, material=False,
                        value=f"the owner address holds none of the supply: "
                              f"balanceOf({owner}) reads 0",
                        evidence=self.evidence("balanceOf(address)", 0))
        return Slot(slot="owner_holdings", state=FILLED, material=False,
                    value=f"the owner address holds {share}% of supply "
                          f"({balance} units)",
                    evidence=self.evidence("balanceOf(address)", str(balance),
                                           address=owner))

    def _slot_burned_supply(self, selectors: set[str]) -> Slot:
        if not has_any(selectors, "balance_of"):
            return not_checked("burned_supply", R_NO_BALANCE_FN,
                               "no balance can be read from this contract")
        supply = self._supply()
        if not supply:
            return not_checked("burned_supply", R_NO_SUPPLY,
                               "totalSupply() did not answer, so a share of it "
                               "cannot be computed")
        parked: dict[str, str] = {}
        total = 0
        answered = False
        for address in DEAD_ADDRESSES:
            balance = self.balance_of(address)
            if balance is None:
                continue
            answered = True
            if balance:
                parked[address] = str(balance)
                total += balance
        if not answered:
            return not_checked("burned_supply", R_NO_BALANCE_FN,
                               "no balance at a dead address could be read")
        share = _pct(total, supply)
        self.facts["burned_supply"] = {"addresses": parked, "total": str(total),
                                       "share_pct": share}
        if not total:
            return Slot(slot="burned_supply", state=NOT_FOUND,
                        value="none of the supply sits at an address with no "
                              "known key",
                        evidence=self.evidence("balanceOf(address)", 0))
        return Slot(slot="burned_supply", state=FILLED, material=False,
                    value=f"{share}% of supply sits at an address with no known "
                          f"key ({total} units across {len(parked)})",
                    evidence=self.evidence("balanceOf(address)", str(total)))

    # -- the declared pool -----------------------------------------------
    def _slot_liquidity(self, selectors: set[str]) -> Slot:
        if not self.pool:
            return not_checked(
                "liquidity", R_NO_POOL,
                "no pool is named in the asset file. Pools are never discovered: "
                "add the pair address by hand and this slot fills")
        pool = self.pool
        pair_code = self.code(pool)
        if not pair_code:
            return not_checked("liquidity", R_RPC_ERROR,
                               f"no code at the declared pool {pool}")
        pair_selectors = find_selectors(pair_code)
        sides: list[str] = []
        for key in ("token0", "token1"):
            raw = self.call_key(key, to=pool)
            if raw and len(raw) >= 32:
                sides.append(to_address(word(raw, 0)))
        if sides and self.address not in sides:
            # Provenance, in the same spirit as the document whitelist: a number
            # from a pool that does not trade this token is not this token's.
            return not_checked("liquidity", R_POOL_MISMATCH,
                               f"{pool} answers token0/token1 as "
                               f"{', '.join(sides)}, neither is {self.address}")
        supply = self._supply()
        in_pool = self.balance_of(pool)
        lp_supply = (self.supply_of(pool)
                     if has_any(pair_selectors, "total_supply") else None)
        lp_parked = 0
        lp_answered = False
        if lp_supply:
            for address in DEAD_ADDRESSES:
                balance = self.balance_of(address, token=pool)
                if balance is None:
                    continue
                lp_answered = True
                lp_parked += balance
        if in_pool is None and lp_supply is None:
            return not_checked("liquidity", R_NO_BALANCE_FN,
                               f"neither a token balance nor an LP supply could "
                               f"be read from {pool}")
        pool_share = _pct(in_pool, supply or 0) if in_pool is not None else None
        lp_share = _pct(lp_parked, lp_supply) if (lp_supply and lp_answered) else None
        self.facts["liquidity"] = {
            "pool": pool, "sides": sides,
            "token_in_pool": (str(in_pool) if in_pool is not None else None),
            "token_in_pool_pct": pool_share,
            "lp_total_supply": (str(lp_supply) if lp_supply else None),
            "lp_at_dead_addresses": str(lp_parked) if lp_answered else None,
            "lp_burnt_pct": lp_share,
        }
        parts = []
        if pool_share is not None:
            parts.append(f"{pool_share}% of supply sits in the declared pool "
                         f"{pool}")
        elif in_pool is not None:
            parts.append(f"the declared pool {pool} holds {in_pool} units")
        if lp_share is not None:
            parts.append(f"{lp_share}% of the pool's own LP token is parked at an "
                         f"address with no known key")
        elif lp_supply:
            parts.append("the pool's LP balance at a dead address could not be "
                         "read, so how much of it is parked is unknown")
        else:
            parts.append("the declared pool answers no totalSupply(), so it is "
                         "not a UniswapV2-shaped pair and its LP cannot be read")
        # Not material, whatever the number: "material" in this codebase means a
        # power over somebody else's balance, and a pool is not one. How large a
        # share is large enough is the holder's threshold to set, not this
        # function's to decide.
        return Slot(slot="liquidity", state=FILLED, material=False,
                    value="; ".join(parts),
                    evidence=self.evidence("balanceOf(address)",
                                           str(in_pool) if in_pool is not None
                                           else str(lp_supply), address=pool))


def read_coin(config: dict, chain: str, address: str,
              chain_id: int | None = None, pool: str | None = None,
              rpc: Rpc | None = None) -> ChainReport:
    """Read every coin slot, or say why it could not be read."""
    if rpc is None:
        url = (config.get("rpc") or {}).get(chain)
        if not url:
            return ChainReport(
                block=None, chain_id=chain_id,
                slots=[not_checked(name, R_NO_RPC,
                                   f"no [rpc] {chain} entry in config.toml")
                       for name in COIN_SLOTS],
                facts={}, errors=[f"missing rpc endpoint for {chain}"])
        rpc = JsonRpc(url)
    return CoinReader(rpc, chain, address, chain_id=chain_id, pool=pool).read()


__all__ = ["CoinReader", "read_coin", "DEAD_ADDRESSES"]
