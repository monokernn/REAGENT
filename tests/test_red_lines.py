"""Tests 1-3: the red lines. These are blocking by design.

Each of these guards a property that no amount of care would hold on its own:
that the codebase cannot sign or send anything, that the output cannot drift into
advice, and that a gap in the tool cannot be printed as a fact about the asset.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assayer.render import H_NOT_CHECKED, H_NOT_FOUND, H_POWERS, H_REJECTED
from assayer.verify import language

PACKAGE = Path(__file__).resolve().parents[1] / "assayer"

# Built by concatenation so that this file itself does not contain the tokens
# it forbids, which would make the check pass on its own text.
FORBIDDEN_TOKENS = (
    "eth_send" + "Transaction",
    "eth_send" + "RawTransaction",
    "send" + "Transaction",
    "send" + "RawTransaction",
    "PRIVATE" + "_KEY",
    "sign_" + "transaction",
    "signTransaction",
    "from_" + "mnemonic",
    "keystore",
)

FORBIDDEN_IMPORTS = ("eth_account", "eth_keys", "coincurve", "ecdsa", "bip32",
                     "bip39", "web3.auto", "hdwallet", "mnemonic")


def python_sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_1_no_signing_and_no_transactions() -> None:
    """No private keys, no transaction submission, anywhere in the package."""
    offences: list[str] = []
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offences.append(f"{path.name}: {token}")
        for module in FORBIDDEN_IMPORTS:
            if f"import {module}" in text or f"from {module}" in text:
                offences.append(f"{path.name}: imports {module}")
    assert not offences, "red line crossed: " + "; ".join(offences)


def test_1b_rpc_client_refuses_write_methods() -> None:
    """The client is not merely unused for writes; it declines them."""
    from assayer.snapshot.chain import READ_ONLY_METHODS, JsonRpc, RpcError

    client = JsonRpc("http://127.0.0.1:1")  # never contacted
    for method in ("eth_" + "sendTransaction", "eth_sign", "personal_unlockAccount"):
        with pytest.raises(RpcError):
            client.call(method, [])
    assert all(m.startswith(("eth_", "net_")) for m in READ_ONLY_METHODS)
    assert not any("send" in m or "sign" in m for m in READ_ONLY_METHODS)


def test_1c_only_the_agent_module_shells_out() -> None:
    """At most one place may start a process: the agent. Everything else is local."""
    users = {path.name for path in python_sources()
             if "subprocess" in path.read_text(encoding="utf-8")}
    assert users <= {"agent.py"}, users


def _dossier_with(value: str) -> dict:
    return {
        "asset": {"label": "Assay Note", "chain": "ethereum", "address": "0x11"},
        "snapshot_ts": "20260201T000000Z",
        "slots": {
            "legal": [{"slot": "holder_rights", "state": "filled", "value": value,
                       "evidence": {"kind": "document", "doc_id": "prospectus",
                                    "sha256": "ab", "quote": "verbatim span"}}],
            "onchain": [],
        },
        "counters": {"slots_filled": 1, "slots_total": 1},
    }


def test_2_language_linter_fails_a_forbidden_word(config: dict) -> None:
    """A dossier that has drifted towards advice fails; it is not rewritten."""
    clean = language.lint(_dossier_with("holders have no voting rights"), "", config)
    assert [v for v in clean if v.kind == "vocabulary"] == []

    drifted = _dossier_with("this note is safe to buy and the issuer is reliable")
    violations = [v for v in language.lint(drifted, "", config)
                  if v.kind == "vocabulary"]
    assert violations, "forbidden vocabulary passed the linter"
    words = " ".join(v.detail for v in violations)
    assert "safe" in words and "buy" in words


def test_2b_verbatim_quotes_are_exempt(config: dict) -> None:
    """Quoting a prospectus that says "buy" is the job, not a violation."""
    dossier = _dossier_with("holders may not require redemption")
    dossier["slots"]["legal"][0]["evidence"]["quote"] = (
        "Investors should not buy the Notes unless they are able to sustain a loss")
    assert [v for v in language.lint(dossier, "", config)
            if v.kind == "vocabulary"] == []


def test_2c_numeric_scores_are_rejected(config: dict) -> None:
    dossier = _dossier_with("holders have no voting rights")
    dossier["counters"]["risk_score"] = 7
    dossier["summary"] = "overall 7/10 on the issuer power scale"
    violations = [v for v in language.lint(dossier, "", config) if v.kind == "score"]
    assert violations


DOSSIER_THREE_STATES = {
    "asset": {"label": "Assay Note", "chain": "ethereum", "address": "0x11"},
    "snapshot_ts": "20260201T000000Z",
    "slots": {
        "legal": [
            {"slot": "insolvency", "state": "not_found",
             "evidence": {"kind": "absence", "checked_docs": ["prospectus"],
                          "patterns": ["insolvency"]}},
            {"slot": "custodian", "state": "not_checked",
             "reason": "doc_403"},
            {"slot": "redemption", "state": "rejected",
             "reason": "fabricated_quote"},
        ],
        "onchain": [],
    },
    "counters": {"slots_filled": 0, "slots_total": 3},
}

GOOD_RENDER = f"""ASSAY  ·  Assay Note

