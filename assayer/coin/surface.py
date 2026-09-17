"""Which functions each coin slot looks for. One table, read from one place.

A reader that keeps its own private list of function names is a reader that will
one day look for a slightly different set from every other reader and report a
slightly different sheet — not with a crash, but quietly. So the sets live here
and :mod:`assayer.coin.reader` imports them, and anything else that has to ask
the same question of the same bytecode can be generated from this table rather
than retyping it.

``REUSED`` mirrors the four power slots the documents profile already owns, in
:mod:`assayer.snapshot.chain`. Those tuples are written inline in the slot
builders there, so this is genuinely a mirror rather than the original — and
``tests/test_coin.py`` holds it honest by building bytecode out of exactly these
signatures and asserting each builder detects it.
"""

from __future__ import annotations

# Slots this package reads on its own.
OWN: dict[str, dict[str, tuple[str, ...]]] = {
    "ownership": {
        "read": ("owner", "get_owner", "admin", "get_admin"),
        "write": ("transfer_ownership",),
    },
    "trading_switch": {
        "write": ("enable_trading", "open_trading", "start_trading",
                  "set_trading_enabled"),
        "read": ("trading_enabled", "trading_active", "trading_open"),
    },
    "transfer_tax": {
        "read": ("tax_in", "tax_out", "fee_in", "fee_out", "total_fees",
                 "tax_fee", "tax_fee_private"),
        "write": ("set_fee", "set_fees", "set_fee_in", "set_fee_out",
                  "set_taxes"),
    },
    "transfer_limits": {
        "per_transfer": ("max_transaction_amount", "max_tx_amount",
                         "max_tx_amount_private"),
        "per_wallet": ("max_wallet", "max_wallet_amount", "max_wallet_size"),
        "write": ("set_max_tx", "set_max_wallet", "remove_limits"),
        "flag": ("cooldown_enabled",),
    },
    "balances": {"read": ("balance_of", "total_supply")},
    "liquidity": {"read": ("token0", "token1", "total_supply", "balance_of")},
}

# The four the documents profile already reads, mirrored here.
REUSED: dict[str, dict[str, tuple[str, ...]]] = {
    "mint_authority": {
        "write": ("mint_to", "mint_self", "issue", "increase_supply"),
    },
    "burn_authority": {
        "foreign": ("burn_from_address", "burn_from", "destroy_black_funds",
                    "wipe_frozen_address", "burn_black_funds", "confiscate"),
        "own": ("burn_self", "redeem"),
    },
    "pause": {"write": ("pause", "unpause"), "read": ("paused",)},
    "freeze": {
        "write": ("blacklist", "add_blacklist", "freeze", "block_user",
                  "set_address_frozen", "add_to_blocked_list"),
        "read": ("is_blacklisted", "is_blacklisted_alt", "is_frozen", "frozen"),
    },
}

GROUPS: dict[str, dict[str, tuple[str, ...]]] = {**OWN, **REUSED}


def keys_used() -> tuple[str, ...]:
    """Every signature key either reader needs, for the selector export."""
    out: set[str] = set()
    for groups in GROUPS.values():
        for names in groups.values():
            out.update(names)
    out.update(("name", "symbol", "decimals", "total_supply", "balance_of",
                "get_threshold", "get_owners", "get_min_delay", "delay",
                "minimum_delay", "implementation", "upgrade_to",
                "upgrade_to_and_call", "proxiable_uuid"))
    return tuple(sorted(out))
