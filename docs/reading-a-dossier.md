# Reading a dossier

One sheet, two minutes. No points, no verdicts, no assessment of the asset.

## The header

```
ASSAY  ·  Assay Note ASSY  ·  ethereum:0x1111…  ·  2026-02-11

!  The gate caught fabricated quotes: 1. See "REJECTED BY THE GATE" below.
```

The date is the date of the snapshot, not of the printing. Lines starting with
`!` appear only when there is something to report: fabrications, tainted
documents, text found that addresses the agent.

## The sections

| section | what is in it | where it came from |
|---|---|---|
| WHAT THIS IS | legal slots that passed the gate | the model over the documents, quote verified |
| CHANGES | the diff against the previous snapshot | subtraction of structures |
| WHAT THE ISSUER CAN DO | on-chain powers, and powers that are absent | scripts only |
| THRESHOLDS SET IN THE ASSET FILE | your numbers from `[alerts]` and what became of them | readings compared with the threshold |
| NOT FOUND IN THE DOCUMENTS | looked for in the documents, not there | evidence of kind `absence` |
| NOT CHECKED | the tool did not manage, with a reason | a fact about the tool |
| REJECTED BY THE GATE | the model gave a value, the gate cut it | a fact about the model |
| HOW THIS CAN END FOR THE HOLDER | adversarial theses that survived the gate | quote verified |
| SIMILAR CASES | signal intersection with the registry | set intersection |
| TEXT FOUND THAT SPEAKS TO THE AGENT | injections, with the technique named | the detector |
| SUGGESTED SOURCES | links inside documents, never followed | link harvesting |
| CONTEXT (NOT EVIDENCE) | what is not evidence, marked as such | stated explicitly |

Three sections are separated on purpose and never merge:

- **NOT FOUND** is a fact about the asset: we looked, the documents do not say it.
- **NOT CHECKED** is a fact about the tool: it failed to look, and here is why.
- **REJECTED BY THE GATE** is a fact about the model: it made a claim and the
  evidence did not hold.

Merging the first two would be the most damaging thing this program could do: a
failed RPC would read as "the issuer holds no such power". A blocking linter
watches the separation.

The same logic covers a document that could not be read. A scan with no text
layer is watched for substitution but never becomes evidence: both a quote and a
claimed absence over such a document go to **NOT CHECKED** with the reason
`document_has_no_text_layer`. Otherwise an empty document would answer "the
documents do not say" to every question.

**THRESHOLDS** is not an assessment. You set the threshold in `[alerts]` in the
asset file; the tool prints the reading, the threshold, and whether it was
crossed. A threshold it cannot evaluate is marked as not applied: a failed RPC
does not mean the timelock is fine.

## Markers

| marker | meaning |
|---|---|
| `⚠` | a power over somebody else's balance, a material change, or a crossed threshold |
| `·` | a fact with no direct effect on the holder's balance |
| `○` | a power that is absent: the function is not in the bytecode (evidence `absence`) |
| `✗` | not found in the documents |
| `?` | not checked, with a reason |
| `⨯` | rejected by the gate |
| `!` | an adversarial thesis with a verified quote |
| `⚑` | text found that speaks to the agent |

## Citations

```
  holder rights: no voting rights and no shareholder rights   [prospectus, p.2]
  ⚠ freeze: any address can be blocked from moving …    [chain, block 21345678]
  ○ oracle: the token contract reads no price source  [absence, block 21345678]
```

- `[<doc_id>, p.<N>]` is a quote from that document on page N. A `~OCR` mark means
  the text came from recognition and the addressing is approximate.
- `[chain, block N]` is a reading from the chain at that block, reproducible.
- `[absence, …]` means it was looked for and not found: for documents the number
  checked is given, for the chain the block.

Any citation can be opened in ten seconds:

```bash
assayer evidence <asset> holder_rights
```

That prints the window of canonical text with the quote inside `>>>…<<<`, the
path to the `canonical.txt` file, and the result of the comparison. If the
comparison fails, the command returns a non-zero code. This command matters more
than it looks: if re-checking is expensive, a reader starts taking the tool's
word for things, which is exactly what the project avoids.

## The footer

```
COMPLETENESS: 12/16 slots   FABRICATIONS: 1   INJECTIONS: 0   SPENT: $0.1873
snapshot 666e04d955f1  ·  extraction 505856f9a827  ·  block 21345678
```

`COMPLETENESS` is how many of the sixteen slots are filled. Low completeness
often says less about the tool than about the documents being silent: look at
what landed under NOT FOUND.

The hashes on the last line are artefact addresses. `snapshot` locates
`state/snapshots/<asset>/<ts>/snapshot.json`, `extraction` locates
`state/extractions/<hash>/`, which holds the prompt, the model's answer and the
cost. The dossier reproduces byte for byte from those two inputs:

```bash
assayer verify <asset>
```

## What is not in a dossier

An assessment of the asset. No score, no rating, no suggestion to act. The sheet
shows what the sources say, what the contract records, and what could not be
checked. The person decides.
