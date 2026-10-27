# Preview readiness — 27 October 2026

This build is intended for a limited preview. It is not a promise of reliable,
fully automatic production at a million-example scale.

## Available in this build

- Native thinking and the answer can come from the same model response.
- Reading conversations keep their public instructions and material together.
- Users can save, preview, adopt, and restore personal style editions.
- DSH histories can provide full attempts, failures, and corrections for a style
  proposal. Proposals include source quotes and keep their parent edition.
- Completed requests can resume from saved receipts. Existing tasks keep their
  frozen rules and output formats.

Experience proposals currently use the Chat strict-tool route. This action was
tested with V4.1-Flash. The basic model gateway also supports Responses and
Anthropic messages, but that does not certify every new action on all protocols.

## Local quality observation

The local experiment reused ten paper-application scenarios. Eight were single
turn. One had three turns, and one had two. Each arm generated all thirteen
assistant replies anew. Only DeepSeek-V4.1-Flash was used. Both arms received the
same user messages and allowed source material.

One style update used two complete failed and corrected conversations. A third
complete conversation did not fit the same input limit. Its correction and cost
remained in the local report. No reasoning was cut to fit the limit. The proposed
style was tested without human rewriting.

| Observation | Initial style | After one update |
| --- | ---: | ---: |
| Complete conversation and native thinking usable | 4 / 10 | 6 / 10 |
| Final answers usable | 5 / 10 | 6 / 10 |
| Concise | 2 / 10 | 0 / 10 |
| Key needs covered | 8 / 10 | 9 / 10 |
| Observable correction in the same native response | 1 / 10 | 2 / 10 |
| Generation calls | 13 | 13 |
| Generation time | 183.2 s | 213.6 s |
| Native thinking tokens | 36,210 | 41,523 |
| Estimated generation cost | CNY 0.4911 | CNY 0.5362 |

There were thirty model operations in all. These included three correction
attempts and one style proposal. Total cost was estimated at CNY 1.2265 from
provider usage and the rates at the request times. This is not an invoice.

These are operator-reported local results. A coding assistant checked the full
outputs against the paper and user conditions. This was not an independent
benchmark release. The same known tasks informed the update and the comparison.
The results do not establish generalization or a production unit price.

The paper was [Self-Instruct](https://arxiv.org/abs/2212.10560). Remaining defects
included chart values assigned to the wrong curve, reversed nearest-neighbor
comparisons, unsupported conclusions in dialogue history, and overly broad
normalization rules. Some content improved, but the concise style goal was not
met. The experimental proposed style has not become the global default.

## Open work

Automatic feedback collection and pending-experience tracking are not yet wired
into a complete production loop. Optional repair of old failures with a chosen
new edition also needs further integration. The working policy is incremental
updates and bounded, optional correction. A failed correction must remain visible
and must not block new batches.

The legacy OpenCUA `gui-cot` command needs the documented DeepSeek endpoint patch.
See [PATCHES.md](../PATCHES.md). This does not affect the native reading and
personal-style workbench paths.

## Release verification

The release record will list the completed core and interface regressions.
Engineering checks establish the tested software contracts. They do not certify
the semantic quality of generated examples. The latest full raw experiment and
request receipts remain in the local, ignored operation directory.
