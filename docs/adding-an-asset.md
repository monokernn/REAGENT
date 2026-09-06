# Registering an asset

## 1. Domains, by hand

The tool will fetch nothing from a domain that is not in `config.toml`:

```toml
[sources]
allowed_domains = ["docs.issuer.example", "issuer.example"]
```

Subdomains of an allowed domain pass. Lookalikes do not:
`issuer.example.evil.com` is refused.

## 2. An RPC endpoint per chain

```toml
[rpc]
ethereum = "https://…"
arbitrum = "https://…"
```

Without an entry for the chain, the on-chain slots honestly come back as
`not_checked: missing_rpc_endpoint`. The tool makes read-only calls only:
`eth_call`, `eth_getStorageAt`, `eth_getCode`, `eth_getLogs`, `eth_blockNumber`,
`eth_chainId`, `eth_getBlockByNumber`. The client refuses to issue anything else.

## 3. The asset

```bash
assayer add ethereum:0xADDRESS \
  --label "Assay Note ASSY" \
  --docs prospectus=https://docs.issuer.example/prospectus.pdf \
         custody=https://docs.issuer.example/custody.html
```

That writes `assets/ethereum-0xaddress.toml`. A `doc_id` can be given explicitly
as `doc_id=url`; without one it is taken from the filename in the URL. The
`doc_id` is the name evidence refers to, so it is not worth changing later: old
dossiers will keep pointing at the previous name.

The file can be edited by hand:

```toml
label = "Assay Note ASSY"
chain = "ethereum"
address = "0x1111111111111111111111111111111111111111"
chain_id = 1

[[documents]]
doc_id = "prospectus"
url = "https://docs.issuer.example/prospectus.pdf"
version = "2026-01"

[[documents]]
doc_id = "custody"
url = "https://docs.issuer.example/custody.html"

[reserves]
claim_doc = "custody"
claimed_units = 1200000
unit = "share"
period_days = 30
attested_at = "2026-01-31"
attestor = "Independent Attestor LLP"
```

`version` is whatever the issuer itself calls the version of the document. The
point of the field is to catch a substitution of the content while the declared
version stays the same: such a replacement is printed in the changes section as
material.

### The `[reserves]` section

Filled in by hand, and not evidence. The tool only compares it against what is:

| field | what it is for |
|---|---|
| `claimed_units` | compared with `totalSupply` |
| `unit` | what a unit of the collateral is called |
| `claim_doc` | the `doc_id` of the document carrying the reserve report |
| `period_days` | the promised reporting period |
| `attested_at` | the date of the report |
| `attestor` | the independent attestor, if there is one |

If `attested_at` does not occur in the text of `claim_doc`, the
`attestation_freshness` slot goes to
`not_checked: attestation_date_not_found_in_document`. The tool does not borrow
authority from a configuration file: a date the document does not confirm is not
evidence.

If `attestor` is not declared while a report exists, the snapshot emits the
`self_attested_reserves` signal for matching against the registry.

### The `[alerts]` section

Thresholds. These are your numbers, not a judgement by the tool: it reports that
a number you wrote down was crossed, and prints the reading next to it.

| field | crossed when |
|---|---|
| `max_attestation_age_days` | the reserve report is older than the given number of days |
| `min_timelock_delay_s` | the delay on the upgrade path is shorter than the given one |
| `max_supply_over_claim_pct` | tokens exceed the declared collateral by more than the given percentage |
| `require_no_freeze` | the code has an address-freeze function |
| `require_no_foreign_burn` | the code can reduce a balance the issuer does not own |
| `require_immutable_logic` | the logic can be replaced in place |

A threshold that cannot be evaluated, because the RPC was down or the proxy
pattern was not recognised, is marked as not applied rather than as satisfied.
`assayer watch` returns exit code 8 while any threshold is crossed, which makes
it convenient in cron.

## 4. Check the setup

```bash
assayer doctor            # config, RPC, agent CLI, document domains, corpus
assayer doctor --network  # plus whether the documents are actually reachable
assayer doctor --deep     # plus re-hashing every stored document
```

The corpus check is worth its own mention. Every locator in every dossier
addresses bytes in `state/corpus/`, and those bytes sit in an ordinary directory.
The default pass reports anything missing; `--deep` re-hashes each stored
original and compares it with the name it is filed under. A document that no
longer answers to its own hash is reported rather than repaired: quotes verified
against it were checked against different content, and that is a fact about the
archive, not something to quietly fix.

## 5. The first snapshot

```bash
assayer snap ethereum-0xaddress
```

Free, no model. It prints what reached the corpus, what changed and where the
dossier went. The second snapshot onwards prints a diff.

## 6. From there

```bash
assayer watch                        # snapshot every asset; exit 8 if something moved
assayer extract <asset>              # paid: fill the legal slots
assayer extract <asset> --challenge  # paid: plus theses on how this ends for a holder
assayer verify <asset> --all         # free: re-run the gate over the whole archive
assayer show <asset>                 # the latest dossier
assayer evidence <asset> issuer      # the window of text around a quote
assayer scar signals                 # which signals snapshots produce
assayer scar new --from-text case.txt  # paid: decompose an account into signals
```

`watch` is built for cron: it spends nothing and returns 8 when there is drift.

## What breaks, known in advance

- **Prospectuses differ wildly.** Slots fill unevenly and there will be plenty of
  `not_found`. An empty slot is a result, not a failure.
- **Scans.** OCR is not built in. A scan with no text layer is fetched, hashed
  and watched for substitution, but it cannot carry evidence: slots resting on it
  go to `not_checked: document_has_no_text_layer`, and the dossier says so. A
  `bbox` locator marked `degraded` is accepted on input if the document does have
  text.
- **The proxy zoo.** EIP-1967 (including UUPS), OpenZeppelin legacy, EIP-1822 and
  EIP-1167 clones are read. Diamond and custom patterns are handed back as
  `not_checked: unknown_proxy_pattern`.
- **RPC.** A provider that is down moves some slots to `not_checked`. That is not
  masked.
