"""Content-addressed document store.

    state/corpus/<sha256>/original.<ext>
                          canonical.txt      extracted exactly once, never again
                          meta.json          extractor, version, page offsets

The one-time extraction rule is what makes ``char_range`` locators survive
library upgrades: a new extractor version only ever applies to documents that
enter the corpus after it, so old evidence keeps pointing at the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import util

EXT_BY_TYPE = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "txt",
    "text/markdown": "md",
    "application/json": "json",
}


@dataclass
class Extracted:
    """Result of a one-time text extraction."""

    text: str
    extractor: str
    extractor_version: str
    page_offsets: list[int]
    degraded: bool = False
    note: str | None = None


@dataclass
class CorpusDoc:
    sha256: str
    dir: Path

    @property
    def canonical_path(self) -> Path:
        return self.dir / "canonical.txt"

    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    @property
    def meta(self) -> dict:
        return util.read_json(self.meta_path)

    @property
    def text(self) -> str:
        return self.canonical_path.read_text(encoding="utf-8")

    @property
    def readable(self) -> bool:
        """Did extraction actually produce text?

        A scan with no text layer lands here as an empty ``canonical.txt``. Such
        a document proves nothing: a quote cannot be checked against it and an
        absence cannot be established from it. Callers must treat it as unread.
        """
        if self.meta.get("degraded"):
            return False
        return bool(self.text.strip())

    @property
    def original_path(self) -> Path:
        for path in sorted(self.dir.glob("original.*")):
            return path
        raise FileNotFoundError(f"no original in {self.dir}")

    @property
    def raw(self) -> bytes:
        return self.original_path.read_bytes()

    def page_of(self, offset: int) -> int | None:
        """1-based page containing ``offset``, or None if page map is unknown."""
        offsets = self.meta.get("page_offsets") or []
        if not offsets:
            return None
        page = 0
        for i, start in enumerate(offsets):
            if offset >= start:
                page = i + 1
            else:
                break
        return page or 1


@dataclass
class CorpusProblem:
    """One stored document that no longer matches how it is filed."""
    sha256: str
    kind: str
    detail: str


class Corpus:
    def __init__(self, root: Path):
        self.root = Path(root)

    def dir_for(self, sha: str) -> Path:
        return self.root / sha

    def has(self, sha: str) -> bool:
        d = self.dir_for(sha)
        return (d / "canonical.txt").exists() and (d / "meta.json").exists()

    def get(self, sha: str) -> CorpusDoc:
        if not self.has(sha):
            raise KeyError(f"document {sha[:12]} not in corpus")
        return CorpusDoc(sha256=sha, dir=self.dir_for(sha))

    def list(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(d.name for d in self.root.iterdir()
                      if d.is_dir() and (d / "canonical.txt").exists())

    def check(self, deep: bool = False) -> list["CorpusProblem"]:
        """Is every stored document still answering to the name it is filed under?

        Content addressing is the whole reason a locator written months ago still
        points at the same characters. That promise rests on bytes in a directory
        anyone can edit, so it is worth re-reading rather than assuming: the
        shallow pass checks that nothing has gone missing, and ``deep`` re-hashes
        each original and compares it with the directory it lives in.

        A document whose bytes no longer hash to its own name is not a small
        problem. Every quote verified against it was checked against different
        content, so the finding is reported rather than repaired.
        """
        problems: list[CorpusProblem] = []
        if not self.root.exists():
            return problems
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            sha = entry.name
            if not (entry / "canonical.txt").exists():
                problems.append(CorpusProblem(sha, "missing_canonical",
                                              "canonical.txt is gone, so no quote "
                                              "can be checked against this document"))
                continue
            if not (entry / "meta.json").exists():
                problems.append(CorpusProblem(sha, "missing_meta",
                                              "meta.json is gone: the extractor and "
                                              "the page offsets are unknown"))
            originals = sorted(entry.glob("original.*"))
            if not originals:
                problems.append(CorpusProblem(sha, "missing_original",
                                              "the fetched bytes are gone, so the "
                                              "hash cannot be re-checked"))
                continue
            if deep:
                actual = util.sha256_bytes(originals[0].read_bytes())
                if actual != sha:
                    problems.append(CorpusProblem(
                        sha, "hash_mismatch",
                        f"{originals[0].name} now hashes to {actual[:12]}: the "
                        f"stored bytes are not the ones this entry is named for"))
        return problems

    def put(
        self,
        data: bytes,
        *,
        url: str,
        content_type: str,
        extract: Callable[[bytes, str], Extracted],
        fetched_at: str | None = None,
    ) -> CorpusDoc:
        """Store raw bytes; extract text only if this content is new.

        Returns the corpus entry. Re-storing identical bytes is a no-op for
        ``canonical.txt`` and for every extractor field in ``meta.json`` —
        only the list of observed source URLs grows.
        """
        sha = util.sha256_bytes(data)
        target = self.dir_for(sha)
        target.mkdir(parents=True, exist_ok=True)
        ext = EXT_BY_TYPE.get(content_type.split(";")[0].strip().lower(), "bin")
        original = target / f"original.{ext}"
        if not original.exists():
            util.atomic_write_bytes(original, data)

        if self.has(sha):
            doc = self.get(sha)
            meta = doc.meta
            sources = list(meta.get("sources") or [])
            if url and url not in sources:
                sources.append(url)
                meta["sources"] = sources
                util.write_json(doc.meta_path, meta)
            return doc

        result = extract(data, content_type)
        util.atomic_write_text(target / "canonical.txt", result.text)
        meta = {
            "sha256": sha,
            "bytes": len(data),
            "content_type": content_type,
            "sources": [url] if url else [],
            "fetched_at": fetched_at or util.iso(util.utc_now()),
            "extractor": result.extractor,
            "extractor_version": result.extractor_version,
            "extracted_at": util.iso(util.utc_now()),
            "page_offsets": result.page_offsets,
            "pages": len(result.page_offsets) or None,
            "canonical_chars": len(result.text),
            "degraded": result.degraded,
            "extract_note": result.note,
        }
        util.write_json(target / "meta.json", meta)
        return self.get(sha)


def window(text: str, start: int, end: int, pad: int = 240) -> tuple[str, int]:
    """Text around ``[start:end]`` for eyeballing, plus the window's own start."""
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return text[lo:hi], lo
