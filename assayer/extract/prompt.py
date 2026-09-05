"""Prompt assembly from a snapshot. Deterministic: same snapshot, same prompt.

The model is given the canonical texts, the sha256 of each, the fixed
questionnaire, and one instruction it cannot talk its way around: every filled
slot needs a verbatim span from a named document. It is not asked for character
offsets — those are computed by the gate, which can be trusted to count.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import util
from ..corpus import Corpus
from ..model import LEGAL_SLOTS, SLOT_QUESTIONS
from ..verify import sources as sources_mod
from .schema import CONTRACT

DEFAULT_MAX_CHARS = 120_000

RULES = """\
RULES
1. Answer with one JSON object and nothing else. No prose before or after it.
2. Answer only the slots listed below. Do not invent slots.
3. state=filled requires evidence: kind=document, the doc_id, that document's
   sha256 copied exactly, and a verbatim span of at least 8 characters taken
   from the text given to you. Copy the span character for character. A quote
   that is not present in the text will be detected and the slot discarded.
4. If a document does not answer a slot, use state=not_found with
   kind=absence, listing the doc_ids you searched and the search terms you used.
   An absent answer is a result, not a failure. Do not guess.
5. If you cannot answer for a reason about the material itself (unreadable
   text, wrong language, missing page), use state=not_checked with a reason.
6. Never use knowledge from outside these documents. There is no evidence kind
   for it, and any claim without evidence is removed before a human sees it.
7. Describe what the documents say. Do not assess, rate, rank or advise, and do
   not use words like safe, risky, scam, reliable, buy or sell.
8. Quote the document's own language for the value where you can. Keep each
   value to one or two short sentences.
"""

CHALLENGE_RULES = """\
CHALLENGE (optional second part of the same JSON object)
Add "challenge": a list of concrete ways the arrangement described in these
documents can end badly for a holder. Each item needs "claim" and the same
document evidence as above: doc_id, sha256 and a verbatim quote. Items without
a quote are discarded before anybody reads them, so a general observation with
no textual basis is wasted output. Ground every item in a specific clause.
"""


@dataclass
class Prompt:
    text: str
    hash: str
    documents: list[dict] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)


def build(snapshot: dict, corpus: Corpus, *, max_chars: int = DEFAULT_MAX_CHARS,
          challenge: bool = False) -> Prompt:
    asset = snapshot.get("asset") or {}
    stored = sources_mod.stored_documents(snapshot)
    parts: list[str] = []
    manifest: list[dict] = []
    truncated: list[str] = []

    parts.append("You are reading the documents of a tokenised real-world asset "
                 "and filling a fixed questionnaire.")
    parts.append(f"ASSET  {asset.get('label')}  "
                 f"{asset.get('chain')}:{asset.get('address')}")
    parts.append(RULES)
    if challenge:
        parts.append(CHALLENGE_RULES)

    parts.append("SLOTS")
    for name in LEGAL_SLOTS:
        parts.append(f"  {name}: {SLOT_QUESTIONS[name]}")

    parts.append("")
    parts.append("RESPONSE SHAPE")
    parts.append(json.dumps(CONTRACT, indent=2, ensure_ascii=False))

    parts.append("")
    parts.append("DOCUMENTS")
    for doc_id, sha in sorted(stored.items()):
        doc = corpus.get(sha)
        text = doc.text
        meta = doc.meta
        cut = text[:max_chars]
        if len(text) > max_chars:
            truncated.append(doc_id)
        readable = doc.readable
        manifest.append({"doc_id": doc_id, "sha256": sha,
                         "chars": len(text), "chars_given": len(cut),
                         "pages": meta.get("pages"),
                         "truncated": len(text) > max_chars,
                         "readable": readable,
                         "degraded": bool(meta.get("degraded"))})
        header = (f"--- doc_id={doc_id} sha256={sha} "
                  f"pages={meta.get('pages')} chars={len(text)}"
                  + (" TRUNCATED" if len(text) > max_chars else "")
                  + ("" if readable else " NO TEXT LAYER") + " ---")
        parts.append(header)
        if readable:
            parts.append(cut)
        else:
            # Saying "nothing here" invites an absence claim, which would be a
            # statement about the asset drawn from the tool's own blindness.
            parts.append("(this document produced no text: it is probably a scan. "
                         "You cannot read it, so do not quote it and do not claim "
                         "that anything is absent from it. If a slot depends on "
                         "this document, answer state=not_checked with "
                         "reason=document_has_no_text_layer.)")
        parts.append(f"--- end doc_id={doc_id} ---")

    if not stored:
        parts.append("(no documents reached the corpus for this snapshot)")

    text = "\n".join(parts) + "\n"
    return Prompt(text=text, hash=util.sha256_text(text), documents=manifest,
                  truncated=truncated)
