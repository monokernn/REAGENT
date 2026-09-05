"""Provenance of documents: whitelist, redirects, and links found inside them.

Three rules, all mechanical:

* a document is fetched only from a domain a human put in ``config.toml``;
* a redirect that leaves the whitelist aborts the fetch — it is not followed
  and then judged afterwards;
* links inside documents are never fetched. They surface as ``suggested_sources``
  and stay inert until somebody adds the domain by hand.
"""

from __future__ import annotations

from typing import Iterable

from ..snapshot.docs import domain_allowed, host_of

REFUSAL_REASONS = ("domain_not_whitelisted", "redirect_outside_whitelist",
                   "document_over_size_limit")


def review(snapshot: dict, allowed: Iterable[str]) -> dict:
    """Provenance report for one snapshot: refusals, failures, redirects, links."""
    allowed = list(allowed)
    refused: list[dict] = []
    failed: list[dict] = []
    off_whitelist_redirects: list[dict] = []

    for document in snapshot.get("documents", []):
        state = document.get("state")
        if state == "refused":
            refused.append({"doc_id": document.get("doc_id"),
                            "url": document.get("url"),
                            "reason": document.get("reason"),
                            "detail": document.get("detail")})
            continue
        if state == "error":
            failed.append({"doc_id": document.get("doc_id"),
                           "url": document.get("url"),
                           "reason": document.get("reason"),
                           "detail": document.get("detail")})
            continue
        for hop in list(document.get("redirects") or []) + \
                [document.get("final_url") or document.get("url")]:
            if hop and not domain_allowed(hop, allowed):
                off_whitelist_redirects.append({
                    "doc_id": document.get("doc_id"), "url": hop,
                    "host": host_of(hop)})

    return {
        "refused": refused,
        "failed": failed,
        "off_whitelist_redirects": off_whitelist_redirects,
        "suggested_sources": list(snapshot.get("suggested_sources") or []),
        "allowed_domains": sorted(str(d).lower() for d in allowed),
    }


def unavailable_reasons(snapshot: dict) -> dict[str, str]:
    """doc_id -> reason, for every document that did not make it into the corpus."""
    out: dict[str, str] = {}
    for document in snapshot.get("documents", []):
        if document.get("state") != "stored":
            out[str(document.get("doc_id"))] = str(document.get("reason")
                                                   or document.get("state"))
    return out


def stored_documents(snapshot: dict) -> dict[str, str]:
    """doc_id -> sha256 for everything the snapshot actually holds."""
    out: dict[str, str] = {}
    for document in snapshot.get("documents", []):
        if document.get("state") == "stored" and document.get("sha256"):
            out[str(document.get("doc_id"))] = str(document["sha256"])
    return out
