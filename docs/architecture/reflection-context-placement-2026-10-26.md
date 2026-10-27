# Reflection guidance after actual dialogue

## Why

The previous case 07 retained three material defects: a historical assistant's
decoding choice became a universal requirement, unnamed private-source narration
appeared in the rationale, and a reusable conclusion template omitted numeric
conditions. Its opening delivered a decision before examining qualifications,
and its rationale repeatedly addressed the reader as “你”. These observations
are bound to exact saved quotations in the offline experiment record.

The earlier instruction already said history was not verified fact. It appeared
before a long source and several dialogue turns. Placement is a plausible
contributor, not a proven explanation of hidden model behavior. This change
moves guidance instead of adding another review agent or reauthoring loop.

## Versioned placement

Recipe 35 freezes `reflection_guidance_placement_version=1`. The CoT application
does not concatenate guidance into the early instruction for these recipes.
The request adapter appends the applicable frozen rules once, after actual
dialogue and the existing authored-stage contract. Joint writing gets applicable
final rules; rationale writing gets prefix rules; native answer continuation
gets only final-only rules. The complete authored native prefix remains intact.

The rule-selection helpers live in the workflow domain. The application defers
placement, infrastructure assembles the wire, and the legacy factory only freezes
the marker. There is no new domain I/O or parallel implementation. Recipe 34 and
older requests, checkpoints, histories and exports are not migrated. Saved custom
rules remain literal; new defaults can still be selected through the existing
node reset and prompt-library controls.

## Editable defaults

- Prior assistant statements are claims to recheck, not user instructions or
  verified evidence. An earlier suggestion is not a user-selected setting.
- Necessary conditions, explicit user requirements, optional reproducibility
  choices and empirical recommendations are distinct. Numeric conclusions must
  retain metric, scale, direction and denominator conditions.
- A real, supported work name may be recalled naturally. Generic references to
  private supplied material should not enter training prose. The system neither
  fabricates titles nor postprocesses “资料里” into an unverified title.
- The rationale is the assistant's working explanation, with “I” for its own
  judgment and “the user” or “the tool” for dialogue actors. It should not turn
  into an early second-person reply. Final answers may address the user normally.
- A complex task with unresolved decisive conditions cannot open with a complete
  settled answer and then retrofit reasons. Tentative local ideas are allowed;
  there is no mandatory opening, question, step order or reasoning length.
- Editable contrastive examples distinguish a comparison from an observed gain,
  matched evaluation from mandatory greedy decoding, training-seed uncertainty
  from test-sample inference, and score points from percentage points. They are
  explicitly procedural examples, not supplied facts or required discussion
  topics. Final-answer guidance also binds the first citation and any fill-in
  conclusion to conditions visible in that answer.

## Validation and limitations

`tests/test_reflection_guidance_placement.py` checks real request assembly,
literal custom rules, unchanged erroneous history, legacy versions, shared-rule
deduplication, phase boundaries, native-prefix preservation and named citation
handling. The durable integrated-reflection test also checks exactly one copy of
each applicable rule and unchanged training projection/resume behavior.

262 related offline tests passed before the live run. They verify contracts and
transport, not semantic success. The isolated experiment is
`.dataforge/software-ops-2026-10-24/role-anchor-context/`: the same cases 02/07,
same source, histories, base templates, routes and V4.1-Flash configuration.
The expected path is three generation requests and no judge requests. The report
must preserve complete before/after outputs, unfinished issues, native reasoning,
actual usage and estimated costs without promoting the model's own verdict.

## Actual two-attempt result

The first live attempt preserved roles but still asserted improvement without
measurements, conflated units and inherited overly strong decoding advice. Its
complete output and assessment remain in `role-anchor-context/`. The second
attempt, `role-anchor-context-v2/`, used the additional contrastive examples on
the same two cases, without adding case-specific measurements or target answers.
The 71 core placement/architecture tests passed again after this wording update;
the 191 wider request/transport regressions had passed before it.

| Case | Latest generation requests | Wall seconds | Estimated CNY | Full-text inspection |
| --- | ---: | ---: | ---: | --- |
| 02, English single turn | 1 | 56.70 | 0.07557 | No targeted defect found in the current reply |
| 07, Chinese third turn | 2 | 58.93 | 0.06817 | Role and conditionality improved; causal wording and external-ranking comparability still need review |

