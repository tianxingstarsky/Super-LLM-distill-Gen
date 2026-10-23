# Independent dsh adapter validation — 2026-10-23

The Python product no longer vendors, installs, launches, builds, or imports DeepSeek Harness. The optional adapter is a separate npm archive with its own version, SDK peers, compatibility declaration, and tests. It uses the provider-neutral [control protocol v1](external-control.md).

## Tested versions

- Adapter: `dsh-shujian-dataforge@0.2.0`.
- Official Harness: `0.2.1-rc.2`, commit `687f282e8625c6b8d11ff58211a90524be2e4317`.
- Archive SHA-256: `d828494379a3d21094d8f146562db814eb1cf825ac5e9363f60a96c827d95952`.
- Host: Windows, Python 3.11.9. A separately installed modern Docling environment covers native parser version conditions.

## Execution results

| Check | Passed | Skipped | Failures / errors |
| --- | ---: | ---: | ---: |
| Full unfiltered Python regression | 7,953 | 51 | 0 / 0 |
| Modern Docling supplement | 77 | 0 | 0 / 0 |
| Installed adapter archive tests | 13 | 0 | 0 / 0 |
| Focused export and archive regression | 94 | 0 | 0 / 0 |

The full run completed in 1,762.77 seconds. Fifty base-environment Docling version conditions were selected for the modern-environment supplement; parameter expansion produced 77 passing cases. The remaining skip concerns POSIX termination semantics that do not apply on Windows. Assertions were not weakened and no new skip was introduced. Focused tests overlap the full suite and must not be added to it as independent coverage.

The scale checks retained their original sizes. The 50,000-row arithmetic preparation took 218.16 seconds; the 50,000-row Agent review index took 122.87 seconds. Neither test was reduced to obtain a passing result.

## Real Harness interoperability

The final archive was installed with the official plugin CLI into an isolated profile, including its declared SDK peers. A real Harness boot, tool registry, and Windows managed subprocess provider successfully executed:

- `capabilities`;
- `runs.list`;
- `models.list`;
- `runs.inspect`;
- `runs.preview`, retaining all three assistant reasoning fields in the selected multi-turn record.

No paid model API was invoked. Background scheduling, dependency failure, priority/FIFO admission, ownership, cancellation, and cleanup tests use the declared real SDK with controlled job and subprocess services. This is not a claim that a paid Agent generation task was run. Model limits remain shared within a core installation, not across machines.

## Actual data inspection

Thirty-two existing manifests passed integrity verification. The inspected eligible native records contained 52 SFT, 22 multi-turn, and 8 CoT records. Native reasoning and corresponding TRL thinking fields had zero comparison mismatches. Integrity and preservation do not establish semantic accuracy.

The 74 dialogue records represented 61 distinct visible dialogues. Sixteen sample families retained multiple revisions. None of the 105 user utterances was 20 characters or shorter; median length was 257. Historical records lacked the current claim-review receipt field, so current code tests cannot retroactively certify them.

Exact local comparison also confirmed that legacy export formats contained a configured model credential. No value is included here. A real attempt to republish an affected SFT file was blocked with `export_contains_configured_credential`; its original hash remained unchanged. The publication guard checks retained reasoning, tool arguments, sidecars, and ZIP members. It rejects a release instead of deleting reasoning or silently dropping records. Exact configured-credential matching is not a general privacy or unknown-secret detector.

Historical files were not rewritten, deleted, re-reviewed by a model, or declared ready for training. The local report includes full candidate dialogue and reasoning for inspection; private source data is not committed to GitHub.

The run logs, JUnit XML, archive, local audit, and validation JSON are stored under `.dataforge/dsh-adapter-audit-2026-10-23/`. Installation instructions are in the [independent package README](../plugins/dsh-dataforge/README.md).
