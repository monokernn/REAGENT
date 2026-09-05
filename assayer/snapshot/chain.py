"""On-chain readers: powers, owners, proxy pattern, timelock, freeze.

Read-only by construction. :class:`JsonRpc` refuses any method outside
:data:`READ_ONLY_METHODS`, so a state-changing call cannot be made even by
mistake, and no signing library is imported anywhere in the package.

Filling these slots never involves a model. What the issuer *may do* to a
balance is a fact about bytecode and storage, and it is read as one.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from ..keccak import keccak_hex, selector, storage_slot
from ..model import (
    E_ABSENCE, E_ONCHAIN, FILLED, NOT_FOUND, R_NO_RPC, R_RPC_ERROR,
    R_UNKNOWN_PROXY, Evidence, Slot, not_checked,
)

READ_ONLY_METHODS = frozenset({
    "eth_call",
    "eth_getStorageAt",
    "eth_getCode",
    "eth_getLogs",
    "eth_blockNumber",
    "eth_chainId",
    "eth_getBlockByNumber",
})

ZERO_ADDRESS = "0x" + "0" * 40


class RpcError(Exception):
    pass


class Rpc(Protocol):
    def call(self, method: str, params: list) -> Any: ...


class JsonRpc:
    """Minimal JSON-RPC client restricted to read-only methods."""

    def __init__(self, url: str, timeout_s: float = 30.0):
        self.url = url
        self.timeout_s = timeout_s
        self._id = 0

    def call(self, method: str, params: list) -> Any:
        if method not in READ_ONLY_METHODS:
            raise RpcError(f"method not permitted: {method}")
        self._id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self._id,
                              "method": method, "params": params}).encode()
        request = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": "assayer/0.3 (read-only)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RpcError(f"{type(exc).__name__}: {exc}") from exc
        if isinstance(body, dict) and body.get("error"):
            raise RpcError(str(body["error"]))
        if not isinstance(body, dict) or "result" not in body:
            raise RpcError(f"malformed response: {body!r}")
        return body["result"]


# ---------------------------------------------------------------- selectors

SIGNATURES = {
    # ERC-20 surface
    "name()": "name",
    "symbol()": "symbol",
    "decimals()": "decimals",
    "totalSupply()": "total_supply",
    # ownership
    "owner()": "owner",
    "getOwner()": "get_owner",
    "admin()": "admin",
    "getAdmin()": "get_admin",
    "hasRole(bytes32,address)": "has_role",
    "getRoleMember(bytes32,uint256)": "get_role_member",
    "getRoleMemberCount(bytes32)": "get_role_member_count",
    "DEFAULT_ADMIN_ROLE()": "default_admin_role",
    "MINTER_ROLE()": "minter_role",
    "PAUSER_ROLE()": "pauser_role",
    "BURNER_ROLE()": "burner_role",
    # mint
    "mint(address,uint256)": "mint_to",
    "mint(uint256)": "mint_self",
    "issue(uint256)": "issue",
    "increaseSupply(uint256)": "increase_supply",
    # burn
    "burn(uint256)": "burn_self",
    "burn(address,uint256)": "burn_from_address",
    "burnFrom(address,uint256)": "burn_from",
    "redeem(uint256)": "redeem",
    "destroyBlackFunds(address)": "destroy_black_funds",
    "wipeFrozenAddress(address)": "wipe_frozen_address",
    "burnBlackFunds(address,uint256)": "burn_black_funds",
    "confiscate(address,address,uint256)": "confiscate",
    # pause
    "pause()": "pause",
    "unpause()": "unpause",
    "paused()": "paused",
    # freeze
    "blacklist(address)": "blacklist",
    "addBlackList(address)": "add_blacklist",
    "isBlacklisted(address)": "is_blacklisted",
    "isBlackListed(address)": "is_blacklisted_alt",
    "freeze(address)": "freeze",
    "isFrozen(address)": "is_frozen",
    "frozen(address)": "frozen",
    "blockUser(address)": "block_user",
    "setAddressFrozen(address,bool)": "set_address_frozen",
    "addToBlockedList(address)": "add_to_blocked_list",
    # upgrade
    "upgradeTo(address)": "upgrade_to",
    "upgradeToAndCall(address,bytes)": "upgrade_to_and_call",
    "implementation()": "implementation",
    "proxiableUUID()": "proxiable_uuid",
    "facets()": "facets",
    "facetAddresses()": "facet_addresses",
    "diamondCut((address,uint8,bytes4[])[],address,bytes)": "diamond_cut",
    # timelock
    "getMinDelay()": "get_min_delay",
    "delay()": "delay",
    "MINIMUM_DELAY()": "minimum_delay",
    # multisig (Gnosis Safe surface)
    "getThreshold()": "get_threshold",
    "getOwners()": "get_owners",
    # oracle
    "latestAnswer()": "latest_answer",
    "latestRoundData()": "latest_round_data",
    "oracle()": "oracle",
    "priceFeed()": "price_feed",
    "getPrice()": "get_price",
    "aggregator()": "aggregator",
}

SEL: dict[str, str] = {}
for _sig, _key in SIGNATURES.items():
    SEL[_key] = selector(_sig.split("#")[0])

SEL_TO_SIG = {selector(sig.split("#")[0]): sig.split("#")[0] for sig in SIGNATURES}

# EIP-1967 and its predecessors.
SLOT_1967_IMPL = storage_slot("eip1967.proxy.implementation", True)
SLOT_1967_ADMIN = storage_slot("eip1967.proxy.admin", True)
SLOT_1967_BEACON = storage_slot("eip1967.proxy.beacon", True)
SLOT_OZ_IMPL = storage_slot("org.zeppelinos.proxy.implementation")
SLOT_OZ_ADMIN = storage_slot("org.zeppelinos.proxy.admin")
SLOT_EIP1822 = storage_slot("PROXIABLE")

PROXY_SLOTS = {
    "eip1967.implementation": SLOT_1967_IMPL,
    "eip1967.admin": SLOT_1967_ADMIN,
    "eip1967.beacon": SLOT_1967_BEACON,
    "zeppelinos.implementation": SLOT_OZ_IMPL,
    "zeppelinos.admin": SLOT_OZ_ADMIN,
    "eip1822.proxiable": SLOT_EIP1822,
}

MINIMAL_PROXY_PREFIX = "363d3d373d3d3d363d73"  # EIP-1167 clone


# ---------------------------------------------------------------- abi helpers

def word(data: bytes, index: int) -> bytes:
    return data[index * 32:(index + 1) * 32]


def to_int(data: bytes) -> int:
    return int.from_bytes(data, "big")


def to_address(data: bytes) -> str:
    return "0x" + data[-20:].hex()


def decode_string(raw: bytes) -> str | None:
    """Decode ``string``/``bytes32`` return data, tolerating both conventions."""
    if not raw:
        return None
    if len(raw) == 32:
        try:
            return raw.rstrip(b"\x00").decode("utf-8").strip() or None
        except UnicodeDecodeError:
            return None
    if len(raw) < 64:
        return None
    try:
        offset = to_int(word(raw, 0))
        length = to_int(raw[offset:offset + 32])
        body = raw[offset + 32:offset + 32 + length]
        return body.decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def find_selectors(code: bytes) -> set[str]:
    """Every 4-byte value pushed by PUSH4 in the runtime bytecode.

    This is how a dispatch table is built by every mainstream compiler, so it
    is a reliable read of which functions the deployed code answers.
    """
    out: set[str] = set()
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if op == 0x63 and i + 5 <= n:  # PUSH4
            out.add("0x" + code[i + 1:i + 5].hex())
            i += 5
            continue
        if 0x60 <= op <= 0x7F:  # PUSH1..PUSH32
            i += 1 + (op - 0x5F)
            continue
        i += 1
    return out


def has_opcode(code: bytes, opcode: int) -> bool:
    """Opcode-accurate scan: PUSH payloads are skipped, not matched."""
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if op == opcode:
            return True
        if 0x60 <= op <= 0x7F:
            i += 1 + (op - 0x5F)
            continue
        i += 1
    return False


def has_any(selectors: set[str], *keys: str) -> list[str]:
    """Which of the named functions the code answers, by signature name."""
    present = []
    for key in keys:
        sel = SEL.get(key)
        if sel and sel in selectors:
            present.append(key)
    return present


# ---------------------------------------------------------------- reader

@dataclass
class ChainReport:
    block: int | None = None
    chain_id: int | None = None
    slots: list[Slot] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "block": self.block,
            "chain_id": self.chain_id,
            "facts": self.facts,
            "errors": list(self.errors),
            "slots": [s.to_json() for s in self.slots],
        }


class ChainReader:
    """Fills the eight on-chain slots for one address at one block."""

    def __init__(self, rpc: Rpc, chain: str, address: str,
                 chain_id: int | None = None):
        self.rpc = rpc
        self.chain = chain
        self.address = address.lower()
        self.chain_id = chain_id
        self.block: int | None = None
        self.block_tag = "latest"
        self.errors: list[str] = []
        self.facts: dict[str, Any] = {}
        self._code_cache: dict[str, bytes] = {}

    # -- primitives ------------------------------------------------------
    def _eth_call(self, to: str, data: str) -> bytes | None:
        try:
            raw = self.rpc.call("eth_call", [{"to": to, "data": data},
                                             self.block_tag])
        except RpcError as exc:
            self.errors.append(f"eth_call {data[:10]} -> {exc}")
            return None
        if not isinstance(raw, str) or not raw.startswith("0x"):
            return None
        return bytes.fromhex(raw[2:])

    def call_key(self, key: str, to: str | None = None) -> bytes | None:
        sel = SEL[key]
        return self._eth_call(to or self.address, sel)

    def code(self, address: str) -> bytes:
        address = address.lower()
        if address in self._code_cache:
            return self._code_cache[address]
        try:
            raw = self.rpc.call("eth_getCode", [address, self.block_tag])
            data = bytes.fromhex(raw[2:]) if isinstance(raw, str) else b""
        except RpcError as exc:
            self.errors.append(f"eth_getCode {address} -> {exc}")
            data = b""
        self._code_cache[address] = data
        return data

    def storage(self, slot: str, address: str | None = None) -> str | None:
        try:
            raw = self.rpc.call("eth_getStorageAt",
                                [address or self.address, slot, self.block_tag])
        except RpcError as exc:
            self.errors.append(f"eth_getStorageAt {slot[:10]} -> {exc}")
            return None
        return raw if isinstance(raw, str) else None

    def evidence(self, method: str, raw: Any, address: str | None = None) -> Evidence:
        return Evidence(kind=E_ONCHAIN, chain_id=self.chain_id,
                        address=(address or self.address), method=method,
                        block=self.block, raw=_short(raw))

    def absence(self, checked: Iterable[str]) -> Evidence:
        return Evidence(kind=E_ABSENCE, chain_id=self.chain_id,
                        address=self.address, block=self.block,
                        checked_selectors=sorted(set(checked)))

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
                       for name in _ONCHAIN_SCRIPT_SLOTS],
                facts={}, errors=[f"eth_blockNumber -> {exc}"])

        if self.chain_id is None:
            try:
                self.chain_id = int(self.rpc.call("eth_chainId", []), 16)
            except (RpcError, ValueError, TypeError):
                pass

        code = self.code(self.address)
        self.facts["code_size"] = len(code)
        self.facts["code_hash"] = keccak_hex(code) if code else None
        selectors = find_selectors(code)

        if not code:
            slots = [not_checked(name, R_RPC_ERROR,
                                "по этому адресу нет кода")
                     for name in _ONCHAIN_SCRIPT_SLOTS]
            return ChainReport(block=self.block, chain_id=self.chain_id,
                               slots=slots, facts=self.facts, errors=self.errors)

        self._read_token_basics()
        proxy_slot, implementation, selectors = self._read_proxy(code, selectors)
        self.facts["selectors_seen"] = len(selectors)

        slots = [
            self._slot_mint(selectors),
            self._slot_burn(selectors),
            self._slot_pause(selectors),
            proxy_slot,
            self._slot_freeze(selectors),
            self._slot_oracle(selectors),
        ]
        if implementation:
            self.facts["implementation"] = implementation
        return ChainReport(block=self.block, chain_id=self.chain_id, slots=slots,
                           facts=self.facts, errors=self.errors)

    # -- token basics ----------------------------------------------------
    def _read_token_basics(self) -> None:
        for key in ("name", "symbol"):
            raw = self.call_key(key)
            if raw:
                self.facts[key] = decode_string(raw)
        raw = self.call_key("decimals")
        if raw:
            self.facts["decimals"] = to_int(word(raw, 0))
        raw = self.call_key("total_supply")
        if raw:
            self.facts["total_supply"] = str(to_int(word(raw, 0)))

    # -- ownership -------------------------------------------------------
    def owner_address(self) -> tuple[str | None, str | None]:
        """(address, method) of whoever the contract calls its owner."""
        for key, method in (("owner", "owner()"), ("get_owner", "getOwner()"),
                            ("admin", "admin()"), ("get_admin", "getAdmin()")):
            raw = self.call_key(key)
            if raw and len(raw) >= 32:
                address = to_address(word(raw, 0))
                if address != ZERO_ADDRESS:
                    return address, method
        return None, None

    def describe_controller(self, address: str) -> str:
        """EOA, multisig with threshold, timelock with delay, or plain contract.

        The structured reading is kept in ``facts["controllers"]`` as well, so
        that signal matching never has to parse this sentence back.
        """
        controllers = self.facts.setdefault("controllers", {})
        code = self.code(address)
        if not code:
            controllers[address] = {"kind": "eoa"}
            return f"{address} (EOA — один ключ, порога подписей нет)"
        selectors = find_selectors(code)
        if has_any(selectors, "get_threshold", "get_owners"):
            threshold = self._read_uint(address, "get_threshold")
            owners = self._read_address_array(address, "get_owners")
            count = len(owners) if owners is not None else None
            controllers[address] = {"kind": "multisig", "threshold": threshold,
                                    "owners": count}
            if threshold is not None and count is not None:
                return f"{address} (мультиподпись, {threshold} из {count})"
            return f"{address} (мультиподпись)"
        delay = self._timelock_delay(address, selectors)
        if delay is not None:
            controllers[address] = {"kind": "timelock", "delay_s": delay}
            return f"{address} (timelock, задержка {delay}с)"
        controllers[address] = {"kind": "contract"}
        return f"{address} (контракт)"

    def _read_uint(self, address: str, key: str) -> int | None:
        raw = self.call_key(key, to=address)
        if raw and len(raw) >= 32:
            return to_int(word(raw, 0))
        return None

    def _read_address_array(self, address: str, key: str) -> list[str] | None:
        raw = self.call_key(key, to=address)
        if not raw or len(raw) < 64:
            return None
        try:
            offset = to_int(word(raw, 0))
            length = to_int(raw[offset:offset + 32])
            body = raw[offset + 32:offset + 32 + 32 * length]
            return [to_address(body[i * 32:(i + 1) * 32]) for i in range(length)]
        except Exception:
            return None

    def _timelock_delay(self, address: str, selectors: set[str]) -> int | None:
        for key in ("get_min_delay", "delay", "minimum_delay"):
            if SEL[key] in selectors:
                value = self._read_uint(address, key)
                if value is not None:
                    return value
        return None

    # -- slots -----------------------------------------------------------
    def _slot_mint(self, selectors: set[str]) -> Slot:
        present = has_any(selectors, "mint_to", "mint_self", "issue", "increase_supply")
        if not present:
            self.facts["mint"] = None
            return Slot(slot="mint_authority", state=NOT_FOUND,
                        value="в задеплоенном коде нет функции, увеличивающей выпуск",
                        evidence=self.absence(
                            SEL[k] for k in ("mint_to", "mint_self", "issue",
                                             "increase_supply")))
        owner, method = self.owner_address()
        role_gated = bool(has_any(selectors, "has_role", "minter_role"))
        parts = ["выпуск возможен: " + ", ".join(f"{_sig_of(k)}" for k in present)]
        if owner:
            parts.append("контролирует " + self.describe_controller(owner))
        elif role_gated:
            parts.append("доступ через роли AccessControl; состав держателей роли "
                         "по хранилищу не восстанавливается")
        self.facts["mint"] = {"functions": present, "owner": owner,
                              "role_gated": role_gated}
        return Slot(slot="mint_authority", state=FILLED, material=True,
                    value="; ".join(parts),
                    evidence=self.evidence(method or "eth_getCode",
                                           owner or self.facts.get("code_hash")))

    def _slot_burn(self, selectors: set[str]) -> Slot:
        others = has_any(selectors, "burn_from_address", "burn_from",
                         "destroy_black_funds", "wipe_frozen_address",
                         "burn_black_funds", "confiscate")
        self_only = has_any(selectors, "burn_self", "redeem")
        if not others and not self_only:
            self.facts["burn"] = None
            return Slot(slot="burn_authority", state=NOT_FOUND,
                        value="в задеплоенном коде нет функции сжигания",
                        evidence=self.absence(
                            SEL[k] for k in ("burn_self", "burn_from_address",
                                             "burn_from", "destroy_black_funds",
                                             "wipe_frozen_address")))
        owner, method = self.owner_address()
        self.facts["burn"] = {"foreign_balances": others, "self_only": self_only,
                              "owner": owner}
        if others:
            value = ("может уменьшить баланс, которым не владеет: "
                     + ", ".join(_sig_of(k) for k in others))
            if owner:
                value += "; контролирует " + self.describe_controller(owner)
            return Slot(slot="burn_authority", state=FILLED, material=True,
                        value=value,
                        evidence=self.evidence(method or "eth_getCode",
                                               owner or self.facts.get("code_hash")))
        return Slot(slot="burn_authority", state=FILLED,
                    value="сжигание затрагивает только баланс вызывающего: "
                          + ", ".join(_sig_of(k) for k in self_only),
                    evidence=self.evidence("eth_getCode",
                                           self.facts.get("code_hash")))

    def _slot_pause(self, selectors: set[str]) -> Slot:
        present = has_any(selectors, "pause", "unpause", "paused")
        if not present:
            self.facts["pause"] = None
            return Slot(slot="pause", state=NOT_FOUND,
                        value="в задеплоенном коде нет функции остановки переводов",
                        evidence=self.absence(SEL[k] for k in
                                              ("pause", "unpause", "paused")))
        state_now = None
        if "paused" in present:
            raw = self.call_key("paused")
            if raw and len(raw) >= 32:
                state_now = bool(to_int(word(raw, 0)))
        owner, method = self.owner_address()
        self.facts["pause"] = {"functions": present, "paused": state_now,
                               "owner": owner}
        value = "переводы можно остановить"
        if state_now is True:
            value = "переводы остановлены прямо сейчас"
        if owner:
            value += "; контролирует " + self.describe_controller(owner)
        return Slot(slot="pause", state=FILLED, material=True, value=value,
                    evidence=self.evidence(method or "paused()",
                                           state_now if state_now is not None
                                           else owner))

    def _slot_freeze(self, selectors: set[str]) -> Slot:
        keys = ("blacklist", "add_blacklist", "is_blacklisted", "is_blacklisted_alt",
                "freeze", "is_frozen", "frozen", "block_user", "set_address_frozen",
                "add_to_blocked_list")
        present = has_any(selectors, *keys)
        if not present:
            self.facts["freeze"] = None
            return Slot(slot="freeze", state=NOT_FOUND,
                        value="в задеплоенном коде нет функции блокировки адреса "
                              "или чёрного списка",
                        evidence=self.absence(SEL[k] for k in keys))
        owner, method = self.owner_address()
        self.facts["freeze"] = {"functions": present, "owner": owner}
        value = ("любой адрес может быть лишён возможности двигать свой баланс: "
                 + ", ".join(_sig_of(k) for k in present))
        if owner:
            value += "; контролирует " + self.describe_controller(owner)
        return Slot(slot="freeze", state=FILLED, material=True, value=value,
                    evidence=self.evidence(method or "eth_getCode",
                                           owner or self.facts.get("code_hash")))

    def _slot_oracle(self, selectors: set[str]) -> Slot:
        keys = ("oracle", "price_feed", "aggregator", "latest_answer",
                "latest_round_data", "get_price")
        present = has_any(selectors, *keys)
        if not present:
            self.facts["oracle"] = None
            return Slot(slot="oracle", state=NOT_FOUND,
                        value="контракт токена не читает источник цены",
                        evidence=self.absence(SEL[k] for k in keys))
        feed = None
        for key in ("oracle", "price_feed", "aggregator"):
            if key in present:
                raw = self.call_key(key)
                if raw and len(raw) >= 32:
                    candidate = to_address(word(raw, 0))
                    if candidate != ZERO_ADDRESS:
                        feed = candidate
                        break
        staleness = None
        updated_at = None
        if feed:
            data = self.call_key("latest_round_data", to=feed)
            if data and len(data) >= 160:
                updated_at = to_int(word(data, 3))
                block_ts = self._block_timestamp()
                if updated_at and block_ts:
                    staleness = block_ts - updated_at
        self.facts["oracle"] = {"functions": present, "feed": feed,
                                "updated_at": updated_at}
        value = "источник цены: " + (feed or ", ".join(_sig_of(k) for k in present))
        if staleness is not None:
            value += (f"; последнее обновление за {staleness}с до блока "
                      f"{self.block}")
        return Slot(slot="oracle", state=FILLED, value=value,
                    evidence=self.evidence("latestRoundData()" if feed else
                                           _sig_of(present[0]),
                                           updated_at if updated_at else feed,
                                           address=feed))

    def _block_timestamp(self) -> int | None:
        try:
            block = self.rpc.call("eth_getBlockByNumber", [self.block_tag, False])
        except RpcError as exc:
            self.errors.append(f"eth_getBlockByNumber -> {exc}")
            return None
        if isinstance(block, dict) and isinstance(block.get("timestamp"), str):
            return int(block["timestamp"], 16)
        return None

    # -- proxy -----------------------------------------------------------
    def _read_proxy(self, code: bytes,
                    selectors: set[str]) -> tuple[Slot, str | None, set[str]]:
        """Detect the proxy pattern and merge the implementation's selectors in.

        Recognised: EIP-1967 (transparent and UUPS), OpenZeppelin legacy,
        EIP-1822, EIP-1167 minimal clones. Anything else that delegates is
        reported as ``not_checked: unknown_proxy_pattern`` rather than guessed at.
        """
        readings: dict[str, str] = {}
        unreadable = 0
        for label, slot in PROXY_SLOTS.items():
            value = self.storage(slot)
            if value is None:
                unreadable += 1
                continue
            if int(value, 16) != 0:
                readings[label] = value
        self.facts["proxy_slots"] = {k: to_address(bytes.fromhex(v[2:]))
                                     for k, v in readings.items()}

        implementation = None
        pattern = None
        admin = None
        for label in ("eip1967.implementation", "zeppelinos.implementation",
                      "eip1822.proxiable"):
            if label in readings:
                implementation = to_address(bytes.fromhex(readings[label][2:]))
                pattern = label.split(".")[0]
                break
        if implementation is None and "eip1967.beacon" in readings:
            beacon = to_address(bytes.fromhex(readings["eip1967.beacon"][2:]))
            raw = self.call_key("implementation", to=beacon)
            if raw and len(raw) >= 32:
                implementation = to_address(word(raw, 0))
                pattern = "eip1967.beacon"
                self.facts["beacon"] = beacon
        for label in ("eip1967.admin", "zeppelinos.admin"):
            if label in readings:
                admin = to_address(bytes.fromhex(readings[label][2:]))
                break

        code_hex = code.hex()
        if implementation is None and code_hex.startswith(MINIMAL_PROXY_PREFIX):
            implementation = "0x" + code_hex[len(MINIMAL_PROXY_PREFIX):
                                            len(MINIMAL_PROXY_PREFIX) + 40]
            pattern = "eip1167.minimal"

        impl_selectors: set[str] = set()
        if implementation and implementation != ZERO_ADDRESS:
            impl_code = self.code(implementation)
            impl_selectors = find_selectors(impl_code)
            self.facts["implementation_code_hash"] = (keccak_hex(impl_code)
                                                     if impl_code else None)
            selectors = selectors | impl_selectors

        delegates = has_opcode(code, 0xF4)  # DELEGATECALL
        diamond = bool(has_any(selectors, "facets", "facet_addresses", "diamond_cut"))

        if implementation is None and diamond:
            self.facts["upgradeable"] = {"pattern": "diamond"}
            return (not_checked("upgradeable", R_UNKNOWN_PROXY,
                                "EIP-2535 diamond: фасеты перечислимы, но эта "
                                "сборка их не обходит"),
                    None, selectors)

        if implementation is None:
            if unreadable:
                # A storage read that failed says nothing about the asset.
                self.facts["upgradeable"] = None
                return (not_checked("upgradeable", R_RPC_ERROR,
                                    f"не удалось прочитать {unreadable} из "
                                    f"{len(PROXY_SLOTS)} слотов хранилища proxy"),
                        None, selectors)
            if delegates and len(code) > 128:
                self.facts["upgradeable"] = {"pattern": "unknown_delegating"}
                return (not_checked("upgradeable", R_UNKNOWN_PROXY,
                                    "код делегирует вызовы, но ни один известный "
                                    "слот реализации не совпал"),
                        None, selectors)
            self.facts["upgradeable"] = None
            return (Slot(slot="upgradeable", state=NOT_FOUND,
                         value="ни один известный паттерн proxy не найден: "
                               "логику по этому адресу нельзя заменить на месте",
                         evidence=self.absence(PROXY_SLOTS.values())),
                    None, selectors)

        uups = bool(has_any(impl_selectors, "upgrade_to", "upgrade_to_and_call",
                            "proxiable_uuid"))
        flavour = "UUPS" if (uups and admin is None) else pattern
        parts = [f"proxy ({flavour}); логика лежит по адресу {implementation}"]
        delay = None
        controller = admin
        if admin:
            described = self.describe_controller(admin)
            parts.append("апгрейд контролирует " + described)
            admin_code = self.code(admin)
            delay = self._timelock_delay(admin, find_selectors(admin_code))
        elif uups:
            owner, _ = self.owner_address()
            if owner:
                controller = owner
                parts.append("апгрейд контролирует "
                             + self.describe_controller(owner))
                delay = self._timelock_delay(owner, find_selectors(self.code(owner)))
        if delay:
            parts.append(f"задержка timelock {delay}с")
        elif delay == 0:
            # A timelock configured to zero is not a waiting period.
            parts.append("задержка timelock равна 0с: апгрейд вступает в силу "
                         "без ожидания")
        else:
            parts.append("задержки на пути апгрейда не найдено")
        self.facts["upgradeable"] = {"pattern": flavour,
                                     "implementation": implementation,
                                     "admin": admin, "controller": controller,
                                     "timelock_delay_s": delay}
        return (Slot(slot="upgradeable", state=FILLED, material=True,
                     value="; ".join(parts),
                     evidence=self.evidence("eth_getStorageAt " + SLOT_1967_IMPL,
                                            implementation)),
                implementation, selectors)


_ONCHAIN_SCRIPT_SLOTS = ("mint_authority", "burn_authority", "pause",
                         "upgradeable", "freeze", "oracle")


def _sig_of(key: str) -> str:
    sel = SEL.get(key)
    return SEL_TO_SIG.get(sel or "", key)


def _short(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= 200 else text[:197] + "..."


def read_chain(config: dict, chain: str, address: str,
               chain_id: int | None = None,
               rpc: Rpc | None = None) -> ChainReport:
    """Read every script-owned on-chain slot, or say why it could not be read."""
    if rpc is None:
        url = (config.get("rpc") or {}).get(chain)
        if not url:
            return ChainReport(
                block=None, chain_id=chain_id,
                slots=[not_checked(name, R_NO_RPC,
                                   f"в config.toml нет [rpc] {chain}")
                       for name in _ONCHAIN_SCRIPT_SLOTS],
                facts={}, errors=[f"missing rpc endpoint for {chain}"])
        rpc = JsonRpc(url)
    return ChainReader(rpc, chain, address, chain_id=chain_id).read()
