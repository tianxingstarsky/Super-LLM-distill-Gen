# ShuJian Cube for Codex

This independent plugin contains one self-contained skill. It uses the existing
`dataforge.control` v1 subprocess interface. It has no agent SDK, model client,
MCP server, or scheduler of its own. Updating this package does not modify saved
product tasks, prompts, caches, or exports.

## Install the skill

From a repository checkout, run:

```sh
python plugins/codex-shujian/install.py
```

This installs `shujian-cube` under `~/.agents/skills`. For a project or an isolated
test, pass the **parent directory that holds skills**:

```sh
python plugins/codex-shujian/install.py --destination /absolute/project/.agents/skills
```

The installer copies only the packaged skill. It does not configure models,
start the app, edit Codex settings, or run a generation request. Identical content
returns `unchanged`. A different existing skill is left intact unless you pass
`--replace`; replacement first saves the complete old directory in
`.shujian-cube-backups` beside the skill.

Codex normally detects local skills automatically. If the skill does not appear,
restart Codex. Invoke `$shujian-cube` with the absolute product installation path.
The installed product's Python environment must already be ready.

This folder is also a portable skills-only plugin with `plugin.json`. It can be
packaged or added to a local plugin catalog using the host's official plugin
installation flow. The local skill-copy command above requires no catalog.

## Use it

```text
Use $shujian-cube with the installation at /absolute/path/to/ShuJian Cube.
Inspect saved work, find tasks needing attention, and show the useful next actions.
Do not start generation yet.
```

The skill explains the small control CLI. It reads current capabilities before
choosing actions, uses saved run identities and cursors, and keeps long-running
process ownership with Codex. Automatic execution requires the user's task to
authorize the work; `--allow-mutations` passes that choice to the existing core.
Model keys remain in the product's normal configuration.

## Files and validation

- `install.py`: standard-library skill installer with collision preservation.
- `skills/shujian-cube/SKILL.md`: discoverable orchestration instructions.
- `skills/shujian-cube/scripts/control.py`: UTF-8 protocol transport helper.
- `skills/shujian-cube/references/control.md`: action parameters and run contracts.
- `test_package.py`: installation and transport-boundary behavior checks.

Run `python -m unittest discover -s plugins/codex-shujian -p test_package.py`.
Also run a real read-only `capabilities` request against an existing installation.
Neither check needs a model API. Skill edits need a fresh, context-free agent
trial; schema validation alone does not establish that the instructions work.

Official references: [Build skills](https://learn.chatgpt.com/docs/build-skills)
and [Package a plugin](https://developers.openai.com/plugins/build/plugins).
