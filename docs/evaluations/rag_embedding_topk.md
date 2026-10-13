# `rag_embedding_topk`

Embedding-similarity top-k: cosine over document and question embeddings from either a local hashing TF-IDF backend (no model files) or an offline sentence-transformers snapshot validated against a model lock (per-file sha256 and size, `offline_required`). Status: pilot-era family, not used by a Capstone experiment.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/config.yaml`; in the sentence-transformers mode the lock JSON at `model_lock_path` and the snapshot at `model_cache_path` (mounted read-only; the pilot configs pin `sentence-transformers/all-MiniLM-L6-v2`).

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; items `source: embedding_topk`, `rank`; metadata records `ranker: cosine`, the backend, model id/revision, lock and cache paths, `model_provenance`, `offline_required`.

## Knobs

| Key | Default |
| --- | --- |
| `top_k` | `5` |
| `max_context_chars_per_doc` | `1500` |
| `embedding_dimensions` | `512` (min 64) |
| `model_id` | `local-hashing-tfidf-v1` |
| `embedding_backend` | `local_hashing_tfidf` (or `sentence_transformers_local`) |
| `model_revision` | `""` |
| `model_lock_path` | `/model_provenance/embedding_models.lock.json` |
| `model_cache_path` | `/models/minilm` |
| `batch_size` | `32` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); `configs/minilm_smoke.yaml` and the `docs_pilot_*.yaml` corpus pilots.

**Tests:** no runner test; `unit_tests/test_config.py` (`test_loads_minilm_smoke_with_e009`, read-only volumes) and `unit_tests/test_harness.py` (missing volume host path) cover its configuration.

**Source:** `tests/rag_embedding_topk/runner.py` (Dockerfile installs `sentence-transformers>=3.0`).
