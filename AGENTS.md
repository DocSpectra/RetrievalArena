# Retrieval Arena Agent Guide

Retrieval Arena (RA) is the evaluator for DocSpectra experiments. It is
driven by declarative configuration: providers are containerized tests under
`tests/`, experiments are YAML under `configs/`, and the immutable
verification image exposes a small operator command set. Workspace policy
lives in `../../AGENTS.md`; this file is RA-specific.

## Progressive loading for extensions

Before adding experiments, evaluations, providers or dataset processing, read
[docs/extending.md](docs/extending.md), then only the relevant workflow page
and its linked contracts/source. It distinguishes YAML execution from the
candidate operator, legacy scoring from ranked evaluation, and acquisition
from preprocessing and independent judgments. Keep reusable RA knowledge in
these docs; keep paper-specific protocol and evidence decisions in the
consuming workspace's context. Update the relevant page when implementation
changes a documented contract.

Use [architecture conventions](docs/architecture-conventions.md) for names
and execution/artifact distinctions. Reference its RA-D follow-up IDs when
a change crosses those boundaries; record a decision before changing shared
semantics. Do not treat deferred architecture work as a prerequisite unless
the chosen experiment needs that specific boundary resolved.

## Providers

One directory per provider under `tests/<name>/` (`runner.py`, container
build context). Each reads `/input` (`questions.jsonl`, `corpus/`, optional
`graph_edges.csv`, `config.yaml`) and writes `/output/predictions.jsonl`.
Knobs are the `config.get(...)` keys in each runner.

| Provider | What it does |
| --- | --- |
| `rag_lexical_topk` | BM25 over whole documents, top-k; the EXP-1 flat retriever; `answer_policy: head_of_document_v1 \| best_passage_v1` (default head-of-document; best-passage adds `answer_provenance`) |
| `rag_embedding_topk` | embedding similarity top-k (pinned local model, offline) |
| `rag_vector` | minimal vector top-k example |
| `rag_graph_expand` | BM25 seeds expanded along `graph_edges.csv` by N hops |
| `rag_graph_ranked_expand` | BM25 seeds plus a ranked one-hop neighbor budget (`seed_top_k`, `neighbor_budget`; `top_k` = their sum, `graph_expansion_hops` fixed at 1); the S025 graph retriever; `answer_policy` as `rag_lexical_topk` |
| `rag_graph_rerank` | BM25 seeds, graph-aware rerank to `final_top_k` |
| `rag_graph_multihop_rerank` | multi-hop frontier search with distance penalty and reserved deep slots |
| `rag_directed_spectral_rerank` | directed link bonuses plus PageRank/hub authority priors |
| `rag_semantic_graph_rerank` | embeddings plus graph hops and distance penalty |
| `rag_random_seed_graph_traversal` | randomized traversal baseline (`num_trials`, `rng_seed`) |
| `rag_iterative_search` | iterative query refinement over the corpus |
| `rag_llm_guided_search_mock` | bounded action controller and action-trace schema (`e007b.action_trace.v1`) with a mock policy; the navigator scaffold |
| `rag_llm_guided_search` | S026 (EXP-1c): the promoted navigator. Starts at the corpus root `index.md`, actions `open(link)` (a link the current page carries, resolved through `graph_edges.csv`), `back`, `answer(cited_pages, text)`; budgets `page_visit_budget` opens and `model_call_budget` calls per query; a real local model (`model_policy`: Ollama, digest-pinned, observed before the run and after every call) reached only at `http://host.docker.internal:11434` over the allowlisted routes; one trace per query under `traces/<question_id>.jsonl` (`e007b.action_trace.v1`, policy `bounded_provider_action_controller_v1`), `answer_policy: agent_answer_v1` (the agent's text; cited pages are the context items). `scripts/navigation_metrics.py` recomputes success@N, hops-to-first-support, pages-per-success, and dead-ends from the traces plus the judge's support file |
| `rag_repo_search_agent` / `rag_repo_search_agent_nolinks` | S046 (EXP-1e): the repo-search agent reader, one image in two variants fixed by the provider name (`follow_links` true / false). Lands by an automatic search of the question over the corpus Markdown under the request's `landing_policy` (S046b, closed: `search_question_text`, a literal case-insensitive line grep of the whole question; `tokenised_content_words_v1`, the question's content words -- `[a-z0-9]+` tokens, `STOP_WORDS_EN_V1` removed -- matched as a set with files ranked by matched-word count, then best line, then id; `search_hit_files` files, `search_hit_lines_per_file` lines each; a query is text, never a path; the endpoint recomputes the landing terms at finalize); actions `search(query)`, `open(doc_id)` (a hit of any search so far or, in the links variant, an admissible outlink of the current page), `back`, `answer(cited_pages, text)`; budgets `search_budget` (the landing counts), `page_visit_budget`, `model_call_budget`; the navigator's model policy, relay rules and network exception; one `e007c.action_trace.v1` trace per query (policy `bounded_repo_search_controller_v1`: search lines with their queries and returned ids, opens with a `route` -- `landing`/`search`/`link`, search before link when both offered the page). `scripts/search_navigation_metrics.py` recomputes success, landing hits, successes by route, `link_new` against a baseline graph, cost to first support, from the traces plus the judge's support file |
| `oracle_perfect` / `oracle_partial` / `oracle_empty` / `oracle_graph_support` | oracles for calibration and graph-support bounds |
| `llm_only` / `local_no_context_llm_e001b` | no-retrieval answer baselines |

