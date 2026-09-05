"""Vocabulary linter. Mechanical defence against wording drift.

The failure mode that would make this tool worse than nothing is a slow slide
from "the document says X" to "this asset is sound". Discipline does not survive
a year of edits; a blocking check does. A hit fails the run — it is never
rewritten, because rewriting would hide that the drift happened.

Verbatim source text is exempt: a prospectus is allowed to contain any word it
likes, and quoting it accurately is the whole job. Exempt keys are listed in
:data:`EXEMPT_KEYS` and nothing else escapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .. import util
from ..model import NOT_CHECKED, NOT_FOUND, REJECTED
from ..render import (
    H_NOT_CHECKED, H_NOT_FOUND, H_POWERS, H_REJECTED, SECTION_HEADERS,
)

# Keys whose values are somebody else's words, not the tool's.
EXEMPT_KEYS = frozenset({
    "quote", "excerpt", "label", "symbol", "name", "url", "final_url",
    "urls", "examples", "domain", "sources", "source", "raw", "what",
})

SCORE_KEY = re.compile(r"(?:^|[._])(?:score|rating|grade|verdict|recommendation)s?$",
                       re.I)
SCORE_TEXT = (
    re.compile(r"\b\d{1,3}\s*/\s*(?:5|10|100)\b"),
    re.compile(r"\b\d{1,2}\s+(?:из|of)\s+(?:5|10|100)\b", re.I),
    re.compile(r"\b(?:score|балл|рейтинг)\s*[:=]\s*\d", re.I),
)


@dataclass
class Violation:
    kind: str        # vocabulary | score | sections
    where: str
    detail: str
    excerpt: str | None = None

    def to_json(self) -> dict:
        out = {"kind": self.kind, "where": self.where, "detail": self.detail}
        if self.excerpt:
            out["excerpt"] = self.excerpt
        return out

    def __str__(self) -> str:
        tail = f" | {self.excerpt}" if self.excerpt else ""
        return f"{self.kind} @ {self.where}: {self.detail}{tail}"


def _word_pattern(word: str) -> re.Pattern:
    word = word.strip()
    if not word:
        return re.compile(r"(?!x)x")
    if " " in word:
        return re.compile(re.escape(word).replace(r"\ ", r"\s+"), re.I | re.U)
    return re.compile(rf"(?<![\w-]){re.escape(word)}(?![\w-])", re.I | re.U)


def check_vocabulary(dossier: dict, forbidden: Iterable[str],
                     exempt_keys: Iterable[str] = ()) -> list[Violation]:
    """Forbidden vocabulary anywhere in the tool's own wording."""
    skip = set(EXEMPT_KEYS) | {str(k) for k in exempt_keys}
    patterns = [(word, _word_pattern(str(word))) for word in forbidden
                if str(word).strip()]
    out: list[Violation] = []
    for path, text in util.flatten_strings(dossier, skip_keys=skip):
        for word, pattern in patterns:
            match = pattern.search(text)
            if match:
                out.append(Violation("vocabulary", path,
                                     f"forbidden word {word!r}",
                                     _around(text, match.start())))
    return out


def check_scores(dossier: dict) -> list[Violation]:
    """No numeric risk scores, in any field, under any name."""
    out: list[Violation] = []
    skip = set(EXEMPT_KEYS)
    for path, text in util.flatten_strings(dossier, skip_keys=skip):
        for pattern in SCORE_TEXT:
            match = pattern.search(text)
            if match:
                out.append(Violation("score", path, "looks like a numeric score",
                                     _around(text, match.start())))
    for path, _value in _walk_keys(dossier):
        if SCORE_KEY.search(path.split("[")[-1].split(".")[-1]):
            out.append(Violation("score", path, "field name promises a score"))
    return out


def _walk_keys(obj, path: str = ""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, value
            yield from _walk_keys(value, child)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk_keys(value, f"{path}[{index}]")


def _around(text: str, index: int, pad: int = 60) -> str:
    lo = max(0, index - pad)
    hi = min(len(text), index + pad)
    return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


# ---------------------------------------------------------------- sections

def split_sections(rendered: str) -> dict[str, list[str]]:
    """Rendered text split by its fixed headers."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in rendered.splitlines():
        stripped = line.strip()
        header = next((h for h in SECTION_HEADERS
                       if stripped == h or stripped.startswith(h + "  ")), None)
        if header:
            current = header
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


_SLOT_LINE = re.compile(r"^\s*(?:[^\s\w]{1,2}\s+)?([a-z_]+)\s*:", re.M)


def _slots_mentioned(lines: Iterable[str]) -> set[str]:
    """Slot identifiers that this section presents as its own entries."""
    found: set[str] = set()
    for line in lines:
        match = _SLOT_LINE.match(line)
        if match:
            found.add(match.group(1))
    return found


def check_sections(dossier: dict, rendered: str) -> list[Violation]:
    """``not_found``, ``not_checked`` and ``rejected`` must never share a section."""
    out: list[Violation] = []
    sections = split_sections(rendered)
    mentions = {header: _slots_mentioned(lines) for header, lines in sections.items()}

    slots = (dossier.get("slots") or {})
    expected: dict[str, str] = {}
    for group in ("legal", "onchain"):
        for slot in slots.get(group) or []:
            name = str(slot.get("slot"))
            state = str(slot.get("state"))
            if state == NOT_CHECKED:
                expected[name] = H_NOT_CHECKED
            elif state == REJECTED:
                expected[name] = H_REJECTED
            elif state == NOT_FOUND:
                expected[name] = H_NOT_FOUND if group == "legal" else H_POWERS

    for name, header in expected.items():
        present_in = [h for h, found in mentions.items() if name in found]
        if header not in present_in:
            out.append(Violation("sections", name,
                                 f"expected under «{header}», not printed there"))
        for other in present_in:
            if other != header and other in (H_NOT_FOUND, H_NOT_CHECKED,
                                             H_REJECTED, H_POWERS):
                out.append(Violation(
                    "sections", name,
                    f"printed under «{other}» but its state belongs "
                    f"under «{header}»"))

    return out


def lint(dossier: dict, rendered: str, config: dict) -> list[Violation]:
    language = (config or {}).get("language") or {}
    forbidden = language.get("forbidden") or []
    exempt = language.get("exempt_keys") or []
    return (check_vocabulary(dossier, forbidden, exempt)
            + check_scores(dossier)
            + check_sections(dossier, rendered))
