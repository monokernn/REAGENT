"""Detection of text addressed at the agent rather than at the reader.

Documents come from an issuer's web server: an untrusted source. Somebody who
knows a machine will read the prospectus can hide a sentence in it that the
machine obeys and the reader never sees.

Three surfaces are examined:

* HTML — CSS and attributes that remove text from view;
* PDF — invisible render mode, sub-point fonts, text off the page, metadata,
  annotations, attachments;
* the canonical text itself — phrasing that speaks to a system, not a person.

The response is to stop and print what was found, where, and how it was hidden.
Silently scrubbing the document would leave the reader believing the corpus is
clean, which is worse than refusing to continue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable

INSTRUCTION = "instruction"  # text speaking to a system
HIDDEN = "hidden"            # text removed from view, no instruction phrasing

MIN_HIDDEN_CHARS = 12
EXCERPT_CHARS = 220


@dataclass
class Finding:
    doc_id: str
    sha256: str
    surface: str      # html | pdf | text | metadata
    technique: str
    severity: str     # instruction | hidden
    excerpt: str
    page: int | None = None
    where: str | None = None
    pattern: str | None = None

    def to_json(self) -> dict:
        out = {
            "doc_id": self.doc_id,
            "sha256": self.sha256,
            "surface": self.surface,
            "technique": self.technique,
            "severity": self.severity,
            "excerpt": self.excerpt,
        }
        if self.page is not None:
            out["page"] = self.page
        if self.where:
            out["where"] = self.where
        if self.pattern:
            out["pattern"] = self.pattern
        return out


def flatten_patterns(configured: dict | list | None) -> list[str]:
    if not configured:
        return []
    if isinstance(configured, list):
        return [str(p).strip().casefold() for p in configured if str(p).strip()]
    out: list[str] = []
    for value in configured.values():
        out.extend(flatten_patterns(value))
    return out


def matched_pattern(text: str, patterns: Iterable[str]) -> str | None:
    folded = re.sub(r"\s+", " ", text).casefold()
    for pattern in patterns:
        if pattern and pattern in folded:
            return pattern
    return None


def _excerpt(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:EXCERPT_CHARS] + ("…" if len(flat) > EXCERPT_CHARS else "")


# ---------------------------------------------------------------- html

_HIDING_DECLS = (
    (re.compile(r"display\s*:\s*none", re.I), "css:display-none"),
    (re.compile(r"visibility\s*:\s*hidden", re.I), "css:visibility-hidden"),
    (re.compile(r"opacity\s*:\s*0(?:\.0*)?\s*(?:;|$|!)", re.I), "css:opacity-0"),
    (re.compile(r"font-size\s*:\s*0(?:\.0*)?(?:px|pt|em|rem|%)?\s*(?:;|$|!)", re.I),
     "css:font-size-0"),
    (re.compile(r"clip-path\s*:", re.I), "css:clip-path"),
    (re.compile(r"clip\s*:\s*rect\(\s*0", re.I), "css:clip-rect"),
    (re.compile(r"text-indent\s*:\s*-\s*\d{3,}", re.I), "css:text-indent-offscreen"),
    (re.compile(r"(?:left|top|right|bottom)\s*:\s*-\s*\d{3,}\s*(?:px|em|rem|%)", re.I),
     "css:positioned-offscreen"),
    (re.compile(r"height\s*:\s*0(?:px)?\s*;[^}]*overflow\s*:\s*hidden", re.I),
     "css:zero-height-clipped"),
    (re.compile(r"(?:^|;)\s*width\s*:\s*0(?:px)?\s*(?:;|$)", re.I), "css:zero-width"),
    (re.compile(r"transform\s*:\s*scale\(\s*0", re.I), "css:scale-0"),
)

_COLOR = re.compile(r"(?<!-)\bcolor\s*:\s*([^;}]+)", re.I)
_BG = re.compile(r"background(?:-color)?\s*:\s*([^;}]+)", re.I)
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.I | re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
_COMMENT = re.compile(r"<!--(.*?)-->", re.S)

_NAMED_COLORS = {
    "white": "#ffffff", "black": "#000000", "transparent": "transparent",
    "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
}


def _norm_color(value: str) -> str:
    value = value.strip().lower().rstrip("!important").strip()
    value = _NAMED_COLORS.get(value, value)
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    match = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if match:
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) >= 3:
            try:
                r, g, b = (int(float(p)) for p in parts[:3])
                return "#%02x%02x%02x" % (r, g, b)
            except ValueError:
                return value
    return value


def _hiding_techniques(style: str) -> list[str]:
    found = [name for pattern, name in _HIDING_DECLS if pattern.search(style)]
    color = _COLOR.search(style)
    background = _BG.search(style)
    if color and background:
        if _norm_color(color.group(1)) == _norm_color(background.group(1)):
            found.append("css:text-colour-equals-background")
    return found


def _css_hidden_selectors(raw_html: str) -> dict[str, list[str]]:
    """Class and id selectors whose rules hide their content."""
    out: dict[str, list[str]] = {}
    for block in _STYLE_BLOCK.findall(raw_html):
        for selectors, body in _RULE.findall(block):
            techniques = _hiding_techniques(body)
            if not techniques:
                continue
            for selector in selectors.split(","):
                selector = selector.strip()
                match = re.match(r"^([.#][A-Za-z0-9_\-]+)", selector)
                if match:
                    out.setdefault(match.group(1), []).extend(techniques)
    return out


class _HiddenTextParser(HTMLParser):
    """Collects text sitting inside elements that are hidden from a reader."""

    def __init__(self, hidden_selectors: dict[str, list[str]]):
        super().__init__(convert_charrefs=True)
        self.hidden_selectors = hidden_selectors
        self.stack: list[tuple[str, list[str]]] = []
        self.hits: list[tuple[str, str]] = []  # (technique, text)
        self.attr_hits: list[tuple[str, str, str]] = []  # (technique, where, text)
        self._buffer: list[str] = []
        self._techniques: list[str] = []

    def _element_techniques(self, tag: str, attrs: dict) -> list[str]:
        techniques: list[str] = []
        style = attrs.get("style") or ""
        techniques += _hiding_techniques(style)
        if attrs.get("hidden") is not None:
            techniques.append("attr:hidden")
        if (attrs.get("aria-hidden") or "").lower() == "true":
            techniques.append("attr:aria-hidden")
        for name in ("class", "id"):
            value = attrs.get(name) or ""
            prefix = "." if name == "class" else "#"
            for token in value.split():
                techniques += self.hidden_selectors.get(prefix + token, [])
        if tag in ("template", "noscript"):
            techniques.append(f"tag:{tag}")
        return techniques

    def handle_starttag(self, tag: str, attrs) -> None:
        adict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        for name in ("alt", "title", "aria-label", "data-note", "placeholder"):
            value = adict.get(name)
            if value and len(value) > 120:
                self.attr_hits.append((f"attr:overlong-{name}", f"<{tag} {name}>",
                                       value))
        techniques = self._element_techniques(tag, adict)
        if techniques and not self._techniques:
            self._flush()
            self._techniques = techniques
        self.stack.append((tag, techniques))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                closing = self.stack[index][1]
                del self.stack[index:]
                if closing and not any(t for _, t in self.stack):
                    self._flush()
                break

    def handle_data(self, data: str) -> None:
        if self._techniques or any(t for _, t in self.stack):
            self._buffer.append(data)

    def _flush(self) -> None:
        text = "".join(self._buffer).strip()
        techniques = self._techniques or next(
            (t for _, t in reversed(self.stack) if t), [])
        self._buffer = []
        self._techniques = []
        if not text:
            return
        for technique in dict.fromkeys(techniques):
            self.hits.append((technique, text))

    def close(self) -> None:  # noqa: D102
        self._flush()
        super().close()


def scan_html(raw: bytes, *, doc_id: str, sha256: str,
              patterns: Iterable[str]) -> list[Finding]:
    from ..snapshot.docs import decode_html

    patterns = list(patterns)
    text = decode_html(raw)
    findings: list[Finding] = []

    parser = _HiddenTextParser(_css_hidden_selectors(text))
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        pass

    for technique, hidden in parser.hits:
        if len(hidden.strip()) < MIN_HIDDEN_CHARS:
            continue
        pattern = matched_pattern(hidden, patterns)
        findings.append(Finding(
            doc_id=doc_id, sha256=sha256, surface="html", technique=technique,
            severity=INSTRUCTION if pattern else HIDDEN,
            excerpt=_excerpt(hidden), pattern=pattern))

    for technique, where, value in parser.attr_hits:
        pattern = matched_pattern(value, patterns)
        findings.append(Finding(
            doc_id=doc_id, sha256=sha256, surface="html", technique=technique,
            severity=INSTRUCTION if pattern else HIDDEN, where=where,
            excerpt=_excerpt(value), pattern=pattern))

    for comment in _COMMENT.findall(text):
        body = comment.strip()
        if len(body) < MIN_HIDDEN_CHARS:
            continue
        pattern = matched_pattern(body, patterns)
        if pattern:
            findings.append(Finding(
                doc_id=doc_id, sha256=sha256, surface="html",
                technique="html:comment", severity=INSTRUCTION,
                excerpt=_excerpt(body), pattern=pattern))

    return _dedupe(findings)


# ---------------------------------------------------------------- pdf

_NUM = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)$")


def _pdf_tokens(stream: bytes):
    """Yield ('str', text) / ('arr', [text…]) / ('tok', token) from a content stream."""
    i = 0
    n = len(stream)
    while i < n:
        ch = stream[i:i + 1]
        if ch in b" \t\r\n\f\x00":
            i += 1
            continue
        if ch == b"%":
            end = stream.find(b"\n", i)
            i = n if end == -1 else end + 1
            continue
        if ch == b"(":
            text, i = _read_literal(stream, i + 1)
            yield "str", text
            continue
        if ch == b"<" and stream[i + 1:i + 2] != b"<":
            end = stream.find(b">", i)
            body = stream[i + 1:end if end != -1 else n]
            i = (end + 1) if end != -1 else n
            yield "str", _from_hex(body)
            continue
        if ch == b"[":
            items: list[str] = []
            i += 1
            while i < n and stream[i:i + 1] != b"]":
                c = stream[i:i + 1]
                if c == b"(":
                    text, i = _read_literal(stream, i + 1)
                    items.append(text)
                elif c == b"<":
                    end = stream.find(b">", i)
                    items.append(_from_hex(stream[i + 1:end if end != -1 else n]))
                    i = (end + 1) if end != -1 else n
                else:
                    i += 1
            i += 1
            yield "arr", items
            continue
        if ch == b"<":  # dictionary, skipped
            i += 2
            continue
        end = i
        while end < n and stream[end:end + 1] not in b" \t\r\n\f()[]<>/%":
            end += 1
        if end == i:
            end = i + 1
        token = stream[i:end].decode("latin-1")
        i = end
        yield "tok", token


def _read_literal(stream: bytes, i: int) -> tuple[str, int]:
    out = bytearray()
    depth = 1
    n = len(stream)
    while i < n:
        byte = stream[i]
        if byte == 0x5C:  # backslash
            nxt = stream[i + 1:i + 2]
            mapping = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                       b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
            if nxt in mapping:
                out += mapping[nxt]
                i += 2
                continue
            if nxt.isdigit():
                octal = stream[i + 1:i + 4]
                digits = bytes(c for c in octal if 0x30 <= c <= 0x37)
                out.append(int(digits, 8) & 0xFF)
                i += 1 + len(digits)
                continue
            i += 2
            continue
        if byte == 0x28:
            depth += 1
            out.append(byte)
        elif byte == 0x29:
            depth -= 1
            if depth == 0:
                return out.decode("latin-1"), i + 1
            out.append(byte)
        else:
            out.append(byte)
        i += 1
    return out.decode("latin-1"), i


def _from_hex(body: bytes) -> str:
    cleaned = re.sub(rb"[^0-9A-Fa-f]", b"", body)
    if len(cleaned) % 2:
        cleaned += b"0"
    try:
        raw = bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return ""
    if raw[:2] in (b"\xfe\xff",):
        return raw.decode("utf-16-be", "replace")
    return raw.decode("latin-1")


@dataclass
class _ShownText:
    text: str
    mode: int
    size: float
    x: float
    y: float


def _shown_text(stream: bytes) -> list[_ShownText]:
    """Every string drawn by the stream, with the graphics state that drew it."""
    out: list[_ShownText] = []
    operands: list[tuple[str, object]] = []
    mode = 0
    size = 12.0
    x = y = 0.0
    for kind, value in _pdf_tokens(stream):
        if kind in ("str", "arr"):
            operands.append((kind, value))
            continue
        token = str(value)
        if _NUM.match(token) or token.startswith("/"):
            operands.append(("tok", token))
            continue
        numbers = [float(v) for k, v in operands
                   if k == "tok" and _NUM.match(str(v))]
        if token == "Tr" and numbers:
            mode = int(numbers[-1])
        elif token == "Tf" and numbers:
            size = numbers[-1]
        elif token in ("Td", "TD") and len(numbers) >= 2:
            x += numbers[-2]
            y += numbers[-1]
        elif token == "Tm" and len(numbers) >= 6:
            x, y = numbers[-2], numbers[-1]
        elif token in ("BT", "ET"):
            x = y = 0.0
        elif token in ("Tj", "'", '"'):
            for kind2, value2 in reversed(operands):
                if kind2 == "str":
                    out.append(_ShownText(str(value2), mode, size, x, y))
                    break
        elif token == "TJ":
            for kind2, value2 in reversed(operands):
                if kind2 == "arr":
                    joined = "".join(str(v) for v in value2)  # type: ignore[union-attr]
                    out.append(_ShownText(joined, mode, size, x, y))
                    break
        operands = []
    return out


def scan_pdf(raw: bytes, *, doc_id: str, sha256: str,
             patterns: Iterable[str]) -> list[Finding]:
    import io

    from pypdf import PdfReader

    patterns = list(patterns)
    findings: list[Finding] = []
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        return [Finding(doc_id=doc_id, sha256=sha256, surface="pdf",
                        technique="pdf:unreadable", severity=HIDDEN,
                        excerpt=f"{type(exc).__name__}: {exc}")]

    for index, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        crop = getattr(page, "cropbox", None) or box
        try:
            left = float(min(crop.left, box.left))
            right = float(max(crop.right, box.right))
            bottom = float(min(crop.bottom, box.bottom))
            top = float(max(crop.top, box.top))
        except Exception:
            left, bottom, right, top = 0.0, 0.0, 612.0, 792.0
        try:
            contents = page.get_contents()
            stream = contents.get_data() if contents is not None else b""
        except Exception:
            stream = b""
        for shown in _shown_text(stream):
            if not shown.text.strip():
                continue
            techniques = []
            if shown.mode == 3:
                techniques.append("pdf:render-mode-3")
            if 0 < shown.size < 1:
                techniques.append("pdf:font-under-1pt")
            if shown.size == 0:
                techniques.append("pdf:font-size-0")
            if not (left - 1 <= shown.x <= right + 1) or \
                    not (bottom - 1 <= shown.y <= top + 1):
                techniques.append("pdf:text-outside-page-box")
            if not techniques:
                continue
            pattern = matched_pattern(shown.text, patterns)
            for technique in techniques:
                findings.append(Finding(
                    doc_id=doc_id, sha256=sha256, surface="pdf",
                    technique=technique,
                    severity=INSTRUCTION if pattern else HIDDEN,
                    excerpt=_excerpt(shown.text), page=index, pattern=pattern))

        annotations = page.get("/Annots")
        if annotations:
            try:
                items = list(annotations)
            except Exception:
                items = []
            for annotation in items:
                try:
                    obj = annotation.get_object()
                except Exception:
                    continue
                body = " ".join(str(obj.get(key, "")) for key in
                                ("/Contents", "/T", "/Subj", "/RC"))
                uri = ""
                action = obj.get("/A")
                if action is not None:
                    try:
                        uri = str(action.get_object().get("/URI", ""))
                    except Exception:
                        uri = ""
                blob = (body + " " + uri).strip()
                if not blob:
                    continue
                pattern = matched_pattern(blob, patterns)
                if pattern:
                    findings.append(Finding(
                        doc_id=doc_id, sha256=sha256, surface="pdf",
                        technique="pdf:annotation", severity=INSTRUCTION,
                        excerpt=_excerpt(blob), page=index, pattern=pattern))

    # metadata: /Info dictionary and XMP
    meta_blob_parts = []
    try:
        info = reader.metadata or {}
        for key, value in dict(info).items():
            meta_blob_parts.append(f"{key}={value}")
    except Exception:
        pass
    try:
        xmp = reader.xmp_metadata
        if xmp is not None and getattr(xmp, "stream", None) is not None:
            meta_blob_parts.append(xmp.stream.get_data().decode("utf-8", "replace"))
    except Exception:
        pass
    for blob in meta_blob_parts:
        pattern = matched_pattern(blob, patterns)
        if pattern:
            findings.append(Finding(
                doc_id=doc_id, sha256=sha256, surface="metadata",
                technique="pdf:document-metadata", severity=INSTRUCTION,
                excerpt=_excerpt(blob), pattern=pattern))

    # embedded files
    try:
        names = reader.trailer["/Root"].get("/Names", {})
        embedded = names.get("/EmbeddedFiles") if names else None
        if embedded is not None:
            findings.append(Finding(
                doc_id=doc_id, sha256=sha256, surface="pdf",
                technique="pdf:embedded-file", severity=HIDDEN,
                excerpt="the document carries an embedded file attachment"))
    except Exception:
        pass

    return _dedupe(findings)


# ---------------------------------------------------------------- canonical text

def scan_text(text: str, *, doc_id: str, sha256: str,
              patterns: Iterable[str]) -> list[Finding]:
    """Instruction phrasing in plainly visible text is still addressed at a machine."""
    findings: list[Finding] = []
    folded = re.sub(r"\s+", " ", text).casefold()
    for pattern in patterns:
        start = folded.find(pattern)
        while start != -1:
            findings.append(Finding(
                doc_id=doc_id, sha256=sha256, surface="text",
                technique="text:addressed-to-system", severity=INSTRUCTION,
                excerpt=_excerpt(folded[max(0, start - 80):start + 160]),
                where=f"offset~{start}", pattern=pattern))
            if len(findings) > 40:
                return _dedupe(findings)
            start = folded.find(pattern, start + len(pattern))
    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for finding in findings:
        key = (finding.technique, finding.severity, finding.excerpt, finding.page,
               finding.where)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


# ---------------------------------------------------------------- entry point

def scan_document(raw: bytes, canonical_text: str, *, doc_id: str, sha256: str,
                  content_type: str, patterns: Iterable[str]) -> list[Finding]:
    patterns = list(patterns)
    findings: list[Finding] = []
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind == "application/pdf" or raw[:5] == b"%PDF-":
        findings += scan_pdf(raw, doc_id=doc_id, sha256=sha256, patterns=patterns)
    elif kind in ("text/html", "application/xhtml+xml") or \
            raw[:200].lstrip()[:1] == b"<":
        findings += scan_html(raw, doc_id=doc_id, sha256=sha256, patterns=patterns)
    findings += scan_text(canonical_text, doc_id=doc_id, sha256=sha256,
                          patterns=patterns)
    return _dedupe(findings)


def halting(findings: Iterable[Finding], config: dict) -> list[Finding]:
    """Which findings stop the run, given ``[gate]`` settings."""
    gate = config.get("gate") or {}
    halt_instruction = bool(gate.get("halt_on_injection", True))
    halt_hidden = bool(gate.get("halt_on_hidden_text", False))
    out = []
    for finding in findings:
        if finding.severity == INSTRUCTION and halt_instruction:
            out.append(finding)
        elif finding.severity == HIDDEN and halt_hidden:
            out.append(finding)
    return out
