"""Tests 9-11: provenance. Where a document may come from, and where it may not.

The third of these is the one that matters most in practice: a document that
names another URL is not an instruction to go and fetch it. The link is offered
to a human, who adds the domain by hand or does not.
"""

from __future__ import annotations

import pytest

from assayer.snapshot import docs as docs_mod
from conftest import CUSTODY_URL, FakeFetcher, take_snapshot, write_asset
from fixtures import pages

ALLOWED = ["issuer.example", "docs.issuer.example"]


def test_9_domain_outside_the_whitelist_is_refused_before_any_request():
    """The check precedes the socket: nothing is contacted to find out."""
    fetcher = docs_mod.UrllibFetcher(ALLOWED, max_bytes=1024)
    with pytest.raises(docs_mod.SourceRefused) as caught:
        fetcher.fetch("https://elsewhere.example/prospectus.pdf")
    assert caught.value.reason == "domain_not_whitelisted"

    assert docs_mod.domain_allowed("https://docs.issuer.example/a", ALLOWED)
    assert docs_mod.domain_allowed("https://sub.issuer.example/a", ALLOWED)
    assert not docs_mod.domain_allowed("https://issuer.example.evil.com/a", ALLOWED)
    assert not docs_mod.domain_allowed("https://notissuer.example/a", ALLOWED)


def test_9b_a_refused_document_is_reported_not_hidden(paths, config, rpc):
    asset = write_asset(paths, [("terms", "https://elsewhere.example/terms.pdf",
                                 None)])
    snap = take_snapshot(paths, config, asset, rpc, {})
    document = snap.snapshot["documents"][0]
    assert document["state"] == "refused"
    assert document["reason"] == "domain_not_whitelisted"
    assert document.get("sha256") is None


def test_10_a_redirect_leaving_the_whitelist_is_cut(monkeypatch):
    """The handler refuses the hop; it does not follow and judge afterwards."""
    chain: list[str] = []
    handler = docs_mod._WhitelistRedirectHandler(ALLOWED, chain)

    class Request:
        full_url = "https://docs.issuer.example/prospectus.pdf"

        def get_full_url(self):
            return self.full_url

    with pytest.raises(docs_mod.SourceRefused) as caught:
        handler.redirect_request(Request(), None, 302, "Found", {},
                                 "https://cdn.elsewhere.example/prospectus.pdf")
    assert caught.value.reason == "redirect_outside_whitelist"
    assert chain == ["https://cdn.elsewhere.example/prospectus.pdf"]


def test_10b_redirect_inside_the_whitelist_is_allowed(paths, config, rpc):
    routes = {
        CUSTODY_URL: "https://issuer.example/custody-final.html",
        "https://issuer.example/custody-final.html":
            ("text/html", pages.CLEAN_HTML.encode("utf-8")),
    }
    asset = write_asset(paths, [("custody", CUSTODY_URL, None)])
    snap = take_snapshot(paths, config, asset, rpc, routes)
    document = snap.snapshot["documents"][0]
    assert document["state"] == "stored"
    assert document["final_url"] == "https://issuer.example/custody-final.html"
    assert document["redirects"] == ["https://issuer.example/custody-final.html"]


def test_10c_an_off_whitelist_redirect_aborts_the_document(paths, config, rpc):
    routes = {CUSTODY_URL: "https://cdn.elsewhere.example/custody.html"}
    asset = write_asset(paths, [("custody", CUSTODY_URL, None)])
    snap = take_snapshot(paths, config, asset, rpc, routes)
    document = snap.snapshot["documents"][0]
    assert document["state"] == "refused"
    assert document["reason"] == "redirect_outside_whitelist"


def test_11_links_inside_documents_are_offered_not_followed(paths, config, rpc):
    routes = {CUSTODY_URL: ("text/html", pages.LINKS_OUTSIDE.encode("utf-8"))}
    asset = write_asset(paths, [("custody", CUSTODY_URL, None)])
    snap = take_snapshot(paths, config, asset, rpc, routes)

    assert snap.fetcher.asked == [CUSTODY_URL], \
        "the fetcher was asked for a URL that came out of a document"

    domains = {entry["domain"] for entry in snap.snapshot["suggested_sources"]}
    assert domains == {"terms.elsewhere.example", "registry.other.example",
                       "support.elsewhere.example"}, domains
    for entry in snap.snapshot["suggested_sources"]:
        assert entry["doc_id"] == "custody"
        assert entry["count"] >= 1

    # Whitelisted hosts are not offered: there is no decision to make about them.
    fetcher = FakeFetcher(ALLOWED, routes)
    body = pages.CLEAN_HTML.replace("filings.example.org",
                                    "docs.issuer.example")
    routes2 = {CUSTODY_URL: ("text/html", body.encode("utf-8"))}
    asset2 = write_asset(paths, [("custody2", CUSTODY_URL, None)],
                         address="0x4444444444444444444444444444444444444444")
    snap2 = take_snapshot(paths, config, asset2, rpc, routes2)
    assert snap2.snapshot["suggested_sources"] == []
    assert fetcher.asked == []


def test_11b_the_dossier_carries_the_suggestions(paths, config, rpc):
    from assayer.render import H_SOURCES
    from assayer.verify import gate as gate_mod

    routes = {CUSTODY_URL: ("text/html", pages.LINKS_OUTSIDE.encode("utf-8"))}
    asset = write_asset(paths, [("custody", CUSTODY_URL, None)])
    snap = take_snapshot(paths, config, asset, rpc, routes)
    result = gate_mod.build_dossier(paths, config, asset, snap.snapshot)
    assert result.ok
    assert H_SOURCES in (result.text or "")
    assert "terms.elsewhere.example" in (result.text or "")
    assert "never followed" in (result.text or "")
