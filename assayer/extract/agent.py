"""The one place in this codebase where a model is invoked.

REAGENT runs on its own model. ``[model]`` in ``config.toml`` selects it with
``provider = "reagent"``; where it is served and with which key comes from the
environment (``REAGENT_MODEL_URL``, ``REAGENT_MODEL_KEY``, ``REAGENT_MODEL``), so
neither ever sits in a file in this repository. It is spoken to over the
OpenAI-compatible chat API that every serving stack offers.

Where that environment is not set, any agent CLI that takes a prompt and prints
JSON will do: ``[agent]`` names the command, its arguments, and the two fields to
read the response and the cost out of. Nothing else in the package calls a model,
so "did this cost money" is answerable by reading one file.

Without such a CLI installed, ``snap``, ``verify``, ``diff`` and ``show`` all
work; only ``extract`` reports itself unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import ledger, util
from ..corpus import Corpus
from ..model import SCHEMA_VERSION, Asset
from . import prompt as prompt_mod
from . import schema as schema_mod


class AgentUnavailable(RuntimeError):
    pass


class AgentFailed(RuntimeError):
    pass


@dataclass
class AgentCall:
    response: str
    cost_usd: float
    raw_stdout: str
    duration_s: float
    command: list[str]


@dataclass
class ExtractResult:
    extraction: dict
    path: Path
    cost_usd: float
    attempts: int
    parse_errors: list[str] = field(default_factory=list)
    raw_responses: list[str] = field(default_factory=list)


# ------------------------------------------------------------- REAGENT model

MODEL_PROVIDER = "reagent"
MODEL_URL_ENV = "REAGENT_MODEL_URL"
MODEL_KEY_ENV = "REAGENT_MODEL_KEY"
MODEL_NAME_ENV = "REAGENT_MODEL"
DEFAULT_MODEL_NAME = "reagent"


@dataclass
class ModelSettings:
    url: str
    key: str | None
    name: str
    temperature: float
    timeout_s: float
    usd_per_mtok_in: float
    usd_per_mtok_out: float


def model_settings(config: dict) -> ModelSettings | None:
    """REAGENT's own model, when ``[model]`` selects it and it has an endpoint."""
    model = config.get("model") or {}
    if str(model.get("provider") or "").strip().lower() != MODEL_PROVIDER:
        return None
    url = (os.environ.get(MODEL_URL_ENV) or str(model.get("url") or "")).strip()
    if not url:
        return None
    return ModelSettings(
        url=url.rstrip("/"),
        key=os.environ.get(MODEL_KEY_ENV) or None,
        name=(os.environ.get(MODEL_NAME_ENV) or str(model.get("name") or "")
              or DEFAULT_MODEL_NAME).strip(),
        temperature=float(model.get("temperature", 0)),
        timeout_s=float(model.get("timeout_s", 600)),
        usd_per_mtok_in=float(model.get("usd_per_mtok_in", 0) or 0),
        usd_per_mtok_out=float(model.get("usd_per_mtok_out", 0) or 0),
    )


