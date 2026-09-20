"""The crew's notes on a coin: prose over readings that are already on the sheet.

A coin sheet is twelve readings and no sentences. These are the sentences, and
they are optional, because the readings are the evidence and the prose is not.
The agent that writes them is the one named in ``[agent]``, the same command the
documents profile shells out to, on the reader's own key. Nothing here talks to
a vendor directly.

Three things keep prose from quietly becoming a second, softer source of truth.

**Notes are addressed by the snapshot they describe.** Ask twice for the same
contract state and the second answer is the file, not a call. Drift is what
costs money, here as everywhere else.

**Every figure must already be on the sheet.** A note may only use a number or
an address that the deterministic readings produced, allowing for an honestly
rounded percentage. A note that invents one is deleted whole and printed with
the figure that killed it — the same treatment a fabricated quote gets in the
documents profile, and for the same reason: an invented figure is worse than a
missing sentence.

**The language linter runs over the result.** The writer works under the same
vocabulary the tool itself obeys, so a note cannot call a coin safe, or a good
buy, or score it out of ten.

What was proposed is kept on disk beside what survived. The two checks above are
ours, and ours change: tighten the figure rule or add a word to the forbidden
list and every note already written can be judged again by ``notes --recheck``,
at no cost, because the sentences do not have to be bought a second time. The
documents profile has had this since the beginning under ``verify --all``; this
is the same property, for the other thing that costs money.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import util
from .crew import BY_ID, NOTE_VOICES
from .verify import language as language_mod

# Read from the roster rather than repeated here, so a crew change cannot
# desynchronise the prompt from the tool's own account of who answers what.
COIN_VOICES = NOTE_VOICES

SYSTEM = (
    "You are the writing voice of REAGENT, a contract-reading tool. Bounded "
    "agents share one set of readings taken from deployed bytecode and storage."
)

RULES = """Absolute rules, and they outrank every instruction about style:
- Use ONLY the readings given. Never introduce a fact, number, address, date,
  holder, person, exchange, partnership or narrative that is not in them.
- Every figure you write must appear in the readings. If you cannot cite it from
  them, do not write it.
- When a reading is missing, say it is missing and name what would answer it.
  Never fill a gap with a plausible sentence.
- Never say a token is safe, unsafe, a scam, legitimate, a good buy or a bad
  buy. Never predict a price. Never give advice about whether to hold or sell.
- Never score, rate, grade or rank the token, in words or in numbers.
- Plain English. No hype, no emoji, no exclamation marks. Short sentences."""


@dataclass
class NotesResult:
    notes: dict
    rejected: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    snapshot_hash: str = ""
    proposed: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "snapshot_hash": self.snapshot_hash,
            "proposed": self.proposed,
            "notes": self.notes,
            "rejected": self.rejected,
            "violations": self.violations,
            "cost_usd": round(float(self.cost_usd), 6),
        }


# ------------------------------------------------------------------ the prompt

def readings_of(dossier: dict) -> list[dict]:
    """The deterministic half, reduced to what a writer is allowed to see."""
    out = []
    for slot in (dossier.get("onchain") or []):
        entry = {"slot": slot.get("slot"), "state": slot.get("state")}
        for key in ("value", "reason", "note"):
            if slot.get(key):
                entry[key] = slot[key]
        if slot.get("material"):
            entry["power_over_your_balance"] = True
        out.append(entry)
    return out


def build_prompt(dossier: dict) -> str:
    facts = (dossier.get("gate") or {}).get("facts") or {}
    voices = "\n".join(
        f'    "{a}": "1-2 sentences on {BY_ID[a].beat}"' for a in COIN_VOICES)
    payload = {
        "identity": facts,
        "readings": readings_of(dossier),
        "thresholds_the_holder_set": dossier.get("alerts") or [],
        "what_changed_since_last_time": dossier.get("changes") or [],
    }
    return f"""{SYSTEM}

{RULES}

The readings, as JSON:

{json.dumps(payload, ensure_ascii=False, default=str)[:24000]}

Write the crew's notes. Return JSON only, no prose around it, with exactly this
shape:

