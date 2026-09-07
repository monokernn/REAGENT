# REAGENT (RWA Evidence Agent)

![tests](https://img.shields.io/badge/tests-100%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

**REAGENT (RWA Evidence Agent)** takes the contract address of a tokenised
real-world asset and the issuer's documents, and produces one readable sheet.

It answers four questions:

- what you hold legally,
- what the issuer can do to your balance technically,
- what changed since the last check,
- which piece of evidence each statement rests on.

A blockchain keeps an honest record of the token. It keeps no record of the
existence or the status of the real asset: that lives in the issuer's prospectus,
in the custodian's statement, in the jurisdiction of incorporation, and in the
powers the issuer kept for itself inside the contract. The holder sees a company
name and a chart. The prospectus may say this is a debt note of an offshore
issuer carrying no shareholder rights, and the contract may say the issuer can
freeze any address and replace the logic with no delay. All of it is public, and
none of it gets read: the documents are long, the contracts are unreadable by
eye, and both change quietly. REAGENT works in that gap.

Locally. No private keys, no transactions, no opinion about what you should do.
The tool produces a document; the person decides.

> A *reagent* is what an assay office puts a sample through to find out what
> the metal is actually made of. Hence the name.

![assayer in a terminal](media/demo.gif)

*A real session, rendered frame by frame: the documents come over HTTP from a
local server, the on-chain slots over JSON-RPC from a local stub, and between the
two snapshots the prospectus really is replaced behind the tool's back. Only the
issuer is fictional. Nothing here costs a token: `extract` is not even available,
since no agent CLI is installed.*

**A live illustration of the gap.** Robinhood Stock Tokens: the interface shows
the ticker and the price of a familiar company, while the prospectus describes
tokenised debt securities of an issuer incorporated in Jersey. They give economic
exposure to the price of the underlying share, but they do not make the holder a
shareholder and carry no shareholder rights. This is not an assessment of the
company or of the instrument. It is an illustration that the interface and the
document say different things, and that nobody reads the second one.

---

## Table of contents

| section | what is in it |
|---|---|
| [The idea](#the-idea) | three artefacts, and what falls out of separating them |
| [Example dossier](#example-dossier) | the actual output, in full |
| [Start](#start) | install, configure, first snapshot |
| [How it works](#how-it-works) | snapshot, extract, verify |
| [The gate](#the-gate) | how fabrications and injections are caught |
| [What this is not](#what-this-is-not) | stated plainly |
| [Cost](#cost) | real numbers |
| [Requirements](#requirements) | Python, RPC, optional agent CLI |
| [Tests](#tests) | what is held by a blocking test |
| [The registry](#the-registry-of-known-cases) | signal matching against past failures |

---

## The idea

One observation, from which everything else follows:

> Extraction is expensive and untrusted. Verification is cheap and trusted.
> Therefore they must not be the same operation.

Hence three separate artefact types on disk, each addressed by its content:

| artefact | produced by | cost | trust |
|---|---|---|---|
| **snapshot** | scripts: on-chain readers and document fetches | ~0 | trusted, reproducible |
| **extraction** | a model, reading the documents in a snapshot | money | **untrusted** |
| **dossier** | the verifier: extraction intersected with snapshot | ~0 | trusted |

Four properties fall out of the separation for free.

**Reproducibility.** `dossier = f(snapshot, extraction)` is a pure function. A
dossier carries no wall-clock stamp of when it was produced; its date comes from
the snapshot. Two runs over the same inputs produce a byte-identical
`dossier.json`, and a test holds that.

**Free re-verification.** Improve the rules of the gate, then run them over the
whole archive of old extractions without a single model call:
`assayer verify --all`.

**Drift as the base case, not a feature.** Snapshots are structured and
content-addressed, so "what changed" is not a subsystem. It is subtraction.

**Separate budgets.** Observation costs nothing and can run every hour. You pay
only when a snapshot has actually changed.

The product here is not "AI reads a prospectus". It is tamper-evident
surveillance of drift, with every claim traceable to the bytes it came from.

---

## Example dossier

```
ASSAY  ·  Assay Note ASSY  ·  ethereum:0x1111111111111111111111111111111111111111  ·  2026-02-11

!  The gate caught fabricated quotes: 1. See "REJECTED BY THE GATE" below.

WHAT THIS IS
  instrument: debt_security, a note of the issuer giving economic exposure,    [prospectus, p.1]
  not a share in the company
  issuer: Assay Issuer Limited, incorporated in Jersey                         [prospectus, p.1]
  holder rights: no voting rights and no shareholder rights in the underlying  [prospectus, p.2]
  asset
  issuer insolvency: holders rank as unsecured creditors; the underlying       [prospectus, p.2]
  asset is not segregated
  custodian: Assay Custody Services Limited; no independent attestor is named     [custody, p.1]

CHANGES  (previous snapshot: 7 days ago)
  ⚠ document prospectus: content was substituted (e3c43c39dd8f -> c57e3440d840); the      [diff]
  declared version stayed 2026-01, the content differs
  · totalSupply: 1240000000000000000000000 -> 1310000000000000000000000                   [diff]

WHAT THE ISSUER CAN DO
  ⚠ mint_authority: minting is possible: mint(address,uint256);          [chain, block 21345678]
  controlled by 0x6666666666666666666666666666666666666666 (multisig, 2
  of 3)
  ⚠ burn_authority: can reduce a balance it does not own:                [chain, block 21345678]
  burn(address,uint256); controlled by
  0x6666666666666666666666666666666666666666 (multisig, 2 of 3)
  ⚠ pause: transfers can be halted; controlled by                        [chain, block 21345678]
  0x6666666666666666666666666666666666666666 (multisig, 2 of 3)
  ⚠ upgradeable: proxy (eip1967); the logic lives at                     [chain, block 21345678]
  0x3333333333333333333333333333333333333333; upgrade controlled by
  0x5555555555555555555555555555555555555555 (timelock, delay 0s);
  timelock delay reads 0s: an upgrade takes effect with no waiting
  period
  ⚠ freeze: any address can be blocked from moving its balance:          [chain, block 21345678]
  blacklist(address), isBlacklisted(address); controlled by
  0x6666666666666666666666666666666666666666 (multisig, 2 of 3)
  ○ oracle: the token contract reads no price source                   [absence, block 21345678]
  ⚠ supply_vs_reserves: totalSupply 1310000 against declared collateral  [chain, block 21345678]
  1200000 share; 110000 more tokens than declared collateral (9.17%);
  the collateral figure is declared in the asset file, sourced from
  custody
  · attestation_freshness: reserve report dated 2026-01-31, 11 days old at        [custody, p.1]
  snapshot time; declared period 30 days

THRESHOLDS SET IN THE ASSET FILE
  · the reserve report is 11 days old, threshold is 30 days                         [asset file]
  ⚠ the delay on the upgrade path is 0s, minimum set to 172800s (threshold          [asset file]
  crossed)
  ⚠ tokens exceed the declared collateral by 9.17%, threshold is 1.0% (threshold    [asset file]
  crossed)
  ⚠ the issuer has an address-freeze function (threshold crossed)                   [asset file]

NOT FOUND IN THE DOCUMENTS
  ✗ corporate_actions: the checked documents say nothing about           [absence, documents: 2]
  dividends, splits or delisting. Documents checked: 2.

NOT CHECKED
  ? restrictions: document_is_a_scan. the restrictions section is a scan with no text layer

REJECTED BY THE GATE
  ⨯ redemption: fabricated_quote. the quote does not occur in the canonical text

HOW THIS CAN END FOR THE HOLDER
  ! getting out depends on the issuer: it may decline a redemption request at  [prospectus, p.3]
  its own discretion
  ! on an insolvency of the issuer the holder joins the general queue of       [prospectus, p.2]
  unsecured creditors

SIMILAR CASES
  · issuer_can_freeze_address, offshore_issuer, upgrade_no_timelock                 [scars: 011]
  · offshore_issuer, self_attested_reserves, upgrade_no_timelock                    [scars: 019]

SUGGESTED SOURCES
  Links inside documents are never followed. Add a domain by hand in config.toml.
  · filings.example.org, from custody, mentions: 1

CONTEXT (NOT EVIDENCE)
  · document terms did not reach the corpus: doc_unavailable
  · a thesis was cut by the gate (claim_without_evidence) and did not reach the document

COMPLETENESS: 12/16 slots   FABRICATIONS: 1   INJECTIONS: 0   SPENT: $0.1873
snapshot 0f0dffc3cbc5  ·  extraction d7a8561316ac  ·  block 21345678
This sheet shows what the sources say and what the contract records. The person decides.
```

### Three sections that never merge

| section | what it is a fact about |
|---|---|
| `NOT FOUND IN THE DOCUMENTS` | **the asset.** We looked; the documents do not say it. |
| `NOT CHECKED` | **the tool.** It failed to look, and here is the reason. |
| `REJECTED BY THE GATE` | **the model.** It made a claim and the evidence did not hold. |

A blocking linter enforces the separation, not the author's care. Collapsing the
first two would turn an outage at an RPC provider into a statement that the issuer
holds no such power.

### Two sections worth explaining

**`THRESHOLDS SET IN THE ASSET FILE`** holds your numbers, not a judgement by the
tool. It does not decide that two days of timelock is enough. It reports that the
number you wrote down was crossed, and prints the reading next to the threshold. A
threshold that cannot be evaluated, because the RPC was down or the proxy pattern
was not recognised, is marked as not applied rather than as satisfied.

**`HOW THIS CAN END FOR THE HOLDER`** is the adversarial second mandate: same
corpus, opposite question. Its theses pass the same evidence contract as the
slots, so "may not scale" never reaches the sheet while "redemption depends on the
issuer's discretion" reaches it with a page number. Enabled with `--challenge`.

### Marker vocabulary

| marker | meaning |
|---|---|
| `⚠` | a power over somebody else's balance, a material change, or a crossed threshold |
| `·` | a fact with no direct effect on the holder's balance |
| `○` | a power that is absent: the function is not in the bytecode |
| `✗` | not found in the documents |
| `?` | not checked, with a reason |
| `⨯` | rejected by the gate |
| `!` | an adversarial thesis with a verified quote |
| `⚑` | text found that speaks to the agent |

---

## Start

```bash
git clone https://github.com/monokernn/REAGENT.git reagent && cd reagent
pip install -e .
```

Put an RPC endpoint and the domains you allow documents to be fetched from into
`config.toml`:

```toml
[rpc]
ethereum = "https://…"

[sources]
allowed_domains = ["docs.issuer.example", "issuer.example"]
```

Check the environment, then register the first asset:

```bash
assayer doctor
assayer add ethereum:0xADDRESS --label "Assay Note" \
  --docs prospectus=https://docs.issuer.example/prospectus.pdf \
         custody=https://docs.issuer.example/custody.html
assayer snap ethereum-0xaddress
assayer show ethereum-0xaddress
```

`snap` is free and calls no model. On its own it already gives you a monitor of
on-chain powers and of document substitution.

### Commands

| command | what it does | cost |
|---|---|---|
| `assayer add <chain>:<address> --docs …` | register an asset | 0 |
| `assayer snap <asset> [--all]` | take a snapshot; says whether anything changed | 0 |
| `assayer extract <asset> [--force] [--challenge]` | fill the legal slots with a model | money |
| `assayer verify <asset> [--all]` | run the gate again | 0 |
| `assayer show <asset> [--json]` | the latest dossier | 0 |
| `assayer diff <asset> [a] [b]` | what changed between two snapshots | 0 |
| `assayer evidence <asset> <slot>` | the window of text around a quote | 0 |
| `assayer watch [--asset X]` | snapshot every asset, alert on drift | 0 |
| `assayer scar new --what … --signals …` | write a case into the registry | 0 |
| `assayer scar new --from-text <file\|->` | decompose an account into signals | money |
| `assayer scar signals` | the signal names snapshots produce | 0 |
| `assayer status` | assets, spend, state | 0 |
| `assayer doctor [--network] [--deep]` | RPC, documents, agent CLI, corpus integrity | 0 |

### Exit codes

| code | meaning |
|---|---|
| `0` | ok |
| `1` | error |
| `2` | usage |
| `3` | the gate stopped the run |
| `4` | a red line was crossed |
| `5` | refused by the budget |
| `6` | no agent CLI |
| `7` | nothing to do |
| `8` | drift found (`watch` only) |

`assayer watch` is built for cron: it spends nothing and returns 8 when something
moved.

---

## How it works

### snapshot: zero tokens

A fully deterministic phase. Eight on-chain slots are read by scripts alone:

| slot | what is read |
|---|---|
| `mint_authority` | who may increase supply, EOA or multisig, threshold |
| `burn_authority` | whether a balance the issuer does not own can be reduced |
| `pause` | whether transfers can be halted, and by whom |
| `upgradeable` | proxy pattern, admin, timelock and its delay |
| `freeze` | whether any address can be blocked from moving its balance |
| `oracle` | the price source and its staleness |
| `supply_vs_reserves` | totalSupply against the declared collateral |
| `attestation_freshness` | the age of the reserve report against the promised period |

Documents are fetched from allowed domains, their text is extracted exactly once,
and they enter the corpus under their sha256. The result is `snapshot.json` with
two hashes: `docs_hash` over the document set, and `snapshot_hash` over the whole
observed identity of the asset.

Neither hash covers the block number or the clock. Otherwise every new block would
look like a change, and "pay only for drift" would decay into "pay always".

### extract: the only phase that costs money

The model receives the canonical texts and a fixed questionnaire of eight legal
slots: instrument type, issuer, holder rights, insolvency, custodian, corporate
actions, redemption, restrictions. No open questions are asked, because an open
question produces prose that cannot be diffed against the same question next
month.

Rules the phase enforces:

- the answer is JSON only, no prose;
- every filled slot carries a doc_id, the document's sha256 and a verbatim quote;
- offsets are computed by the gate, never trusted from the model;
- invalid JSON gets one retry, after which slots are marked `not_checked`;
- if `snapshot_hash` has not changed, the phase refuses to spend anything.

### verify: the gate

Deterministic code, zero model calls. Output is `dossier.json` and `dossier.txt`.
It can be re-run over existing extractions as often as you like.

---

## The gate

Four independent checks, all in `assayer/verify/`, all without a model. **The model
does not verify itself.**

### Quotes

Every claim is checked against the bytes it cites. The span is cut out of
`canonical.txt`, both sides are normalised for what extractors are allowed to
disagree about (whitespace, dashes, quotes, case), and compared.

| outcome | what the gate does |
|---|---|
| the span matches | the slot passes |
| no offsets were offered, the quote is found by search | the slot passes |
| offsets drifted, the quote is found by search | the locator is repaired, the slot passes |
| the quote is absent | the slot is deleted and printed as rejected |

A rejected slot does not "lose confidence". It is deleted whole, and the deletion
is printed with its reason. Three inventions from one document and that document
is marked `tainted`, printed with a warning. The tool catching its own model
inventing a source, and showing the reader, is the only direct evidence that the
gate works.

### Injections

Documents come from the issuer's web server: an untrusted source. Somebody who
knows a machine will read the prospectus can hide a sentence in it for the
machine.

| surface | what is examined |
|---|---|
| HTML | `display:none`, `opacity:0`, zero font size, `clip-path`, off-viewport positioning, text colour equal to background, `aria-hidden`, comments, and rules from `<style>` applied by class or id |
| PDF | text rendering mode 3, fonts under 1pt, text outside the MediaBox, `/Info` and XMP metadata, annotations, attachments |
| text | phrasing addressed at a system, in any of the configured languages |

The response is to stop and print what was found, in which document, on which
page, and by which technique it was hidden. Quietly scrubbing the document would
leave the reader believing the corpus is honest, which is worse than refusing to
continue.

Detection is free, so it runs twice: during the snapshot, before any paid call,
and again at the gate under current rules.

### Provenance

- Fetching happens only from domains a human entered by hand, and the check
  precedes the socket.
- A redirect leaving the whitelist aborts the fetch.
- Links found inside documents are never followed. They surface as a suggestion,
  and a human adds the domain.

### Language

The dossier is checked against a forbidden vocabulary. A hit fails the run rather
than being rewritten, because rewriting would hide that the drift happened.
Verbatim quotes are exempt: a prospectus may contain any word it likes, and
quoting it accurately is the whole job.

The linter also enforces that there is no numeric scoring anywhere, in text or in
field names, and that the three slot states render into three distinct sections.

Details: [docs/verification.md](docs/verification.md),
[docs/evidence.md](docs/evidence.md).

---

## What this is not

**Not an audit. Not investment advice. It does not establish that the collateral
exists.**

REAGENT shows what the documents say and what the contract records, and states
just as loudly what it could not check.

- An empty slot is a result. "Three documents say nothing about what happens if
  the issuer becomes insolvent" is often the most valuable finding on the sheet,
  and most tools of this kind pass over it in silence.
- A failed RPC yields `not_checked` with a reason, never `not_found`.
- A document with no text layer is monitored for substitution but cannot carry
  evidence. Otherwise an empty scan would answer "the documents do not say" to
  every question.
- A prospectus may lie. The tool shows what it actually says, from which page and
  at which block, so a person can check it themselves in ten seconds:

```bash
assayer evidence <asset> holder_rights
```

There is no assessment of the asset in the output: no score, no rating, no
suggestion to act. That is a property of the product, not a disclaimer. A tool
that slid from "the document says X" to "this asset is sound" would become a
source of false confidence and would be worse than no tool at all.

---

## Cost

**Observation is free.** A snapshot is a handful of read-only RPC calls and some
document fetches: zero tokens, zero dollars, as often as you like. The gate is
free too, which is why `assayer verify --all` re-checks the whole archive under
improved rules at no cost.

**You pay for drift only.** `extract` calls the model, and refuses to do so if
`snapshot_hash` has not changed since last time, or if the documents are the same
ones you already paid to read.

| situation | cost |
|---|---|
| `snap`, `watch`, `verify`, `show`, `diff`, `evidence` | $0.00 |
| one extraction over two documents | roughly $0.10 to $0.40 |
| any number of runs while the documents are unchanged | $0.00 |

Two limits live in `[budget]`:

- `max_usd_per_day` refuses the next extraction once the day's total is reached.
- `max_usd_per_extraction` cannot interrupt a call that is already running, so its
  cost is recorded and the next call refuses to start until a person has looked.

Every spend lands in `state/budget.json` and `state/log.jsonl`, written atomically
and safe against concurrent writers.

---

## Requirements

- Python 3.10+
- an RPC endpoint per observed chain, read-only
- `pypdf` for PDFs, and `tomli` on 3.10; both install with the package
- any agent CLI on PATH, optional

The agent CLI is declared in `config.toml`. Anything that accepts a prompt and
returns JSON will do: change the command, the args and two field names.

```toml
[agent]
command = "claude"
args = ["-p", "--output-format", "json"]
response_field = "result"
cost_field = "total_cost_usd"
```

With no CLI installed, `snap`, `verify`, `diff`, `show` and `watch` work in full,
and `extract` reports itself unavailable and returns code 6.

---

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The twenty-two tests from the build plan are numbered in the docstring of each
file:

| file | plan tests | subject |
|---|---|---|
| `test_red_lines.py` | 1 to 3 | no keys, no transactions, no advice |
| `test_gate_quotes.py` | 4 to 5 | fabricated quotes, drifted locators |
| `test_gate_injection.py` | 6 to 8 | hidden text in HTML, PDF, metadata |
| `test_gate_sources.py` | 9 to 11 | whitelist, redirects, links inside documents |
| `test_artifacts.py` | 12 to 16 | determinism, free re-verification, substitution |
| `test_states.py` | 17 to 18 | failed RPC, unknown proxy pattern |
| `test_infra.py` | 19 to 22 | budgets, concurrent ledger writes, exit codes |

The rest came out of building it: `test_drift.py` on the quality of the diff,
`test_scans.py` on a document with no text layer, `test_alerts.py` on thresholds,
`test_challenge.py` on the adversarial mandate, `test_scars_registry.py` on the
registry, and `test_extract.py`, where the paid phase runs through a real
subprocess with a stand-in agent CLI.

**Held by a blocking test:** a grep of the sources for the absence of signing and
transaction submission, the RPC client refusing to call anything but read-only
methods, the language linter failing on a forbidden word and on merged sections,
byte-identical dossiers across runs, zero model calls in `verify --all`, and no
lost spend under concurrent writes.

**Adversarial fixtures:** a PDF hiding text three ways, another carrying an
injection in its metadata, HTML hiding text through CSS classes and through a text
colour matching the background.

---

## The registry of known cases

`state/scars.jsonl` holds cases where holders of a tokenised asset lost access,
value or recourse, decomposed into the same signal names the snapshots produce:

```json
{"id": "019", "date": "2025-11-03",
 "what": "Issuer suspended redemptions; collateral partly illiquid",
 "signals": ["upgrade_no_timelock", "self_attested_reserves", "offshore_issuer"],
 "source": "https://…"}
```

Matching is mechanical: set intersection, no model and no similarity threshold to
tune. Signals are derived from the structured readings of a snapshot rather than
from the text of a dossier, because prose gets translated and rewritten, and a
registry that matched on prose would quietly stop matching one day.

The registry is empty on a fresh install and useless for the first month. It is a
notebook, not a database shipped with the tool.

---

## Docs

| file | subject |
|---|---|
| [docs/evidence.md](docs/evidence.md) | the evidence contract, slot states, locator stability |
| [docs/verification.md](docs/verification.md) | how fabrications and injections are caught |
| [docs/adding-an-asset.md](docs/adding-an-asset.md) | registering an asset |
| [docs/reading-a-dossier.md](docs/reading-a-dossier.md) | reading a dossier |

## License

MIT.
