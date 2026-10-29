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