Providers emit `generated_answer` as the head of the top document
(`max_context_chars_per_doc`) unless they implement `best_passage_v1`:
`rag_lexical_topk` and `rag_graph_ranked_expand` do (the graph runner carries a
byte-identical copy of the passage functions, asserted by its unit test);
other providers still emit head-of-document answers.

The answer *synthesis* stage (S031, EXP-1d: a fixed local model reading a
provider's `best_passage_v1` passages) does not live here. It is the
Capstone capsule command `synthesize-direct-exp1`
(`git/DocSpectraCapstone/src/docspectra_capstone/direct_exp1_synthesis.py`),
because it needs the host model through the capsule's Ollama relay with the
same observed-identity admission as authoring, and `candidate-retrieve`
provider containers run with `--network none` (the
`local_no_context_llm_e001b` loopback gate cannot reach the host model from
a provider container). RA judges the synthesized answers with the unchanged
`score-bertscore` command over pairs Capstone composes
(`<directory>::<context_policy>::<query_id>`).

## Support Reachability At Import (S036)

`retrieval_arena/corpus/dataset_export.py::build_reachability_surface` runs
after `build_support_surface` in the import chain and standalone as
`python -m retrieval_arena.cli corpus reachability --dataset <path>`. It reads
`corpus/`, `graph_edges.csv`, `questions.jsonl` and the support audit's target
ids and `support_label` (never `answers.jsonl`) and writes
`support_reachability.v1.json` beside the audit (per-target degrees, size,
directed/undirected hops from the root `index` document; fractions within
1/2/3/8 hops, median hops, orphan fraction, questions with a target within 3
hops, support-class mix) plus `support_reachability_manifest.json` (file hash,
method id `support_reachability_v1`). A dataset missing an input gets
`status: unavailable` with the input named. The computation is
`retrieval_arena/support/reachability.py`; `unit_tests/test_reachability_surface.py`
is in the `focused` suite.

## Declaring An Experiment

`configs/<experiment>.yaml`: `datasets` (name, path), `tests` (provider
name, pinned `image`, `build_context`, `config`), `scoring` (method,
threshold), `output_dir`. See `configs/experiment.example.yaml` and the
`docs_pilot_*.yaml` corpus pilots. Run with
`python -m retrieval_arena.cli ...` (the operator commands are
`docs/operator.md`). The table of every evaluation a test or scoring
surface can apply, one page per row, is `docs/evaluations/README.md`
(`docs/retrieval_evaluation.md` explains the contract). Corpus
import policies derive document ids from paths (`<stem>__<sha1(path)[:8]>`),
so ids survive content edits: `cikm_flat_path_sha1_v1` hashes the path as
written; `cikm_flat_path_sha1_lower_v1` (S044) hashes it lowercased, the rule
of the CIKM dataset importer that produced the support ids, so candidate ids
and support ids join under one rule. The policy is declared per
candidate-retrieve request (`corpus.import_policy`) and recorded in the
launch record; a corpus whose paths differ only by case is refused at import
(`candidate-retrieval-document-id-collision`), never merged. A request may
also declare `corpus.exclude_policy` (S047): the closed id
`cikm_faq_pattern_v1` is the CIKM exporter's FAQ rule (the lowercased
corpus-relative path matches `*faq*` or carries `faq` in any path part,
Capstone `configs/pinned_cikm_exporter.yaml` as the rule of record); a page it
names is neither copied into the provider input nor a node of the extracted
graph (a link to it resolves to nothing), and the launch record and the
operator receipt carry `corpus_exclude_policy`, `excluded_document_count` and
`excluded_document_ids`, so a scored root states which corpus it read. A
request whose `experiment_id` starts with `E-EXP` (every direct-lane
campaign) is refused without the field
(`candidate-retrieval-exclude-policy-required`). The rule reads paths, never
content. `cikm_faq_pattern_v2` (S023) is the same rule with the corpus's own
pattern list declared on the request (`corpus.faq_patterns`, required under
v2, refused under v1) plus the exporter's underscore-directory skip
(`_snippets` and every other `_`-prefixed directory but `_sources`); the
launch record and the receipt carry `corpus_faq_patterns`.

