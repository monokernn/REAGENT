# The evidence contract

The central structure of the project. Everything else answers to it.

```json
{
  "slot": "instrument_type",
  "state": "filled",
  "value": "debt_security, a note of the issuer",
  "evidence": {
    "kind": "document",
    "doc_id": "prospectus",
    "sha256": "9f2c1a…",
    "locator": {"type": "char_range", "page": 14, "start": 48210, "end": 48252},
    "quote": "tokenised debt securities issued by"
  }
}
```

## Exactly three kinds of evidence

| kind | contents | produced by | how it is verified |
|---|---|---|---|
| `document` | sha256, locator, verbatim quote | proposed by the model, also emitted by scripts | the span is cut out of `canonical.txt` and compared |
| `onchain` | chain_id, address, method, block, raw | scripts only | reproducible at that block number |
| `absence` | the doc_ids searched and the patterns used | scripts, and the model for documents | the search is repeated |

There is no `knowledge` kind and there will not be one. The model asserts nothing
from erudition: such an assertion has no format, and therefore no way to be
checked. General observations land in the `CONTEXT (NOT EVIDENCE)` section, set
apart on purpose.

`absence` is evidence in full standing, not a gap. "Three documents say nothing
about what happens if the issuer becomes insolvent" is often the most valuable
finding on the sheet. It is checked as mechanically as a quote: the gate repeats
the search with the same patterns, and if the word does occur after all, the slot
is rejected as an invention.

## Slot states

| state | means | required field |
|---|---|---|
| `filled` | a value, and evidence that passed the gate | `evidence` |
| `not_found` | looked for it, it is not there | `evidence` of kind `absence` |
| `not_checked` | the tool did not manage to look | `reason` |
| `rejected` | the model offered a value, the gate cut it | `reason` |

`not_found` is a fact about the asset. `not_checked` is a fact about the tool.
They never merge, in the JSON, in the render or in the diff. A blocking linter
enforces that (`verify/language.py`), not the author's care.

`rejected` is printed in the dossier. The reader is entitled to see the tool
catch its own model inventing a source: that is the only direct evidence that the
gate works.

On-chain slots in state `not_found` are printed under `WHAT THE ISSUER CAN DO`
rather than under `NOT FOUND IN THE DOCUMENTS`. The absence of a `pause` function
in the bytecode is a fact about code, not a gap in a document. Their evidence is
an `absence` naming the selectors checked and the block.

## Canonical text, and why locators survive

Offsets break the moment text is re-extracted by a different library version. The
answer is to extract exactly once:

- a document enters the corpus under its sha256: `state/corpus/<sha256>/`;
- the text is extracted on first arrival into `canonical.txt` and never
  re-extracted (`Corpus.put` calls the extractor only when the file is absent);
- `meta.json` records the extractor, its version, the date, the page start
  offsets and every URL those bytes were observed at;
- `char_range` addresses `canonical.txt`, and the page is computed from the
  offset.

Upgrading `pypdf` does not touch the existing corpus: new documents simply get
the new extractor in their own `meta.json`. Compatibility by construction rather
than by promise.

## A document that could not be read

OCR is not built in. A scan with no text layer enters the corpus, is hashed and
is watched for substitution like any other document, but it cannot carry
evidence: its `canonical.txt` is empty.

This is the sharpest form of the difference between `not_found` and
`not_checked`. If absence claims were honoured against such a document, every
question would come back "the documents do not say": a statement about the asset
manufactured out of the tool's own blindness. So:

- any `absence` naming an unreadable document becomes
  `not_checked: document_has_no_text_layer`, listing those documents;
- a quote from an unreadable document becomes `not_checked` for the same reason
  rather than a charge of invention, because it was this tool that handed the
  model an empty document;
- the text of the claim is kept in `gate.unverifiable_claims` and counted
  separately, so nothing is lost;
- in the prompt such a document is marked `NO TEXT LAYER`, with an explicit
  instruction to claim neither presence nor absence from it.

## What the gate does with a quote

1. Read `corpus/<sha256>/canonical.txt`.
2. Cut out `[start:end]`.
3. Normalise both sides: soft hyphens dropped, hyphenation across line breaks
   joined, quotes and dashes canonicalised, whitespace collapsed, casefolded.
4. Compare.

| outcome | what the gate does |
|---|---|
| it matches | the slot passes |
| no offsets were offered and the quote is found by search | the slot passes, `locator_computed` |
| the offsets drifted and the quote is found by search | the locator is repaired, the slot passes, `locator_drift` is logged |
| the quote is not in the document | the slot is `rejected`, `fabricated_quote`, the fabrication counter goes up |

The normalisation is deliberately one-sided. It forgives what extractors are
entitled to disagree about (whitespace, dashes, case) and forgives nothing that
carries meaning.

## Why extraction and verification are separate operations

Extraction costs money and is untrusted. Verification costs nothing and is
trusted. Hence

```
dossier = f(snapshot, extraction)
```

is a pure function: a dossier carries no stamp of when it was produced, and its
date comes from the snapshot directory. From which: `assayer verify --all`
re-checks the whole archive under improved rules without a single model call.
