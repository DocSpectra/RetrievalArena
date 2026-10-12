# Retrieval Arena

Retrieval Arena is a containerized evaluator for documentation retrieval:
retrieval providers are declarative containers under `tests/`, experiments
are YAML under `configs/`, and an immutable verification image exposes a
small operator command set; results are scored with ranked-retrieval
metrics v2 (`ir-measures`) and, optionally, offline BERTScore over answers.
The working branch is `capstone/retrieval-metrics-v2`. Its caller is
DocSpectra Capstone, which evaluates documentation refinements with
before/after Retrieval Arena runs.

## Functionality

- Retrieval evaluation — the provider contract, the scoring chain and the
  table of every evaluation: [docs/retrieval_evaluation.md](docs/retrieval_evaluation.md)
- Corpus import and dataset preparation — the import chain, the dataset
  layout, document-id and exclude policies: [docs/corpus_import.md](docs/corpus_import.md)
- The operator and the verification image — commands, receipts, isolation,
  admission at `candidate-retrieve`: [docs/operator.md](docs/operator.md)
- Experiments — an experiment is a YAML under [configs/](configs/); see
  [configs/experiment.example.yaml](configs/experiment.example.yaml)
- Historical (BigData 2026 pilots, calibration bundles, HTML report,
  evidence export, task completion): [docs/bigdata_pilots.md](docs/bigdata_pilots.md),
  [docs/task_completion_evaluation_plan.md](docs/task_completion_evaluation_plan.md)

## Development

- Extension entry point: [experiments, evaluations and dataset workflows](docs/extending.md).

- Install: `pip install -e .`
- Unit tests: `python -m unittest discover -s unit_tests`
- Agent policy and the provider reference: [AGENTS.md](AGENTS.md)

## License

MIT — see [LICENSE](LICENSE).

## Citation

See [CITATION.cff](CITATION.cff).
