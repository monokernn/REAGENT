"""The coin profile: an asset read from its contract alone.

A memecoin has no prospectus, no custodian and no reserve report, so the legal
questionnaire has nothing to be filled from and the paid phase has nothing to
read. Everything a holder can be told about one is a fact about bytecode and
storage, and this package reads it with scripts — no model, no money, and the
same read-only JSON-RPC client the RWA profile uses.

The thesis does not change, only where the gap sits. For a tokenised security
the interface says one thing and the prospectus says another. For a coin the
interface says one thing and the contract says another: the page promises a
renounced owner and no cut on transfers, while ``owner()`` still answers a live
key and ``setFees`` is still in the dispatch table.
"""

from .reader import CoinReader, read_coin  # noqa: F401