Both authored rationales contain no second-person narration and start with
interpretation of the actual user request or its decisive premise, not a full
settled answer. This is a reading of these two saved outputs, not a guarantee of
future generations or proof of hidden reasoning faithfulness. Case 07 now
explicitly distinguishes having a control checkpoint from observing a gain, but
still calls it the only causal comparison without enough qualification. Old
assistant messages remain unchanged; the full historical conversation is not
therefore certified as training-ready.

Both attempts together made six generation requests and zero judge requests,
with estimated cost CNY 0.43019, including the unsuccessful first attempt. The
latest attempt alone cost CNY 0.14375. Requests ran during the verified October 26
weekday peak window, so the report uses CNY 0.04/2/8 per million cache-hit input,
cache-miss input and output tokens. The earlier recipe-34 comparison ran off-peak;
its invoice estimate must not be used as a direct efficiency comparison. Reports
retain complete requests, responses, native reasoning, authored rationale,
answers, token accounting, exact-quote assessments and both attempts. Local HTTP
bytes, full candidate text and every relative report link were checked. No new
screenshot-based visual acceptance was performed in this backend-only change.

Transformers documents multiple valid [decoding strategies](https://huggingface.co/docs/transformers/main/en/generation_strategies).
The previous claim that one deterministic configuration is universally necessary
does not follow from that documentation. Fair comparison and a chosen decoding
policy are separate conditions. Live prices are checked against the
[official DeepSeek schedule](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/).

The distinction between training repeats and test-sample inference is informed
by [Dror et al., ACL 2018](https://aclanthology.org/P18-1128/), which discusses
paired significance tests and their metric/sampling assumptions. This does not
mean a significance test was performed in either generated example. The workday
calendar was checked against the [government holiday notice](https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html).

## Follow-up: case 07 causal wording and inherited claims

The next requested repair keeps only case 07. Its original user messages,
assistant history, full source, model bindings and base templates remain fixed.
Defaults distinguish confirmed user facts from desired conclusions and old
assistant claims. `revision` binds the same evidence status to prose, trace,
comparison names and conclusions. `comparison_scope` separates shared origin,
verified configuration, measured change and attribution. An untreated baseline
does not have the treatment run's fine-tuning data or budget.

Wire inspection also found that native continuation received final-only rules,
without the specific shared `basis` rules used for rationale writing. The new
editable `continuation_basis` rule carries relevant evidence, units and decoding
boundaries into the final stage. This is an observed prompt difference and a
plausible contributor to regression, not proof of the model's internal cause.
No new model call, reviewer, postprocessing substitution or fixed reasoning
sequence was added. Frozen runs and custom policies are not rewritten.

The 71 placement, native-prefix, integration and architecture regressions passed
after the final default-policy edit. Four isolated attempts were needed; none
are hidden from the report:

| Attempt directory under `.dataforge/software-ops-2026-10-24/` | Requests | Estimated CNY | Inspection |
| --- | ---: | ---: | --- |
| `role-anchor-causality` | 2 | 0.05183 | Historical causal label, units and second-person quotation recurred |
| `role-anchor-causality-v2` | 1 | 0.03828 | Malformed JSON rejected; no answer continuation; full raw content retained |
| `role-anchor-causality-final` | 2 | 0.06596 | Invented configuration equality and invalid training-match requirement |
| `role-anchor-causal-scope` | 2 | 0.05914 | Targeted current-reply defects corrected; report conclusion remains conditional on real measurements |

All seven requests used verified V4.1-Flash, with zero judge requests. Total
estimated cost is CNY 0.21521 including all unsuccessful attempts. The latest
candidate took 49.48 seconds and used 65,499 input tokens (63,744 cache hits,
1,755 misses) and 6,635 output tokens including provider reasoning. Estimates
use request-time peak pricing; they are not invoices.

The latest rationale explicitly says an executable comparison is not an
observed improvement. The answer offers improvement, decline or no clear
difference as result-dependent alternatives, corrects the training-budget
matching mistake, and conditions external ranking on comparable evaluation.
Authored narration keeps the assistant/user distinction without invented
second-person reviewer speech. Complete prose and `reasoning_trace` were read;
exact quotations are bound to candidate hashes in `assessment.json`.

This is targeted inspection of a known historical case, not an independent
blind evaluation or a production acceptance-rate estimate. The phrase about a
limited whole-intervention effect in the conditional draft must still be
interpreted against actual measurements and design evidence, never as a positive
result established here. Old assistant errors remain in the original input
history; repairing the current reply does not certify every historical turn as
a training target. The report includes full before/after dialogue, authored and
provider reasoning, trace, request/response artifacts and all attempt costs.
