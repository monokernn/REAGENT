"""The coin profile on Solana: a mint read from its accounts alone.

An SPL token has no bytecode of its own. Every token runs on one of two shared
programs, and what makes one token different from another is written into its
mint account: who may print more, who may freeze a holder, and — on Token-2022
— a set of extensions that can take a fee on every transfer, move any holder's
balance, pause the token or run a program on each transfer. The same twelve
coin slots are answered from those fields, so a Solana sheet reads in the same
grammar as an EVM one.

Read-only by construction. :class:`SolanaRpc` refuses any method outside
:data:`READ_ONLY_METHODS`, and nothing here signs. The endpoints default to
public ones; ``[rpc] solana`` in config.toml takes precedence when it is set.

The two rules of the EVM reader hold here too. The pool a coin trades in is
read only when the asset file names it. A fee is printed in the unit the mint
declares it in — basis points — and nothing else is converted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import urllib.parse
import urllib.request
from typing import Any

from ..model import (
    COIN_SLOTS, E_ABSENCE, E_ONCHAIN, FILLED, NOT_FOUND, R_RPC_ERROR, Evidence,
    Slot, not_checked,
)
from ..snapshot.chain import ChainReport, RpcError

CHAIN = "solana"

DEFAULT_RPCS = (
    "https://api.mainnet-beta.solana.com",
    "https://solana-mainnet.gateway.tatum.io",
    "https://solana-rpc.publicnode.com",
)

READ_ONLY_METHODS = frozenset({
    "getAccountInfo",
    "getMultipleAccounts",
    "getTokenAccountsByOwner",
    "getTokenLargestAccounts",
    "getTokenSupply",
    "getSlot",
})

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
# Tokens sent here leave circulation: nobody holds a key to this address.
INCINERATOR = "1nc1nerator11111111111111111111111111111111"

R_NO_POOL = "no_pool_declared"
R_NOT_A_MINT = "not_an_spl_mint"


# ---------------------------------------------------------------- base58

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        if char not in _INDEX:
            raise ValueError(f"not base58: {char!r}")
        number = number * 58 + _INDEX[char]
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, rem = divmod(number, 58)
        out = _ALPHABET[rem] + out
    pad = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * pad + out


def is_address(text: str) -> bool:
    """A Solana account address: base58 that decodes to exactly 32 bytes."""
    if not isinstance(text, str) or not 32 <= len(text) <= 44:
        return False
    try:
        return len(b58decode(text)) == 32
    except ValueError:
        return False


# ------------------------------------------------------------- ed25519

_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def on_curve(raw: bytes) -> bool:
    """Whether 32 bytes decode to a point on ed25519.

    A wallet's address is a public key, so it is on the curve. A program-derived
    address is chosen to be off it, which is what makes it controllable by a
    program and by no key — the test that tells a holder from a vault.
    """
    if len(raw) != 32:
        return False
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    sign = raw[31] >> 7
    if y >= _P:
        return False
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    x = (u * pow(v, 3, _P) * pow(u * pow(v, 7, _P), (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    if vx2 == u:
        pass
    elif vx2 == (-u) % _P:
        x = (x * _SQRT_M1) % _P
    else:
        return False
    return not (x == 0 and sign == 1)


def find_program_address(seeds: list[bytes], program: str) -> tuple[str, int]:
    program_bytes = b58decode(program)
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(b"".join(seeds) + bytes([bump]) + program_bytes
                                + b"ProgramDerivedAddress").digest()
        if not on_curve(digest):
            return b58encode(digest), bump
    raise ValueError("no program address found")


def metadata_address(mint: str) -> str:
    """The Metaplex metadata account a classic SPL mint keeps its name in."""
    return find_program_address(
        [b"metadata", b58decode(METADATA_PROGRAM), b58decode(mint)],
        METADATA_PROGRAM)[0]


def parse_metaplex(raw: bytes) -> dict:
    """Update authority, name, symbol and mutability out of a metadata account."""
    offset = 1
    update_authority = b58encode(raw[offset:offset + 32]); offset += 32
    mint = b58encode(raw[offset:offset + 32]); offset += 32

    def string() -> str:
        nonlocal offset
        (length,) = struct.unpack_from("<I", raw, offset); offset += 4
        value = raw[offset:offset + length]; offset += length
        return value.decode("utf-8", "replace").rstrip("\0").strip()

    name, symbol, uri = string(), string(), string()
    offset += 2                                     # seller fee basis points
    if raw[offset] == 1:                            # Option<Vec<Creator>>
        offset += 1
        (count,) = struct.unpack_from("<I", raw, offset); offset += 4
        offset += count * 34
    else:
        offset += 1
    offset += 1                                     # primary sale happened
    mutable = bool(raw[offset])
    return {"update_authority": update_authority, "mint": mint, "name": name,
            "symbol": symbol, "uri": uri, "mutable": mutable}


# ----------------------------------------------------------------- rpc

def _host(url: str) -> str:
    """The endpoint as an error message may print it: scheme and host only.

    Paid endpoints carry their key in the query (Helius) or the path
    (QuickNode), and an error message ends up on the sheet and in logs.
    """
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else "endpoint"


class SolanaRpc:
    """JSON-RPC over the first endpoint that answers, read-only methods only."""

    def __init__(self, urls: list[str] | tuple[str, ...] | None = None,
                 timeout_s: float = 20.0):
        self.urls = list(urls or DEFAULT_RPCS)
        self.timeout_s = timeout_s
        self._id = 0

    def call(self, method: str, params: list) -> Any:
        if method not in READ_ONLY_METHODS:
            raise RpcError(f"method not permitted: {method}")
        errors = []
        for url in self.urls:
            self._id += 1
            payload = json.dumps({"jsonrpc": "2.0", "id": self._id,
                                  "method": method, "params": params}).encode()
            request = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "assayer/0.3 (read-only)"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except Exception as exc:                # noqa: BLE001 — try the next one
                errors.append(f"{_host(url)}: {type(exc).__name__}: {exc}")
                continue
            if isinstance(body, dict) and body.get("error"):
                errors.append(f"{_host(url)}: {body['error']}")
                continue
            if isinstance(body, dict) and "result" in body:
                return body["result"]
            errors.append(f"{_host(url)}: malformed response")
        raise RpcError(" | ".join(errors) or "no endpoint configured")


# -------------------------------------------------------------- reader

def _pct(part: int, whole: int) -> float | None:
    if not whole or part < 0:
        return None
    return round(part * 100.0 / whole, 4)


def _ext(info: dict) -> dict[str, dict]:
    return {str(e.get("extension")): (e.get("state") or {})
            for e in info.get("extensions") or []}


class SolanaCoinReader:
    """Fills the twelve coin slots for one mint at one slot."""

    def __init__(self, rpc, address: str, pool: str | None = None):
        self.rpc = rpc
        self.address = address
        self.pool = pool
        self.block: int | None = None
        self.facts: dict = {"profile": "coin", "chain": CHAIN}
        self.errors: list[str] = []

    # -- evidence --------------------------------------------------------
    def evidence(self, method: str, raw: Any, address: str | None = None) -> Evidence:
        text = None if raw is None else str(raw)
        if text and len(text) > 200:
            text = text[:197] + "..."
        return Evidence(kind=E_ONCHAIN, chain_id=None, address=address or self.address,
                        method=method, block=self.block, raw=text)

    def absence(self, checked: list[str]) -> Evidence:
        return Evidence(kind=E_ABSENCE, chain_id=None, address=self.address,
                        block=self.block, checked_selectors=sorted(set(checked)))

    # -- primitives ------------------------------------------------------
    def account(self, address: str, encoding: str = "jsonParsed") -> dict | None:
        result = self.rpc.call("getAccountInfo", [address, {"encoding": encoding}])
        if isinstance(result, dict):
            slot = (result.get("context") or {}).get("slot")
            if slot and self.block is None:
                self.block = int(slot)
            return result.get("value")
        return None

    def balance_held_by(self, owner: str) -> int | None:
        """Every unit of this mint in token accounts ``owner`` controls."""
        try:
            result = self.rpc.call("getTokenAccountsByOwner",
                                   [owner, {"mint": self.address},
                                    {"encoding": "jsonParsed"}])
        except RpcError as exc:
            self.errors.append(f"getTokenAccountsByOwner({owner}) -> {exc}")
            return None
        total = 0
        for item in (result or {}).get("value") or []:
            amount = ((((item.get("account") or {}).get("data") or {})
                       .get("parsed") or {}).get("info") or {}).get("tokenAmount") or {}
            total += int(amount.get("amount") or 0)
        return total

    # -- entry point -----------------------------------------------------
    def read(self) -> ChainReport:
        try:
            mint = self.account(self.address)
        except RpcError as exc:
            return ChainReport(block=None, chain_id=None,
                               slots=[not_checked(n, R_RPC_ERROR, str(exc)) for n in COIN_SLOTS],
                               facts=self.facts, errors=[f"getAccountInfo -> {exc}"])
        parsed = ((mint or {}).get("data") or {}).get("parsed") \
            if isinstance((mint or {}).get("data"), dict) else None
        if not mint or not parsed or parsed.get("type") != "mint":
            note = "no account at this address" if not mint else \
                "the account at this address is not an SPL mint"
            return ChainReport(block=self.block, chain_id=None,
                               slots=[not_checked(n, R_NOT_A_MINT, note) for n in COIN_SLOTS],
                               facts=self.facts, errors=self.errors)

        info = parsed.get("info") or {}
        program = mint.get("owner")
        self.ext = _ext(info)
        self.info = info
        self.supply = int(info.get("supply") or 0)
        self.facts.update({
            "program": "token-2022" if program == TOKEN_2022_PROGRAM else "spl-token",
            "program_id": program,
            "total_supply": str(self.supply),
            "decimals": info.get("decimals"),
            "extensions": sorted(self.ext),
        })
        self.meta = self._read_metadata()
        if self.meta.get("name"):
            self.facts["name"] = self.meta["name"]
        if self.meta.get("symbol"):
            self.facts["symbol"] = self.meta["symbol"]

        slots = [
            self._slot_ownership(),
            self._slot_mint(),
            self._slot_burn(),
            self._slot_pause(),
            self._slot_freeze(),
            self._slot_upgradeable(),
            self._slot_trading_switch(),
            self._slot_transfer_tax(),
            self._slot_transfer_limits(),
            self._slot_owner_holdings(),
            self._slot_burned_supply(),
            self._slot_liquidity(),
        ]
        return ChainReport(block=self.block, chain_id=None, slots=slots,
                           facts=self.facts, errors=self.errors)

    def _read_metadata(self) -> dict:
        """Name, symbol and who may rewrite them: on the mint, or in Metaplex."""
        own = self.ext.get("tokenMetadata")
        if own:
            return {"source": "tokenMetadata", "name": own.get("name"),
                    "symbol": own.get("symbol"),
                    "update_authority": own.get("updateAuthority"),
                    "mutable": own.get("updateAuthority") is not None}
        address = metadata_address(self.address)
        try:
            account = self.account(address, encoding="base64")
        except RpcError as exc:
            self.errors.append(f"metadata {address} -> {exc}")
            return {}
        data = (account or {}).get("data")
        if not account or not isinstance(data, list) or account.get("owner") != METADATA_PROGRAM:
            return {}
        try:
            meta = parse_metaplex(base64.b64decode(data[0]))
        except Exception as exc:                    # noqa: BLE001 — a name is never a blocker
            self.errors.append(f"metadata {address} unreadable: {exc}")
            return {}
        meta["source"] = "metaplex"
        meta["address"] = address
        if not meta["mutable"]:
            meta["update_authority"] = None
        return meta

    # -- the slots -------------------------------------------------------
    def _authorities(self) -> dict[str, str]:
        """Every live authority over this mint, by the power it holds."""
        out: dict[str, str] = {}
        if self.info.get("mintAuthority"):
            out["mint"] = self.info["mintAuthority"]
        if self.info.get("freezeAuthority"):
            out["freeze"] = self.info["freezeAuthority"]
        for name, key, label in (("permanentDelegate", "delegate", "permanent delegate"),
                                 ("pausableConfig", "authority", "pause"),
                                 ("transferHook", "authority", "transfer hook"),
                                 ("transferFeeConfig", "transferFeeConfigAuthority", "fee"),
                                 ("mintCloseAuthority", "closeAuthority", "close")):
            holder = (self.ext.get(name) or {}).get(key)
            if holder:
                out[label] = holder
        if self.meta.get("update_authority"):
            out["metadata"] = self.meta["update_authority"]
        return out

    def _slot_ownership(self) -> Slot:
        live = self._authorities()
        balance_powers = {k: v for k, v in live.items() if k not in ("metadata", "close")}
        holder = next(iter(balance_powers.values()), None) or live.get("metadata")
        self.facts["ownership"] = {"authorities": live, "owner": holder,
                                   "renounced": not live}
        if not live:
            return Slot(slot="ownership", state=FILLED, material=False,
                        value="every authority over this mint is set to none: no key "
                              "can change it",
                        evidence=self.evidence("getAccountInfo(mint)", "authorities: none"))
        listed = "; ".join(f"{k}: {v}" for k, v in live.items())
        if balance_powers:
            return Slot(slot="ownership", state=FILLED, material=True,
                        value=f"the mint still answers to live authorities — {listed}",
                        evidence=self.evidence("getAccountInfo(mint)", listed))
        return Slot(slot="ownership", state=FILLED, material=False,
                    value=f"only the metadata can still be rewritten — {listed}",
                    evidence=self.evidence(f"{self.meta.get('source')}.updateAuthority",
                                           live.get("metadata")))

    def _slot_mint(self) -> Slot:
        authority = self.info.get("mintAuthority")
        if not authority:
            return Slot(slot="mint_authority", state=NOT_FOUND,
                        value="mint authority is set to none: the supply is fixed",
                        evidence=self.evidence("getAccountInfo(mint).mintAuthority", None))
        return Slot(slot="mint_authority", state=FILLED, material=True,
                    value=f"new tokens can still be printed by {authority}",
                    evidence=self.evidence("getAccountInfo(mint).mintAuthority", authority))

    def _slot_burn(self) -> Slot:
        delegate = (self.ext.get("permanentDelegate") or {}).get("delegate")
        if not delegate:
            return Slot(slot="burn_authority", state=NOT_FOUND,
                        value="no permanent delegate: only a holder moves or burns "
                              "their own balance",
                        evidence=self.absence(["permanentDelegate"]))
        return Slot(slot="burn_authority", state=FILLED, material=True,
                    value=f"a permanent delegate can move or burn any holder's "
                          f"balance: {delegate}",
                    evidence=self.evidence("permanentDelegate.delegate", delegate))

    def _slot_pause(self) -> Slot:
        config = self.ext.get("pausableConfig")
        if not config:
            return Slot(slot="pause", state=NOT_FOUND,
                        value="the mint carries no pausable extension",
                        evidence=self.absence(["pausableConfig"]))
        authority, paused = config.get("authority"), bool(config.get("paused"))
        value = "transfers of this token can be paused"
        if authority:
            value += f" by {authority}"
        if paused:
            value += "; it reads paused at this slot"
        return Slot(slot="pause", state=FILLED, material=bool(authority or paused),
                    value=value, evidence=self.evidence("pausableConfig", config))

    def _slot_freeze(self) -> Slot:
        authority = self.info.get("freezeAuthority")
        if not authority:
            return Slot(slot="freeze", state=NOT_FOUND,
                        value="freeze authority is set to none: no holder's account "
                              "can be frozen",
                        evidence=self.evidence("getAccountInfo(mint).freezeAuthority", None))
        return Slot(slot="freeze", state=FILLED, material=True,
                    value=f"any holder's token account can be frozen by {authority}",
                    evidence=self.evidence("getAccountInfo(mint).freezeAuthority", authority))

    def _slot_upgradeable(self) -> Slot:
        hook = self.ext.get("transferHook") or {}
        program, authority = hook.get("programId"), hook.get("authority")
        if not program and not authority:
            return Slot(slot="upgradeable", state=NOT_FOUND,
                        value=f"the token runs on the standard {self.facts['program']} "
                              f"program with no transfer hook",
                        evidence=self.absence(["transferHook"]))
        if program:
            value = f"every transfer runs program {program}"
            if authority:
                value += f", and {authority} can point it at another"
        else:
            value = (f"no program runs on transfer yet, and {authority} can set one "
                     f"that every transfer would then run")
        return Slot(slot="upgradeable", state=FILLED, material=True, value=value,
                    evidence=self.evidence("transferHook", hook))

    def _slot_trading_switch(self) -> Slot:
        if "nonTransferable" in self.ext:
            self.facts["trading_switch"] = {"open": False, "non_transferable": True}
            return Slot(slot="trading_switch", state=FILLED, material=True,
                        value="the token is non-transferable: it cannot change hands",
                        evidence=self.evidence("nonTransferable", True))
        state = (self.ext.get("defaultAccountState") or {}).get("accountState")
        if state == "frozen":
            owner = self.info.get("freezeAuthority")
            self.facts["trading_switch"] = {"open": False, "owner": owner}
            value = "every new token account starts frozen, so holding needs a thaw"
            if owner:
                value += f" from {owner}"
            return Slot(slot="trading_switch", state=FILLED, material=True, value=value,
                        evidence=self.evidence("defaultAccountState", state))
        self.facts["trading_switch"] = None
        return Slot(slot="trading_switch", state=NOT_FOUND,
                    value="new token accounts start open: nothing gates who may hold "
                          "or trade",
                    evidence=self.absence(["defaultAccountState", "nonTransferable"]))

    def _slot_transfer_tax(self) -> Slot:
        config = self.ext.get("transferFeeConfig")
        if not config:
            self.facts["transfer_tax"] = None
            return Slot(slot="transfer_tax", state=NOT_FOUND,
                        value="no transfer fee extension: a transfer moves what it "
                              "says it moves",
                        evidence=self.absence(["transferFeeConfig"]))
        fee = config.get("newerTransferFee") or config.get("olderTransferFee") or {}
        bps = int(fee.get("transferFeeBasisPoints") or 0)
        authority = config.get("transferFeeConfigAuthority")
        withdraw = config.get("withdrawWithheldAuthority")
        self.facts["transfer_tax"] = {"basis_points": bps, "authority": authority,
                                      "withdraw_authority": withdraw,
                                      "maximum_fee": str(fee.get("maximumFee"))}
        value = f"a transfer pays {bps} basis points ({bps / 100:g}%)"
        if authority:
            value += f"; the rate can be changed by {authority}"
        if withdraw:
            value += f"; the withheld fees are collected by {withdraw}"
        return Slot(slot="transfer_tax", state=FILLED, material=bool(bps or authority),
                    value=value, evidence=self.evidence("transferFeeConfig", fee))

    def _slot_transfer_limits(self) -> Slot:
        return Slot(slot="transfer_limits", state=NOT_FOUND,
                    value="the token programs keep no per-transfer or per-wallet cap",
                    evidence=self.absence(["maxTransaction", "maxWallet"]))

    def _slot_owner_holdings(self) -> Slot:
        owner = (self.facts.get("ownership") or {}).get("owner")
        if not owner:
            self.facts["owner_holdings"] = None
            return Slot(slot="owner_holdings", state=NOT_FOUND,
                        value="no authority is live, so there is no owner balance to weigh",
                        evidence=self.absence(["mintAuthority", "updateAuthority"]))
        held = self.balance_held_by(owner)
        if held is None:
            return not_checked("owner_holdings", R_RPC_ERROR,
                               f"the balance of {owner} could not be read")
        share = _pct(held, self.supply)
        self.facts["owner_holdings"] = {"owner": owner, "amount": str(held),
                                        "share_pct": share}
        return Slot(slot="owner_holdings", state=FILLED, material=False,
                    value=f"{owner} holds {share}% of supply",
                    evidence=self.evidence("getTokenAccountsByOwner", held, address=owner))

    def _slot_burned_supply(self) -> Slot:
        held = self.balance_held_by(INCINERATOR)
        if held is None:
            return not_checked("burned_supply", R_RPC_ERROR,
                               "the incinerator balance could not be read")
        share = _pct(held, self.supply) or 0.0
        self.facts["burned_supply"] = {"amount": str(held), "share_pct": share}
        return Slot(slot="burned_supply", state=FILLED, material=False,
                    value=f"{share}% of supply sits at the incinerator {INCINERATOR}; "
                          f"burns on Solana also leave the supply figure itself",
                    evidence=self.evidence("getTokenAccountsByOwner", held,
                                           address=INCINERATOR))

    def _slot_liquidity(self) -> Slot:
        if not self.pool:
            self.facts["liquidity"] = None
            return not_checked("liquidity", R_NO_POOL,
                               "no pool in the asset file; pools are never discovered")
        held = self.balance_held_by(self.pool)
        if held is None:
            return not_checked("liquidity", R_RPC_ERROR,
                               f"the balance of the declared pool {self.pool} could not "
                               f"be read")
        share = _pct(held, self.supply)
        self.facts["liquidity"] = {"pool": self.pool, "token_in_pool": str(held),
                                   "token_in_pool_pct": share, "lp_burnt_pct": None}
        if not held:
            value = (f"the declared pool {self.pool} owns no account of this token; "
                     f"its vaults may sit under a separate authority")
        else:
            value = f"{share}% of supply sits in accounts the declared pool {self.pool} owns"
        return Slot(slot="liquidity", state=FILLED, material=False, value=value,
                    evidence=self.evidence("getTokenAccountsByOwner", held, address=self.pool))


def read_solana_coin(config: dict, address: str, pool: str | None = None,
                     rpc=None) -> ChainReport:
    """Read every coin slot for a Solana mint, or say why it could not be read."""
    if rpc is None:
        configured = (config.get("rpc") or {}).get(CHAIN)
        urls = [configured] if isinstance(configured, str) and configured else []
        rpc = SolanaRpc(urls + [u for u in DEFAULT_RPCS if u not in urls])
    return SolanaCoinReader(rpc, address, pool=pool).read()
