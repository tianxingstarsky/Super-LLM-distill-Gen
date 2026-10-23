# Sample distributions and export sizes

Open **Data Library → Sample Statistics**. The release package page also links to the statistics for its selected run.

The dashboard counts native training records in one verified workflow version. It does not add converted copies as new samples or merge historical runs. Counts across objectives are not claims of independent knowledge or unique dialogues. Agent failure traces are separate from positive totals. Other quarantined records remain in the quality report and are not counted as delivered samples.

Detailed distributions include target, body language, user language, user turns, body length, reasoning length, and records with images or tools. Body text excludes system prompts, tool results, and separate reasoning fields. Alias reasoning fields count once. CoT message-list questions and reasoning lists retain their native meaning. A preference pair is one record, not two samples.

Language is a deterministic estimate based on Han and Latin character shares. A share between 20% and 80% is classified as mixed. Other scripts, numeric-only text, and absent user text have explicit categories. This is not a model language assessment. Length uses characters, not tokens. Reasoning-length percentages use only records with reasoning; other distributions use the selected record count.

Sizes come from actual files in the verified manifest. Native training size excludes format conversions, failure traces, resources, and audit sidecars. Total export size includes all listed artifacts and `manifest.json`, but excludes the ZIP. ZIP size appears only after a completed archive exists. Values use binary KiB/MiB/GiB units; exact byte counts are available in the file table and statistics JSON.

The first visit scans in an isolated local worker. Navigation stays available. Progress and cancellation use the existing persistent local job service. The worker streams one JSONL record at a time and merges fixed-size histograms. A record above 64 MiB fails explicitly instead of being truncated. Native row counts must match the manifest, and source hashes are checked before publishing the cache. Changing artifact identity or the manifest invalidates the cache. Completed views do not keep a polling timer running.

Statistics are saved under each run's `analysis/` directory. No training artifact is rewritten. No generation or model API is called. The dashboard can download a compact JSON report containing counts, sizes, and hashes rather than sample contents.

Validation on 2026-10-23: 97 related tests passed, including a 50,000-record scan below 4 MiB of traced Python allocations. The check does not bound process RSS or a large individual record. All 32 existing verified workflow versions were inspected successfully, with native counts matching their manifests. Historical revisions were kept separate. These checks establish counting and storage behavior, not semantic sample accuracy.