{H_NOT_FOUND}
  ✗ insolvency: no such section. Documents checked: 1.

{H_NOT_CHECKED}
  ? custodian: doc_403

{H_REJECTED}
  ⨯ redemption: fabricated_quote
"""

MERGED_RENDER = f"""ASSAY  ·  Assay Note

{H_NOT_FOUND}
  ✗ insolvency: no such section. Documents checked: 1.
  ? custodian: doc_403

{H_REJECTED}
  ⨯ redemption: fabricated_quote
"""


def test_3_not_found_and_not_checked_may_not_share_a_section() -> None:
    """A fact about the asset and a failure of the tool are never one line apart."""
    assert language.check_sections(DOSSIER_THREE_STATES, GOOD_RENDER) == []

    violations = language.check_sections(DOSSIER_THREE_STATES, MERGED_RENDER)
    assert violations, "merged sections passed the linter"
    detail = " ".join(str(v) for v in violations)
    assert "custodian" in detail
    assert H_NOT_CHECKED in detail or H_NOT_FOUND in detail


def test_3b_onchain_absence_belongs_to_the_powers_section() -> None:
    """An absent on-chain power is a fact about bytecode, not a documentary gap."""
    dossier = {
        "slots": {
            "legal": [],
            "onchain": [{"slot": "pause", "state": "not_found",
                         "value": "no pause function in the deployed code"}],
        },
    }
    good = f"{H_POWERS}\n  ○ pause: no pause function in the deployed code\n"
    bad = f"{H_NOT_FOUND}\n  ✗ pause: no pause function in the deployed code\n"
    assert language.check_sections(dossier, good) == []
    assert language.check_sections(dossier, bad)


@pytest.mark.parametrize("name", ["README.md", "README.ru.md"])
def test_3c_readme_keeps_the_vocabulary(name: str) -> None:
    """Every README is held to the same words as the output, with two exceptions.

    "audit" and "investment advice" are each permitted once, inside the section
    that denies being either. Everything else on the list is out.
    """
    readme = PACKAGE.parent / name
    if not readme.exists():
        pytest.skip(f"{name} not present")
    text = readme.read_text(encoding="utf-8").lower()

    assert text.count("audit") + text.count("аудит") <= 2, \
        "the word audit appears more than once outside its denial"
    assert text.count("investment advice") + text.count("инвестиционный совет") <= 1

    for word in ("risk score", "риск-скор", "verified safe", "guaranteed",
                 "гарантирует", "надёжный актив", "рискованный", "audited",
                 "рекомендуем", "recommend"):
        assert word not in text, f"{name} uses {word!r}"

    # The vocabulary the plan asks for, present and used.
    wanted = ("surveillance", "assay", "adversarial",
              "дрейф" if name.endswith("ru.md") else "drift")
    for word in wanted:
        assert word in text, f"{name} never uses {word!r}"
