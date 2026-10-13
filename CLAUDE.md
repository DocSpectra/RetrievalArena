@AGENTS.md
@../../AGENTS.md

- Extend RA declaratively: a provider is a `tests/<name>/` directory plus a config entry; an experiment is a `configs/*.yaml`.
- The operator (`scripts/run-verification.ps1`) needs PowerShell 7 and named parameters; a repository commit changes HEAD and requires an image rebuild before the operator accepts the receipt again.
