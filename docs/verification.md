# The gate: how fabrications and injections are caught

The gate is deterministic code. Not one check consults a model: the model does
not verify itself.

Four independent checks in `assayer/verify/`, plus a fifth application of the
evidence contract to the adversarial mandate.

## 1. Quotes, in `quotes.py`

A claim is checked against the bytes it cites. The mechanics are in
[evidence.md](evidence.md); the thresholds are here:

- `fabrications > 0` puts a banner at the top of the dossier;
- `fabrications >= taint_threshold` for one document (3 by default) marks that
  document `tainted` and prints it with a warning that nothing sourced from it
  can be taken as verified.

A rejected slot does not "lose confidence". The value is deleted whole and the
deletion is printed under `REJECTED BY THE GATE` with its reason.

Evidence of kind `absence` is re-checked the same way: the gate repeats the
search for the declared patterns in the declared documents. Claim that insolvency
is never mentioned while the word does occur, and the slot is rejected.

## 2. Injections, in `injection.py`

Documents arrive from the issuer's web server. That is an untrusted source, and
somebody who knows a machine will read the prospectus can hide a sentence in it
that the machine obeys and the reader never sees.

**HTML.** `display:none`, `visibility:hidden`, `opacity:0`, `font-size:0`,
`clip-path`, `clip:rect(0…)`, `text-indent:-9999`, positioning off the viewport,
zero height with `overflow:hidden`, `transform:scale(0)`, text colour equal to
the background, `aria-hidden="true"` over real text, the `hidden` attribute,
`<template>`, `<noscript>`, HTML comments, and abnormally long
`alt`/`title`/`aria-label`. Rules inside `<style>` blocks are parsed too: if a
class or an id hides its content, elements carrying it fall under the same check.

**PDF.** The drawing stream itself is parsed: text rendering mode 3 (drawn and
never shown), fonts under 1pt, text outside the MediaBox or CropBox, the `/Info`
dictionary and XMP metadata, annotations, embedded files.

**Text.** Phrases addressed at a system (`ignore previous`, `the report must`,
`system:`, and their equivalents in other languages). The list lives in
`config.toml` under `[gate.injection_patterns]`, not in the code: the detector is
code, the vocabulary is configuration, and it stays multilingual because an
issuer document can be written in any language. Visible text addressed at the
agent is caught exactly like hidden text.

**Two severities:**

| severity | what it is | reaction |
|---|---|---|
| `instruction` | hidden or visible text matching a pattern that speaks to a system | the run stops (`[gate] halt_on_injection`, true by default) |
| `hidden` | text removed from view but not phrased at an agent | printed in the dossier, does not stop the run (`halt_on_hidden_text = false`) |

The split exists so the tool does not block on every hidden menu of an ordinary
website while still refusing to continue on a real attempt.

**The reaction is to stop, not to scrub.** What was found is printed: which
document, which page, which technique hid it. The person decides. Silently
cleaning the document would leave the reader believing the corpus is honest,
which is worse than refusing to continue.

Detection is free, so it runs twice: during the snapshot, so a finding never
reaches a paid call, and again at the gate over the corpus under the current
rules. The second run means improved rules apply to documents fetched months ago.

## 3. Provenance, in `sources.py`

- Fetching happens only from domains listed by hand in
  `[sources] allowed_domains`, and the check precedes the socket.
- A redirect leaving the whitelist aborts the fetch rather than being followed
  and judged afterwards.
- Links inside documents are never followed. They land in the dossier under
  `SUGGESTED SOURCES` with the document they came from and how often they appear.
  A person adds the domain.
- Everything fetched is hashed into the corpus before any processing.

"Finish my todo list" authorises reading the list, not executing what is written
in it. A document is data in exactly the same way.

## 4. Language, in `language.py`

Checks `dossier.json` and the rendered sheet against the forbidden vocabulary in
`[language] forbidden`. A hit fails the run rather than being rewritten, because
rewriting would hide the fact that the drift happened.

Verbatim quotes are exempt. A prospectus may contain any word it likes, and
quoting it accurately is the whole job. The exempt keys are listed in
`language.EXEMPT_KEYS`: `quote`, `excerpt`, `label`, `symbol`, `name`, `url`,
`domain` and a few of the same sort. Everything else is the tool's own wording
and is checked.

Additionally:

- no numeric scoring, neither in text (`7/10`, "score: 8") nor in field names
  (`*_score`, `*_rating`, `*_verdict`);
- `not_found`, `not_checked` and `rejected` render into three distinct sections
  and never merge; an on-chain `not_found` prints under the powers section.

The main risk of this project is not technical. If the wording slides from "the
document says X" to "this asset is sound", the tool becomes a source of false
confidence and is worse than no tool at all. This linter is the mechanical
defence against that drift, and it blocks.

## 5. The adversarial mandate, in `gate.py`

`extract --challenge` adds a second call with the opposite question: how what
these documents describe can end for a holder. Unfiltered, such a question
produces a stream of observations that could be written about any asset ever
issued, so the theses pass the same evidence contract as the questionnaire:

- a thesis with no evidence is cut (`claim_without_evidence`);
- a thesis with an invented quote is cut and counted as a fabrication like any
  other, so `taint_threshold` counts it too;
- a thesis resting on a document with no text layer is cut;
- what survives is printed under `HOW THIS CAN END FOR THE HOLDER` with the
  document and the page.

The number cut is printed in the dossier's context section, so a reader can see
how much of the answer had nothing behind it.

## What the gate does not do

It does not check whether what the document says is true. It checks that the
document says it. A prospectus may lie; the tool shows what is actually written
in it, at which address and from which block.
