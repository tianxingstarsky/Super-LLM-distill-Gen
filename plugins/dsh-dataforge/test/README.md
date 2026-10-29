# Independent adapter tests

These Node tests belong to the independently versioned DSH adapter. They use the
declared SDK and local fixtures. They do not call model APIs or share the Python
mock server port.

- `plugin.test.mjs`: official tool registration and controller boundaries.
- `dispatcher.test.mjs`: job ownership, ordering, cancellation, and limits.
- `transport.test.mjs`: versioned subprocess protocol and safe failure output.
- `skills.test.mjs`: actual official skill registry, bundled asset loading, override priority, and disposal.
- `setup.test.mjs`: explicit operator configuration, new-file writes, refusal of implicit homes, and non-destructive refusal of old hoisted profiles.
- `import-conversations.test.mjs`: real official session decoding, Zstandard,
  source-byte preservation, original roles, absent/present reasoning, tool IDs,
  inherited branch history, surface replacement audit, invalid records, and
  read-only SQLite search-index refusal.

Run `npm test` after `npm install --ignore-scripts` in the independent plugin
directory or a separate installed copy. Import fixtures use documented v4 event contracts
and validated by the actual released DSH reader. Decoder failures must never be
converted into successful imports by skipping records or weakening validation.

`node setup.mjs doctor --root <absolute core> --python <absolute python>` additionally checks real SDK tool/skill registration and the read-only Python `capabilities` response. With `--home` and `--profile`, it forwards to the installed package and verifies that profile's adapter project. It never creates an Agent or dispatches a job; native host startup/session tool availability and live job ownership are explicitly unverified. Test installation only in an explicit isolated home/profile. Do not treat SDK registration or a config dump as native host validation.
