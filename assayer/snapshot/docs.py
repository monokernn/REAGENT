"""Document acquisition: fetch, extract text once, canonicalise, store.

Two rules govern this module and both are enforced here rather than by habit:

* nothing is fetched from a host outside ``[sources] allowed_domains``;
* a redirect that leaves the whitelist aborts the fetch instead of following it.

Links found *inside* documents are never followed. They are harvested and
surfaced as ``suggested_sources`` so a human can add the domain by hand.
"""

from __future__ import annotations

import html
import io
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable

from .. import corpus as corpus_mod
from .. import util

USER_AGENT = "assayer/0.3 (+read-only document fetcher)"
PAGE_SEP = "\n\n"


class SourceRefused(Exception):
    """Raised when a URL or a redirect target is outside the whitelist."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{reason}: {url}")
        self.url = url
        self.reason = reason


# ---------------------------------------------------------------- whitelist

# The protocols a document may arrive over. The whitelist says where a fetch may
# go; without this it says nothing about how, and urllib will happily speak ftp
# or read a local file for any host that passes the domain check.
ALLOWED_SCHEMES = ("http", "https")


def host_of(url: str) -> str:
    # A trailing dot is the fully qualified form of the same name, so it is
    # stripped: refusing issuer.example. while allowing issuer.example would be
    # this tool inventing a difference DNS does not make.
    return (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")


def scheme_allowed(url: str) -> bool:
    return urllib.parse.urlsplit(url).scheme.lower() in ALLOWED_SCHEMES


def domain_allowed(url: str, allowed: Iterable[str]) -> bool:
    """Host equals an allowed domain, or is a subdomain of one."""
    host = host_of(url)
    if not host:
        return False
    for entry in allowed:
        entry = str(entry).strip().lower().lstrip(".")
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            return True
    return False


# ---------------------------------------------------------------- fetching

@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    data: bytes
    redirects: list[str] = field(default_factory=list)


class _WhitelistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed: Iterable[str], chain: list[str]):
        self.allowed = list(allowed)
        self.chain = chain

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        self.chain.append(newurl)
        if not scheme_allowed(newurl):
            raise SourceRefused(newurl, "redirect_to_unsupported_scheme")
        if not domain_allowed(newurl, self.allowed):
            raise SourceRefused(newurl, "redirect_outside_whitelist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibFetcher:
    """The only outbound HTTP path in the codebase."""

    def __init__(self, allowed_domains: Iterable[str], max_bytes: int,
                 timeout_s: float = 60.0):
        self.allowed = list(allowed_domains)
        self.max_bytes = int(max_bytes)
        self.timeout_s = timeout_s

    def fetch(self, url: str) -> FetchResult:
        if not scheme_allowed(url):
            raise SourceRefused(url, "unsupported_scheme")
        if not domain_allowed(url, self.allowed):
            raise SourceRefused(url, "domain_not_whitelisted")
        chain: list[str] = []
        opener = urllib.request.build_opener(
            _WhitelistRedirectHandler(self.allowed, chain))
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener.open(request, timeout=self.timeout_s) as response:
            data = response.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                raise SourceRefused(url, "document_over_size_limit")
            final_url = response.geturl()
            if not scheme_allowed(final_url):
                raise SourceRefused(final_url, "redirect_to_unsupported_scheme")
            if not domain_allowed(final_url, self.allowed):
                raise SourceRefused(final_url, "redirect_outside_whitelist")
            return FetchResult(
                url=url,
                final_url=final_url,
                status=getattr(response, "status", 200) or 200,
                content_type=response.headers.get("Content-Type", "") or "",
                data=data,
                redirects=chain,
            )


# ---------------------------------------------------------------- html -> text

_BLOCK = {
    "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "table", "ul", "ol", "blockquote",
    "pre", "hr", "figure", "figcaption", "main", "nav", "aside",
}
_DROP = {"script", "style", "noscript", "svg", "template", "head", "iframe"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP:
            self._skip += 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip() + "\n"


_META_CHARSET = re.compile(rb"""charset\s*=\s*["']?([\w\-]+)""", re.I)


def decode_html(data: bytes, content_type: str = "") -> str:
    match = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    encodings = []
    if match:
        encodings.append(match.group(1))
    meta = _META_CHARSET.search(data[:4096])
    if meta:
        encodings.append(meta.group(1).decode("ascii", "ignore"))
    encodings += ["utf-8", "cp1251", "latin-1"]
    for enc in encodings:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def html_to_text(data: bytes, content_type: str = "") -> str:
    parser = _TextExtractor()
    parser.feed(decode_html(data, content_type))
    parser.close()
    return parser.text()


# ---------------------------------------------------------------- pdf -> text

