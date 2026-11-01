# Agent cold-start validation — 29 October 2026

Each candidate was frozen before testing. Each behavioral trial used a new
subagent with no inherited conversation. It received only the package, task,
raw document, interpreter location, and allowed paths. It received no expected
commands, previous conclusions, or repair hints. No paid model was called.

## Codex connector

Two different new agents tested two immutable candidates of the `0.1.0` package.
The first exposed a missing source-only CPT example. The revised instructions
were then tested by another agent, not the first tester.

| Check | Final candidate |
| --- | --- |
| Skill installation | `installed` |
| Repeated installation | `unchanged`; installed contents retained |
| Core connection | Actual capabilities and saved task queries succeeded |
| Local source-only CPT | Completed; one record matched the input and preview |
| Events | Five durable events; returned cursor used for continuation |
| Repeated creation request | Same run ID; `replayed=true`; one persisted task |
| Core and dependency boundary regression | 66 checks passed |

The final cold-test run was `81a19a4db4af498ea79cd5f21447468a`.
The immutable ZIP SHA256 was
`0cdc1f9f77716051fba5f4be06393e25665ae3da8496e597736388a5d0dc5ccf`.
The tester used the installed skill's own helper and reference. It did not read
production implementation or tests to infer a recipe.

A separate deterministic check used the installed Codex app-server's official
`skills/list` method. With an isolated Codex home and the test project as `cwd`,
it found `shujian-cube`, enabled, with repository scope. No model turn was
started. This confirms real skill discovery. It does not establish that the
portable plugin was installed through a marketplace; its `pluginId` was null.

This was a source-only CPT smoke task. It did not use an LLM and did not measure
AI cleaning, reasoning quality, or large-batch performance.

## DeepSeek Harness connector

The old `0.3.0` package was installed by a separate fresh agent with the official
DSH `0.2.1-rc.2` CLI. The installed module imported. Native Web showed its
component running. The instructions required the tester to discover the host
installation and skill-directory setup separately.

Creating an empty native session then failed with
`agent-preset/invalid` and a duplicate `deployment:persona-prefix` registration.
No prompt was sent. Skill discovery, workflow dispatch, and CPT execution were
not counted as passed.

The revised `0.3.1` package adds an installation entry, bounded diagnostics, and
bundled skill registration. Its immutable TGZ SHA256 is
`ebb2150a9ebc50273a7ca7836a1490398453c79e074efc0c6c506408cd3d9cc7`.
The SDK remains pinned to `0.2.1-rc.2`. The package checks actual SDK registration
and the read-only core connection. It labels native host/session and live jobs
as unverified rather than claiming they were exercised.

Another fresh agent installed and reinstalled `0.3.1`. Configuration preservation
and doctor passed, but two attempts to create a real native session failed with
the same duplicate registration. Successful diagnostics were not counted as
successful native loading.

### Dependency-layout diagnosis

Controls used the same official `0.2.1-rc.2` CLI, without model prompts:

| Installation | Empty native sessions |
| --- | --- |
| Official host without this adapter | 5 of 5 succeeded |
| Adapter in the older hoisted dependency layout | 5 of 5 failed |
| Same adapter archive with isolated dependencies | 5 of 5 succeeded |
| Final `0.3.2` in a new isolated profile | 5 of 5 succeeded |

Resolver traces showed that public SDK copies in the hoisted profile shadowed
the host's services. Different copies created different private scope symbols.
That split caused duplicate persona, command, and tool registrations. The fix
uses pnpm's isolated layout. It does not patch the SDK, change the pinned host
version, or relax registration checks.

The `0.3.2` installer persists and verifies that layout. It refuses an already
hoisted profile before changing its dependencies. A refusal check retained all
four recorded configuration and lockfile hashes. Migration uses a new profile
pointing at the same existing app; saved product tasks stay in that app.

The immutable `0.3.2` TGZ SHA256 is
`c6ec24ba72475002b98c8c1216bbe6ee2c3249ad1559177eaf950c4de7009762`.
The preceding candidate passed 26 package checks. After the layout change, the
two affected setup checks passed, followed by the native controls above. These
counts are separate; they do not represent a new full-suite run for `0.3.2`.

### Final context-free trial

A new agent received the frozen `0.3.2` archive and a separate app fixture. It
followed only the packaged instructions, without implementation or test code.

