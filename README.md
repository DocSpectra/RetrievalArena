# RetrievalArena

RetrievalArena is a dependency-light, containerized evaluation harness for documentation question-answering systems. It runs Dockerized retrieval/generation tests against prepared dataset directories, validates the input/output contracts, computes deterministic lexical scores, and writes reproducible reports.

## Quick start

```cmd
pip install -e .
retrieval-arena run --config configs\experiment.example.yaml
retrieval-arena validate --config configs\experiment.example.yaml
```

The example run writes:

```text
results/toy_faq_reconstruction/summary.csv
results/toy_faq_reconstruction/summary.md
```

See the parent workspace specification at `project_specifications/RetrievalArenaSpec.md` for the v1 contract and validation plan.
