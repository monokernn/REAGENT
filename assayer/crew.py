"""The crew: who owns which question, and which slots answer it.

One place where the division of work is written down, so that the roster in the
documentation and the roster the programs act on cannot drift apart.

Each agent owns one question and is handed only the evidence that bears on it.
None of them fetches anything: a snapshot is collected once, by scripts, and
given to all of them at the same moment, so two readings can never rest on two
different observations of the same contract.

Only CRUCIBLE writes prose, and only in the documents profile, where the notes
and the summary are produced in one call rather than one per agent. Eight calls
would cost eight times as much to answer the same questionnaire and would let
two answers disagree about the same sentence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    beat: str
    asks: str
    coin_slots: tuple[str, ...] = ()
    doc_slots: tuple[str, ...] = ()
    calls_model: bool = False


CREW: tuple[Agent, ...] = (
    Agent(
        id="slate",
        name="Slate",
        beat="identity and origin",
        asks="Is this a deployed token on this chain, and what does it call "
             "itself?",
    ),
    Agent(
        id="clasp",
        name="Clasp",
        beat="contract powers",
        asks="What does the deployed code still permit somebody to do to your "
             "balance?",
        coin_slots=("ownership", "mint_authority", "burn_authority", "pause",
                    "freeze", "upgradeable", "trading_switch", "transfer_tax",
                    "transfer_limits"),
        doc_slots=("mint_authority", "burn_authority", "pause", "upgradeable",
                   "freeze"),
    ),
    Agent(
        id="gauge",
        name="Gauge",
        beat="liquidity and exits",
        asks="How deep is the pool you would have to leave through, and can "
             "its LP still be moved?",
        coin_slots=("liquidity",),
    ),
    Agent(
        id="tare",
        name="Tare",
        beat="supply and burn",
        asks="Where does the supply sit, and how much of it can nobody move?",
        coin_slots=("burned_supply",),
        doc_slots=("supply_vs_reserves",),
    ),
    Agent(
        id="sift",
        name="Sift",
        beat="holders and control",
        asks="How much of the supply sits with whoever still holds the keys?",
        coin_slots=("owner_holdings",),
    ),
    Agent(
        id="pulse",
        name="Pulse",
        beat="price and attestation",
        asks="What price source does the contract read, and how old is the "
             "reserve report?",
        doc_slots=("oracle", "attestation_freshness"),
    ),
    Agent(
        id="docket",
        name="Docket",
        beat="documents and RWA",
        asks="What do the issuer's own documents say, and does every quote "
             "survive the gate?",
        doc_slots=("instrument_type", "issuer", "holder_rights", "insolvency",
                   "custodian", "corporate_actions", "redemption",
                   "restrictions"),
        calls_model=True,
    ),
    Agent(
        id="crucible",
        name="Crucible",
        beat="the whole picture",
        asks="Put the others together without adding anything they did not "
             "find.",
    ),
)

BY_ID = {agent.id: agent for agent in CREW}


def owner_of(slot: str, profile: str = "coin") -> Agent | None:
    """Which agent answers this slot, or None if no one claims it."""
    field = "coin_slots" if profile == "coin" else "doc_slots"
    for agent in CREW:
        if slot in getattr(agent, field):
            return agent
    return None