| Check | Final candidate |
| --- | --- |
| Installation and installed doctor | Passed against the actual official SDK and local core |
| Dependency isolation | Isolated layout; no public SDK shadowing |
| Repeated installation | Three recorded file hashes unchanged, including unrelated edits |
| Native host and empty session | Started successfully |
| Native skill discovery | `dataforge` and `review-team` visible in the slash menu |
| Native session tool listing | Unverified; SDK registration alone was not counted |
| Native workflow dispatch, monitoring, and CPT result | Unverified; no model prompt was sent |

The supplied instructions name the tools and provide a source-only CPT recipe.
The tester did not find a documented no-model entry to invoke session tools in
the native UI. It did not replace that missing evidence with a direct core call
or a simulated host. Model-led DSH orchestration remains unverified by this
trial. The adapter's host loaded with both skills; that result is narrower than
an end-to-end generation claim.

The native session used the host's default workspace, while its session log
stayed inside the isolated DSH home. No tool ran against that default workspace.
The tester saved 16 raw command receipts, accessibility captures, and native UI
screenshots, then closed its host and temporary tab. Its final record is
`dsh-v2/evidence.json` under the local evidence directory below.

## Evidence and interpretation

Raw commands, package hashes, control receipts, source text, generated files,
and test reports are retained locally under
`.dataforge/agent-cold-validation/`. Host-discovery evidence is under
`.dataforge/agent-integration-refresh/`. These local operation records are not
included in the public package.

Skill frontmatter checks and package tests complement these trials. Neither
proves that an agent can use the skill independently. Installation, discovery,
real host loading, workflow execution, and semantic quality remain separate
claims. A later skill or plugin edit requires a new context-free trial.

## Node controls and troubleshooting — 1 November 2026

The node-rule release adds editable controls and bilingual help inside the actual
workflow panel. Rules travel from the saved draft to the frozen recipe, native
and TRL records, and lazy training reads. Original reasoning is retained.
Disabled rules are not treated as disabling the model's thinking.

Focused verification passed in separate affected suites: 90 backend, format,
loader and DDD checks; 4 editor checks; 36 workbench/navigation checks; 23 recipe
reuse checks; and 2 external-control checks. This was not a full repository run.
The local Qwen3.5 tokenizer verified enable/disable/reenable and explicit caller
override without loading model weights or calling an API. Desktop browser
checks covered editing exact literals and replacement text, local preview,
reload persistence, switching off, resetting defaults, and Chinese/English tabs.

Cold trials found actionable gaps: an undocumented workspace-ID distinction,
a missing required schema example, missing DSH parameter reference, and a
misnamed tool in a candidate document. The revised packages explain these.
Each changed package was retested by a different agent with no inherited context.

The same trials exposed two product issues. `runs.inspect` now returns an
explicit `node_control_text_configs` snapshot only after checking the frozen
recipe hash. Legacy tasks without that field keep their old response shape.
Parameter-validation failures now retain a valid request ID, so adapters can
show the real error instead of misreporting a protocol mismatch. Invalid IDs
are still not echoed. Real subprocess regressions cover both changes.

The final Codex 0.1.1 ZIP has SHA256
`fec8e840dda0783e4bb0316fd56098e488a33cc28b9fc5fe438d4e98387210a7`.
Its fresh tester installed the skill, completed source-only CPT task
`3c50a32dcbae4a179cd4de6c0f6f4885`, and froze an unexecuted SFT task
`d8e6689f70c8455eab4b44285ba60405` with rules disabled. It used the packaged
instructions without reading implementation/tests to infer usage. This proves
manual skill/helper and local protocol use, not native Codex host discovery.

The final DSH 0.3.3 archive has SHA256
`9ac2e1d824db72144bff2a8a3cf49d7a12261b0a2d88e0a2a07b115611f70ea3`.
Another fresh tester completed isolated installation and the official SDK
registration/doctor checks, then created SFT task
`ab4f2524f25b4b1494174182beb6bc38` through the documented local protocol.
`runs.inspect` confirmed the schema, `sft.enabled=false`, `queued`,
`attempt=0`, `active=false`, and empty usage. No implementation/test reading was
needed to discover how to operate it. This final trial did not launch the DSH
host or invoke native host tools/jobs. Its evidence is not an end-to-end host
execution claim.

### Follow-up: preserve text and require explicit saves