def pdf_to_text(data: bytes) -> tuple[str, list[int], bool, str | None]:
    """Extract text page by page. Returns (text, page_offsets, degraded, note)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    chunks: list[str] = []
    offsets: list[int] = []
    cursor = 0
    empty_pages = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            empty_pages += 1
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        offsets.append(cursor)
        chunks.append(text)
        cursor += len(text) + len(PAGE_SEP)
    joined = PAGE_SEP.join(chunks)
    total = len(reader.pages)
    degraded = total > 0 and empty_pages == total
    note = None
    if degraded:
        note = "no_text_layer_on_any_page"
    elif empty_pages:
        note = f"no_text_layer_on_{empty_pages}_of_{total}_pages"
    return joined, offsets, degraded, note


# ---------------------------------------------------------------- extractor

def extract_text(data: bytes, content_type: str) -> corpus_mod.Extracted:
    """Called exactly once per unique document, from :meth:`Corpus.put`."""
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind == "application/pdf" or data[:5] == b"%PDF-":
        import pypdf

        text, offsets, degraded, note = pdf_to_text(data)
        return corpus_mod.Extracted(
            text=text, extractor="pypdf",
            extractor_version=getattr(pypdf, "__version__", "unknown"),
            page_offsets=offsets, degraded=degraded, note=note,
        )
    if kind in ("text/html", "application/xhtml+xml") or data[:200].lstrip()[:1] == b"<":
        text = html_to_text(data, content_type)
        return corpus_mod.Extracted(
            text=text, extractor="assayer.html", extractor_version="1",
            page_offsets=[0],
        )
    text = decode_html(data, content_type)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return corpus_mod.Extracted(text=text, extractor="assayer.text",
                               extractor_version="1", page_offsets=[0])


# ---------------------------------------------------------------- link harvest

_HREF = re.compile(rb"""(?:href|src)\s*=\s*["']([^"'<>\s]+)["']""", re.I)
_URL_IN_TEXT = re.compile(r"""https?://[^\s"'<>\)\]]+""")


def harvest_links(doc: corpus_mod.CorpusDoc, doc_id: str,
                  allowed: Iterable[str]) -> list[dict]:
    """Collect outbound links from a stored document. Nothing is fetched.

    Links whose host is already whitelisted are dropped: they carry no decision
    for the user. Everything else is offered as a suggestion.
    """
    found: dict[str, dict] = {}
    raw = doc.raw
    urls: list[str] = []
    if doc.original_path.suffix == ".html" or raw[:200].lstrip()[:1] == b"<":
        for match in _HREF.finditer(raw):
            urls.append(html.unescape(match.group(1).decode("utf-8", "replace")))
    urls += _URL_IN_TEXT.findall(doc.text)
    for url in urls:
        if not url.lower().startswith(("http://", "https://")):
            continue
        url = url.rstrip(".,;)")
        host = host_of(url)
        if not host or domain_allowed(url, allowed):
            continue
        entry = found.setdefault(host, {"domain": host, "doc_id": doc_id,
                                        "examples": [], "count": 0})
        entry["count"] += 1
        if len(entry["examples"]) < 3 and url not in entry["examples"]:
            entry["examples"].append(url)
    return [found[k] for k in sorted(found)]


# ---------------------------------------------------------------- orchestration

@dataclass
class DocResult:
    doc_id: str
    url: str
    state: str  # stored | refused | error
    sha256: str | None = None
    version: str | None = None
    reason: str | None = None
    detail: str | None = None
    content_type: str | None = None
    bytes: int | None = None
    pages: int | None = None
    degraded: bool = False
    final_url: str | None = None
    redirects: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        out: dict = {"doc_id": self.doc_id, "url": self.url, "state": self.state}
        for key in ("sha256", "version", "reason", "detail", "content_type",
                    "bytes", "pages", "final_url"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.degraded:
            out["degraded"] = True
        if self.redirects:
            out["redirects"] = list(self.redirects)
        return out


def _reason_for_http(code: int) -> str:
    if code in (401, 403):
        return f"doc_{code}"
    if code == 404:
        return "doc_404"
    if code == 429:
        return "doc_rate_limited"
    return f"doc_http_{code}"


def collect(documents, *, fetcher, corpus: corpus_mod.Corpus,
            allowed: Iterable[str]) -> list[DocResult]:
    """Fetch and store every declared document. Failures are reported, not hidden."""
    results: list[DocResult] = []
    for doc in documents:
        try:
            got = fetcher.fetch(doc.url)
        except SourceRefused as exc:
            results.append(DocResult(doc_id=doc.doc_id, url=doc.url, state="refused",
                                     reason=exc.reason, detail=exc.url,
                                     version=doc.version))
            continue
        except urllib.error.HTTPError as exc:
            results.append(DocResult(doc_id=doc.doc_id, url=doc.url, state="error",
                                     reason=_reason_for_http(exc.code),
                                     detail=str(exc.reason), version=doc.version))
            continue
        except Exception as exc:  # network down, DNS, TLS, timeout
            results.append(DocResult(doc_id=doc.doc_id, url=doc.url, state="error",
                                     reason="doc_unavailable",
                                     detail=f"{type(exc).__name__}: {exc}",
                                     version=doc.version))
            continue

        content_type = got.content_type or _guess_type(got.final_url, got.data)
        stored = corpus.put(got.data, url=got.url, content_type=content_type,
                            extract=extract_text)
        meta = stored.meta
        results.append(DocResult(
            doc_id=doc.doc_id, url=doc.url, state="stored", sha256=stored.sha256,
            version=doc.version, content_type=content_type, bytes=meta.get("bytes"),
            pages=meta.get("pages"), degraded=bool(meta.get("degraded")),
            final_url=got.final_url, redirects=list(got.redirects),
        ))
    return results


def _guess_type(url: str, data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith(".pdf"):
        return "application/pdf"
    if path.endswith((".html", ".htm")):
        return "text/html"
    if path.endswith(".txt"):
        return "text/plain"
    if data[:200].lstrip()[:1] == b"<":
        return "text/html"
    return "text/plain"


def find_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Locate ``quote`` in ``text`` under comparison normalisation.

    Returns raw offsets into ``text``, or None. Used both to repair a drifted
    locator and to prove that a quote is absent from a document.
    """
    needle = util.normalize_for_compare(quote)
    if not needle:
        return None
    haystack, offsets = util.normalize_with_map(text)
    pos = haystack.find(needle)
    if pos == -1:
        return None
    start = offsets[pos]
    end = offsets[pos + len(needle) - 1] + 1
    return start, end
