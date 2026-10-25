# Accuracy computation — no measured claim yet

**Status: not evaluated.** The files shipped here contain a calculator, empty decision templates, and engineering regression tests. They contain no formal SO4 accuracy result and no claim that 95% has been achieved. Historical RAG benchmark outputs elsewhere in the original ZIP are unrelated to this SO4 metric.

An OpenAI key is **not required** for deterministic SO4 evaluation. The authors have requested only the computation facilities at this stage, so a representative, independently verified academic decision dataset has not been scored.

## Formula and citation

`Accuracy (%) = correct decisions / total evaluated decisions × 100`

For binary decisions this equals `(TP + TN) / (TP + TN + FP + FN) × 100`. See Grandini, M., Bagli, E., & Visani, G. (2020). *Metrics for Multi-Class Classification: an Overview*. arXiv:2008.05756, Section 2. https://doi.org/10.48550/arXiv.2008.05756

This reference supports the computation. It does not establish that 95% is an institutionally mandated threshold or that the threshold is achieved by this project. The thesis authors must justify their target and evaluation design separately.

## Unit of evaluation

Record one independently specified binary decision per row:

* `eligibility`: whether a student can take a specified subject with their stated completion/enrollment records and a fully specified proposed concurrent plan. `true` means eligible; `false` means ineligible.
* `constraint`: whether a specified complete plan satisfies the applicable prerequisite and course-load rules. `true` means valid; `false` means invalid.

Each decision has a unique case ID. If one scenario produces both types of decisions, assign distinct IDs such as `SC-001-EL` and `SC-001-CV`. Calculate the two metrics separately. Do not substitute retrieval quality, chatbot answer similarity, response latency or a count of passing software assertions for either academic decision metric.

Before execution, prepare expected outcomes directly from official checklists and the applicable load policy. Do not obtain the expected answer by calling the implementation under test. Keep the source/page, input profile, exact progress states, plan, permitted alternatives and load limit with each scenario. A knowledgeable independent reviewer can strengthen this ground truth; ambiguous cases need resolution before they are scored as verified.

Include eligible and ineligible outcomes, valid and invalid plans, hard/soft/co-requisite cases, zero-unit requirements, equivalent/GE credit pending and confirmed states, exact-limit and overload boundaries, other standing and unresolved requirements across the supported curricula. The chosen scenario count and coverage require justification. Accuracy on an unrepresentative set does not establish performance on future users; report the confusion matrices and false-eligible/false-valid decisions as well.

## Input format

Download the empty CSV from the Accuracy computation tab, or start with `evaluation/so4/decisions.template.csv`:

| Column | Required value |
|---|---|
| `case_id` | Nonempty unique decision ID |
| `decision_type` | `eligibility` or `constraint` |
| `expected` | `true`/`false`, or `1`/`0`, independently derived |
| `actual` | Actual observed module decision using the same inputs |
| `source_reference` | Official source, page and policy applicable to the expected answer |
| `expected_verified` | `true` after independent source checking; otherwise `false` |

The template intentionally leaves expected and actual outcomes blank. Neither the template nor checking a box alone creates verified evidence. A report with blank/unverified records retains status `not_evaluated` and no headline percentage. Zero observations produce `null`, not 0% or 100%. A missing decision category remains unmeasured.

## Compute locally

With the SO4 virtual environment installed:

```powershell
.venv-so4\Scripts\python.exe -m personalization.accuracy --input evaluation/so4/decisions.template.csv
```

The template returns `not_evaluated`. Once real, independently verified decisions have been recorded in a private local file:

```powershell
.venv-so4\Scripts\python.exe -m personalization.accuracy --input evaluation/so4/local-results/actual-decisions.csv --output evaluation/so4/local-results/report.json
```

Or upload that CSV in the application's **Accuracy computation** tab. Uploads are processed in memory and not retained. The calculator reports separate denominators, correct counts, percentages, TP/TN/FP/FN, and whether each supplied metric meets the target. It does not call MySQL, RAG or any model API. Keep actual student data out of public commits; prefer synthetic, de-identified scenarios.

## Engineering checks versus the thesis claim

`tests/test_rules.py` and `tests/test_app.py` verify software behavior with synthetic fixtures. These checks help detect programming errors; they are **not** an independently verified academic accuracy experiment and their pass rate must not be reported as SO4 accuracy. The formal status file remains `evaluation/so4/status.json` with null results until the authors conduct and document that experiment.
