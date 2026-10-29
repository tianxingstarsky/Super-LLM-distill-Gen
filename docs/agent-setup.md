# Install and use agent support

DeepSeek Harness is the primary external controller. Codex has an independent
skill package. Both use the app's saved workflows and control protocol.
The app, connector, and agent host have separate installation and update cycles.

## Give an agent the installation job

Copy the setup request from the [project page](../README.md#install-with-your-agent).
Provide an existing app path if you have one. Give the agent a dedicated profile
or project folder. It should inspect the current installation before changing it.

The expected outcome is concrete: a usable app, the matching installed skill or
plugin, a verified control connection, and a report of what remains unverified.
Installing files is not proof that a host has loaded them.

## Prepare the app

An app root contains `lib/control.py`, `configs/`, and its working Python
environment. An existing installation can be reused. Do not move its saved work
or install over a source checkout.

For Windows x64, use the [published installer](https://github.com/tianxingstarsky/Super-LLM-distill-Gen/releases).
Verify its SHA256 against the attached checksum file. The setup can run silently
with Inno Setup's `/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART` switches.
Use `/DIR="<dedicated app folder>"` for a chosen path and `/LOG="<log file>"`
for diagnostics. Wait for the process. Check both its exit status and the new
`.runtime/verified.json` receipt. First setup needs internet access and about 2 GB.

For a source installation, create a Python 3.11 environment and install
`requirements.txt` using the project's existing setup. Keep the source checkout
and a packaged Windows installation as separate roots.

Connector source is in this repository's `plugins/` directory. An agent can clone
the repository to get it. The app's configured models remain on the app side;
setup does not require copying model keys into a connector.

## DeepSeek Harness

Use Node 24 or later and the supported DSH version declared by the independent
[adapter package](../plugins/dsh-dataforge/README.md). Check the declared release;
do not silently upgrade a working host to an untested alpha.

Install this independent package's declared dependencies in its own directory
first with `npm install --ignore-scripts`. Then, from the source checkout, use
the automated setup entry:

```text
node plugins/dsh-dataforge/setup.mjs install --home <DSH-home> --profile <profile-name> --root <app-root> --python <app-python> --allow-mutations
```

Use absolute paths. On Windows the app interpreter is
`<app-root>/.venv/Scripts/python.exe`. Omit `--allow-mutations` for an
inspection-only connection. Creating, executing, or cancelling tasks requires
that setting. A user's generation request still determines which work to run.

The setup uses the official DSH plugin installer. It registers the bundle's
skills with the host and preserves unrelated profile configuration. Follow the
package guide for an explicit DSH CLI path, configuration conflicts, and removal.

The profile uses isolated dependencies so its SDK copies do not shadow the
host's own services. An older hoisted profile is not upgraded in place. Keep
that profile and create a new named one pointing to the same app. Saved product
runs remain in the app. Follow the package's migration instructions before
starting either controller against existing work.

Check the installed package:

```text
node plugins/dsh-dataforge/setup.mjs doctor --home <DSH-home> --profile <profile-name> --root <app-root> --python <app-python>
```

Read the individual outcomes. SDK registration, the core connection, native
session loading, and actual workflow execution are different checks. Do not turn
an untested field into a pass. In a real host, confirm the `dataforge` and
`dataforge_dispatch` tools and the `dataforge` and `review-team` skills.

## Codex

Install into an explicit project skill directory:

```text
<app-python> plugins/codex-shujian/install.py --destination <project>/.agents/skills
```

Without `--destination`, the installer uses `~/.agents/skills`. It preserves an
existing different skill. Use its documented replacement option only when an
update is intended. The old edition is backed up before replacement.

Ask Codex to use `$shujian-cube` with the absolute app path. Codex normally
discovers local skills automatically. If needed, restart it. Check discovery in
the actual host; manually reading `SKILL.md` is a separate validation.

The package also contains a portable `plugin.json` for the host's plugin flow.
The direct skill installer does not install a marketplace plugin. See the
[package guide](../plugins/codex-shujian/README.md) for its scope.

## Let the agent choose the next action

Start with the user's objective and current capabilities. Inspect persisted
tasks, node bindings, source availability, and model waits. Reuse an existing
result when it satisfies the request. Create a new recipe when the requirements
change. Keep frozen runs intact.

Independent tasks can run together. A dependent task starts after its
prerequisite succeeds. A dependency controls order; it does not pass source
files. The downstream recipe must contain the required source references.

Use durable event and stream cursors for monitoring. Reuse completed requests
after an interruption. After a host restart, inspect the saved run ID. An old
background job handle is not a persistent task identity.

An agent can inspect failures, propose a new recipe, resume checkpoints, or use
an available correction path within the user's request. It must report an
unsupported action rather than edit frozen internal files. Quality policy can
be changed explicitly for a new task; hidden changes must not turn failed
records into apparently approved data.

## Verify with a fresh agent

Use an isolated app, profile, and skill folder. A no-model CPT task can verify
creation, execution, event reading, preview, and request reuse. It does not
measure LLM generation quality.

After every skill or plugin change, test the immutable package with a new agent
that inherits no conversation. Give it the package, objective, raw inputs, and
allowed paths. Do not give it previous commands, conclusions, or repair hints.
Keep its raw receipts and report its actual level of success. See the
[validation record](agent-cold-validation.md).

## Official host references

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
- [Codex skill discovery](https://learn.chatgpt.com/docs/build-skills)
- [Portable plugin packaging](https://developers.openai.com/plugins/build/plugins)

The Python app's stable boundary is described in the
[external control protocol](external-control.md). These host packages are
independent community integrations.
