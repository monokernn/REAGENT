# REAGENT

![tests](https://img.shields.io/badge/tests-96%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

REAGENT takes the contract address of a tokenised real-world asset and the issuer's
documents, and produces one readable sheet: what you hold legally, what the issuer
can do to your balance technically, what changed since the last check — and which
piece of evidence each statement rests on. Locally, with no private keys, no
transactions, and no opinion about what you should do. The tool produces a
document; the person decides.

*Assay* is the fire test that tells you what a metal is actually made of. Hence
the name.

## What RWA is, and where the gap opens

Tokenising a real-world asset means issuing a token that stands for something off
the chain: a share, a bond, a fund unit, metal, property. The appeal is concrete:
trading around the clock, lots divisible to a fraction of a percent, settlement in
seconds instead of two days, and composability — the token can be posted as
collateral in another protocol.

A blockchain keeps an honest record of the token. It keeps no record of the
existence or the status of the real asset. Those live in the issuer's prospectus,
in the custodian's statement, in the jurisdiction of incorporation, and in the
powers the issuer kept for itself inside the contract. The holder sees a company
name and a chart. The prospectus may say this is a debt note of an offshore issuer
carrying no shareholder rights, and the contract may say the issuer can freeze any
address and replace the logic with no delay. All of it is public. None of it gets
read: the documents are long, the contracts are unreadable by eye, and both change
quietly.

A live illustration of the gap between interface and document is Robinhood Stock
Tokens. The interface shows the ticker and the price of a familiar company. The
prospectus describes tokenised debt securities of an issuer incorporated in Jersey:
they give economic exposure to the price of the underlying share, but they do not
make the holder a shareholder and carry no shareholder rights. This is not an
assessment of the company or of the instrument — it is an illustration that the
interface and the document say different things, and that nobody reads the second
one.

REAGENT works in exactly that gap. It does not decide whether an asset is good or
bad. It shows what is written, what is not written, what is recorded in the
contract — and states just as loudly what it could not check.

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

Properties that fall out of the separation for free:

**Reproducibility.** `dossier = f(snapshot, extraction)` is a pure function. A
dossier carries no wall-clock stamp of when it was produced; its date comes from
the snapshot. Two runs over the same inputs produce a byte-identical
`dossier.json`, and a test holds that.

**Free re-verification.** Improve the rules of the gate, then run them over the
whole archive of old extractions without a single model call: `assayer verify
--all`.

**Drift as the base case, not a feature.** Snapshots are structured and
content-addressed, so "what changed" is not a subsystem — it is subtraction.

**Separate budgets.** Observation costs nothing and can run every hour. You pay
only when a snapshot has actually changed.

The product here is not "AI reads a prospectus". It is tamper-evident surveillance
of drift, with every claim traceable to the bytes it came from.

## Example dossier

The sheet itself renders in Russian, as specified for this build; the citations,
slot names, function signatures and reason codes are the machine-readable parts and
read the same in any locale.

```
ASSAY  ·  Assay Note ASSY  ·  ethereum:0x1111111111111111111111111111111111111111  ·  2026-02-11

!  Гейт поймал выдуманные цитаты: 1. Раздел «ОТКЛОНЕНО ГЕЙТОМ» ниже.

ЧТО ЭТО
  инструмент: долговая нота эмитента; экономическая экспозиция, не доля в      [prospectus, с.1]
  компании
  эмитент: Assay Issuer Limited, зарегистрирована на Джерси                    [prospectus, с.1]
  права держателя: нет права голоса и нет прав акционера в отношении базового  [prospectus, с.2]
  актива
  банкротство эмитента: держатели — необеспеченные кредиторы, обособления      [prospectus, с.2]
  базового актива нет
  кастодиан: Assay Custody Services Limited; независимый проверяющий не назван    [custody, с.1]

ИЗМЕНЕНИЯ  (прошлый снапшот: 7 дн. назад)
  ⚠ документ prospectus: содержимое подменено (e3c43c39dd8f -> c57e3440d840) —            [diff]
  заявленная версия осталась 2026-01, содержимое другое
  · totalSupply: 1240000000000000000000000 -> 1310000000000000000000000                   [diff]

ЧТО МОЖЕТ ЭМИТЕНТ
  ⚠ mint_authority: выпуск возможен: mint(address,uint256);              [chain, block 21345678]
  контролирует 0x6666666666666666666666666666666666666666
  (мультиподпись, 2 из 3)
  ⚠ burn_authority: может уменьшить баланс, которым не владеет:          [chain, block 21345678]
  burn(address,uint256); контролирует
  0x6666666666666666666666666666666666666666 (мультиподпись, 2 из 3)
  ⚠ pause: переводы можно остановить; контролирует                       [chain, block 21345678]
  0x6666666666666666666666666666666666666666 (мультиподпись, 2 из 3)
  ⚠ upgradeable: proxy (eip1967); логика лежит по адресу                 [chain, block 21345678]
  0x3333333333333333333333333333333333333333; апгрейд контролирует
  0x5555555555555555555555555555555555555555 (timelock, задержка 0с);
  задержка timelock равна 0с: апгрейд вступает в силу без ожидания
  ⚠ freeze: любой адрес может быть лишён возможности двигать свой        [chain, block 21345678]
  баланс: blacklist(address), isBlacklisted(address); контролирует
  0x6666666666666666666666666666666666666666 (мультиподпись, 2 из 3)
  ○ oracle: контракт токена не читает источник цены                    [absence, block 21345678]
  ⚠ supply_vs_reserves: totalSupply 1310000 против заявленного залога    [chain, block 21345678]
  1200000 share; токенов больше, чем заявленного залога на 110000
  (9.17%); цифра залога заявлена в файле актива со ссылкой на custody
  · attestation_freshness: отчёт о резервах от 2026-01-31, на момент снапшота     [custody, с.1]
  ему 11 дн.; заявленная периодичность 30 дн.

ПОРОГИ, ЗАДАННЫЕ В ФАЙЛЕ АКТИВА
  · отчёту о резервах 11 дн., задан порог 30 дн.                                   [файл актива]
  ⚠ задержка на пути апгрейда 0с, задан минимум 172800с — порог пройден            [файл актива]
  ⚠ токенов больше заявленного залога на 9.17%, задан порог 1.0% — порог пройден   [файл актива]
  ⚠ у эмитента есть функция блокировки адреса — порог пройден                      [файл актива]

НЕ НАЙДЕНО В ДОКУМЕНТАХ
  ✗ corporate_actions: о дивидендах, сплитах и делистинге в             [absence, документов: 2]
  проверенных документах не сказано. Проверено документов: 2.

НЕ ПРОВЕРЕНО
  ? restrictions: document_is_a_scan — раздел об ограничениях подан сканом без текстового слоя

ОТКЛОНЕНО ГЕЙТОМ
  ⨯ redemption: fabricated_quote — такой цитаты в каноническом тексте нет

ЧЕМ ЭТО МОЖЕТ КОНЧИТЬСЯ ДЛЯ ДЕРЖАТЕЛЯ
  ! выход из позиции зависит от решения эмитента: он может отказать в выкупе   [prospectus, с.3]
  по своему усмотрению
  ! при банкротстве эмитента держатель встаёт в общую очередь необеспеченных   [prospectus, с.2]
  кредиторов

ПОХОЖИЕ СЛУЧАИ
  · issuer_can_freeze_address, offshore_issuer, upgrade_no_timelock                 [scars: 011]
  · offshore_issuer, self_attested_reserves, upgrade_no_timelock                    [scars: 019]

ПРЕДЛОЖЕННЫЕ ИСТОЧНИКИ
  Ссылки изнутри документов не переходятся. Домен добавляется руками в config.toml.
  · filings.example.org — из custody, упоминаний: 1

КОНТЕКСТ (НЕ ДОКАЗАТЕЛЬСТВО)
  · документ terms не попал в корпус: doc_unavailable
  · тезис отсечён гейтом (claim_without_evidence) и в документ не попал

ПОЛНОТА: 12/16 слотов   ФАБРИКАЦИЙ: 1   ИНЪЕКЦИЙ: 0   ПОТРАЧЕНО: $0.1873
snapshot 666e04d955f1  ·  extraction 505856f9a827  ·  block 21345678
Документ показывает, что написано в источниках и что записано в контракте. Решение принимает человек.
```

Three sections are separated on purpose and never merge. **НЕ НАЙДЕНО** (not found)
is a fact about the asset: we looked, the documents do not say it. **НЕ ПРОВЕРЕНО**
(not checked) is a fact about the tool: it failed to look, and here is why.
**ОТКЛОНЕНО ГЕЙТОМ** (rejected by the gate) is a fact about the model: it made a
claim and the evidence did not hold. A blocking linter enforces the separation, not
the author's care.

**ПОРОГИ** (thresholds) are your numbers from the asset file, not a judgement by
the tool. It does not decide that two days of timelock is enough; it reports that
the number you wrote down was crossed, and prints the reading next to the
threshold. A threshold that cannot be evaluated — the RPC was down, the proxy
pattern was not recognised — is marked as not applied rather than as satisfied.

**ЧЕМ ЭТО МОЖЕТ КОНЧИТЬСЯ ДЛЯ ДЕРЖАТЕЛЯ** (how this can end for the holder) is the
adversarial second mandate: same corpus, opposite question. Its theses pass the
same evidence contract as the slots, so "may not scale" never reaches the sheet
while "redemption depends on the issuer's discretion" reaches it with a page
number. Enabled with `--challenge`.

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

Check the environment and add the first asset:

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

The full command set:

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
| `assayer scar new --from-text <file\|->` | decompose an account into signals with a model | money |
| `assayer scar signals` | the signal names snapshots produce | 0 |
| `assayer status` | assets, spend, state | 0 |
| `assayer doctor [--network]` | RPC, documents, agent CLI | 0 |

Exit codes: `0` ok, `1` error, `2` usage, `3` the gate stopped the run, `4` a red
line was crossed, `5` refused by the budget, `6` no agent CLI, `7` nothing to do,
`8` drift found (`watch` only). `assayer watch` is built for cron: it spends
nothing and returns 8 when something moved.

## How it works

**snapshot — zero tokens.** A fully deterministic phase. Eight on-chain slots are
read by scripts alone: the power to mint, the power to burn a balance the issuer
does not hold, halting transfers, the proxy pattern with its admin and timelock
delay, freezing an address, the price source, supply against declared collateral,
and the age of the reserve report. Documents are fetched from allowed domains,
their text is extracted exactly once, and they enter the corpus under their sha256.
The result is `snapshot.json` with two hashes: `docs_hash` over the document set
and `snapshot_hash` over the whole observed identity of the asset. Neither covers
the block number or the clock — otherwise every new block would look like a change
and "pay only for drift" would decay into "pay always".

**extract — the only phase that costs money.** The model receives the canonical
texts and a fixed questionnaire of eight legal slots: instrument type, issuer,
holder rights, insolvency, custodian, corporate actions, redemption, restrictions.
No open questions are asked — an open question produces prose that cannot be
diffed against the same question next month. It returns JSON only. Every filled
slot must carry a doc_id, the document's sha256 and a verbatim quote; the offsets
are computed by the gate, not trusted from the model. Invalid JSON gets one retry,
after which the slots are honestly marked `not_checked`. If `snapshot_hash` has not
changed, the phase refuses to spend anything.

**verify — the gate.** Deterministic code, zero model calls; see the next section.
Its output is `dossier.json` and `dossier.txt`. It can be re-run over existing
extractions as often as you like.

## The gate

Four independent checks, all in `assayer/verify/`, all without a model. The model
does not verify itself.

**Quotes.** Every claim is checked against the bytes it cites: the span is cut out
of `canonical.txt`, both sides are normalised for what extractors are allowed to
disagree about (whitespace, dashes, quotes, case), and compared. Drifted offsets
are repaired by search and the slot stands. A quote that is absent means the slot
is deleted whole — it does not "lose confidence" — and the deletion is printed with
its reason. Three inventions from one document and that document is marked
`tainted` and printed with a warning that it cannot be relied on. The tool catching
its own model inventing a source, and showing the reader, is the only direct
evidence that the gate works.

**Injections.** Documents come from the issuer's web server: an untrusted source.
Somebody who knows a machine will read the prospectus can hide a sentence in it for
the machine. HTML is examined (`display:none`, `opacity:0`, zero font size,
`clip-path`, positioning off the viewport, text colour equal to background,
`aria-hidden`, comments, and rules from `<style>` blocks applied by class or id),
so is PDF (text rendering mode 3, fonts under 1pt, text outside the MediaBox,
`/Info` and XMP metadata, annotations, attachments), and so is the text itself for
phrasing addressed at a system. The response is to stop and print what was found,
in which document, on which page, and by which technique it was hidden. Quietly
scrubbing the document would leave the reader believing the corpus is honest, which
is worse than refusing to continue. Detection is free, so it runs twice: during the
snapshot, before any paid call, and again at the gate under current rules.

**Provenance.** Fetching happens only from domains a human entered by hand, and the
check precedes the socket. A redirect leaving the whitelist aborts the fetch. Links
found inside documents are never followed: they surface as a suggestion, and a
human adds the domain.

**Language.** The dossier is checked against a forbidden vocabulary. A hit fails
the run rather than being rewritten — rewriting would hide that the drift
happened. Verbatim quotes are exempt: a prospectus may contain any word it likes,
and quoting it accurately is the whole job. Separately, the linter enforces that
there is no numeric scoring anywhere, in text or in field names, and that the three
slot states render into three distinct sections.

Details: [docs/verification.md](docs/verification.md),
[docs/evidence.md](docs/evidence.md).

## What this is not

Not an audit. Not investment advice. It does not establish that the collateral
exists.

REAGENT shows what the documents say and what the contract records, and states just
as loudly what it could not check. An empty slot is a result: "three documents say
nothing about what happens if the issuer becomes insolvent" is often the most
valuable finding on the sheet, and most tools of this kind pass over it in silence.
A failed RPC yields `not_checked` with a reason, never `not_found`. A document with
no text layer is monitored for substitution but cannot carry evidence — otherwise
an empty scan would answer "the documents do not say" to every question. A
prospectus may lie; the tool shows what it actually says, from which page and at
which block, so a person can check it themselves in ten seconds:

```bash
assayer evidence <asset> holder_rights
```

There is no assessment of the asset in the output — no score, no rating, no
suggestion to act. That is a property of the product, not a disclaimer: a tool that
slid from "the document says X" to "this asset is sound" would become a source of
false confidence and would be worse than no tool at all.

## Cost

Observation is free. A snapshot is a handful of read-only RPC calls and some
document fetches: zero tokens, zero dollars, as often as you like. The gate is free
too, which is why `assayer verify --all` re-checks the whole archive under improved
rules at no cost.

You pay for drift only. `extract` calls the model, and refuses to do so if
`snapshot_hash` has not changed since last time, or if the documents are the same
ones you already paid to read. In practice: one extraction run over two documents
costs on the order of $0.10–0.40 depending on their size and the model; with
unchanged documents it costs $0.00 no matter how often you run `snap`, `watch`,
`verify` and `show`.

Two limits live in `[budget]`. `max_usd_per_day` refuses the next extraction once
the day's total is reached. `max_usd_per_extraction` cannot interrupt a call that is
already running, so its cost is recorded and the next call refuses to start until a
person has looked. Every spend lands in `state/budget.json` and `state/log.jsonl`,
written atomically and safe against concurrent writers.

## Requirements

* Python 3.10+
* an RPC endpoint per observed chain (read-only)
* `pypdf` for PDFs; `tomli` on 3.10 — both install with the package
* any agent CLI on PATH — optional

The agent CLI is declared in `config.toml`: anything that accepts a prompt and
returns JSON will do. Change the command, the args and two field names:

```toml
[agent]
command = "claude"
args = ["-p", "--output-format", "json"]
response_field = "result"
cost_field = "total_cost_usd"
```

With no CLI installed, `snap`, `verify`, `diff`, `show` and `watch` work in full,
and `extract` reports itself unavailable and returns code 6.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The twenty-two tests from the build plan are numbered in the docstring of each
file: `test_red_lines.py` (1–3), `test_gate_quotes.py` (4–5),
`test_gate_injection.py` (6–8), `test_gate_sources.py` (9–11),
`test_artifacts.py` (12–16), `test_states.py` (17–18), `test_infra.py` (19–22).
The rest came out of building it: `test_drift.py` on the quality of the diff,
`test_scans.py` on a document with no text layer, `test_alerts.py` on thresholds,
`test_challenge.py` on the adversarial mandate, `test_scars_registry.py` on the
registry, and `test_extract.py`, where the paid phase runs through a real
subprocess with a stand-in agent CLI.

The red lines are held by blocking tests: a grep of the sources for the absence of
signing and transaction submission, the RPC client refusing to call anything but
read-only methods, the language linter failing on a forbidden word and on merged
sections, byte-identical dossiers across runs, zero model calls in `verify --all`,
and no lost spend under concurrent writes. The fixtures are adversarial: a PDF
hiding text three ways and another carrying an injection in its metadata, HTML
hiding text through CSS classes and through a text colour matching the background.

## The registry of known cases

`state/scars.jsonl` holds cases where holders of a tokenised asset lost access,
value or recourse, decomposed into the same signal names the snapshots produce:

```json
{"id": "019", "date": "2025-11-03",
 "what": "Issuer suspended redemptions; collateral partly illiquid",
 "signals": ["upgrade_no_timelock", "self_attested_reserves", "offshore_issuer"],
 "source": "https://…"}
```

Matching is mechanical — set intersection, no model and no similarity threshold to
tune. Signals are derived from the structured readings of a snapshot rather than
from the text of a dossier: prose gets translated and rewritten, and a registry
that matched on prose would quietly stop matching one day.

The registry is empty on a fresh install and useless for the first month. It is a
notebook, not a database shipped with the tool.

## Docs

The documents below are in Russian, like the rendered sheet.

* [docs/evidence.md](docs/evidence.md) — the evidence contract, slot states,
  locator stability
* [docs/verification.md](docs/verification.md) — how fabrications and injections
  are caught
* [docs/adding-an-asset.md](docs/adding-an-asset.md) — registering an asset
* [docs/reading-a-dossier.md](docs/reading-a-dossier.md) — reading a dossier

## License

MIT.