The next package revision documents v2 annotations: mark-only retains the exact
text and records spans separately. V1 frozen recipes retain their original
semantics. The node editor now separates pending edits from the committed draft,
validates before Save, and persists the previous configuration in the same
write. Cancel and failed writes leave the committed rules in place.

Fresh, context-free agents tested these final immutable packages independently:

| Package | SHA256 | Created, unexecuted SFT task |
| --- | --- | --- |
| Codex 0.1.2 | `830e14fa02dda65cd953a343739812d0dbfc0d42d7ea03e8baefb2d0ebeb6b45` | `b8e07859a42847d493aa5b765030e175` |
| DSH 0.3.4 | `3faf4853e814b82ac596f18e996bd660dc195628db7d1e0ca39f851b9fb3c85d` | `09c5d192d9b6464e85111acb19b6df80` |

Each tester installed into an isolated directory, followed the bundled guide,
and read back a v2 exact-match rule with `replacement: null`. Both tasks stayed
queued with zero attempts and no model usage. Neither tester needed to infer
instructions from implementation or test code. These are installation and local
protocol findings, not native host or generated-data quality claims.

The affected regressions passed: 34 rule/legacy/pipeline checks, 26 editor and
issue-navigation checks, and 72 DDD/draft/reuse checks. The editor cases include
cancel, invalid input, write failure, retry, reload, previous-version recovery,
and explicit v1-to-v2 adoption. No full repository or paid model run was used.
Evidence is under `.dataforge/node-marker-preserve-20261101/`.

Raw immutable packages, reports and receipts are retained locally under
`.dataforge/node-controls-cold-20261101/`. UI screenshots and observations are
under `.dataforge/node-controls-ui-20261101/`. These runtime files remain ignored.
No paid model call was made. Native host discovery, native tool/job execution,
local protocol business and semantic quality remain separate evidence claims.

### Paired control tags (v3)

Two new context-free testers independently installed the final packages below.
Each received only the package, source document, isolated directory and goal.
Neither inferred usage from implementation or tests. Both created and inspected
an SFT recipe with v3 enabled, `annotation_tag=reason-strength`, exact source
matches and `replacement=null`. Tasks remained queued, inactive and at zero
attempts with empty usage; neither invoked generation or a paid model.

| Package | SHA256 | Created, unexecuted SFT task |
| --- | --- | --- |
| Codex 0.1.3 | `726fa449b3e6179059046d61f169d07f222dc015b39da7619faafaea66f324e4` | `b3667f6e2ff24a0d97cf5fa6067f109a` |
| DSH 0.3.5 | `c5ce1df4fae762f86cd1bd8c9cdafa000924fb1d988c345e559d811fda569d0c` | `66907fe964ca42a892f4ffecc01d79e4` |

Codex verification covers isolated installation, file-level skill discovery and
real local control calls. DSH additionally covers registration through its
official SDK. Native host automatic discovery and native tool execution were
not tested. These cold starts do not establish generated-output quality or
actual tag transformation; those are separate core and export regressions.
Ignored evidence is under `.dataforge/paired-reason-strength-20261101/`.

### Prefix scope and reviewer suggestions

The final packages below supersede the preceding development packages. Two new
context-free testers installed them independently and used only their bundled
instructions to create and inspect an unexecuted SFT task. Both recovered v3,
`reason-strength`, `prefix_char_limit=512`, `review_suggestions=true` and
`replacement=null`. Both tasks were queued, inactive, at attempt zero and with
empty usage. Neither tester read implementation or tests to infer usage.

| Package | SHA256 | Created, unexecuted SFT task |
| --- | --- | --- |
| Codex 0.1.3 | `574dcf08cd555a21a0224d41aedfe784793d4498dcdd4ddf29cbae8e021b195e` | `d98946385ecf4d3d8a0f1550c4e0b87a` |
| DSH 0.3.5 | `e1074bf4cd9a120e02f8632af54967164653edee02147e499602b25f1eb03ffa` | `a913aa3fca0f4095bb38008578d187fb` |

Codex coverage is isolated installation, manual skill discovery and real local
control calls. DSH also passed official SDK registration and skill discovery.
Its initial runtime copy omitted untracked modules; completing that copy fixed
the preparation error, and the failed receipts are retained. Neither test proves
native host loading, generated-output quality, live reviewer suggestions or
actual marker conversion. Those remain separate verification layers. Packages
and evidence are retained under `.dataforge/prefix-control-review-20261101/`.