## Operator Contract

`scripts/run-verification.ps1` / `.sh` run the immutable verification image
(`containers/verification/`), repository mounted read-only, network none.
Parameters, by name: `-Command` (`focused`, `all`, `evaluate-profile`,
`review-packet`, `score-bertscore`, `candidate-retrieve`, `measure-docstyle`, `measure-graph-spectra`, `measure-magnetic-attribution`, `audit-trace-provenance`, `compare-answer-elements`), `-InputRoot`,
`-OutputRoot` (fresh, containing only `.retrieval-arena-output-owner`),
`-CandidateRoot` (candidate-retrieve and measure-docstyle), `-Receipt`. PowerShell 7 is
required. Since S024 the `.ps1` binds by name only (`PositionalBinding =
$false`; `Command` is the sole positional), so `-InputRoot -OutputRoot
[-CandidateRoot] -Receipt` must be spelled out; the `.sh` accepts the
same as `--input-root= --output-root= [--candidate-root=] --receipt=`
alongside its positional form. The receipt
(`retrieval_arena.verification_image_receipt.v2`) pins image digest,
source commit/tree, and dependency lock; the operator refuses unless
repository HEAD, tree, and lock match it and the image labels agree — so
any commit here needs `build-verification` and a new receipt.
`candidate-retrieve` admits one config-declared provider per request from
the closed profile table in `retrieval_arena/candidate_retrieval.py`
(`rag_lexical_topk` only at `d00b38d`; `rag_graph_ranked_expand` added in
S025; `rag_llm_guided_search` added in S026, `rag_repo_search_agent` and its `_nolinks` variant in S046, each pinned by `image@sha256`) and requires a standalone candidate
repository whose corpus sits under a subdirectory (`corpus.root`, e.g.
`docs`); `measure-docstyle` (S052) shares that admission but also admits
`corpus.root: "."`, a whole repository, because it reads documents and
ranks nothing. A `requires_graph` provider gets the candidate's own link graph
(`graph_edges.csv`, `write_markdown_link_graph`'s resolver over the corpus
tree with flat ids) extracted at prepare, hashed into the launch and the
receipt (`graph_edges_sha256`), and published beside the predictions. The provider container runs as uid/gid
65532:65532 (the prepare step's user, which owns `provider-output`) with
`--cap-drop ALL`; running it as root cannot write the output directory.
Network: every retriever's provider container runs `--network none`. The one
exception (S026; S046 extends it to the repo-search agent's two variants) is the navigator profile (`network: host_model_alias`): the
operator runs it on Docker's default bridge with `--add-host
host.docker.internal:host-gateway` (the direct lane's relay posture, S031 D4),
the runner's own client enforces the relay rules (private upstream only,
`GET /api/tags` and `POST /api/chat`, model digest observed per call), and
the receipt's `isolation.network` records the exception. `measure-docstyle`
(S052) is the one single-phase command with the candidate mounted: the
DocEng 2026 Appendix B per-document metrics with spaCy `en_core_web_sm`
3.8.0 (rows of the dependency lock, installed in the provisioned base) over
the request's corpus and policy, a summary, the four-signal Spearman matrix
and support-page percentile ranks, and (S055) the identifier columns: a rule-based identifier counter over an identifier view that keeps inline code, and mentions of a repository-grounded lexicon harvested from the candidate when the config carries a `lexicon` block (written as `repo_lexicon.v1.json`) or read through `lexicon_path`; `docs/evaluations/measure_docstyle.md`.
The navigator's
`traces/` directory and `navigation.json` are published beside the
predictions and inventoried in the receipt (`traces_inventory_sha256`,
`navigation_sha256`); the operator-side projection validates every trace
against the declared budgets and the admitted document ids.

Follow-up (S024): the operator's adapter identity is still the whole
repository (HEAD and tree must equal `receipt.source_commit` /
`source_tree`); binding `run-verification.ps1`/`.sh` by their own file
hashes in the receipt instead, so an adapter-only fix would not force an
image rebuild, was judged too invasive for S024 and remains open.

## Checkouts

For Capstone, `git/RetrievalArena` on `capstone/retrieval-metrics-v2` is the
only working tree of this object store that outlives a sprint. A pin is a
tag, never a directory (`e-exp1-evaluator-pin` names the 33V evaluator
source `d412e87`; the BigData freeze is `bigdata2026-evidence-freeze-2026-05-31`);
replaying one is `git worktree add <short-path> <tag>` for the replay, then
`git worktree remove`. A sprint's linked worktrees (`.s0xxwt/ra`,
`.s0xxwt/ra-review`, an LF operator checkout at the receipt's commit) are
transient: created from a short workspace-root path, removed at that
sprint's close (S048, 2026-09-16). Because the operator's identity is the
whole repository, an RA scoring command after a docs-only commit still
runs from a transient worktree at the receipt's `source_commit` until the
next image rebuild rebinds the receipt.

A consuming workspace may explicitly authorize a longer-lived worktree.
The ICSC workspace has authorized `icsc2027/downstream-case` for its paper
work; its root guide controls that worktree's lifetime. Do not apply
Capstone sprint cleanup to it or switch Capstone's checkout for ICSC work.

## Collaboration

Added October 12, 2026 (ICSC C3). `main` on `origin`
(`DocSpectra/RetrievalArena`, private) is the integration branch for every
workspace (Capstone, BigData, JCDL, CIKM, ICSC).

- **Branch per workspace sprint** from `origin/main`
  (`git fetch origin && git switch -c <workspace>/<topic> origin/main`).
  Workspace protocol, configs and evidence stay in that workspace's own
  context repository.
- **Land back by pull request or fast-forward** only. Fast-forward `main`
  when `origin/main` is an ancestor of the branch; otherwise open a pull
  request. Tag consolidation points (for example `ra-consolidation-2026-10`).
- **Never rewrite pushed history.** No force-push, rebase, squash or amend of
  pushed commits: consuming workspaces pin RA commit SHAs in their evidence.
- **Document what is reusable** in the same branch: a page under `docs/`, a
  catalog row in `docs/evaluations/README.md` for an evaluation, and offline
  unit tests in the `focused` suite (`containers/verification/verify.py`).
- **Corpus-independent.** RA code and defaults name no corpus; corpus ids,
  paths, study parameters and dataset-specific prompts live in the consuming
  workspace's configuration.
- **Container-first and portable.** New stages run as pinned-image operator
  commands with receipts; host scripts are thin shims, preferably Python.
  Targets include DGX Spark (linux/arm64), macOS and HPC (Apptainer): record
  any new amd64-only, Windows-only, CUDA-required or Docker-daemon-only
  assumption as an RA-D row in `docs/architecture-conventions.md`.
- **Model calls and credentials.** Hosted model calls go through
  `retrieval_arena/model_providers/` with a priced config and a spend ledger,
  in `external_api` containers that receive credentials by name only. Never
  commit or print keys.

## Adding A Provider

Create `tests/<name>/runner.py` and build context, read the standard
`/input` layout, write `/output/predictions.jsonl` with the common row
shape, add a `tests` entry with a pinned image digest to the experiment
YAML, and a unit test under `unit_tests/`. To admit it at
`candidate-retrieve`, add a `ProviderProfile` row (image repository, item
source, provider-specific config keys, `requires_graph`) and its metadata
shape in `retrieval_arena/candidate_retrieval.py`. No host-side executor
scripts.
