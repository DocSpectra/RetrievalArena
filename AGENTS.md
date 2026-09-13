# Retrieval Arena Agent Guide

Retrieval Arena (RA) is the evaluator for DocSpectra experiments. It is
driven by declarative configuration: providers are containerized tests under
`tests/`, experiments are YAML under `configs/`, and the immutable
verification image exposes a small operator command set. Workspace policy
lives in `../../AGENTS.md`; this file is RA-specific.

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
| `rag_graph_ranked_expand` | BM25 seeds plus a ranked neighbor budget (`seed_top_k`, `neighbor_budget`) |
| `rag_graph_rerank` | BM25 seeds, graph-aware rerank to `final_top_k` |
| `rag_graph_multihop_rerank` | multi-hop frontier search with distance penalty and reserved deep slots |
| `rag_directed_spectral_rerank` | directed link bonuses plus PageRank/hub authority priors |
| `rag_semantic_graph_rerank` | embeddings plus graph hops and distance penalty |
| `rag_random_seed_graph_traversal` | randomized traversal baseline (`num_trials`, `rng_seed`) |
| `rag_iterative_search` | iterative query refinement over the corpus |
| `rag_llm_guided_search_mock` | bounded action controller and action-trace schema (`e007b.action_trace.v1`) with a mock policy; the navigator scaffold |
| `oracle_perfect` / `oracle_partial` / `oracle_empty` / `oracle_graph_support` | oracles for calibration and graph-support bounds |
| `llm_only` / `local_no_context_llm_e001b` | no-retrieval answer baselines |

All providers currently emit `generated_answer` as the head of the top
document (`max_context_chars_per_doc`). Only `rag_lexical_topk` implements
`best_passage_v1`; other providers still emit head-of-document answers.

## Declaring An Experiment

`configs/<experiment>.yaml`: `datasets` (name, path), `tests` (provider
name, pinned `image`, `build_context`, `config`), `scoring` (method,
threshold), `output_dir`. See `configs/experiment.example.yaml` and the
`docs_pilot_*.yaml` corpus pilots. Run with
`python -m retrieval_arena.cli ...` (see README Core Commands). Corpus
import policy `cikm_flat_path_sha1_v1` derives document ids from paths
(`<stem>__<sha1(path)[:8]>`), so ids survive content edits.

## Operator Contract

`scripts/run-verification.ps1` / `.sh` run the immutable verification image
(`containers/verification/`), repository mounted read-only, network none.
Parameters, by name: `-Command` (`focused`, `all`, `evaluate-profile`,
`review-packet`, `score-bertscore`, `candidate-retrieve`), `-InputRoot`,
`-OutputRoot` (fresh, containing only `.retrieval-arena-output-owner`),
`-CandidateRoot` (candidate-retrieve only), `-Receipt`. PowerShell 7 is
required. Since S024 the `.ps1` binds by name only (`PositionalBinding =
$false`; `Command` is the sole positional), so `-InputRoot -OutputRoot
[-CandidateRoot] -Receipt` must be spelled out; the `.sh` accepts the
same as `--input-root= --output-root= [--candidate-root=] --receipt=`
alongside its positional form. The receipt
(`retrieval_arena.verification_image_receipt.v2`) pins image digest,
source commit/tree, and dependency lock; the operator refuses unless
repository HEAD, tree, and lock match it and the image labels agree — so
any commit here needs `build-verification` and a new receipt.
`candidate-retrieve` admits one config-declared provider per request
(`rag_lexical_topk` only at `d00b38d`; generalized in S025) and requires
a standalone candidate repository whose corpus sits under a subdirectory
(`corpus.root`, e.g. `docs`). The provider container runs as uid/gid
65532:65532 (the prepare step's user, which owns `provider-output`) with
`--cap-drop ALL`; running it as root cannot write the output directory.

Follow-up (S024): the operator's adapter identity is still the whole
repository (HEAD and tree must equal `receipt.source_commit` /
`source_tree`); binding `run-verification.ps1`/`.sh` by their own file
hashes in the receipt instead, so an adapter-only fix would not force an
image rebuild, was judged too invasive for S024 and remains open.

## Adding A Provider

Create `tests/<name>/runner.py` and build context, read the standard
`/input` layout, write `/output/predictions.jsonl` with the common row
shape, add a `tests` entry with a pinned image digest to the experiment
YAML, and a unit test under `unit_tests/`. Declare graph needs so the
operator supplies `graph_edges.csv`. No host-side executor scripts.
