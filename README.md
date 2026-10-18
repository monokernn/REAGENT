# REAGENT (RWA Evidence Agent)

![tests](https://img.shields.io/badge/tests-154%20passing-2ea44f)
![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

**REAGENT (RWA Evidence Agent)** reads a token and writes one sheet on which
every line names the function, the block or the page it came from.

**A Grok Bot crew.** REAGENT is built the way a Grok Bot team is built: eight
bots, one job each, a lead that fuses their work, and one shared set of
evidence for all of them. On the hosted desk every agent is one of the Grok Bot
mascots, from Slate's black ball to Crucible's orange blob.

The work is divided among a crew of agents. Each owns one question, is handed
only the evidence that bears on it, and answers from that alone. They do not
talk to each other: a snapshot is collected once, before any of them answers, and
given to all of them at the same moment, so two readings cannot rest on two
different observations of the same contract.

Most of the crew only reads: bytecode, storage, the stored bytes of a document.
Two jobs involve writing — Docket answering the document questionnaire, and five
agents adding sentences to a coin sheet. What they write is a proposal. It is
checked against the bytes first, and the sheet takes what survived.

![REAGENT — RWA Evidence Agent](media/banner1.gif)

---

## The crew

Every reading the tool produces is owned by exactly one agent. A test holds
that: add a reading without an owner and the suite fails.

| agent | beat | asks | the readings it owns |
|---|---|---|---|
| **Slate** | identity and origin | Is this a deployed token on this chain, and what does it call itself? | the name, symbol, decimals and supply the contract answers with |
| **Clasp** | contract powers | What does the deployed code still permit somebody to do to your balance? | `ownership`, `mint_authority`, `burn_authority`, `pause`, `freeze`, `upgradeable`, `trading_switch`, `transfer_tax`, `transfer_limits` |
| **Gauge** | liquidity and exits | How deep is the pool you would have to leave through, and can its LP still be moved? | `liquidity` |
| **Tare** | supply and burn | Where does the supply sit, and how much of it can nobody move? | `burned_supply`, `supply_vs_reserves` |
| **Sift** | holders and control | How much of the supply sits with whoever still holds the keys? | `owner_holdings` |
| **Pulse** | price and attestation | What price source does the contract read, and how old is the reserve report? | `oracle`, `attestation_freshness` |
| **Docket** | documents and RWA | What do the issuer's own documents say, and does every quote survive the gate? | `instrument_type`, `issuer`, `holder_rights`, `insolvency`, `custodian`, `corporate_actions`, `redemption`, `restrictions` |
| **Crucible** | the whole picture | Puts the others together, adding nothing they did not find. | none of its own |

**Docket** reads the issuer's own files. Every quote it proposes is cut out of
the stored bytes and compared before it reaches the sheet; what does not match is
deleted whole and printed with the reason it died.

**Crucible** goes last. It takes what the other seven found and fuses it into one
picture, and it is allowed to add nothing they did not find.

Prose is a separate thing from a reading, and the roster says so separately.
Slate, Clasp, Gauge, Tare and Sift can each write a sentence about what they
read, through `assayer notes` — but the reading itself is still bytecode, and
the sheet prints in full without ever asking for one.

## The rule underneath it

One observation, from which the architecture follows:

> Writing a claim costs money. Checking it against the bytes costs nothing.
> So they are separate operations, run by separate code.

Work is handed over through artefacts on disk, each addressed by its content:

| artefact | produced by | cost | before anybody reads it |
|---|---|---|---|
| **snapshot** | the crew, reading the chain and fetching the issuer's files | ~0 | nothing; it is the record |
| **extraction** | Docket, over the documents in that snapshot | money | every quote is cut from the stored bytes and compared |
| **notes** | five agents, over readings already taken | money | every figure is checked against those readings, then the linter runs |
| **dossier** | the verifier | ~0 | it is what survived |

Four properties fall out of the separation for free.

**Reproducibility.** `dossier = f(snapshot, extraction)` is a pure function. A
dossier carries no wall-clock stamp of when it was produced; its date comes from
the snapshot. Two runs over the same inputs produce a byte-identical
`dossier.json`, and a test holds that.

**Free re-verification.** The rules of the gate are ours and they move. Tighten
one, then run it over everything already written without buying any of it again:
`assayer verify --all` for extractions, `assayer notes <asset> --recheck` for
notes. Both are possible because what was proposed is kept on disk beside what
survived it.

**Drift as the base case, not a feature.** Snapshots are structured and
content-addressed, so "what changed" is not a subsystem. It is subtraction.

**Separate budgets.** Observation costs nothing and can run every hour. You pay
only when a snapshot has actually changed.

What it amounts to is tamper-evident surveillance of drift: every statement on
the sheet traces to the bytes it came from, and whatever could not be traced was
deleted before anybody saw it.

## Two profiles, one machine

| | `--coin` | the documents profile |
|---|---|---|
| what it reads | the contract | the contract **and** the issuer's documents |
| slots | 12, all from bytecode and storage | 8 legal + 8 on-chain |
| the crew | seven agents | all eight |
| what gets written | nothing, unless you ask for notes | the eight legal slots, once per changed snapshot |
| cost | the sheet is free, always; notes are the only thing that can be billed | one call per changed snapshot, priced by your agent CLI against the documents' length, under the ceiling in `[budget]` |
| the gate | readings need none; notes get the figure check and the linter | quotes, injections, provenance, language |
| catches | a power that is still live, a pool whose LP is still movable | a prospectus that says something the interface does not |

Both profiles answer the same four questions:

- what you hold legally,
- what the issuer can do to your balance technically,
- what changed since the last check,
- which piece of evidence each statement rests on.

Locally. No private keys, no transactions, no opinion about what you should do.
The tool produces a document; the person decides.

> A *reagent* is what an assay office puts a sample through to find out what the
> metal is actually made of. Hence the name.

---

## Table of contents

| section | what is in it |
|---|---|
| [The crew](#the-crew) | eight agents, and which readings each one owns |
| [Ordinary coins](#ordinary-coins) | the default profile: twelve readings of a Solana mint, taken from the chain alone |
| [Assets with documents](#assets-with-documents) | the gap this was built for, and a dossier in full |
| [Start](#start) | install, configure, first snapshot |
| [How it works](#how-it-works) | snapshot, extract, verify |
| [The gate](#the-gate) | how fabrications and injections are caught |
| [Where it stops](#where-it-stops) | the boundary, stated plainly |
| [Cost](#cost) | real numbers |
| [Requirements](#requirements) | Python, RPC, optional agent CLI |
| [Tests](#tests) | what is held by a blocking test |
| [The registry](#the-registry-of-known-cases) | signal matching against past failures |

---

## Ordinary coins

A memecoin has no prospectus, no custodian and no reserve report. The legal
questionnaire therefore has nothing to be filled from, and the paid role has
nothing to read — so it is not started. Everything a holder can be told about one
is a fact about the chain, and seven agents read it straight off it.

Solana is the default. A token there is a mint account on one of two shared
programs, SPL Token or Token-2022, and what makes one token different from
another is written into that account: who may print more, who may freeze a
holder, and the Token-2022 extensions that take a fee on every transfer, move
any holder's balance, pause the token or run a program on each transfer.

```bash
assayer add solana:DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP --coin --label "$SI"
assayer snap si
assayer show si
```

EVM chains are read the same way, from bytecode and storage instead of a mint:
`assayer add ethereum:0xADDRESS --coin --pool 0xPAIR`.

`--pool` is optional and nothing is discovered without it: a pool the asset file
does not name is a pool this tool does not read, and the `liquidity` slot says so
under `NOT CHECKED` rather than going looking.

### The twelve readings

| slot | on Solana | on an EVM chain |
|---|---|---|
| `ownership` | every live authority over the mint, and which of them touch a balance | does the contract still answer to a live owner, one key or a threshold |
| `mint_authority` | the mint authority: can supply still be increased, and by whom | the same, from the mint functions in the bytecode |
| `burn_authority` | a permanent delegate that can move or burn any holder's balance | can a balance the owner does not hold be reduced |
| `pause` | the pausable extension and its authority | can transfers be halted |
| `freeze` | the freeze authority over every holder's token account | can one address be blocked from moving its balance |
| `upgradeable` | a transfer hook: a program every transfer runs, and who can repoint it | can the logic be replaced in place, and after what delay |
| `trading_switch` | accounts that start frozen, or a token that cannot be transferred | can transfers be turned on or off after deployment |
| `transfer_tax` | the transfer fee extension: basis points, cap, and who can change it | does a transfer pay a fee, and can that figure still be moved |
| `transfer_limits` | the token programs keep none | per-transaction and per-wallet caps, as a share of supply |
| `owner_holdings` | what share of supply the live authority holds | what share of supply sits in the owner's own balance |
| `burned_supply` | what share sits at the incinerator | what share sits at an address with no known key |
| `liquidity` | how much of the token the declared pool owns | how much is in the declared pool, and how much of its LP is parked |

Clasp asks the same nine questions whichever chain the token lives on; only the
place the answer is written changes.

### What the sheet looks like

```
ASSAY  ·  COIN  ·  $SI  ·  solana:DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP  ·  2026-10-07

! Powers over somebody else's balance are live in this contract: ownership, transfer_tax.

WHAT THIS IS
  the mint calls itself Super Inu (SI)                                                   [chain]
  total supply 996393517508835, 6 decimals                                               [chain]
  an SPL mint on the token-2022 program; metadataPointer, tokenMetadata,                 [chain]
  transferFeeConfig

WHAT THE CONTRACT STILL ALLOWS
  ⚠ ownership: the mint still answers to live authorities — fee:        [chain, block 454318633]
  5KXDF6QnqhBj72hDtJNkkpFaQVUfbFXNybMsp3DiK6tD; metadata:
  WLHv2UAZm6z4KyaaELi5pjdbJh6RESMva1Rnn8pJVVh
  ○ mint_authority: mint authority is set to none: the supply is fixed  [chain, block 454318633]
  ○ freeze: freeze authority is set to none: no holder's account can    [chain, block 454318633]
  be frozen
  ⚠ transfer_tax: a transfer pays 100 basis points (1%); the rate can   [chain, block 454318633]
  be changed by 5KXDF6QnqhBj72hDtJNkkpFaQVUfbFXNybMsp3DiK6tD; the
  withheld fees are collected by
  5KXDF6QnqhBj72hDtJNkkpFaQVUfbFXNybMsp3DiK6tD
  · owner_holdings: 5KXDF6QnqhBj72hDtJNkkpFaQVUfbFXNybMsp3DiK6tD holds  [chain, block 454318633]
  0.0076% of supply

NOT CHECKED
  ? liquidity: no_pool_declared. no pool in the asset file; pools are never discovered

COMPLETENESS: 4/12 slots   POWERS LIVE: 2   SPENT: $0.0000
snapshot 314b255b8d9f  ·  no extraction: this profile calls no model  ·  block 454318633
This sheet shows what the contract records. The person decides.
```
There is no score and no verdict, for the same reason as everywhere else in this
tool. `POWERS LIVE: 5` is a count of readings, not a number that means anything
on its own: five live powers on a coin whose pool is parked and five on one whose
owner holds half the supply are five either way, and the reader is the one who
weighs them.

### Two units, and only one of them is invented

A fee getter answers an integer. Whether `5` is five percent, half a percent or
five basis points is a convention of a source this program never read, so the
reading is printed with the function it came from and left alone. A cap is
different: it is denominated in the token's own units, so dividing it by
`totalSupply` is exact, and that is why `transfer_limits` prints a percentage
while `transfer_tax` does not.

### Thresholds

The same contract as everywhere: your numbers, the reading printed beside them,
and an explicit *not applied* whenever the reading is missing.

```toml
[alerts]
require_renounced_ownership = true
require_no_trading_switch = true
require_no_transfer_tax = true
require_no_freeze = true
max_owner_share_pct = 5.0
min_burnt_lp_pct = 95.0
```

`assayer watch` returns exit code 8 while any of them is crossed, which is what
makes the coin profile useful in cron: a rug is not a slow drift, and the point
of watching is to hear the owner key wake up.

### What moves, and what counts as a change

Two kinds of movement are told apart rather than both reported as *something
changed*.

| movement | in the snapshot hash | how it is watched |
|---|---|---|
| a balance drifting, a pool filling, a share of supply moving | no | the thresholds above |
| a fee changed, a cap raised, ownership transferred, a switch flipped | yes | `snap` reports the snapshot as changed |

If a drifting balance flipped the hash, `watch` would cry drift on every block
and the holder would learn to ignore it.

### Notes, if you want sentences

The sheet is twelve readings and no prose. `assayer notes` adds the prose, and
it is the one coin command that can cost anything:

```bash
assayer notes si              # calls the agent CLI named in config.toml
assayer notes si --recheck    # judge what was written, free
assayer show si --notes       # prints what was already written, free
```

The writing goes through the same agent CLI the documents profile uses, on your
own key. No vendor is wired in, and the readings never depend on it.

Three things keep the sentences from becoming a second, softer source of truth.

**They are addressed by the snapshot they describe.** Ask twice about a contract
that has not moved and the second answer is the file, not a call.

**Every figure must already be on the sheet.** A sentence may only use a number
or an address the readings produced, allowing for a percentage rounded honestly:
`96%` is accepted against a reading of `96.0%`, `97%` is not. A sentence that
invents a figure is deleted whole and printed with the figure that killed it,
the same treatment a fabricated quote gets in the documents profile. A name is
not a figure, so `ERC-20` and `EIP-1967` pass untouched.

**The linter runs over the result.** The writer works under the vocabulary the
tool forbids itself, so a note cannot call a coin safe, or a good buy, or score
it out of ten. A hit fails the command with code 4 rather than being quietly
rewritten.

Both of those checks are ours, and ours change. So the sentences that were
proposed are kept on disk beside the ones that survived: tighten the figure rule
or add a word to the forbidden list, and `--recheck` puts every note already
written in front of the new rules, prints what the change did, and spends
nothing. The sentences were bought once.

```
CREW NOTES
  The owner still holds 5.0% of supply and can still change the fee.

  worth checking next
    whether ownership is given up
    whether the fee changes

  Clasp · contract powers
    A fee is taken on transfer and the figure can still move.
  Gauge · liquidity and exits
    96.0% of the pool's LP is parked at an address with no known key.

  DELETED BY THE FIGURE CHECK
    watch[2]: 40000000 (figure is not in the readings)
```

---

## Assets with documents

This is the profile the tool started as, and the gap it was built for.

A blockchain keeps an honest record of the token. It keeps no record of the
existence or the status of the real asset: that lives in the issuer's prospectus,
in the custodian's statement, in the jurisdiction of incorporation, and in the
powers the issuer kept for itself inside the contract. The holder sees a company
name and a chart. The prospectus may say this is a debt note of an offshore
issuer carrying no shareholder rights, and the contract may say the issuer can
freeze any address and replace the logic with no delay. All of it is public, and
none of it gets read: the documents are long, the contracts are unreadable by
eye, and both change quietly. REAGENT works in that gap.

**A live illustration.** Tokenised stocks on Solana: the interface shows the ticker
and the price of a familiar company, while the prospectus describes tokenised
debt securities of an issuer incorporated in Jersey. They give economic exposure
to the price of the underlying share, but they do not make the holder a
shareholder and carry no shareholder rights. This is not an assessment of the
company or of the instrument. It is an illustration that the interface and the
document say different things, and that nobody reads the second one.

![assayer in a terminal](media/demo.gif)

*A real session, rendered frame by frame: the documents come over HTTP from a
local server, the on-chain slots over JSON-RPC from a local stub, and between the
two snapshots the prospectus really is replaced behind the tool's back. Only the
issuer is fictional. Nothing here costs a token: `extract` is not even available,
since no agent CLI is installed.*

### Example dossier

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
| `REJECTED BY THE GATE` | **Docket.** It made a claim and the evidence did not hold. |

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

A coin on Solana needs no configuration: public endpoints are read unless
`[rpc] solana` in `config.toml` names your own. A coin on an EVM chain needs one
line, an RPC endpoint for that chain.

```bash
assayer doctor
assayer add solana:DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP --coin --label "$SI"
assayer snap si
```

That is the whole loop for the coin profile. It calls no model and spends
nothing, so it can run as often as you like.

### With documents

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
| `assayer add <chain>:<address> --coin [--pool …]` | register a coin | 0 |
| `assayer add <chain>:<address> --docs …` | register an asset with documents | 0 |
| `assayer snap <asset> [--all]` | take a snapshot; says whether anything changed | 0 |
| `assayer extract <asset> [--force] [--challenge]` | put Docket to the legal slots; refuses a coin | money |
| `assayer verify <asset> [--all]` | run the gate again | 0 |
| `assayer show <asset> [--json] [--notes]` | the latest sheet, and notes already written | 0 |
| `assayer notes <asset> [--force]` | write the crew's notes on a coin | money |
| `assayer notes <asset> --recheck` | judge notes already written under today's rules | 0 |
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

Three phases for the sheet. Two are free, the third is not, and the expensive one
sits between two that check it. `notes` is the only other thing that spends
anything, and it is held to the same shape: propose, then be checked.

### snapshot: zero tokens

Reading only, fully deterministic. Eight on-chain slots are read and handed to
the agents that own them — five to Clasp, two to Pulse, one to Tare:

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

### extract: where the documents are read

Docket, put to the issuer's files. It receives the canonical texts and a fixed
questionnaire of eight legal slots: instrument type, issuer, holder rights,
insolvency, custodian, corporate actions, redemption, restrictions. No open
questions are asked, because an open question produces prose that cannot be
diffed against the same question next month.

Rules Docket works under:

- the answer is JSON only, no prose;
- every filled slot carries a doc_id, the document's sha256 and a verbatim quote;
- offsets are computed by the gate, never taken from the answer;
- invalid JSON gets one retry, after which slots are marked `not_checked`;
- if `snapshot_hash` has not changed, the phase refuses to spend anything.

It is one call per changed snapshot, not one per slot: eight calls would cost
eight times as much to answer the same questionnaire, and would let two slots
disagree about the same sentence.

### verify: the gate

Deterministic code that calls nothing: every claim Docket proposed is intersected
with the bytes it cited, and what survives is assembled into `dossier.json` and
`dossier.txt`. It can be re-run over existing extractions as often as you like.

---

## The gate

Four independent checks on an extraction, all in `assayer/verify/`, none of which
asks anybody to write anything. **Nothing here is left to judge its own work.**
The coin notes get their own two checks, described [above](#notes-if-you-want-sentences):
the same idea, over figures instead of quotes.

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
is marked `tainted`, printed with a warning. The tool catching an invented source
and showing it to the reader is the only direct evidence that the gate works.

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

## Where it stops

REAGENT prints what the contract records and what the documents say, and marks
every place it could not look with the reason. That is the whole job. An audit is
a different one, advice is somebody else's, and whether the collateral is really
there is a question no reader of a chain can answer.

There is no assessment of the asset in the output: no score, no rating, no
suggestion to act. That is a property of the product, not a disclaimer, and a
test holds it. A tool that slid from "the document says X" to "this asset is
sound" would become a source of false confidence, and that is worse than having
no tool.

What that looks like in the output:

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

---

## Cost

**Observation is free.** A snapshot is a handful of read-only RPC calls and some
document fetches: zero tokens, zero dollars, as often as you like. The gate is
free too, which is why `assayer verify --all` re-checks the whole archive under
improved rules at no cost.

**You pay for drift only.** `extract` calls the agent CLI, and refuses to do so
if `snapshot_hash` has not changed since last time, or if the documents are the
same ones you already paid to read.

| situation | cost |
|---|---|
| every reading on a coin, forever | $0.00 |
| `snap`, `watch`, `verify`, `show`, `diff`, `evidence` | $0.00 |
| `notes` on a coin, once per changed snapshot | whatever your agent CLI charges |
| `notes` again while the contract has not moved | $0.00 |
| `notes --recheck` over the whole archive, under tightened rules | $0.00 |
| one extraction over two documents | whatever your agent CLI charges to read them |
| any number of runs while the documents are unchanged | $0.00 |

What an extraction costs is not something this repository can quote for you. It
is one call carrying the documents' full text, so the bill is set by their length
and by the price of whatever CLI you named in `[agent]` — the same prospectus
runs orders of magnitude apart on a small model and a frontier one. What the tool
gives you instead is a ceiling you set yourself, and a record of every cent that
actually went out.

Two limits live in `[budget]`:

- `max_usd_per_day` refuses the next extraction once the day's total is reached.
  The shipped `config.toml` sets it to `2.00`.
- `max_usd_per_extraction` cannot interrupt a call that is already running, so its
  cost is recorded and the next call refuses to start until a person has looked.
  The shipped value is `0.40`; raise it for long documents or an expensive CLI,
  and the first real extraction tells you what yours actually costs.

Every spend lands in `state/budget.json` and `state/log.jsonl`, written atomically
and safe against concurrent writers.

---

## Requirements

- Python 3.10+
- an RPC endpoint per observed EVM chain, read-only; Solana runs on public endpoints
- `pypdf` for PDFs, and `tomli` on 3.10; both install with the package
- any agent CLI on PATH, optional

Docket and the coin notes shell out to whatever agent CLI is declared in
`config.toml`, on your own key — a language model of your choosing, not one wired
in here. Anything that accepts a prompt and returns JSON will do, and swapping it
is a change of four lines.

```toml
[agent]
command = "claude"
args = ["-p", "--output-format", "json"]
response_field = "result"
cost_field = "total_cost_usd"
```

With no CLI installed every reading still runs: `snap`, `verify`, `diff`, `show`
and `watch` work in full and a coin sheet prints complete. Only `extract` and
`notes` report themselves unavailable and return code 6.

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
| `test_coin.py` | — | the coin profile, end to end |
| `test_crew.py` | — | the roster against the program's own slot vocabulary |
| `test_notes.py` | — | the figure check, and that the sheet stays free |

`test_coin.py` holds the coin profile: that it reads without documents, without
a model and without spending; that a given-up owner, a live owner and a contract
that never had one are three different readings; that a pool which does not trade
the token is refused rather than believed; and that a drifting balance is not
reported as a change while a changed fee is.

The rest came out of building it: `test_drift.py` on the quality of the diff,
`test_scans.py` on a document with no text layer, `test_alerts.py` on thresholds,
`test_challenge.py` on the adversarial mandate, `test_scars_registry.py` on the
registry, and `test_extract.py`, where the paid role runs through a real
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
