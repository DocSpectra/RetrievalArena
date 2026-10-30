# Page-purpose annotation and human-reference evaluation v1

`page_role_annotation.py`, `page_role_evaluation.py`, offline operator `page-roles`.
The consuming study supplies all role names, rubric, corpus, thresholds, strata,
budgets and classifier settings. No role or scientific acceptance is built in.

Existing `review_packets.py` represents query/snapshot judgments; its fixed
query/condition fields do not represent page-purpose labels and exact source
spans. This separate versioned exchange reuses the immutable operator and
`model_providers`, rather than creating a second client or changing old schemas.

## Source, views and leakage

`pages.json` is sorted unique doc IDs, raw UTF-8 SHA256, declared source family
and outcome-independent topic stratum; `corpus/<id>.md` contains exact bytes.
Static `view` removes frontmatter, destinations, comments and unrendered
directives. Headings, available prose and code remain; images/templates are
marked unavailable. This is source inspection, not a website renderer. Prose
may disclose its own purpose; no redaction can make semantic role labeling
independent of the content. No excerpt or silent context truncation is allowed.

Before sampling, transitive union joins declared source families, normalized
body duplicates (including empty bodies), shared literal includes/readfiles,
and all-pairs word-shingle Jaccard above the caller's threshold. Union reasons
are exported. Conservative source grouping can remove substantial coverage;
it is not proof that every undocumented template family is detected. Audit
those limitations in the consuming study. Discovery pages and their entire
groups are ineligible for primary evaluation.

Hamilton proportional allocation uses lexicographic remainder ties and seeded
within-stratum SRS without replacement. Small strata can get zero; per-stratum
population/sample counts and per-page inclusion probabilities remain explicit.
No rare-class enrichment, class balancing or label-dependent resampling occurs.
Correlated pages may be sampled; grouped folds/bootstrap preserve that dependence.

## Offline operator

`retrieval_arena.page_roles_request.v1` binds a canonical regular input inventory
and SHA256. `config.json` selects `prepare`, `sample`, `import` or `evaluate` and
declares resource/output ceilings (at most four hours/6 GiB). Standard immutable
sh/PowerShell launchers run with network none, four CPUs and 8 GiB. Stages require
fresh output directories and refuse undeclared input assets.

- `prepare`: audit groups, export full discovery source views, opaque card key
  and blank human form. Export only an empty evaluation-form template. No
  evaluation pages are sampled at this stage.
- `sample`: requires a recorded author `yes`, exact rubric/population/group-policy
  hashes and approved count. Exports sample, blind packet, private key and blank
  initial-human form. Keep the key and AI labels away from the reviewer.
- `import`: exact packet/card/rubric/content joins, named reviewer, initial-human
  stream. Blank roles remain unresolved, not completed judgments. Duplicate or
  conflicting imports fail; corrections use new versioned files.
- `evaluate`: exact-source, rubric-approved initial/adjudicated human reference;
  adjudication additionally binds the initial-human file. Discovery groups cannot
  enter labels. Strict `load_bundle` verifies content/node/q/t/graph and saved
  arrays. Missing simple values use training means plus caller-declared missing
  flags. No automatic AI-label substitution is supported.

The output manifest inventories artifacts and pins code/source/image/lock.
Runtime and output budgets are checked between stages, not inside a single
set-comparison or numerical solve; the caller should impose a container timeout.

## Annotation batch (separate network entry point)

`page_role_batch.py` and `scripts/run-page-role-batch.py` reuse the same immutable
verification image, rebind/check source/image/lock, and enable only the existing
Ollama `host_model_alias` transport. This is separate from offline `page-roles`.
No new dependency, credentials, live discovery or model downloads are needed.
The model service has separate host resource costs. Hosted-provider launch is
not admitted here; `run_batch` can accept a provider-neutral test transport.

The approval record requires an explicit recorded author `yes` for the exact
manifest SHA256. Manifest binds full raw page hashes, rubric, prompt, schema,
settings/model/digest, maximum pages/attempts/retries and wall-clock duration.
The batch refuses context overflow using conservative UTF-8 byte admission,
with provider framing covered by the existing transport's context allowance.
No truncation, fallback, preferred retry selection or evaluation-label examples.

Every call starts an append-only journal event before execution. Raw responses
are archived before schema validation; invalid attempts count. First valid
attempt is retained; cached identities include content/view/rubric/prompt/schema
and complete settings. Resume refuses changed manifests or interrupted calls
requiring spend review. A per-call timeout and launcher timeout bound new calls;
resumed completed-call time counts against the cumulative cap. Provider failure
records preserve error type only, avoiding accidental data/secret disclosure.
Do not launch two writers in one output directory.

Annotation requires one allowed role, concise rationale, exact body quote,
ambiguity and incomplete flags. RA resolves the first exact body occurrence to
decoded Unicode-codepoint offsets and records match multiplicity. Unknown may
omit evidence only with incomplete=true. Frontmatter and invented quotes fail.
Model confidence is not used. Call timestamps, digest and attempt IDs remain in
valid records. Schema validity does not establish correctness.

## Agreement and classifier endpoint

`agreement` compares saved independent initial human/AI label streams; it reports
coverage, confusion, per-class scores, raw agreement, Cohen kappa and grouped
uncertainty. Undefined kappa draws are counted. Prevalence and single-human
reference limits remain. Run adjudicated-reference analysis separately, binding
the original files; do not overwrite initial agreement or call it inter-human.

`evaluate` excludes declared mixed/unknown/unresolved cases but counts them in
coverage. It freezes eligible classes from minimum page/group support, then
deterministically balances class counts across whole groups. Actual test and
train class feasibility is mandatory; an unsupported fold yields partial status,
without resampling to get a favorable result.

Training-only word/bigram TF-IDF uses sublinear term frequency, smoothed IDF,
L2 row normalization, declared min_df/max_features and deterministic vocabulary
ties. Historical `clean_text` and `training_boilerplate` are reused unchanged;
no held-out vocabulary/IDF/boilerplate is fitted. Dense graph/simple features
use training mean/std, with zero scale replaced by one. Numeric missingness
requires caller-provided flags. A fixed L2 multinomial softmax minimizes summed
cross entropy plus ||W||²/(2C), with unpenalized intercept, SciPy L-BFGS-B and
declared iteration bound. Nonconvergence fails explicitly. No scikit-learn or
new inference dependency is required.

Majority, text, text+simple and caller-declared kernel-row arms share OOF folds.
Outputs include fixed splits/hashes, predictions, dimensions/convergence,
absolute/per-class/group performance, paired macro-F1 contrasts and joint
group bootstrap intervals. Missing-class draws are skipped and counted.
Full rows are transductive in the fixed graph, not cross-corpus encoders.
Role prediction and human-AI agreement establish neither retrieval nor answer
benefit. Acceptance thresholds belong to the study before scoring.

Fixtures in `unit_tests.test_page_roles` exercise source/cache identity, malformed
annotations, evidence offsets, blind exchange, stale/conflicting labels,
duplicate exclusion, deterministic sampling, group isolation, absent classes,
training-only transforms, retry/resume bounds and offline end-to-end execution.
