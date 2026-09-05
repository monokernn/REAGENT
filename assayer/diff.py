"""Drift as subtraction.

Because snapshots are structured and content-addressed, "what changed" needs no
subsystem: two snapshots are compared field by field. The same routine compares
two dossiers, so legal slots drift the same way on-chain powers do.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .model import VOLATILE_VALUE_SLOTS

_DIGITS = re.compile(r"\d[\d,._ ]*")


def _comparable(slot_name: str, value: Any) -> Any:
    """A slot value stripped of the digits that move on their own.

    ``attestation_freshness`` gains a day every day. Reporting that as drift
    every single run would bury the changes somebody actually decided on, so the
    shape of the sentence is compared instead: "overdue by N days" appearing is a
    change, "4 days old" becoming "11 days old" is not.
    """
    if slot_name in VOLATILE_VALUE_SLOTS and isinstance(value, str):
        return _DIGITS.sub("#", value)
    return value

TRACKED_FACTS = (
    ("code_hash", "deployed bytecode"),
    ("implementation_code_hash", "implementation bytecode"),
    ("implementation", "implementation address"),
    ("name", "token name"),
    ("symbol", "token symbol"),
    ("decimals", "decimals"),
    ("total_supply", "totalSupply"),
    ("beacon", "beacon address"),
)


def _change(kind: str, subject: str, before: Any, after: Any, *,
            material: bool = False, detail: str | None = None) -> dict:
    out: dict[str, Any] = {"kind": kind, "subject": subject,
                           "from": before, "to": after}
    if material:
        out["material"] = True
    if detail:
        out["detail"] = detail
    return out


def _docs_by_id(snapshot: dict) -> dict[str, dict]:
    return {str(d.get("doc_id")): d for d in snapshot.get("documents", [])}


def _slots_by_name(snapshot: dict, group: str = "onchain") -> dict[str, dict]:
    slots = (snapshot.get("slots") or {}).get(group) or []
    return {str(s.get("slot")): s for s in slots}


def diff_snapshots(before: dict, after: dict) -> list[dict]:
    """Every difference that a holder would want to know about."""
    changes: list[dict] = []

    old_docs, new_docs = _docs_by_id(before), _docs_by_id(after)
    for doc_id in sorted(set(old_docs) | set(new_docs)):
        old, new = old_docs.get(doc_id), new_docs.get(doc_id)
        if old is None:
            changes.append(_change("document_added", doc_id, None,
                                   new.get("sha256") if new else None))
            continue
        if new is None:
            changes.append(_change("document_removed", doc_id,
                                   old.get("sha256"), None))
            continue
        if old.get("state") != new.get("state"):
            changes.append(_change("document_state", doc_id, old.get("state"),
                                   new.get("state"),
                                   detail=new.get("reason") or old.get("reason")))
        old_sha, new_sha = old.get("sha256"), new.get("sha256")
        if old_sha and new_sha and old_sha != new_sha:
            same_version = (old.get("version") or None) == (new.get("version") or None)
            detail = None
            if same_version and old.get("version"):
                detail = (f"заявленная версия осталась {old.get('version')}, "
                          f"содержимое другое")
            elif same_version:
                detail = "в файле актива не заявлена версия, сравнивать нечего"
            changes.append(_change("document_replaced", doc_id, old_sha, new_sha,
                                   material=True, detail=detail))

    old_slots, new_slots = _slots_by_name(before), _slots_by_name(after)
    for name in sorted(set(old_slots) | set(new_slots)):
        old, new = old_slots.get(name), new_slots.get(name)
        if old is None or new is None:
            continue
        if old.get("state") != new.get("state"):
            changes.append(_change("slot_state", name, old.get("state"),
                                   new.get("state"), material=bool(new.get("material")),
                                   detail=new.get("reason")))
        elif _comparable(name, old.get("value")) != \
                _comparable(name, new.get("value")):
            changes.append(_change("slot_value", name, old.get("value"),
                                   new.get("value"),
                                   material=bool(new.get("material"))))

    old_facts = before.get("facts") or {}
    new_facts = after.get("facts") or {}
    for key, label in TRACKED_FACTS:
        old_value, new_value = old_facts.get(key), new_facts.get(key)
        if old_value != new_value and (old_value is not None or new_value is not None):
            changes.append(_change("fact", label, old_value, new_value,
                                   material=key.endswith("code_hash")
                                   or key == "implementation"))

    old_inj = {(f.get("doc_id"), f.get("technique"), f.get("excerpt"))
               for f in before.get("injections", [])}
    for finding in after.get("injections", []):
        key = (finding.get("doc_id"), finding.get("technique"),
               finding.get("excerpt"))
        if key not in old_inj:
            entry = _change("injection_new", str(finding.get("doc_id")), None,
                            finding.get("technique"), material=True)
            # Verbatim source text lives under "excerpt", which the language
            # linter exempts: it is the issuer's wording, not the tool's.
            entry["excerpt"] = finding.get("excerpt")
            changes.append(entry)

    document_kinds = {"document_added", "document_removed", "document_state",
                      "document_replaced"}
    if before.get("docs_hash") != after.get("docs_hash") and \
            not any(c["kind"] in document_kinds for c in changes):
        # Only worth a line when no per-document change already explains it.
        changes.append(_change("docs_hash", "document set",
                               before.get("docs_hash"), after.get("docs_hash")))
    return changes


def diff_dossiers(before: dict, after: dict) -> list[dict]:
    """Legal and on-chain slots side by side, plus the counters."""
    changes: list[dict] = []
    for group in ("legal", "onchain"):
        old = _slots_by_name(before, group)
        new = _slots_by_name(after, group)
        for name in sorted(set(old) | set(new)):
            a, b = old.get(name), new.get(name)
            if a is None:
                changes.append(_change("slot_added", name, None,
                                       b.get("state") if b else None))
                continue
            if b is None:
                changes.append(_change("slot_removed", name, a.get("state"), None))
                continue
            if a.get("state") != b.get("state"):
                changes.append(_change("slot_state", name, a.get("state"),
                                       b.get("state"),
                                       material=bool(b.get("material")),
                                       detail=b.get("reason")))
            elif a.get("value") != b.get("value"):
                changes.append(_change("slot_value", name, a.get("value"),
                                       b.get("value"),
                                       material=bool(b.get("material"))))
    old_counters = before.get("counters") or {}
    new_counters = after.get("counters") or {}
    for key in sorted(set(old_counters) | set(new_counters)):
        if old_counters.get(key) != new_counters.get(key):
            changes.append(_change("counter", key, old_counters.get(key),
                                   new_counters.get(key)))
    return changes


_SHORTEN = 60


def _short(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    if len(text) > _SHORTEN:
        return text[:_SHORTEN - 1] + "…"
    return text


def describe(change: dict) -> str:
    """One line per change, for the human-readable sheet."""
    kind = change.get("kind")
    subject = change.get("subject")
    before, after = change.get("from"), change.get("to")
    detail = change.get("detail") or change.get("excerpt")
    if kind == "document_replaced":
        line = (f"документ {subject}: содержимое подменено "
                f"({_short(before)[:12]} -> {_short(after)[:12]})")
    elif kind == "document_added":
        line = f"документ {subject}: добавлен в наблюдение"
    elif kind == "document_removed":
        line = f"документ {subject}: убран из наблюдения"
    elif kind == "document_state":
        line = f"документ {subject}: {_short(before)} -> {_short(after)}"
    elif kind == "slot_state":
        line = f"{subject}: состояние {_short(before)} -> {_short(after)}"
    elif kind == "slot_value":
        line = f"{subject}: {_short(before)} -> {_short(after)}"
    elif kind == "fact":
        line = f"{subject}: {_short(before)} -> {_short(after)}"
    elif kind == "injection_new":
        line = f"{subject}: новая находка, {_short(after)}"
    elif kind == "docs_hash":
        line = "набор документов изменился"
    elif kind == "counter":
        line = f"счётчик {subject}: {_short(before)} -> {_short(after)}"
    elif kind == "slot_added":
        line = f"{subject}: появился, состояние {_short(after)}"
    elif kind == "slot_removed":
        line = f"{subject}: исчез из документа"
    else:
        line = f"{kind} {subject}: {_short(before)} -> {_short(after)}"
    if detail:
        line += f" — {detail}"
    return line


def material(changes: Iterable[dict]) -> list[dict]:
    return [c for c in changes if c.get("material")]