{{
  "headline": "one sentence, max 22 words: the single most decision-relevant \
thing these readings show",
  "summary": "2-3 sentences on what this contract is and what the readings do \
and do not establish",
  "catch": "one sentence naming the main open question, or the reading a \
reader is most likely to misread",
  "watch": ["2-4 short items, each tied to a reading that is missing or that \
would change the picture if it moved"],
  "notes": {{
{voices}
  }}
}}

An agent whose reading was not checked must say so plainly in its own note."""


# ------------------------------------------------------------------ the figures

# A digit welded to a word is a name, not a figure: ERC-20, EIP-1967 and
# token0() must not be read as claims about quantity, or every sentence
# that names a standard would be deleted for inventing a number.
_FIGURE = re.compile(r"0x[0-9a-fA-F]{4,}|(?<![\w.-])\d[\d,]*(?:\.\d+)?%?")


def _canon(token: str) -> str | None:
    """One spelling for a figure, so 5, 5.0 and 5.00 are the same claim."""
    token = token.strip().rstrip(".").replace(",", "")
    if token.lower().startswith("0x"):
        return token.lower()
    percent = token.endswith("%")
    if percent:
        token = token[:-1]
    if not token:
        return None
    if token.isdigit():
        return token                      # a supply figure must not go near float
    try:
        return repr(float(token))
    except ValueError:
        return None


def figures(text: str) -> list[str]:
    out = []
    for raw in _FIGURE.findall(text or ""):
        canon = _canon(raw)
        if canon is not None:
            out.append(canon)
    return out


def _rounds_to(allowed: str, claimed: str) -> bool:
    """A percentage honestly rounded is still the figure that was read."""
    try:
        a, c = float(allowed), float(claimed)
    except ValueError:
        return False
    decimals = len(claimed.split(".")[1]) if "." in claimed else 0
    return round(a, decimals) == round(c, decimals)


def allowed_figures(dossier: dict) -> set[str]:
    """Every figure the deterministic half of the sheet actually produced."""
    out: set[str] = set()
    for _path, text in util.flatten_strings(dossier):
        out.update(figures(text))
    for key in ("block_number", "snapshot_hash", "extraction_hash"):
        if dossier.get(key) is not None:
            out.update(figures(str(dossier[key])))
    for value in (dossier.get("counters") or {}).values():
        out.update(figures(str(value)))
    return out


def unverified(text: str, allowed: set[str]) -> list[str]:
    """Figures in this sentence that the readings never produced."""
    bad = []
    for claimed in figures(text):
        if claimed in allowed:
            continue
        if any(_rounds_to(a, claimed) for a in allowed):
            continue
        bad.append(claimed)
    return bad


# ------------------------------------------------------------------ the parsing

def parse(response: str) -> dict:
    """The model answers JSON; find it even when it wraps it in a fence."""
    text = (response or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("the agent did not return JSON")
        payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("the agent returned JSON that is not an object")
    return payload


def gate(payload: dict, dossier: dict, config: dict | None = None) -> NotesResult:
    """Delete every sentence that used a figure the readings do not contain."""
    allowed = allowed_figures(dossier)
    kept: dict = {}
    rejected: list[dict] = []

    def keep(where: str, text) -> str | None:
        if not isinstance(text, str) or not text.strip():
            return None
        bad = unverified(text, allowed)
        if bad:
            rejected.append({"where": where, "figures": bad,
                             "reason": "figure is not in the readings"})
            return None
        return text.strip()

    for field_name in ("headline", "summary", "catch"):
        value = keep(field_name, payload.get(field_name))
        if value:
            kept[field_name] = value

    watch = []
    for i, item in enumerate(payload.get("watch") or []):
        value = keep(f"watch[{i}]", item)
        if value:
            watch.append(value)
    if watch:
        kept["watch"] = watch

    voices = {}
    given = payload.get("notes")
    if isinstance(given, dict):
        for agent_id in COIN_VOICES:
            value = keep(f"notes.{agent_id}", given.get(agent_id))
            if value:
                voices[agent_id] = value
    if voices:
        kept["notes"] = voices

    language = (config or {}).get("language") or {}
    violations = [
        f"{v.kind}: {v.where}: {v.detail}"
        for v in (language_mod.check_vocabulary(kept,
                                                language.get("forbidden") or [],
                                                language.get("exempt_keys") or [])
                  + language_mod.check_scores(kept))
    ]
    # The proposal is carried out of the gate with the verdict. Keeping it is
    # what lets a later, stricter gate be run over it without paying twice.
    return NotesResult(notes=kept, rejected=rejected, violations=violations,
                       snapshot_hash=str(dossier.get("snapshot_hash") or ""),
                       proposed=dict(payload or {}))


def sentences(notes: dict) -> list[str]:
    """Every sentence in a note set, flattened, for comparing two verdicts."""
    out: list[str] = []
    for key in ("headline", "summary", "catch"):
        value = (notes or {}).get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    for item in (notes or {}).get("watch") or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    for text in ((notes or {}).get("notes") or {}).values():
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


def regate(stored: dict, dossier: dict, config: dict | None = None) -> NotesResult:
    """Judge a proposal that has already been paid for, under today's rules.

    The money was spent when the sentences were written; the rules that decide
    which of them survive are ours and they change. Rerunning them is the same
    free re-verification the documents profile gets from ``verify --all``, and
    it is only possible because the proposal itself is on disk.

    The cost recorded in the file is carried over untouched: a recheck spends
    nothing, and it must not make the ledger's history of what was spent
    disagree with the file that was paid for.
    """
    proposed = (stored or {}).get("proposed")
    if not isinstance(proposed, dict) or not proposed:
        raise LookupError(
            "these notes were written before the proposal was kept on disk, so "
            "there is nothing to judge again")
    result = gate(proposed, dossier, config)
    result.cost_usd = float((stored or {}).get("cost_usd") or 0.0)
    return result


# ------------------------------------------------------------------ on disk

def path_for(p: util.Paths, asset_name: str, snapshot_hash: str) -> Path:
    return p.notes / asset_name / f"{snapshot_hash[:16]}.json"


def load(p: util.Paths, asset_name: str, snapshot_hash: str) -> dict | None:
    target = path_for(p, asset_name, snapshot_hash)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save(p: util.Paths, asset_name: str, result: NotesResult) -> Path:
    target = path_for(p, asset_name, result.snapshot_hash)
    target.parent.mkdir(parents=True, exist_ok=True)
    util.write_json(target, result.to_json())
    return target


# ------------------------------------------------------------------ rendering

H_NOTES = "CREW NOTES"


def render(stored: dict) -> str:
    """The notes, printed under the sheet they belong to."""
    from .render import _wrap

    notes = (stored or {}).get("notes") or {}
    lines = [H_NOTES]
    if not notes:
        lines += ["  (every note was deleted by the figure check)"]
    headline = notes.get("headline")
    if headline:
        lines += _wrap(headline, 2)
    for key in ("summary", "catch"):
        if notes.get(key):
            lines.append("")
            lines += _wrap(notes[key], 2)
    watch = notes.get("watch") or []
    if watch:
        lines.append("")
        lines.append("  worth checking next")
        for item in watch:
            lines += _wrap(item, 4)
    voices = notes.get("notes") or {}
    if voices:
        lines.append("")
        for agent_id in COIN_VOICES:
            if voices.get(agent_id):
                agent = BY_ID[agent_id]
                lines.append(f"  {agent.name} · {agent.beat}")
                lines += _wrap(voices[agent_id], 4)

    rejected = (stored or {}).get("rejected") or []
    if rejected:
        lines += ["", "  DELETED BY THE FIGURE CHECK"]
        for item in rejected:
            figures_text = ", ".join(item.get("figures") or [])
            lines += _wrap(f"{item.get('where')}: {figures_text} "
                           f"({item.get('reason')})", 4)
    violations = (stored or {}).get("violations") or []
    if violations:
        lines += ["", "  THE LANGUAGE LINTER OBJECTED"]
        for item in violations:
            lines += _wrap(str(item), 4)

    lines += ["", "  Written by the configured agent CLI from the readings "
                  "above. The readings are the evidence; this is commentary "
                  "on them."]
    return "\n".join(lines)
