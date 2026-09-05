"""The JSON contract with the model, and strict parsing of what comes back.

The model answers a fixed questionnaire. There are no open questions: an open
question produces prose that cannot be diffed against the same question asked
next month. Every filled slot must carry a verbatim quote from a named document;
the offsets are computed here, not trusted from the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .. import util
from ..model import (
    E_ABSENCE, E_DOCUMENT, FILLED, LEGAL_SLOTS, NOT_CHECKED, NOT_FOUND,
    SLOT_QUESTIONS, instrument_category,
)

MODEL_STATES = (FILLED, NOT_FOUND, NOT_CHECKED)
MODEL_EVIDENCE_KINDS = (E_DOCUMENT, E_ABSENCE)

CONTRACT = {
    "slots": [
        {
            "slot": "<one of the fixed slot names>",
            "state": "filled | not_found | not_checked",
            "value": "<one short sentence, plain language, no advice>",
            "evidence": {
                "kind": "document | absence",
                "doc_id": "<doc_id, for kind=document>",
                "sha256": "<sha256 of that document, copied exactly>",
                "quote": "<verbatim span from the document, 8 chars or more>",
                "page": "<page number if the document has pages>",
                "checked_docs": ["<doc_id…>, for kind=absence"],
                "patterns": ["<search terms you looked for, for kind=absence>"],
            },
            "reason": "<for state=not_checked only>",
        }
    ]
}


@dataclass
class ParseResult:
    slots: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    challenge: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Pull one JSON object out of a model response. No prose is tolerated."""
    if text is None:
        raise ValueError("empty response")
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    candidates += [m.strip() for m in _FENCE.findall(text)]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise ValueError(f"no parseable JSON object in the response: {last_error}")


def _clean_string(value: Any, limit: int = 600) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def parse(text: str) -> ParseResult:
    """Validate the response shape. Anything unexpected becomes an error, not a guess."""
    result = ParseResult()
    try:
        payload = extract_json(text)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result
    if not isinstance(payload, dict):
        result.errors.append("top level is not a JSON object")
        return result
    raw_slots = payload.get("slots")
    if not isinstance(raw_slots, list):
        result.errors.append("missing 'slots' array")
        return result

    seen: set[str] = set()
    for index, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            result.errors.append(f"slots[{index}] is not an object")
            continue
        name = str(item.get("slot") or "").strip()
        if name not in LEGAL_SLOTS:
            result.errors.append(f"slots[{index}] unknown slot {name!r}")
            continue
        if name in seen:
            result.errors.append(f"slot {name!r} appears more than once")
            continue
        seen.add(name)
        state = str(item.get("state") or "").strip()
        if state not in MODEL_STATES:
            result.errors.append(f"slot {name!r} has state {state!r}")
            continue
        entry: dict[str, Any] = {"slot": name, "state": state}
        value = _clean_string(item.get("value"))
        if value and name == "instrument_type":
            category = instrument_category(value)
            if category and not value.lower().startswith(category):
                value = f"{category}, {value}"
        if value:
            entry["value"] = value
        note = _clean_string(item.get("note"))
        if note:
            entry["note"] = note
        if state == NOT_CHECKED:
            entry["reason"] = _clean_string(item.get("reason")) or "model_declined"
            result.slots.append(entry)
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            entry["evidence"] = None
            result.slots.append(entry)
            continue
        kind = str(evidence.get("kind") or "").strip()
        cleaned: dict[str, Any] = {"kind": kind}
        if kind == E_DOCUMENT:
            cleaned["doc_id"] = _clean_string(evidence.get("doc_id"), 120)
            cleaned["sha256"] = _clean_string(evidence.get("sha256"), 80)
            quote = evidence.get("quote")
            cleaned["quote"] = str(quote).strip() if quote is not None else None
            page = evidence.get("page")
            locator = {"type": "char_range"}
            if isinstance(page, (int, float)) and not isinstance(page, bool):
                locator["page"] = int(page)
            elif isinstance(page, str) and page.strip().isdigit():
                locator["page"] = int(page.strip())
            for key in ("start", "end"):
                candidate = evidence.get(key)
                if isinstance(candidate, (int, float)) and not isinstance(candidate,
                                                                         bool):
                    locator[key] = int(candidate)
            raw_locator = evidence.get("locator")
            if isinstance(raw_locator, dict):
                for key in ("start", "end", "page"):
                    candidate = raw_locator.get(key)
                    if isinstance(candidate, (int, float)) and \
                            not isinstance(candidate, bool):
                        locator[key] = int(candidate)
                if str(raw_locator.get("type") or "") == "bbox" and \
                        isinstance(raw_locator.get("bbox"), list):
                    locator = {"type": "bbox", "bbox": raw_locator["bbox"],
                               "page": locator.get("page")}
            cleaned["locator"] = locator
        elif kind == E_ABSENCE:
            docs = evidence.get("checked_docs")
            patterns = evidence.get("patterns")
            cleaned["checked_docs"] = [str(d).strip() for d in docs
                                       if str(d).strip()] if isinstance(docs, list) \
                else []
            cleaned["patterns"] = [str(x).strip() for x in patterns
                                   if str(x).strip()] if isinstance(patterns, list) \
                else []
        else:
            result.errors.append(f"slot {name!r} evidence kind {kind!r} not allowed")
            entry["evidence"] = None
            result.slots.append(entry)
            continue
        entry["evidence"] = cleaned
        result.slots.append(entry)

    challenge = payload.get("challenge")
    if isinstance(challenge, list):
        for item in challenge:
            if not isinstance(item, dict):
                continue
            claim = _clean_string(item.get("claim"), 400)
            if not claim:
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, dict):
                # Kept deliberately: the gate cuts it and says so, which is how a
                # reader learns how much of the answer had nothing behind it.
                result.challenge.append({"claim": claim, "evidence": None})
                continue
            result.challenge.append({
                "claim": claim,
                "evidence": {
                    "kind": E_DOCUMENT,
                    "doc_id": _clean_string(evidence.get("doc_id"), 120),
                    "sha256": _clean_string(evidence.get("sha256"), 80),
                    "quote": str(evidence.get("quote") or "").strip() or None,
                    "locator": {"type": "char_range"},
                },
            })
    return result


def questionnaire() -> list[dict]:
    return [{"slot": name, "question": SLOT_QUESTIONS[name]} for name in LEGAL_SLOTS]


def extraction_hash(snapshot_hash: str, prompt_hash: str, slots: list[dict],
                    agent: dict) -> str:
    return util.stable_hash({"snapshot_hash": snapshot_hash,
                             "prompt_hash": prompt_hash,
                             "slots": slots, "agent": agent})