def _host(url: str) -> str:
    """What an error message may print of the endpoint: scheme and host."""
    parts = urllib.parse.urlsplit(url)
    if not parts.hostname:
        return "endpoint"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def call_model(settings: ModelSettings, prompt_text: str) -> AgentCall:
    """One chat completion from REAGENT's model, with the prompt as the user turn."""
    import time

    body = json.dumps({
        "model": settings.name,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": settings.temperature,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "assayer (reagent model)"}
    if settings.key:
        headers["Authorization"] = f"Bearer {settings.key}"
    endpoint = settings.url if settings.url.endswith("/chat/completions") \
        else settings.url + "/chat/completions"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise AgentFailed(f"REAGENT model at {_host(settings.url)} answered "
                          f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AgentUnavailable(f"REAGENT model at {_host(settings.url)} is unreachable: "
                               f"{type(reason).__name__}") from None
    duration = time.monotonic() - started
    try:
        payload = json.loads(raw)
        text = payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise AgentFailed(f"REAGENT model at {_host(settings.url)} returned "
                          f"no completion") from None
    usage = payload.get("usage") or {}
    cost = (float(usage.get("prompt_tokens") or 0) * settings.usd_per_mtok_in
            + float(usage.get("completion_tokens") or 0) * settings.usd_per_mtok_out) / 1e6
    return AgentCall(response=str(text or ""), cost_usd=round(cost, 6), raw_stdout=raw,
                     duration_s=duration, command=[MODEL_PROVIDER, settings.name])


def model_meta(config: dict) -> dict:
    """What an extraction records about the model that produced it."""
    settings = model_settings(config)
    if settings is not None:
        return {"provider": MODEL_PROVIDER, "model": settings.name}
    agent = config.get("agent") or {}
    return {"command": agent.get("command"), "args": list(agent.get("args") or [])}


# ------------------------------------------------------------- agent CLI

def agent_command(config: dict) -> list[str]:
    agent = config.get("agent") or {}
    command = str(agent.get("command") or "").strip()
    if not command:
        raise AgentUnavailable("no [agent] command in config.toml")
    args = [str(a) for a in (agent.get("args") or [])]
    return [command, *args]


def agent_available(config: dict) -> tuple[bool, str]:
    settings = model_settings(config)
    if settings is not None:
        return True, f"REAGENT model {settings.name} at {_host(settings.url)}"
    try:
        command = agent_command(config)
    except AgentUnavailable as exc:
        return False, str(exc)
    resolved = shutil.which(command[0])
    if resolved is None:
        return False, f"{command[0]!r} is not on PATH"
    return True, resolved


def call_agent(config: dict, prompt_text: str) -> AgentCall:
    """Ask REAGENT's model when it is configured, otherwise the agent CLI."""
    settings = model_settings(config)
    if settings is not None:
        return call_model(settings, prompt_text)
    return _call_cli(config, prompt_text)


def _call_cli(config: dict, prompt_text: str) -> AgentCall:
    """Run the configured CLI with the prompt on stdin."""
    import time

    agent = config.get("agent") or {}
    command = agent_command(config)
    available, detail = agent_available(config)
    if not available:
        raise AgentUnavailable(detail)
    placeholder = any("{prompt}" in a for a in command[1:])
    argv = [command[0], *[a.replace("{prompt}", prompt_text) for a in command[1:]]]
    stdin_payload = None if placeholder else prompt_text
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv, input=stdin_payload, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=float(agent.get("timeout_s", 600)))
    except FileNotFoundError as exc:
        raise AgentUnavailable(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise AgentFailed(f"agent timed out after {exc.timeout}s") from exc
    duration = time.monotonic() - started
    if completed.returncode != 0:
        raise AgentFailed(f"agent exited {completed.returncode}: "
                          f"{(completed.stderr or '').strip()[:400]}")

    stdout = completed.stdout or ""
    response_field = str(agent.get("response_field") or "")
    cost_field = str(agent.get("cost_field") or "")
    response = stdout
    cost = 0.0
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        if response_field and response_field in payload:
            response = str(payload.get(response_field) or "")
        if cost_field and cost_field in payload:
            try:
                cost = float(payload.get(cost_field) or 0.0)
            except (TypeError, ValueError):
                cost = 0.0
    return AgentCall(response=response, cost_usd=cost, raw_stdout=stdout,
                     duration_s=duration, command=argv)


def run(p: util.Paths, config: dict, asset: Asset, snapshot: dict, *,
        challenge: bool = False, caller=None) -> ExtractResult:
    """Extract legal slots for one snapshot. Costs money; enforces the budget first."""
    caller = caller or (lambda text: call_agent(config, text))
    corpus = Corpus(p.corpus)
    extract_config = config.get("extract") or {}
    built = prompt_mod.build(
        snapshot, corpus,
        max_chars=int(extract_config.get("max_chars_per_doc",
                                        prompt_mod.DEFAULT_MAX_CHARS)),
        challenge=challenge or bool(extract_config.get("challenge", False)))

    ledger.check(p, config)

    attempts = 0
    errors: list[str] = []
    raw_responses: list[str] = []
    total_cost = 0.0
    parsed: schema_mod.ParseResult | None = None
    prompt_text = built.text

    for attempt in range(2):
        attempts += 1
        call = caller(prompt_text)
        total_cost += float(call.cost_usd or 0.0)
        raw_responses.append(call.response)
        ledger.record(p, call.cost_usd or 0.0, asset=asset.name, kind="extraction",
                      detail=f"attempt {attempts}")
        candidate = schema_mod.parse(call.response)
        if candidate.ok and candidate.slots:
            parsed = candidate
            break
        errors += [f"attempt {attempts}: {e}" for e in candidate.errors] or \
            [f"attempt {attempts}: no slots returned"]
        parsed = candidate
        prompt_text = (built.text + "\n\nYour previous answer was rejected: "
                       + "; ".join(candidate.errors[:6])
                       + "\nReturn one JSON object matching the response shape "
                         "exactly, with no prose.\n")

    slots = parsed.slots if parsed else []
    agent_meta = model_meta(config)
    ehash = schema_mod.extraction_hash(str(snapshot.get("snapshot_hash")),
                                      built.hash, slots, agent_meta)
    extraction = {
        "schema_version": SCHEMA_VERSION,
        "extraction_hash": ehash,
        "asset": snapshot.get("asset"),
        "snapshot_ts": snapshot.get("ts"),
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "docs_hash": snapshot.get("docs_hash"),
        "prompt_hash": built.hash,
        "agent": agent_meta,
        "documents": built.documents,
        "truncated_documents": built.truncated,
        "slots": slots,
        "challenge": parsed.challenge if parsed else [],
        "parse": {"attempts": attempts, "errors": errors},
        "created_at": util.iso(util.utc_now()),
    }

    target = p.extractions / ehash
    util.write_json(target / "extraction.json", extraction)
    util.write_json(target / "cost.json",
                    {"usd": round(total_cost, 6), "attempts": attempts,
                     "asset": asset.name,
                     "snapshot_hash": snapshot.get("snapshot_hash"),
                     "recorded_at": util.iso(util.utc_now())})
    util.atomic_write_text(target / "prompt.txt", built.text)
    for index, raw in enumerate(raw_responses, start=1):
        util.atomic_write_text(target / f"response.{index}.txt", raw)
    util.write_json(p.extractions / "by-snapshot" /
                    f"{snapshot.get('snapshot_hash')}.json",
                    {"extraction_hash": ehash, "asset": asset.name,
                     "snapshot_ts": snapshot.get("ts")})
    util.log_event(p, "extraction", asset=asset.name, extraction_hash=ehash,
                   snapshot_hash=snapshot.get("snapshot_hash"), usd=total_cost,
                   attempts=attempts, slots=len(slots), errors=len(errors))

    return ExtractResult(extraction=extraction, path=target, cost_usd=total_cost,
                         attempts=attempts, parse_errors=errors,
                         raw_responses=raw_responses)


DECOMPOSE_RULES = """\
You are given a short account of something that happened to holders of a
tokenised real-world asset. Decompose it into signal names from the fixed list
below, and nothing else.

RULES
1. Answer with one JSON object and nothing else: {"what": "…", "signals": ["…"]}.
2. "what" is one factual sentence describing what happened, in the language of
   the account you were given. No assessment, no advice, no adjectives about
   how good or bad it was.
3. "signals" may contain only names from the list. A name not on the list is
   discarded, so inventing one wastes the answer.
4. Include a signal only if the account actually supports it. Two accurate
   signals are worth more than six guessed ones: matching is set intersection,
   and a wrong signal produces a false match forever.

SIGNAL NAMES
"""


def decompose_signals(config: dict, story: str,
                      names=None) -> tuple[str | None, list[str], list[str], float]:
    """Turn an account of a failure into signal names.

    The registry is the one place where the plan allows a model in for something
    other than reading documents, and this is that call: it happens once, when a
    human writes an entry down. Its output is filtered against the fixed signal
    vocabulary here, so the model cannot widen it.

    Returns (what, kept signals, discarded names, cost).
    """
    from .. import scars as scars_mod

    names = tuple(names or scars_mod.SIGNAL_NAMES)
    lines = [f"  {name}: {scars_mod.SIGNAL_MEANING.get(name, '')}".rstrip()
             for name in names]
    prompt = (DECOMPOSE_RULES + "\n".join(lines)
              + "\n\nACCOUNT\n" + story.strip() + "\n")
    call = call_agent(config, prompt)
    try:
        payload = schema_mod.extract_json(call.response)
    except ValueError as exc:
        raise AgentFailed(f"the model did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentFailed("the model returned JSON that is not an object")
    proposed = payload.get("signals")
    proposed = [str(s).strip() for s in proposed] if isinstance(proposed, list) else []
    kept = [s for s in dict.fromkeys(proposed) if s in names]
    discarded = [s for s in dict.fromkeys(proposed) if s not in names]
    what = payload.get("what")
    what = str(what).strip() if what else None
    return what, kept, discarded, float(call.cost_usd or 0.0)


def find_extraction(p: util.Paths, snapshot_hash: str) -> tuple[dict, float] | None:
    """The extraction bound to a snapshot hash, with what it cost."""
    pointer = p.extractions / "by-snapshot" / f"{snapshot_hash}.json"
    candidates: list[Path] = []
    if pointer.exists():
        try:
            ehash = str(util.read_json(pointer).get("extraction_hash"))
            candidates.append(p.extractions / ehash)
        except (ValueError, OSError):
            pass
    if not candidates and p.extractions.exists():
        for child in sorted(p.extractions.iterdir()):
            if not child.is_dir() or child.name == "by-snapshot":
                continue
            path = child / "extraction.json"
            if not path.exists():
                continue
            try:
                data = util.read_json(path)
            except (ValueError, OSError):
                continue
            if str(data.get("snapshot_hash")) == snapshot_hash:
                candidates.append(child)
    for directory in candidates:
        path = directory / "extraction.json"
        if not path.exists():
            continue
        extraction = util.read_json(path)
        cost = 0.0
        cost_path = directory / "cost.json"
        if cost_path.exists():
            try:
                cost = float(util.read_json(cost_path).get("usd") or 0.0)
            except (ValueError, OSError, TypeError):
                cost = 0.0
        return extraction, cost
    return None
