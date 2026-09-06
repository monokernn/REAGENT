"""Single entry point. Every subcommand answers and returns a meaningful code.

    0  ok
    1  error
    2  usage
    3  the gate stopped the run (text addressed at the agent was found)
    4  a red line was crossed (forbidden vocabulary or merged sections)
    5  refused by the budget
    6  the agent CLI is unavailable
    7  nothing to do (nothing changed since the last snapshot)
    8  drift found (watch only) — useful as a cron signal
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from . import __version__, diff as diff_mod, ledger, model, scars as scars_mod, util
from .corpus import Corpus, window
from .extract import agent as agent_mod
from .model import E_ABSENCE, E_ONCHAIN, Asset, Document, Dossier
from .snapshot import build as build_mod
from .snapshot import chain as chain_mod
from .snapshot import docs as docs_mod
from .verify import gate as gate_mod
from .verify import injection as injection_mod

OK = 0
ERROR = 1
USAGE = 2
HALTED = 3
RED_LINE = 4
BUDGET = 5
NO_AGENT = 6
NOTHING_TO_DO = 7
DRIFT = 8


def out(text: str = "") -> None:
    print(text)


def _paths_and_config(args) -> tuple[util.Paths, dict]:
    root = Path(args.home).resolve() if getattr(args, "home", None) else None
    p = util.paths(root) if root else util.paths()
    if root is not None:
        p = util.Paths(root)
    return p, util.load_config(p)


def _asset(p: util.Paths, name: str) -> Asset:
    return model.load_asset(p, name)


def _print_injections(findings: Iterable[dict], limit: int = 12) -> None:
    for index, finding in enumerate(findings):
        if index >= limit:
            out(f"    … and {len(list(findings)) - limit} more")
            break
        page = f", p.{finding['page']}" if finding.get("page") else ""
        where = f", {finding['where']}" if finding.get("where") else ""
        out(f"    {finding.get('doc_id')}{page}{where}  "
            f"{finding.get('technique')}  [{finding.get('severity')}]")
        out(f"      {finding.get('excerpt')}")


# ---------------------------------------------------------------- add

def cmd_add(args) -> int:
    p, _config = _paths_and_config(args)
    try:
        chain, address = model.parse_ref(args.ref)
    except ValueError as exc:
        out(f"not an asset reference: {exc}")
        return USAGE
    documents: list[Document] = []
    for entry in args.docs or []:
        if "=" in entry and not entry.split("=", 1)[0].lower().startswith("http"):
            doc_id, url = entry.split("=", 1)
        else:
            url = entry
            stem = Path(urlsplit(url).path).stem or f"doc{len(documents) + 1}"
            doc_id = stem.lower().replace(" ", "-")[:40]
        if any(d.doc_id == doc_id for d in documents):
            doc_id = f"{doc_id}-{len(documents) + 1}"
        documents.append(Document(doc_id=doc_id, url=url))
    asset = Asset(chain=chain, address=address,
                  label=args.label or f"{chain}:{address[:10]}",
                  documents=documents,
                  chain_id=model.CHAIN_IDS.get(chain))
    target = p.assets / f"{asset.name}.toml"
    if target.exists() and not args.force:
        out(f"asset already registered: {target}")
        return ERROR
    util.atomic_write_text(target, model.asset_to_toml(asset))
    out(f"registered {asset.ref}  ->  {target}")
    for doc in documents:
        out(f"  {doc.doc_id}: {doc.url}")
    allowed = list((util.load_config(p).get("sources") or {})
                   .get("allowed_domains") or [])
    missing = sorted({docs_mod.host_of(d.url) for d in documents
                      if not docs_mod.domain_allowed(d.url, allowed)})
    if missing:
        out("")
        out("domains not in the whitelist, fetching will be refused:")
        for host in missing:
            out(f"  {host}")
        out("add them to [sources] allowed_domains in config.toml")
    if not (util.load_config(p).get("rpc") or {}).get(chain):
        out("")
        out(f"no [rpc] {chain} in config.toml: on-chain slots will be "
            f"not_checked: missing_rpc_endpoint")
    return OK


# ---------------------------------------------------------------- snap

def _gate_after_snap(p: util.Paths, config: dict, asset: Asset,
                     snapshot: dict) -> tuple[int, gate_mod.GateResult | None]:
    found = agent_mod.find_extraction(p, str(snapshot.get("snapshot_hash")))
    extraction, cost = found if found else (None, 0.0)
    previous = build_mod.previous_snapshot(p, asset, before=str(snapshot.get("ts")))
    result = gate_mod.build_dossier(p, config, asset, snapshot, extraction,
                                   previous, cost_usd=cost)
    if result.halted:
        out("")
        out("THE GATE STOPPED THE RUN: text addressed at the agent was "
            "found in the documents.")
        _print_injections(result.halting)
        out("")
        out("The person decides from here. Drop the document from the "
            "asset, or turn off [gate] halt_on_injection.")
        return HALTED, result
    if result.violations:
        out("")
        out("RED LINE: the language linter failed the dossier.")
        for violation in result.violations:
            out(f"  {violation}")
        return RED_LINE, result
    gate_mod.write_dossier(p, asset, result)
    return OK, result


def cmd_snap(args) -> int:
    p, config = _paths_and_config(args)
    assets = model.list_assets(p) if args.all else [_asset(p, args.asset)]
    if not assets:
        out("no assets: register the first one with assayer add")
        return ERROR
    worst = OK
    for asset in assets:
        result = build_mod.build(p, config, asset)
        snapshot = result.snapshot
        stored = sum(1 for d in snapshot["documents"] if d["state"] == "stored")
        out(f"{asset.label}  {asset.ref}")
        out(f"  snapshot {result.ts}  block {snapshot['block_number']}  "
            f"documents in corpus {stored}/{len(snapshot['documents'])}")
        out(f"  snapshot_hash {result.snapshot_hash[:16]}  "
            f"docs_hash {snapshot['docs_hash'][:16]}")
        if result.previous_ts is None:
            out("  first snapshot: nothing to compare against yet")
        elif result.changed:
            out(f"  CHANGED since {result.previous_ts}")
            for change in diff_mod.diff_snapshots(result.previous or {}, snapshot):
                mark = "⚠" if change.get("material") else "·"
                out(f"    {mark} {diff_mod.describe(change)}")
        else:
            out("  no changes: there is nothing to pay a model for")
        for document in snapshot["documents"]:
            if document["state"] != "stored":
                out(f"    ! {document['doc_id']}: {document['state']} "
                    f"{document.get('reason')}")
            elif document.get("degraded"):
                out(f"    ! {document['doc_id']}: no text layer, evidence "
                    f"from it is not accepted")
        for alert in result.crossed_alerts:
            out(f"    ⚠ threshold {alert['key']}: {alert['text']}")
        if snapshot["injections"]:
            out(f"  injection findings: {len(snapshot['injections'])}")
        code, _result = _gate_after_snap(p, config, asset, snapshot)
        if code == OK:
            out(f"  dossier: state/dossiers/{asset.name}/{result.ts}/dossier.txt")
        worst = max(worst, code)
        out("")
    return worst


# ---------------------------------------------------------------- extract

def cmd_extract(args) -> int:
    p, config = _paths_and_config(args)
    try:
        asset = _asset(p, args.asset)
    except (FileNotFoundError, ValueError) as exc:
        out(str(exc))
        return ERROR
    snapshots = build_mod.list_snapshots(p, asset)
    if not snapshots:
        out("take a snapshot first: assayer snap " + asset.name)
        return ERROR
    ts = snapshots[-1]
    snapshot = build_mod.load_snapshot(p, asset, ts)

    available, detail = agent_mod.agent_available(config)
    if not available:
        out(f"agent CLI unavailable: {detail}")
        out("snap, verify, diff and show work without it")
        return NO_AGENT

    findings = gate_mod.rescan_injections(snapshot, Corpus(p.corpus), config)
    halting = injection_mod.halting([gate_mod._finding(f) for f in findings], config)
    if halting:
        out("THE GATE STOPPED THE RUN before calling the model: the corpus "
            "carries text addressed at the agent.")
        _print_injections([f.to_json() for f in halting])
        return HALTED

    existing = agent_mod.find_extraction(p, str(snapshot["snapshot_hash"]))
    if existing and not args.force:
        out(f"snapshot_hash {snapshot['snapshot_hash'][:16]} already has an "
            f"extraction: {existing[0]['extraction_hash'][:16]}, "
            f"${existing[1]:.4f}")
        out("nothing to pay for; --force to run it again")
        return NOTHING_TO_DO

    previous = build_mod.previous_snapshot(p, asset, before=ts)
    if previous and not args.force:
        previous_snapshot = previous[1]
        if previous_snapshot.get("snapshot_hash") == snapshot.get("snapshot_hash"):
            out("snapshot_hash has not changed since the previous snapshot: "
                "nothing to pay for")
            return NOTHING_TO_DO
        if previous_snapshot.get("docs_hash") == snapshot.get("docs_hash") and \
                agent_mod.find_extraction(
                    p, str(previous_snapshot.get("snapshot_hash"))):
            out("the documents have not changed since the previous snapshot "
                "and their extraction is already paid for")
            out("--force to call the model anyway")
            return NOTHING_TO_DO

    try:
        result = agent_mod.run(p, config, asset, snapshot, challenge=args.challenge)
    except ledger.BudgetExceeded as exc:
        out(f"budget refused: {exc}")
        return BUDGET
    except agent_mod.AgentUnavailable as exc:
        out(f"agent CLI unavailable: {exc}")
        return NO_AGENT
    except agent_mod.AgentFailed as exc:
        out(f"agent CLI failed: {exc}")
        return ERROR

    out(f"extraction {result.extraction['extraction_hash'][:16]}  "
        f"attempts {result.attempts}  ${result.cost_usd:.4f}")
    if result.parse_errors:
        out("  complaints about the JSON the model returned:")
        for error in result.parse_errors[:6]:
            out(f"    {error}")
    if result.extraction.get("truncated_documents"):
        out("  documents handed to the model truncated: "
            + ", ".join(result.extraction["truncated_documents"]))
    code, gate_result = _gate_after_snap(p, config, asset, snapshot)
    if code == OK and gate_result and gate_result.dossier:
        out("")
        out(gate_result.text or "")
    return code


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    p, config = _paths_and_config(args)
    if args.all:
        targets = model.list_assets(p)
    else:
        try:
            targets = [_asset(p, args.asset)]
        except (FileNotFoundError, ValueError) as exc:
            out(str(exc))
            return ERROR
    if not targets:
        out("no assets")
        return ERROR
    worst = OK
    rebuilt = 0
    for asset in targets:
        timestamps = build_mod.list_snapshots(p, asset)
        if not timestamps:
            out(f"{asset.name}: no snapshots")
            continue
        if not args.all:
            timestamps = timestamps[-1:]
        for ts in timestamps:
            snapshot = build_mod.load_snapshot(p, asset, ts)
            found = agent_mod.find_extraction(p, str(snapshot.get("snapshot_hash")))
            extraction, cost = found if found else (None, 0.0)
            previous = build_mod.previous_snapshot(p, asset, before=ts)
            result = gate_mod.build_dossier(p, config, asset, snapshot, extraction,
                                           previous, cost_usd=cost)
            label = f"{asset.name} {ts}"
            if result.halted:
                out(f"{label}: STOPPED BY THE GATE")
                _print_injections(result.halting, limit=4)
                worst = max(worst, HALTED)
                continue
            if result.violations:
                out(f"{label}: RED LINE")
                for violation in result.violations:
                    out(f"  {violation}")
                worst = max(worst, RED_LINE)
                continue
            gate_mod.write_dossier(p, asset, result)
            rebuilt += 1
            counters = result.dossier.counters if result.dossier else {}
            out(f"{label}: slots {counters.get('slots_filled')}/"
                f"{counters.get('slots_total')}  "
                f"fabrications {counters.get('fabrications')}  "
                f"injections {counters.get('injections')}  "
                f"locator drift {counters.get('locator_drift')}")
    out("")
    out(f"dossiers rebuilt: {rebuilt}; model calls: 0; spent: $0.0000")
    return worst


# ---------------------------------------------------------------- show

def cmd_show(args) -> int:
    p, _config = _paths_and_config(args)
    try:
        asset = _asset(p, args.asset)
    except (FileNotFoundError, ValueError) as exc:
        out(str(exc))
        return ERROR
    timestamps = gate_mod.list_dossiers(p, asset)
    if not timestamps:
        out(f"no dossier yet: assayer snap {asset.name}")
        return ERROR
    ts = args.ts or timestamps[-1]
    if ts not in timestamps:
        matches = [t for t in timestamps if t.startswith(ts)]
        if not matches:
            out(f"no dossier {ts}; available: {', '.join(timestamps[-5:])}")
            return ERROR
        ts = matches[-1]
    path = p.dossiers / asset.name / ts / "dossier.txt"
    if args.json:
        out(util.canonical_json(gate_mod.load_dossier(p, asset, ts)).rstrip())
        return OK
    out(path.read_text(encoding="utf-8").rstrip())
    return OK


# ---------------------------------------------------------------- diff

def cmd_diff(args) -> int:
    p, _config = _paths_and_config(args)
    try:
        asset = _asset(p, args.asset)
    except (FileNotFoundError, ValueError) as exc:
        out(str(exc))
        return ERROR
    if args.dossiers:
        available = gate_mod.list_dossiers(p, asset)
        loader = lambda ts: gate_mod.load_dossier(p, asset, ts)  # noqa: E731
        differ = diff_mod.diff_dossiers
    else:
        available = build_mod.list_snapshots(p, asset)
        loader = lambda ts: build_mod.load_snapshot(p, asset, ts)  # noqa: E731
        differ = diff_mod.diff_snapshots
    if len(available) < 2 and not (args.a and args.b):
        out(f"at least two snapshots are needed; there are {len(available)}")
        return ERROR

    def resolve(token: str | None, default_index: int) -> str | None:
        if token is None:
            return available[default_index] if available else None
        if token in ("last", "latest"):
            return available[-1]
        if token in ("prev", "previous"):
            return available[-2] if len(available) > 1 else None
        matches = [t for t in available if t.startswith(token)]
        return matches[-1] if matches else None

    first = resolve(args.a, -2)
    second = resolve(args.b, -1)
    if not first or not second:
        out(f"snapshots not found; available: {', '.join(available[-6:])}")
        return ERROR
    changes = differ(loader(first), loader(second))
    out(f"{asset.label}  {first} -> {second}")
    if not changes:
        out("  no changes")
        return OK
    for change in changes:
        mark = "⚠" if change.get("material") else "·"
        out(f"  {mark} {diff_mod.describe(change)}")
    return OK


# ---------------------------------------------------------------- evidence

def cmd_evidence(args) -> int:
    p, _config = _paths_and_config(args)
    try:
        asset = _asset(p, args.asset)
    except (FileNotFoundError, ValueError) as exc:
        out(str(exc))
        return ERROR
    timestamps = gate_mod.list_dossiers(p, asset)
    if not timestamps:
        out("no dossier")
        return ERROR
    ts = args.ts or timestamps[-1]
    dossier = Dossier.from_json(gate_mod.load_dossier(p, asset, ts))
    slot = next((s for s in dossier.slots if s.slot == args.slot), None)
    if slot is None:
        out(f"no slot {args.slot!r}; available: "
            + ", ".join(s.slot for s in dossier.slots))
        return ERROR
    out(f"{asset.label}  {args.slot}  [{slot.state}]  snapshot {ts}")
    if slot.value:
        out(f"value: {slot.value}")
    if slot.reason:
        out(f"reason: {slot.reason}")
    if slot.note:
        out(f"note: {slot.note}")
    evidence = slot.evidence
    if evidence is None:
        out("no evidence, which is exactly why the claim never reached the sheet")
        return OK
    if evidence.kind == E_ONCHAIN:
        out(f"onchain: chain_id={evidence.chain_id} address={evidence.address}")
        out(f"  method={evidence.method} block={evidence.block}")
        out(f"  raw={evidence.raw}")
        return OK
    if evidence.kind == E_ABSENCE:
        out("absence: checked and not found:")
        if evidence.checked_docs:
            out("  documents: " + ", ".join(evidence.checked_docs))
        if evidence.patterns:
            out("  patterns: " + ", ".join(evidence.patterns))
        if evidence.checked_selectors:
            out("  selectors: " + ", ".join(evidence.checked_selectors))
        if evidence.block is not None:
            out(f"  block {evidence.block}")
        return OK

    corpus = Corpus(p.corpus)
    try:
        doc = corpus.get(str(evidence.sha256))
    except KeyError as exc:
        out(f"document is not in the corpus: {exc}")
        return ERROR
    text = doc.text
    locator = evidence.locator
    start = locator.start if locator and locator.start is not None else 0
    end = locator.end if locator and locator.end is not None else start
    body, offset = window(text, start, end, pad=args.pad)
    out(f"document {evidence.doc_id}  sha256 {doc.sha256[:16]}  "
        f"p.{locator.page if locator else '?'}  "
        f"char_range [{start}:{end}]"
        + ("  ~OCR" if evidence.degraded else ""))
    out(f"file: {doc.canonical_path}")
    out("")
    prefix = body[:start - offset]
    quoted = body[start - offset:end - offset]
    suffix = body[end - offset:]
    out(prefix + ">>>" + quoted + "<<<" + suffix)
    out("")
    out(f"quote in the dossier: {evidence.quote!r}")
    same = util.normalize_for_compare(quoted) == \
        util.normalize_for_compare(str(evidence.quote))
    out("check: matches" if same else "check: DOES NOT match")
    return OK if same else ERROR


# ---------------------------------------------------------------- watch

def cmd_watch(args) -> int:
    p, config = _paths_and_config(args)
    assets = model.list_assets(p) if not args.asset else [_asset(p, args.asset)]
    if not assets:
        out("no assets")
        return ERROR
    drifted = False
    worst = OK
    for asset in assets:
        result = build_mod.build(p, config, asset)
        snapshot = result.snapshot
        head = f"{asset.label}  {asset.ref}  {result.ts}"
        crossed = result.crossed_alerts
        if not result.changed:
            out(f"{head}  no changes")
        else:
            previous = result.previous or {}
            changes = diff_mod.diff_snapshots(previous, snapshot) if previous else []
            material = diff_mod.material(changes)
            out(f"{head}  CHANGES: {len(changes)}"
                + (f", material {len(material)}" if material else ""))
            for change in changes:
                mark = "⚠" if change.get("material") else "·"
                out(f"    {mark} {diff_mod.describe(change)}")
            if changes:
                drifted = True
            if previous and previous.get("docs_hash") != snapshot.get("docs_hash"):
                out("    the documents changed: extract is worth running")
        for alert in crossed:
            out(f"    ⚠ threshold {alert['key']}: {alert['text']}")
        if crossed:
            drifted = True
        code, _result = _gate_after_snap(p, config, asset, snapshot)
        worst = max(worst, code)
    if worst != OK:
        return worst
    return DRIFT if drifted else OK


# ---------------------------------------------------------------- scar

def cmd_scar(args) -> int:
    p, config = _paths_and_config(args)
    if args.scar_command == "signals":
        out("signals that snapshots produce:")
        for name in scars_mod.SIGNAL_NAMES:
            out(f"  {name}: {scars_mod.SIGNAL_MEANING.get(name, '')}")
        return OK
    if args.scar_command != "new":
        out("assayer scar new --what … --signals a,b [--from-text …]")
        return USAGE

    signals = [s.strip() for s in (args.signals or "").split(",") if s.strip()]
    what = args.what

    if args.from_text:
        story = args.from_text
        if story == "-":
            story = sys.stdin.read()
        elif len(story) < 260 and Path(story).exists():
            story = Path(story).read_text(encoding="utf-8")
        available, detail = agent_mod.agent_available(config)
        if not available:
            out(f"agent CLI unavailable: {detail}")
            out("signals can be listed by hand: --signals a,b")
            return NO_AGENT
        try:
            ledger.check(p, config)
            proposed_what, proposed, discarded, cost = agent_mod.decompose_signals(
                config, story)
        except ledger.BudgetExceeded as exc:
            out(f"budget refused: {exc}")
            return BUDGET
        except (agent_mod.AgentUnavailable, agent_mod.AgentFailed) as exc:
            out(f"agent CLI: {exc}")
            return ERROR
        ledger.record(p, cost, asset="scars", kind="scar_decompose")
        signals = list(dict.fromkeys(signals + proposed))
        what = what or proposed_what
        out(f"the model decomposed the account into signals for ${cost:.4f}")
        if discarded:
            out("  discarded, no such signals exist: " + ", ".join(discarded))

    if not what or not signals:
        out("--what and --signals are required, or --from-text with an agent CLI")
        return USAGE

    unknown = scars_mod.unknown_signals(signals)
    signals = [s for s in signals if s not in unknown]
    if not signals:
        out("no snapshot produces any of these signals: " + ", ".join(unknown))
        out("the list: assayer scar signals")
        return USAGE

    scar = scars_mod.Scar(
        id=args.id or scars_mod.next_id(p.scars),
        date=args.date or util.utc_now().strftime("%Y-%m-%d"),
        what=what, signals=signals, source=args.source)
    scars_mod.append(p.scars, scar)
    out(f"written {scar.id}: {scar.what}")
    out("  signals: " + ", ".join(sorted(signals)))
    if unknown:
        out("  not written, no snapshot produces them: " + ", ".join(unknown))
    neighbours = {i for m in scars_mod.match(signals, scars_mod.load(p.scars))
                  for i in m["ids"]} - {scar.id}
    if neighbours:
        out("  overlaps with cases already recorded: "
            + ", ".join(sorted(neighbours)))
    return OK


# ---------------------------------------------------------------- status

def cmd_status(args) -> int:
    p, config = _paths_and_config(args)
    assets = model.list_assets(p)
    budget = ledger.state(p)
    out(f"assayer {__version__}   root {p.root}")
    out("")
    if not assets:
        out("no assets: assayer add <chain>:<address> --docs <url>")
    for asset in assets:
        timestamps = build_mod.list_snapshots(p, asset)
        dossiers = gate_mod.list_dossiers(p, asset)
        line = f"{asset.label}  {asset.ref}"
        out(line)
        out(f"  documents declared {len(asset.documents)}  "
            f"snapshots {len(timestamps)}  dossiers {len(dossiers)}")
        if timestamps:
            snapshot = build_mod.load_snapshot(p, asset, timestamps[-1])
            stored = sum(1 for d in snapshot["documents"] if d["state"] == "stored")
            out(f"  latest {timestamps[-1]}  block {snapshot.get('block_number')}"
                f"  in corpus {stored}/{len(snapshot['documents'])}"
                f"  snapshot_hash {str(snapshot.get('snapshot_hash'))[:16]}")
            found = agent_mod.find_extraction(p, str(snapshot.get("snapshot_hash")))
            out("  extraction: " + (f"present, ${found[1]:.4f}" if found
                                   else "none, the model was never called"))
    out("")
    corpus = Corpus(p.corpus)
    out(f"corpus: documents {len(corpus.list())}   "
        f"scars: {len(scars_mod.load(p.scars))}")
    budget_config = config.get("budget") or {}
    out(f"spend: today ${budget.spent_today:.4f} of "
        f"${float(budget_config.get('max_usd_per_day', 0)):.2f}   "
        f"total ${budget.total:.4f}   calls today {budget.calls_today}")
    if budget.last_extraction_usd:
        per_call = float(budget_config.get("max_usd_per_extraction", 0))
        out(f"last extraction: ${budget.last_extraction_usd:.4f} "
            f"(per-call limit ${per_call:.2f})")
    return OK


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    p, config = _paths_and_config(args)
    problems = 0
    out(f"config: {p.config}" + ("" if p.config.exists() else "  MISSING"))
    if not p.config.exists():
        problems += 1
    try:
        p.state.mkdir(parents=True, exist_ok=True)
        probe = p.state / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out(f"state: {p.state}  writable")
    except OSError as exc:
        out(f"state: {p.state}  NOT WRITABLE: {exc}")
        problems += 1

    try:
        import pypdf

        out(f"pypdf: {pypdf.__version__}")
    except ImportError:
        out("pypdf: MISSING, PDFs will not be read (pip install pypdf)")
        problems += 1

    if (config.get("corpus") or {}).get("ocr"):
        out("ocr: enabled in the config but not built in, so a scan enters "
            "the corpus with no text layer")
        out("  neither a quote nor an absence is accepted from such a "
            "document: slots go to not_checked: document_has_no_text_layer")

    available, detail = agent_mod.agent_available(config)
    out("agent CLI: " + (f"present, {detail}" if available
                         else f"missing, {detail}"))
    if not available:
        out("  snap / verify / diff / show work; extract is unavailable")

    rpc = config.get("rpc") or {}
    if not rpc:
        out("rpc: no chain configured, on-chain slots will be not_checked")
        problems += 1
    for chain, url in sorted(rpc.items()):
        client = chain_mod.JsonRpc(str(url), timeout_s=args.timeout)
        try:
            block = int(client.call("eth_blockNumber", []), 16)
            chain_id = int(client.call("eth_chainId", []), 16)
            out(f"rpc {chain}: block {block}  chain_id {chain_id}")
        except (chain_mod.RpcError, ValueError) as exc:
            out(f"rpc {chain}: UNREACHABLE, {exc}")
            problems += 1

    allowed = list((config.get("sources") or {}).get("allowed_domains") or [])
    out(f"whitelisted domains: {', '.join(allowed) if allowed else 'none'}")
    assets = model.list_assets(p)
    out(f"assets: {len(assets)}")
    for asset in assets:
        for document in asset.documents:
            host = docs_mod.host_of(document.url)
            if not docs_mod.domain_allowed(document.url, allowed):
                out(f"  {asset.name} {document.doc_id}: domain {host} is "
                    f"outside the whitelist and will be refused")
                problems += 1
                continue
            if args.network:
                status = _probe(document.url, allowed, args.timeout)
                out(f"  {asset.name} {document.doc_id}: {status}")
    if not args.network:
        out("  (document availability was not checked: --network)")

    # The corpus is the one place where a silent local edit would go unnoticed
    # and still be cited: every locator addresses bytes in it.
    corpus = Corpus(p.corpus)
    stored = corpus.list()
    faults = corpus.check(deep=args.deep)
    out(f"corpus: {len(stored)} documents"
        + (", re-hashed" if args.deep else ", presence only (--deep to re-hash)"))
    for fault in faults:
        out(f"  {fault.sha256[:12]}: {fault.kind}. {fault.detail}")
        problems += 1
    if not faults and stored:
        out("  every document still answers to the name it is filed under"
            if args.deep else "  nothing missing")

    out("")
    out("problems: " + str(problems))
    return OK if problems == 0 else ERROR


def _probe(url: str, allowed: list[str], timeout: float) -> str:
    import urllib.error
    import urllib.request

    if not docs_mod.domain_allowed(url, allowed):
        return "domain outside the whitelist"
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": docs_mod.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (f"{getattr(response, 'status', 200)} "
                    f"{response.headers.get('Content-Type', '')}").strip()
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assayer",
        description="Evidence-backed surveillance of tokenised real-world assets. "
                    "Read-only: no keys, no transactions, no advice.")
    parser.add_argument("--version", action="version", version=f"assayer {__version__}")
    parser.add_argument("--home", help="project root (default: search upwards, "
                                       "or $ASSAYER_HOME)")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="register an asset")
    add.add_argument("ref", help="<chain>:<address>")
    add.add_argument("--docs", nargs="*", default=[],
                     help="url or doc_id=url, repeatable")
    add.add_argument("--label")
    add.add_argument("--force", action="store_true")
    add.set_defaults(func=cmd_add)

    snap = sub.add_parser("snap", help="snapshot: free, no model")
    snap.add_argument("asset", nargs="?")
    snap.add_argument("--all", action="store_true")
    snap.set_defaults(func=cmd_snap)

    extract = sub.add_parser("extract", help="the paid phase: calls a model")
    extract.add_argument("asset")
    extract.add_argument("--force", action="store_true",
                         help="call the model even if the snapshot is unchanged")
    extract.add_argument("--challenge", action="store_true",
                         help="add the second mandate: how this ends badly")
    extract.set_defaults(func=cmd_extract)

    verify = sub.add_parser("verify", help="run the gate again, free")
    verify.add_argument("asset", nargs="?")
    verify.add_argument("--all", action="store_true",
                        help="every asset and every snapshot")
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show", help="the latest dossier")
    show.add_argument("asset")
    show.add_argument("--ts", help="a specific snapshot")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)

    diff = sub.add_parser("diff", help="what changed between two snapshots")
    diff.add_argument("asset")
    diff.add_argument("a", nargs="?", help="ts, prev or last")
    diff.add_argument("b", nargs="?", help="ts, prev or last")
    diff.add_argument("--dossiers", action="store_true",
                      help="compare dossiers instead of snapshots")
    diff.set_defaults(func=cmd_diff)

    evidence = sub.add_parser("evidence", help="the text around a quote")
    evidence.add_argument("asset")
    evidence.add_argument("slot")
    evidence.add_argument("--ts")
    evidence.add_argument("--pad", type=int, default=240)
    evidence.set_defaults(func=cmd_evidence)

    watch = sub.add_parser("watch", help="snapshot every asset, alert on drift")
    watch.add_argument("--asset")
    watch.set_defaults(func=cmd_watch)

    scar = sub.add_parser("scar", help="registry of known cases")
    scar.add_argument("scar_command", choices=["new", "signals"],
                      help="new: record a case; signals: list the signals")
    scar.add_argument("--what", help="one sentence: what happened")
    scar.add_argument("--signals", help="comma separated")
    scar.add_argument("--from-text", dest="from_text",
                      help="the account text, a file path, or '-' for stdin: "
                           "decompose it into signals with a model (costs money)")
    scar.add_argument("--date")
    scar.add_argument("--source")
    scar.add_argument("--id")
    scar.set_defaults(func=cmd_scar)

    status = sub.add_parser("status", help="assets, spend, state")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="RPC, documents, agent CLI")
    doctor.add_argument("--network", action="store_true",
                        help="check document availability over the network")
    doctor.add_argument("--deep", action="store_true",
                        help="re-hash every stored document against its own name")
    doctor.add_argument("--timeout", type=float, default=15.0)
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            # noqa/ignore: only file-like streams carry reconfigure
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return USAGE
    if args.command == "snap" and not args.asset and not args.all:
        out("an asset or --all is required")
        return USAGE
    if args.command == "verify" and not args.asset and not args.all:
        out("an asset or --all is required")
        return USAGE
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError) as exc:
        out(f"error: {exc}")
        return ERROR
    except KeyboardInterrupt:
        out("interrupted")
        return ERROR


if __name__ == "__main__":
    raise SystemExit(main())
