"""Artefact schemas, slot vocabulary, states, serialisation.

Three artefact types, each content-addressed on disk:

    snapshot    produced by scripts, free, trusted, reproducible
    extraction  produced by a model, costs money, untrusted
    dossier     produced by the gate: extraction intersected with snapshot

``dossier = f(snapshot, extraction)`` is a pure function, so nothing in a
dossier may depend on wall-clock time or on the order of a dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import util

SCHEMA_VERSION = 1

# ---------------------------------------------------------------- slots

LEGAL_SLOTS: tuple[str, ...] = (
    "instrument_type",
    "issuer",
    "holder_rights",
    "insolvency",
    "custodian",
    "corporate_actions",
    "redemption",
    "restrictions",
)

ONCHAIN_SLOTS: tuple[str, ...] = (
    "mint_authority",
    "burn_authority",
    "pause",
    "upgradeable",
    "freeze",
    "oracle",
    "supply_vs_reserves",
    "attestation_freshness",
)

ALL_SLOTS: tuple[str, ...] = LEGAL_SLOTS + ONCHAIN_SLOTS

# Slots whose values carry numbers that move on their own: a block later, a day
# later, a mint later. Their *shape* is part of the asset's identity and of the
# diff; their digits are not, or every clock tick would read as a change.
VOLATILE_VALUE_SLOTS: frozenset[str] = frozenset({
    "supply_vs_reserves", "oracle", "attestation_freshness",
})

# The instrument_type slot answers from a closed vocabulary. The model writes a
# sentence; the canonical token is recovered from it here so that signals and
# diffs compare a category rather than a phrasing.
INSTRUMENT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "debt_security": ("debt", "note", "bond", "долг", "нот", "облигац"),
    "depositary_receipt": ("depositary", "receipt", "депозитар", "расписк"),
    "synthetic": ("synthetic", "swap", "cfd", "синтет", "своп"),
    "equity": ("equity", "share", "stock", "акци", "доля в капитале"),
}


def instrument_category(value: str | None) -> str | None:
    """The canonical instrument token a free-text answer points at."""
    if not value:
        return None
    lowered = value.lower()
    for token in INSTRUMENT_CATEGORIES:
        if lowered.startswith(token):
            return token
    for token, keywords in INSTRUMENT_CATEGORIES.items():
        if any(keyword in lowered for keyword in keywords):
            return token
    return None


SLOT_QUESTIONS: dict[str, str] = {
    "instrument_type": "equity / debt_security / synthetic / depositary_receipt / other",
    "issuer": "legal entity and jurisdiction of the issuer",
    "holder_rights": "voting, dividends, claim on the underlying asset",
    "insolvency": "on issuer insolvency: segregation or general creditor queue",
    "custodian": "who holds the underlying asset; is an independent attestor named",
    "corporate_actions": "dividends, splits, delisting",
    "redemption": "is redemption into the underlying possible, on what terms, "
                  "who may refuse",
    "restrictions": "excluded jurisdictions and holder categories",
    "mint_authority": "who may mint, EOA or multisig, threshold",
    "burn_authority": "may the issuer burn balances it does not own",
    "pause": "pause capability and its owner",
    "upgradeable": "proxy pattern, admin, timelock and its delay",
    "freeze": "blacklist/frozen: may the issuer block an address",
    "oracle": "price source and staleness",
    "supply_vs_reserves": "totalSupply against the claimed collateral",
    "attestation_freshness": "age of the reserve report against the promised period",
}

# ---------------------------------------------------------------- states

FILLED = "filled"
NOT_FOUND = "not_found"
NOT_CHECKED = "not_checked"
REJECTED = "rejected"

STATES = (FILLED, NOT_FOUND, NOT_CHECKED, REJECTED)

# Reasons for not_checked. A fact about the tool, never about the asset.
R_RPC_ERROR = "rpc_error"
R_NO_RPC = "missing_rpc_endpoint"
R_UNKNOWN_PROXY = "unknown_proxy_pattern"
R_DOC_UNAVAILABLE = "doc_unavailable"
R_NO_DOCUMENTS = "no_documents_in_corpus"
R_NO_EXTRACTION = "no_extraction"
R_MODEL_JSON = "invalid_model_json"
R_SLOT_MISSING = "slot_absent_from_extraction"
R_NO_RESERVE_CLAIM = "no_reserve_claim_declared"
R_NO_PERIOD = "no_attestation_period_declared"
R_NO_ATTESTATION_DATE = "no_attestation_date_declared"
# A document that yielded no text is a hole in the tool, not a silence in the
# asset: nothing may be concluded from it, in either direction.
R_NO_TEXT_LAYER = "document_has_no_text_layer"

# Reasons for rejected. A fact about the model.
J_FABRICATED = "fabricated_quote"
J_UNKNOWN_DOC = "unknown_document_sha256"
J_BAD_EVIDENCE = "malformed_evidence"
J_NO_EVIDENCE = "claim_without_evidence"
J_FORBIDDEN_KIND = "evidence_kind_not_allowed"
J_TAINTED_DOC = "evidence_in_tainted_document"

# Evidence kinds. There is deliberately no "knowledge" kind.
E_DOCUMENT = "document"
E_ONCHAIN = "onchain"
E_ABSENCE = "absence"
EVIDENCE_KINDS = (E_DOCUMENT, E_ONCHAIN, E_ABSENCE)


# ---------------------------------------------------------------- evidence

@dataclass
class Locator:
    """Where inside a canonical document the quote lives."""

    type: str  # char_range | bbox
    start: int | None = None
    end: int | None = None
    page: int | None = None
    bbox: list[float] | None = None

    def to_json(self) -> dict:
        out: dict[str, Any] = {"type": self.type}
        if self.page is not None:
            out["page"] = self.page
        if self.type == "char_range":
            out["start"] = self.start
            out["end"] = self.end
        else:
            out["bbox"] = self.bbox
        return out

    @staticmethod
    def from_json(raw: dict) -> "Locator":
        return Locator(
            type=str(raw.get("type", "char_range")),
            start=raw.get("start"),
            end=raw.get("end"),
            page=raw.get("page"),
            bbox=raw.get("bbox"),
        )


@dataclass
class Evidence:
    kind: str
    # document evidence
    doc_id: str | None = None
    sha256: str | None = None
    locator: Locator | None = None
    quote: str | None = None
    degraded: bool = False
    # onchain evidence
    chain_id: int | None = None
    address: str | None = None
    method: str | None = None
    block: int | None = None
    raw: str | None = None
    # absence evidence
    checked_docs: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    checked_selectors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind}
        if self.kind == E_DOCUMENT:
            out.update({
                "doc_id": self.doc_id,
                "sha256": self.sha256,
                "locator": self.locator.to_json() if self.locator else None,
                "quote": self.quote,
            })
            if self.degraded:
                out["degraded"] = True
        elif self.kind == E_ONCHAIN:
            out.update({
                "chain_id": self.chain_id,
                "address": self.address,
                "method": self.method,
                "block": self.block,
                "raw": self.raw,
            })
        elif self.kind == E_ABSENCE:
            if self.checked_docs:
                out["checked_docs"] = list(self.checked_docs)
            if self.patterns:
                out["patterns"] = list(self.patterns)
            if self.checked_selectors:
                out["checked_selectors"] = list(self.checked_selectors)
            if self.chain_id is not None:
                out["chain_id"] = self.chain_id
            if self.address is not None:
                out["address"] = self.address
            if self.block is not None:
                out["block"] = self.block
        return out

    @staticmethod
    def from_json(raw: dict) -> "Evidence":
        loc = raw.get("locator")
        return Evidence(
            kind=str(raw.get("kind", "")),
            doc_id=raw.get("doc_id"),
            sha256=raw.get("sha256"),
            locator=Locator.from_json(loc) if isinstance(loc, dict) else None,
            quote=raw.get("quote"),
            degraded=bool(raw.get("degraded", False)),
            chain_id=raw.get("chain_id"),
            address=raw.get("address"),
            method=raw.get("method"),
            block=raw.get("block"),
            raw=raw.get("raw"),
            checked_docs=list(raw.get("checked_docs") or []),
            patterns=list(raw.get("patterns") or []),
            checked_selectors=list(raw.get("checked_selectors") or []),
        )


@dataclass
class Slot:
    slot: str
    state: str
    value: str | None = None
    evidence: Evidence | None = None
    reason: str | None = None
    note: str | None = None
    material: bool = False  # onchain power over somebody else's balance

    def to_json(self) -> dict:
        out: dict[str, Any] = {"slot": self.slot, "state": self.state}
        if self.value is not None:
            out["value"] = self.value
        if self.evidence is not None:
            out["evidence"] = self.evidence.to_json()
        if self.reason is not None:
            out["reason"] = self.reason
        if self.note is not None:
            out["note"] = self.note
        if self.material:
            out["material"] = True
        return out

    @staticmethod
    def from_json(raw: dict) -> "Slot":
        ev = raw.get("evidence")
        return Slot(
            slot=str(raw.get("slot", "")),
            state=str(raw.get("state", NOT_CHECKED)),
            value=raw.get("value"),
            evidence=Evidence.from_json(ev) if isinstance(ev, dict) else None,
            reason=raw.get("reason"),
            note=raw.get("note"),
            material=bool(raw.get("material", False)),
        )


def not_checked(slot: str, reason: str, note: str | None = None) -> Slot:
    return Slot(slot=slot, state=NOT_CHECKED, reason=reason, note=note)


# ---------------------------------------------------------------- asset

@dataclass
class Document:
    doc_id: str
    url: str
    version: str | None = None

    def to_json(self) -> dict:
        out = {"doc_id": self.doc_id, "url": self.url}
        if self.version:
            out["version"] = self.version
        return out


@dataclass
class Asset:
    chain: str
    address: str
    label: str
    documents: list[Document] = field(default_factory=list)
    reserves: dict = field(default_factory=dict)
    alerts: dict = field(default_factory=dict)
    chain_id: int | None = None

    @property
    def name(self) -> str:
        return f"{self.chain}-{self.address.lower()}"

    @property
    def ref(self) -> str:
        return f"{self.chain}:{self.address.lower()}"

    def to_json(self) -> dict:
        return {
            "chain": self.chain,
            "chain_id": self.chain_id,
            "address": self.address.lower(),
            "label": self.label,
            "documents": [d.to_json() for d in self.documents],
            "reserves": dict(self.reserves),
        }


CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "optimism": 10,
    "bsc": 56,
    "polygon": 137,
    "base": 8453,
    "arbitrum": 42161,
    "avalanche": 43114,
    "sepolia": 11155111,
}


def parse_ref(ref: str) -> tuple[str, str]:
    """``ethereum:0xabc…`` -> ``("ethereum", "0xabc…")``."""
    if ":" not in ref:
        raise ValueError(f"expected <chain>:<address>, got {ref!r}")
    chain, address = ref.split(":", 1)
    chain = chain.strip().lower()
    address = address.strip().lower()
    if not chain:
        raise ValueError("empty chain")
    if not (address.startswith("0x") and len(address) == 42):
        raise ValueError(f"not a 20-byte address: {address!r}")
    int(address, 16)  # raises on non-hex
    return chain, address


def asset_path(p: util.Paths, chain: str, address: str) -> "object":
    return p.assets / f"{chain}-{address.lower()}.toml"


def slug(text: str) -> str:
    """Lowercase, punctuation to dashes: ``Assay Note ASSY`` -> ``assay-note-assy``."""
    out = re.sub(r"[^0-9a-zа-яё]+", "-", str(text).lower())
    return out.strip("-")


def load_asset(p: util.Paths, name: str) -> Asset:
    """Load an asset by ``<chain>-<address>``, by ``<chain>:<address>`` or by label.

    Labels are matched on their slug, so ``Local Note`` answers to ``local-note``
    and to ``local``.
    """
    candidates = sorted(p.assets.glob("*.toml")) if p.assets.exists() else []
    key = slug(name)
    exact = [c for c in candidates if c.stem.lower() == key]
    if not exact:
        by_label = []
        for c in candidates:
            try:
                label = slug(util.load_toml(c).get("label", ""))
            except Exception:
                continue
            if label and label == key:
                by_label.append(c)
        exact = by_label
    if not exact:
        exact = [c for c in candidates if key and key in c.stem.lower()]
    if not exact:
        for c in candidates:
            try:
                label = slug(util.load_toml(c).get("label", ""))
            except Exception:
                continue
            if key and label and key in label:
                exact.append(c)
    if not exact:
        raise FileNotFoundError(f"no asset matching {name!r} in {p.assets}")
    if len(exact) > 1:
        raise ValueError(f"{name!r} matches several assets: "
                         + ", ".join(c.stem for c in exact))
    return asset_from_toml(util.load_toml(exact[0]))


def asset_from_toml(raw: dict) -> Asset:
    docs = []
    seen: dict[str, str] = {}
    for item in raw.get("documents", []) or []:
        doc_id, url = str(item["doc_id"]), str(item["url"])
        # doc_id is the name every citation in every dossier is written against.
        # Two documents answering to one name would make "[prospectus, p.4]"
        # ambiguous, and downstream the later one silently wins, so the file is
        # refused rather than one of the two quietly chosen.
        if doc_id in seen:
            raise ValueError(
                f"two documents share the doc_id {doc_id!r}: {seen[doc_id]} and "
                f"{url}. Evidence cites documents by this name, so it has to be "
                f"unique within an asset")
        seen[doc_id] = url
        docs.append(Document(doc_id=doc_id, url=url, version=item.get("version")))
    chain = str(raw["chain"]).lower()
    return Asset(
        chain=chain,
        address=str(raw["address"]).lower(),
        label=str(raw.get("label") or raw["address"]),
        documents=docs,
        reserves=dict(raw.get("reserves", {}) or {}),
        alerts=dict(raw.get("alerts", {}) or {}),
        chain_id=raw.get("chain_id") or CHAIN_IDS.get(chain),
    )


def list_assets(p: util.Paths) -> list[Asset]:
    if not p.assets.exists():
        return []
    out = []
    for path in sorted(p.assets.glob("*.toml")):
        try:
            out.append(asset_from_toml(util.load_toml(path)))
        except Exception:  # a malformed asset file must not hide the others
            continue
    return out


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def asset_to_toml(asset: Asset) -> str:
    """Minimal TOML emitter: assayer writes only this one shape."""
    lines = [
        f"label = {_toml_str(asset.label)}",
        f"chain = {_toml_str(asset.chain)}",
        f"address = {_toml_str(asset.address)}",
    ]
    if asset.chain_id:
        lines.append(f"chain_id = {asset.chain_id}")
    for doc in asset.documents:
        lines += ["", "[[documents]]",
                  f"doc_id = {_toml_str(doc.doc_id)}",
                  f"url = {_toml_str(doc.url)}"]
        if doc.version:
            lines.append(f"version = {_toml_str(doc.version)}")
    lines += [
        "",
        "# Declared collateral, used by the supply_vs_reserves slot.",
        "# [reserves]",
        "# claim_doc = \"attestation\"",
        "# claimed_units = 0",
        "# unit = \"share\"",
        "# period_days = 30",
        "# attested_at = \"2026-01-31\"",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- dossier

@dataclass
class Dossier:
    """The trusted artefact. Pure function of a snapshot and an extraction."""

    asset: dict
    snapshot_ts: str
    snapshot_hash: str
    block_number: int | None
    extraction_hash: str | None
    legal: list[Slot]
    onchain: list[Slot]
    changes: list[dict] = field(default_factory=list)
    previous_snapshot_ts: str | None = None
    days_since_previous: int | None = None
    injections: list[dict] = field(default_factory=list)
    suggested_sources: list[dict] = field(default_factory=list)
    tainted_docs: list[str] = field(default_factory=list)
    scars: list[dict] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    # Adversarial theses that survived the same evidence contract as the slots.
    challenge: list[dict] = field(default_factory=list)
    # Thresholds the holder set in the asset file, and which of them were passed.
    alerts: list[dict] = field(default_factory=list)
    counters: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "asset": self.asset,
            "snapshot_ts": self.snapshot_ts,
            "snapshot_hash": self.snapshot_hash,
            "block_number": self.block_number,
            "extraction_hash": self.extraction_hash,
            "slots": {
                "legal": [s.to_json() for s in self.legal],
                "onchain": [s.to_json() for s in self.onchain],
            },
            "changes": self.changes,
            "previous_snapshot_ts": self.previous_snapshot_ts,
            "days_since_previous": self.days_since_previous,
            "injections": self.injections,
            "suggested_sources": self.suggested_sources,
            "tainted_docs": sorted(self.tainted_docs),
            "scars": self.scars,
            "context": self.context,
            "challenge": self.challenge,
            "alerts": self.alerts,
            "counters": self.counters,
            "gate": self.gate,
        }

    @staticmethod
    def from_json(raw: dict) -> "Dossier":
        slots = raw.get("slots", {})
        return Dossier(
            asset=raw.get("asset", {}),
            snapshot_ts=str(raw.get("snapshot_ts", "")),
            snapshot_hash=str(raw.get("snapshot_hash", "")),
            block_number=raw.get("block_number"),
            extraction_hash=raw.get("extraction_hash"),
            legal=[Slot.from_json(s) for s in slots.get("legal", [])],
            onchain=[Slot.from_json(s) for s in slots.get("onchain", [])],
            changes=list(raw.get("changes") or []),
            previous_snapshot_ts=raw.get("previous_snapshot_ts"),
            days_since_previous=raw.get("days_since_previous"),
            injections=list(raw.get("injections") or []),
            suggested_sources=list(raw.get("suggested_sources") or []),
            tainted_docs=list(raw.get("tainted_docs") or []),
            scars=list(raw.get("scars") or []),
            context=list(raw.get("context") or []),
            challenge=list(raw.get("challenge") or []),
            alerts=list(raw.get("alerts") or []),
            counters=dict(raw.get("counters") or {}),
            gate=dict(raw.get("gate") or {}),
        )

    @property
    def slots(self) -> list[Slot]:
        return list(self.legal) + list(self.onchain)
