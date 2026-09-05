"""Quote verification: the claim against the bytes it claims to come from.

A quote either appears in ``corpus/<sha256>/canonical.txt`` or it does not.
When the offsets have drifted but the sentence is really there, the locator is
repaired and the slot stands. When the sentence is not there at all, the slot is
rejected and the rejection is printed: a reader is entitled to see the tool catch
its own model inventing a source.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import util
from ..corpus import Corpus
from ..model import J_BAD_EVIDENCE, J_FABRICATED, J_UNKNOWN_DOC, Evidence, Locator
from ..snapshot.docs import find_quote

OK = "ok"
COMPUTED = "locator_computed"   # none was offered; the gate counted the offsets
REPAIRED = "locator_drift"      # one was offered and it pointed at the wrong place
FABRICATED = J_FABRICATED
UNKNOWN_DOC = J_UNKNOWN_DOC
MALFORMED = J_BAD_EVIDENCE

MIN_QUOTE_CHARS = 8


@dataclass
class QuoteCheck:
    outcome: str
    locator: Locator | None = None
    detail: str | None = None
    degraded: bool = False

    @property
    def passed(self) -> bool:
        return self.outcome in (OK, COMPUTED, REPAIRED)


def check(evidence: Evidence, corpus: Corpus,
          known_sha: set[str] | None = None) -> QuoteCheck:
    """Verify one piece of document evidence against the canonical text."""
    if not evidence.sha256 or not evidence.quote:
        return QuoteCheck(
            MALFORMED,
            detail="document evidence needs a sha256 and a quote")
    if len(evidence.quote.strip()) < MIN_QUOTE_CHARS:
        return QuoteCheck(MALFORMED,
                          detail=f"the quote is shorter than {MIN_QUOTE_CHARS} characters")
    if known_sha is not None and evidence.sha256 not in known_sha:
        return QuoteCheck(UNKNOWN_DOC,
                          detail="this sha256 is not among the snapshot documents")
    try:
        doc = corpus.get(evidence.sha256)
    except KeyError as exc:
        return QuoteCheck(UNKNOWN_DOC, detail=str(exc))

    text = doc.text
    degraded = bool(doc.meta.get("degraded"))
    locator = evidence.locator
    wanted = util.normalize_for_compare(evidence.quote)
    offered = (locator is not None
               and ((locator.type == "char_range" and locator.start is not None
                     and locator.end is not None)
                    or (locator.type == "bbox" and locator.bbox)))

    if locator is not None and locator.type == "char_range" and \
            locator.start is not None and locator.end is not None:
        cut = text[locator.start:locator.end]
        if util.normalize_for_compare(cut) == wanted:
            page = doc.page_of(locator.start)
            return QuoteCheck(OK, locator=Locator(type="char_range",
                                                  start=locator.start,
                                                  end=locator.end, page=page),
                              degraded=degraded)

    found = find_quote(text, evidence.quote)
    if found is None:
        return QuoteCheck(FABRICATED,
                          detail="the quote does not occur in the canonical text")
    start, end = found
    repaired = Locator(type="char_range", start=start, end=end,
                       page=doc.page_of(start))
    if locator is not None and locator.type == "bbox":
        return QuoteCheck(OK, locator=repaired, degraded=True,
                          detail="the bbox locator was resolved by text search")
    if not offered:
        return QuoteCheck(COMPUTED, locator=repaired, degraded=degraded,
                          detail="no offsets were offered; the gate located the quote")
    return QuoteCheck(REPAIRED, locator=repaired, degraded=degraded,
                      detail="the offsets drifted; the quote is present and the "
                             "locator was repaired")


def check_absence(evidence: Evidence, corpus: Corpus,
                  known_sha: dict[str, str]) -> QuoteCheck:
    """Re-run an absence claim: the patterns must really be missing.

    ``known_sha`` maps doc_id to sha256 for the documents in the snapshot.
    """
    if not evidence.checked_docs:
        return QuoteCheck(
            MALFORMED,
            detail="absence evidence names no documents")
    if not evidence.patterns:
        return QuoteCheck(
            MALFORMED,
            detail="absence evidence names no search patterns")
    for doc_id in evidence.checked_docs:
        sha = known_sha.get(doc_id)
        if sha is None:
            return QuoteCheck(UNKNOWN_DOC,
                              detail=f"document {doc_id!r} is not in this snapshot")
        try:
            text = corpus.get(sha).text
        except KeyError as exc:
            return QuoteCheck(UNKNOWN_DOC, detail=str(exc))
        folded = util.normalize_for_compare(text)
        for pattern in evidence.patterns:
            needle = util.normalize_for_compare(str(pattern))
            if needle and needle in folded:
                return QuoteCheck(
                    FABRICATED,
                    detail=(f"absence was claimed, but {pattern!r} "
                            f"occurs in {doc_id}"))
    return QuoteCheck(OK)
