"""The roster is a claim about the program, so it is held to the program.

A crew listed in the documentation and nowhere else drifts the moment a slot is
added: the new reading belongs to nobody, and the text keeps saying it belongs
to somebody. These tests fail instead.
"""

from assayer import model
from assayer.crew import BY_ID, CREW, owner_of


def _claimed(field):
    return [slot for agent in CREW for slot in getattr(agent, field)]


def test_every_coin_reading_has_exactly_one_owner():
    claimed = _claimed("coin_slots")
    assert sorted(claimed) == sorted(model.COIN_SLOTS)
    assert len(claimed) == len(set(claimed)), "a slot is claimed twice"


def test_every_document_slot_has_exactly_one_owner():
    claimed = _claimed("doc_slots")
    assert sorted(claimed) == sorted(model.ALL_SLOTS)
    assert len(claimed) == len(set(claimed)), "a slot is claimed twice"


def test_owner_lookup_agrees_with_the_roster():
    for slot in model.COIN_SLOTS:
        assert slot in owner_of(slot, "coin").coin_slots
    for slot in model.ALL_SLOTS:
        assert slot in owner_of(slot, "docs").doc_slots
    assert owner_of("no_such_slot") is None


def test_only_the_documents_role_is_handed_a_model():
    """The coin profile must stay free, and that is a property of the roster."""
    paid = [agent.id for agent in CREW if agent.calls_model]
    assert paid == ["docket"]
    assert not any(BY_ID[agent_id].coin_slots for agent_id in paid), (
        "an agent that calls a model owns a coin reading: the coin profile "
        "would stop being free")


def test_identifiers_are_unique_and_stable():
    ids = [agent.id for agent in CREW]
    assert len(ids) == len(set(ids))
    assert all(agent.id == agent.name.lower() for agent in CREW)
    # Crucible is handed an instruction rather than a question, so only the
    # agents that own readings are checked for one.
    assert all(agent.asks.endswith("?") for agent in CREW
               if agent.coin_slots or agent.doc_slots)
