# Bulk review and targeted completion

The previous semantic reviewer already asked for a single full-table response.
Its commit was atomic: one invalid entry discarded every otherwise valid entry.
The next response had to submit the entire table again.

## Ownership and flow

New evidence-policy-2 runs freeze recipe 32 and
`self_check.batch_submission_version=1`. The application composes
`ReviewSubmissionBatch` around the existing `ObligationReview` domain workspace.

1. A program-owned system instruction requests every applicable item in the
   first submission. Source reads and calculations remain available for actual
   evidence gaps; the schedule does not prescribe the author's reasoning order.
2. The program checks citations, field types and local item requirements
   independently. Accepted items survive invalid siblings.
3. Feedback contains accepted IDs, missing IDs and item-specific errors. A later
   response sends only these gaps, with empty or omitted completed collections.
4. The program merges receipts and calls the original complete validator. Rule
   coverage, risk coverage, every historical assistant surface, findings and
   their relationships remain mandatory.

`validate_review_entries` and `validate_resolution_entries` do not certify a
complete review. Only `validate_review` / `validate_resolution` can complete a
session. Completion itself is not admission: confirmed defects and uncertainty
still follow the existing repair or unresolved branches.

The author, independent reviewer and dispute resolver have separate workspaces.
No author verdict is sent to the independent review. Disputes are submitted in
one batch too; missing or invalid decisions can then be completed individually.

## Recovery and compatibility

Successful entries are immutable, including negative findings. A malformed
negative entry cannot be replaced by a positive vote during representation
repair. Conflicting committed entries and entries with no usable identity remain
unresolved; the implementation cannot safely infer which allegation to erase.
Repeated errors and overall turn limits remain bounded.

Each existing model-turn checkpoint is replayed deterministically to reconstruct
partial state after interruption. No new database or domain I/O is introduced.
The schedule version participates in review cache identities and workspace
bindings. Old recipes retain their old protocol; export fields and frozen task
records are unchanged. The runtime supported-version list includes both recipe
31 and 32.

## Evidence and limits

`tests/test_review_submission_batch.py` exercises the scheduling contract with
offline ports and adversarial mutations. The local report in
`.dataforge/software-ops-2026-10-24/agent-batch-review/` replays the frozen
ten-case experiment and verifies its 911 source-file hashes. It does not create
new model responses, revise sample quality grades or infer monetary savings.

The historical trace contains 104 tool requests in 36 review sessions. The new
collector can preserve 427 valid entries in 27 sessions whose whole-table
submissions were previously rejected. 209 exact repeated entries are omitted
in the counterfactual packaging replay. Three previously complete sessions also
contain changed already-accepted entries; the new immutable collector flags
these conflicts. The report therefore shows 11 legacy versus 8 new structurally
complete sessions, not an improved usable-sample rate. A future API trial must
measure whether explicit gap-only feedback avoids that historical behavior.

This change addresses redundant submission and lost partial work. It does not
claim to fix semantic false acceptance, source ambiguity or language quality.
