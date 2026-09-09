"""Small shared primitives: canonical JSON, atomic writes, hashing, config, locks.

Everything that must be byte-stable across runs goes through :func:`canonical_json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # python >= 3.11
    import tomllib as _toml
except ModuleNotFoundError:  # python 3.10
    import tomli as _toml  # type: ignore[no-redef]


# ---------------------------------------------------------------- json / hash

def canonical_json(obj: Any) -> str:
    """Deterministic JSON text: sorted keys, unicode kept, trailing newline."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_hash(obj: Any) -> str:
    """sha256 of the canonical JSON form of ``obj``."""
    return sha256_text(canonical_json(obj))


# ---------------------------------------------------------------- filesystem

def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{time.time_ns()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, canonical_json(obj))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FileLock:
    """Cross-platform advisory lock built on exclusive file creation.

    Used by the ledger so concurrent writers cannot lose a spend.
    """

    def __init__(self, path: Path, timeout_s: float = 30.0, poll_s: float = 0.005):
        self.path = Path(path)
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self._fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                pass
            except PermissionError:
                # Windows reports a delete still pending on the lock file as
                # access denied rather than as "already exists". Same meaning:
                # somebody else holds it, wait and try again.
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"lock busy: {self.path}")
            time.sleep(self.poll_s)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(str(self.path))
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- time

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_slug(dt: datetime | None = None) -> str:
    """Directory-safe UTC timestamp; sorts lexicographically."""
    return (dt or utc_now()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_ts_slug(slug: str) -> datetime:
    return datetime.strptime(slug, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


# The one list of date shapes this programme understands. It is shared with the
# reserve reader, which renders a declared date in every one of these forms to
# look for it in the document: reading a date and finding it again must speak the
# same language, or a holder who copies the date exactly as the report prints it
# gets told the tool cannot parse it.
DATE_FORMS = (
    "%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%dT%H%M%SZ",
    "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y",
    "%d.%m.%Y", "%d/%m/%Y",
)


def parse_date(value: str) -> datetime | None:
    text = str(value).strip()
    for fmt in DATE_FORMS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- text

SOFT_HYPHEN = "­"
_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "«": '"', "»": '"',
    "‹": "'", "›": "'", "ʼ": "'", "`": "'",
}
_DASHES = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}
_WS = re.compile(r"\s+")


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise for comparison while keeping a map back to raw offsets.

    ``map[i]`` is the offset in ``text`` of the character that produced
    normalised character ``i``. Soft hyphens dropped, hyphenation across line
    breaks joined, quotes and dashes canonicalised, whitespace runs collapsed to
    one space, casefolded. Comparison only — never written back to the corpus.
    """
    out: list[str] = []
    idx: list[int] = []
    i = 0
    n = len(text)
    pending_space = False
    while i < n:
        ch = text[i]
        if ch == SOFT_HYPHEN:
            i += 1
            continue
        if ch == "-":
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == "\n":
                j += 1
                while j < n and text[j] in " \t":
                    j += 1
                i = j
                continue
        if ch.isspace():
            pending_space = True
            i += 1
            continue
        mapped = _QUOTES.get(ch) or _DASHES.get(ch) or unicodedata.normalize("NFKC", ch)
        mapped = mapped.casefold()
        if pending_space and out:
            out.append(" ")
            idx.append(i)
        pending_space = False
        for c in mapped:
            out.append(c)
            idx.append(i)
        i += 1
    return "".join(out), idx


def normalize_for_compare(text: str) -> str:
    return normalize_with_map(text)[0]


# ---------------------------------------------------------------- config

class ConfigError(RuntimeError):
    pass


def load_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return _toml.load(fh)


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.toml"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def corpus(self) -> Path:
        return self.state / "corpus"

    @property
    def snapshots(self) -> Path:
        return self.state / "snapshots"

    @property
    def extractions(self) -> Path:
        return self.state / "extractions"

    @property
    def dossiers(self) -> Path:
        return self.state / "dossiers"

    @property
    def scars(self) -> Path:
        return self.state / "scars.jsonl"

    @property
    def budget(self) -> Path:
        return self.state / "budget.json"

    @property
    def log(self) -> Path:
        return self.state / "log.jsonl"


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("ASSAYER_HOME")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "config.toml").exists() and (candidate / "assayer").is_dir():
            return candidate
    return here


def paths(start: Path | None = None) -> Paths:
    return Paths(find_root(start))


DEFAULT_CONFIG: dict = {
    "agent": {
        "command": "claude",
        "args": ["-p", "--output-format", "json"],
        "response_field": "result",
        "cost_field": "total_cost_usd",
        "timeout_s": 600,
    },
    "budget": {"max_usd_per_day": 2.0, "max_usd_per_extraction": 0.40},
    "rpc": {},
    "sources": {"allowed_domains": []},
    "corpus": {"max_doc_mb": 40, "ocr": False},
    "gate": {"taint_threshold": 3, "halt_on_injection": True, "injection_patterns": {}},
    "language": {"forbidden": [], "exempt_keys": ["quote"]},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(p: Paths | None = None) -> dict:
    p = p or paths()
    raw = load_toml(p.config) if p.config.exists() else {}
    return _merge(DEFAULT_CONFIG, raw)


def log_event(p: Paths, event: str, **fields: Any) -> None:
    """Append one line to state/log.jsonl under a lock."""
    record: dict[str, Any] = {"ts": iso(utc_now()), "event": event}
    record.update(fields)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    p.state.mkdir(parents=True, exist_ok=True)
    with FileLock(p.state / "log.jsonl.lock"):
        with open(p.log, "a", encoding="utf-8") as fh:
            fh.write(line)


def flatten_strings(obj: Any, path: str = "",
                    skip_keys: Iterable[str] = ()) -> list[tuple[str, str]]:
    """Every string leaf in a nested structure, with its dotted path."""
    skip = set(skip_keys)
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in skip:
                continue
            child = f"{path}.{key}" if path else str(key)
            out.extend(flatten_strings(value, child, skip))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            out.extend(flatten_strings(value, f"{path}[{i}]", skip))
    elif isinstance(obj, str):
        out.append((path, obj))
    return out
