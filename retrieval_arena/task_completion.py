from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .errors import ValidationError


SCHEMA_TASK_PACK = "retrieval_arena.task_pack.v1"
SCHEMA_TASK_DEFINITION = "retrieval_arena.task_definition.v1"
SCHEMA_PROVIDER_REFERENCE = "retrieval_arena.provider_reference.v1"
SCHEMA_TRIAL_IDENTITY = "retrieval_arena.task_trial_identity.v1"
SCHEMA_TASK_EVENT = "retrieval_arena.task_event.v2"
SCHEMA_ORACLE_RESULT = "retrieval_arena.task_oracle_result.v2"
SCHEMA_EFFICIENCY = "retrieval_arena.task_efficiency.v2"
SCHEMA_TASK_SCORE = "retrieval_arena.task_score.v2"
SCHEMA_PAIRED_COMPARISON = "retrieval_arena.task_paired_comparison.v2"
SCHEMA_TASK_REPLAY = "retrieval_arena.task_replay.v2"

RESULT_FAMILY = "deterministic_task_completion"

CAPABILITY_NAMES = {
    "network",
    "filesystem_read",
    "filesystem_write",
    "process",
    "container",
    "secrets",
    "privileges",
}
CAPABILITY_DISPOSITIONS = {"declared", "forbidden"}
PROVIDER_KINDS = {
    "agent_adapter",
    "environment_adapter",
    "event_normalizer",
    "oracle_provider",
}
ASSERTION_CLASSES = {
    "initial_state",
    "semantic_action",
    "temporal_order",
    "terminal_state",
    "causal",
    "prohibition",
    "critical_safety",
    "cleanup",
}
COMPLETION_CLASSES = {
    "semantic_action",
    "temporal_order",
    "terminal_state",
    "causal",
}
EVENT_CLASSES = {
    "tool_invocation",
    "process_lifecycle",
    "service_lifecycle",
    "filesystem_mutation",
    "command_result",
    "http_request",
    "http_response",
    "test_execution",
    "artifact_publication",
    "cleanup",
}
ORACLE_VERDICTS = {"pass", "fail", "unavailable", "invalid"}
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
)
EFFICIENCY_VALUE_FIELDS = (
    "wall_time_ms",
    "active_time_ms",
    "time_to_first_valid_action_ms",
    "time_to_first_successful_verification_ms",
    *TOKEN_FIELDS,
    "context_tokens",
    "context_items",
    "tool_calls",
    "successful_tool_calls",
    "failed_actions",
    "retries",
    "provider_cost",
)
ENTRYPOINT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"value is not canonical JSON: {exc}") from exc


def load_contract_json(text: str) -> dict[str, Any]:
    def reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError(f"duplicate JSON field:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid contract JSON:{exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("contract JSON must contain one object")
    return value


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def self_hash(value: dict[str, Any], field: str) -> str:
    return sha256_json({key: item for key, item in value.items() if key != field})


def _exact(value: Any, fields: set[str], owner: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"{owner} fields must be exactly {sorted(fields)}")
    return value


def _text(value: Any, owner: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{owner} must be a non-empty string")
    return value


def _sha(value: Any, owner: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"{owner} must be a lowercase SHA-256")
    return value


def _optional_sha(value: Any, owner: str) -> str | None:
    if value is None:
        return None
    return _sha(value, owner)


def _integer(value: Any, owner: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{owner} must be an integer >= {minimum}")
    return value


def _optional_integer(value: Any, owner: str, minimum: int = 0) -> int | None:
    if value is None:
        return None
    return _integer(value, owner, minimum)


def _finite_nonnegative(value: Any, owner: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{owner} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValidationError(f"{owner} must be finite and non-negative")
    return result


def _optional_nonnegative(value: Any, owner: str) -> float | None:
    if value is None:
        return None
    return _finite_nonnegative(value, owner)


def _unit(value: Any, owner: str) -> float:
    result = _finite_nonnegative(value, owner)
    if result > 1.0:
        raise ValidationError(f"{owner} must be in [0, 1]")
    return result


def _sorted_unique_texts(value: Any, owner: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError(f"{owner} must be a{' non-empty' if not allow_empty else ''} list")
    for item in value:
        _text(item, owner)
    if value != sorted(set(value)):
        raise ValidationError(f"{owner} must be sorted and unique")
    return value


def _sorted_unique_shas(value: Any, owner: str, *, allow_empty: bool = True) -> list[str]:
    rows = _sorted_unique_texts(value, owner, allow_empty=allow_empty)
    for item in rows:
        _sha(item, owner)
    return rows


def _check_self_hash(value: dict[str, Any], field: str, owner: str) -> None:
    if _sha(value[field], field) != self_hash(value, field):
        raise ValidationError(f"{owner} self-hash mismatch")


def validate_provider_reference(reference: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "provider_id",
        "provider_kind",
        "version",
        "artifact_sha256",
        "entrypoint_id",
        "capabilities",
        "provider_sha256",
    }
    _exact(reference, fields, "provider reference")
    if reference["schema_version"] != SCHEMA_PROVIDER_REFERENCE:
        raise ValidationError("unsupported provider reference schema")
    _text(reference["provider_id"], "provider_id")
    if reference["provider_kind"] not in PROVIDER_KINDS:
        raise ValidationError("unsupported provider_kind")
    _text(reference["version"], "provider version")
    _sha(reference["artifact_sha256"], "artifact_sha256")
    entrypoint = _text(reference["entrypoint_id"], "entrypoint_id")
    if not ENTRYPOINT_PATTERN.fullmatch(entrypoint):
        raise ValidationError("entrypoint_id must be a symbolic identifier, not executable code or a shell fragment")
    capabilities = _sorted_unique_texts(reference["capabilities"], "provider capabilities")
    if not set(capabilities) <= CAPABILITY_NAMES:
        raise ValidationError("provider reference contains an unknown capability")
    _check_self_hash(reference, "provider_sha256", "provider reference")
    return reference


def _validate_assertion(assertion: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "assertion_id",
        "assertion_class",
        "required",
        "critical",
        "predecessor_assertion_ids",
        "provider_id",
        "parameters_sha256",
        "assertion_sha256",
    }
    _exact(assertion, fields, "task assertion")
    assertion_id = _text(assertion["assertion_id"], "assertion_id")
    assertion_class = assertion["assertion_class"]
    if assertion_class not in ASSERTION_CLASSES:
        raise ValidationError(f"unsupported assertion_class:{assertion_class}")
    if not isinstance(assertion["required"], bool) or not isinstance(assertion["critical"], bool):
        raise ValidationError("assertion required and critical must be booleans")
    if assertion["critical"] and assertion_class not in {"prohibition", "critical_safety"}:
        raise ValidationError("only prohibition or critical_safety assertions may be critical")
    if assertion["critical"] and not assertion["required"]:
        raise ValidationError("critical assertions must also be required")
    predecessors = _sorted_unique_texts(
        assertion["predecessor_assertion_ids"],
        f"{assertion_id} predecessor_assertion_ids",
    )
    if assertion_id in predecessors:
        raise ValidationError("assertion cannot depend on itself")
    _text(assertion["provider_id"], "assertion provider_id")
    _sha(assertion["parameters_sha256"], "parameters_sha256")
    _check_self_hash(assertion, "assertion_sha256", "task assertion")
    return assertion


def validate_task_definition(
    definition: dict[str, Any],
    *,
    provider_ids: set[str] | None = None,
    fixture_ids: set[str] | None = None,
    declared_capabilities: set[str] | None = None,
    writable_root_ids: set[str] | None = None,
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "task_id",
        "goal",
        "fixture_id",
        "context_input_sha256s",
        "required_capabilities",
        "writable_root_ids",
        "oracle_references",
        "assertions",
        "missing_evidence_policy",
        "success_policy",
        "cleanup_policy",
        "task_sha256",
    }
    _exact(definition, fields, "task definition")
    if definition["schema_version"] != SCHEMA_TASK_DEFINITION:
        raise ValidationError("unsupported task definition schema")
    _text(definition["task_id"], "task_id")
    _text(definition["goal"], "task goal")
    fixture_id = _text(definition["fixture_id"], "fixture_id")
    if fixture_ids is not None and fixture_id not in fixture_ids:
        raise ValidationError("task definition references an unknown fixture")
    _sorted_unique_shas(definition["context_input_sha256s"], "context_input_sha256s")
    capabilities = _sorted_unique_texts(definition["required_capabilities"], "required_capabilities")
    if not set(capabilities) <= CAPABILITY_NAMES:
        raise ValidationError("task definition contains an unknown capability")
    if declared_capabilities is not None and not set(capabilities) <= declared_capabilities:
        raise ValidationError("task definition requires an undeclared capability")
    roots = _sorted_unique_texts(definition["writable_root_ids"], "writable_root_ids")
    if writable_root_ids is not None and not set(roots) <= writable_root_ids:
        raise ValidationError("task definition references an undeclared writable root")
    oracle_references = _sorted_unique_texts(
        definition["oracle_references"], "oracle_references", allow_empty=False
    )
    if provider_ids is not None and not set(oracle_references) <= provider_ids:
        raise ValidationError("task definition references an unknown oracle provider")

    assertions = definition["assertions"]
    if not isinstance(assertions, list) or not assertions:
        raise ValidationError("assertions must be a non-empty list")
    assertion_by_id: dict[str, dict[str, Any]] = {}
    for assertion in assertions:
        _validate_assertion(assertion)
        assertion_id = assertion["assertion_id"]
        if assertion_id in assertion_by_id:
            raise ValidationError(f"duplicate assertion_id:{assertion_id}")
        if assertion["provider_id"] not in oracle_references:
            raise ValidationError("assertion provider is not declared in oracle_references")
        assertion_by_id[assertion_id] = assertion
    if assertions != sorted(assertions, key=lambda row: row["assertion_id"]):
        raise ValidationError("assertions must be sorted by assertion_id")
    for assertion in assertions:
        for predecessor in assertion["predecessor_assertion_ids"]:
            if predecessor not in assertion_by_id:
                raise ValidationError(f"unknown predecessor assertion:{predecessor}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(assertion_id: str) -> None:
        if assertion_id in visiting:
            raise ValidationError("assertion predecessor graph must be acyclic")
        if assertion_id in visited:
            return
        visiting.add(assertion_id)
        for predecessor in assertion_by_id[assertion_id]["predecessor_assertion_ids"]:
            visit(predecessor)
        visiting.remove(assertion_id)
        visited.add(assertion_id)

    for assertion_id in assertion_by_id:
        visit(assertion_id)

    required_classes = {
        assertion["assertion_class"] for assertion in assertions if assertion["required"]
    }
    if "semantic_action" not in required_classes or "terminal_state" not in required_classes:
        raise ValidationError("task requires at least one mandatory semantic_action and terminal_state assertion")
    if "cleanup" not in required_classes:
        raise ValidationError("task requires at least one mandatory cleanup assertion")

    if definition["missing_evidence_policy"] not in {"failure", "invalid_trial"}:
        raise ValidationError("unsupported missing_evidence_policy")
    success_policy = _exact(
        definition["success_policy"],
        {"required_oracles", "critical_safety_oracles", "cleanup", "composite_metric"},
        "success_policy",
    )
    if success_policy != {
        "required_oracles": "all",
        "critical_safety_oracles": "all",
        "cleanup": "required",
        "composite_metric": None,
    }:
        raise ValidationError("success_policy must preserve lexicographic all-required semantics")
    cleanup_policy = _exact(
        definition["cleanup_policy"],
        {"required", "restoration_required"},
        "cleanup_policy",
    )
    if cleanup_policy["required"] is not True or not isinstance(cleanup_policy["restoration_required"], bool):
        raise ValidationError("cleanup_policy must require cleanup and declare restoration")
    _check_self_hash(definition, "task_sha256", "task definition")
    return definition


def _validate_capability_policy(policy: Any) -> set[str]:
    _exact(policy, CAPABILITY_NAMES, "declared_capabilities")
    declared: set[str] = set()
    for capability, disposition in policy.items():
        if disposition not in CAPABILITY_DISPOSITIONS:
            raise ValidationError(f"unsupported capability disposition:{capability}")
        if disposition == "declared":
            declared.add(capability)
    return declared


def _absolute_portable_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if ".." in normalized.split("/"):
        return False
    return normalized.startswith("/") or bool(re.match(r"^[A-Za-z]:/", normalized))


def _validate_budgets(value: Any) -> dict[str, Any]:
    fields = {
        "wall_time_ms",
        "active_time_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "context_tokens",
        "context_items",
        "tool_calls",
        "retries",
    }
    budgets = _exact(value, fields, "budgets")
    for field in fields:
        _optional_integer(budgets[field], f"budget {field}")
    return budgets


def validate_task_pack(
    pack: dict[str, Any], task_definitions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "pack_id",
        "pack_version",
        "publisher",
        "license",
        "supported_contract_version",
        "task_definition_sha256s",
        "fixture_identities",
        "provider_references",
        "declared_capabilities",
        "writable_roots",
        "cleanup_policy",
        "documentation_binding_policy",
        "budgets",
        "aggregation_policy",
        "replication_policy",
        "pack_sha256",
    }
    _exact(pack, fields, "task pack")
    if pack["schema_version"] != SCHEMA_TASK_PACK:
        raise ValidationError("unsupported task pack schema")
    for field in ("pack_id", "pack_version", "publisher", "license"):
        _text(pack[field], field)
    if pack["supported_contract_version"] != SCHEMA_TASK_DEFINITION:
        raise ValidationError("task pack must bind the task_definition.v1 contract")

    fixtures = pack["fixture_identities"]
    if not isinstance(fixtures, list) or not fixtures:
        raise ValidationError("fixture_identities must be a non-empty list")
    fixture_ids: set[str] = set()
    for fixture in fixtures:
        _exact(fixture, {"fixture_id", "artifact_sha256"}, "fixture identity")
        fixture_id = _text(fixture["fixture_id"], "fixture_id")
        if fixture_id in fixture_ids:
            raise ValidationError(f"duplicate fixture_id:{fixture_id}")
        fixture_ids.add(fixture_id)
        _sha(fixture["artifact_sha256"], "fixture artifact_sha256")
    if fixtures != sorted(fixtures, key=lambda row: row["fixture_id"]):
        raise ValidationError("fixture_identities must be sorted by fixture_id")

    references = pack["provider_references"]
    if not isinstance(references, list) or not references:
        raise ValidationError("provider_references must be a non-empty list")
    provider_ids: set[str] = set()
    provider_kinds: dict[str, int] = {kind: 0 for kind in PROVIDER_KINDS}
    provider_capabilities: set[str] = set()
    for reference in references:
        validate_provider_reference(reference)
        provider_id = reference["provider_id"]
        if provider_id in provider_ids:
            raise ValidationError(f"duplicate provider_id:{provider_id}")
        provider_ids.add(provider_id)
        provider_kinds[reference["provider_kind"]] += 1
        provider_capabilities.update(reference["capabilities"])
    if references != sorted(references, key=lambda row: (row["provider_kind"], row["provider_id"])):
        raise ValidationError("provider_references must be sorted by provider_kind and provider_id")
    for kind in ("agent_adapter", "environment_adapter", "event_normalizer"):
        if provider_kinds[kind] != 1:
            raise ValidationError(f"task pack requires exactly one {kind}")
    if provider_kinds["oracle_provider"] < 1:
        raise ValidationError("task pack requires at least one oracle_provider")

    declared = _validate_capability_policy(pack["declared_capabilities"])
    if not provider_capabilities <= declared:
        raise ValidationError("provider requires a capability not declared by the task pack")

    roots = pack["writable_roots"]
    if not isinstance(roots, list):
        raise ValidationError("writable_roots must be a list")
    root_ids: set[str] = set()
    for root in roots:
        _exact(root, {"root_id", "path", "cleanup_required"}, "writable root")
        root_id = _text(root["root_id"], "root_id")
        path = _text(root["path"], "writable root path")
        if root_id in root_ids:
            raise ValidationError(f"duplicate writable root:{root_id}")
        if not _absolute_portable_path(path):
            raise ValidationError("writable root must be absolute and may not contain parent traversal")
        if root["cleanup_required"] is not True:
            raise ValidationError("every writable root must require cleanup")
        root_ids.add(root_id)
    if roots != sorted(roots, key=lambda row: row["root_id"]):
        raise ValidationError("writable_roots must be sorted by root_id")

    cleanup = _exact(
        pack["cleanup_policy"],
        {"cleanup_required", "missing_cleanup_disposition"},
        "task pack cleanup_policy",
    )
    if cleanup["cleanup_required"] is not True or cleanup["missing_cleanup_disposition"] not in {
        "failure",
        "invalid_trial",
    }:
        raise ValidationError("unsupported task pack cleanup_policy")
    binding = _exact(
        pack["documentation_binding_policy"],
        {"only_variable", "required_pair_labels"},
        "documentation_binding_policy",
    )
    if binding != {"only_variable": "documentation_snapshot", "required_pair_labels": ["before", "after"]}:
        raise ValidationError("documentation binding must freeze every surface except the snapshot")
    _validate_budgets(pack["budgets"])
    aggregation = _exact(
        pack["aggregation_policy"], {"task_weighting", "composite_metric"}, "aggregation_policy"
    )
    if aggregation != {"task_weighting": "macro_equal", "composite_metric": None}:
        raise ValidationError("aggregation_policy must use macro_equal with no composite")
    replication = _exact(
        pack["replication_policy"],
        {"replicate_count", "stochastic_execution", "seed_policy"},
        "replication_policy",
    )
    replicate_count = _integer(replication["replicate_count"], "replicate_count", 1)
    if not isinstance(replication["stochastic_execution"], bool):
        raise ValidationError("stochastic_execution must be boolean")
    if replication["seed_policy"] not in {"required", "optional", "unsupported"}:
        raise ValidationError("unsupported seed_policy")
    if replication["stochastic_execution"] and replicate_count < 2:
        raise ValidationError("stochastic execution requires a frozen replicate_count of at least two")

    task_bindings = pack["task_definition_sha256s"]
    if not isinstance(task_bindings, list) or not task_bindings:
        raise ValidationError("task_definition_sha256s must be a non-empty list")
    bound_task_ids: set[str] = set()
    for binding_row in task_bindings:
        _exact(binding_row, {"task_id", "task_sha256"}, "task definition binding")
        task_id = _text(binding_row["task_id"], "bound task_id")
        if task_id in bound_task_ids:
            raise ValidationError(f"duplicate bound task_id:{task_id}")
        bound_task_ids.add(task_id)
        _sha(binding_row["task_sha256"], "bound task_sha256")
    if task_bindings != sorted(task_bindings, key=lambda row: row["task_id"]):
        raise ValidationError("task_definition_sha256s must be sorted by task_id")
    if task_definitions is not None:
        expected_bindings: list[dict[str, str]] = []
        task_ids: set[str] = set()
        for definition in task_definitions:
            validate_task_definition(
                definition,
                provider_ids={
                    row["provider_id"]
                    for row in references
                    if row["provider_kind"] == "oracle_provider"
                },
                fixture_ids=fixture_ids,
                declared_capabilities=declared,
                writable_root_ids=root_ids,
            )
            task_id = definition["task_id"]
            if task_id in task_ids:
                raise ValidationError(f"duplicate task_id:{task_id}")
            task_ids.add(task_id)
            expected_bindings.append({"task_id": task_id, "task_sha256": definition["task_sha256"]})
        expected_bindings.sort(key=lambda row: row["task_id"])
        if task_bindings != expected_bindings:
            raise ValidationError("task_definition_sha256s do not exactly bind the supplied task definitions")
    _check_self_hash(pack, "pack_sha256", "task pack")
    return pack


def _validate_bound_task(pack: dict[str, Any], task: dict[str, Any]) -> None:
    declared = {
        name
        for name, disposition in pack["declared_capabilities"].items()
        if disposition == "declared"
    }
    validate_task_definition(
        task,
        provider_ids={
            row["provider_id"]
            for row in pack["provider_references"]
            if row["provider_kind"] == "oracle_provider"
        },
        fixture_ids={row["fixture_id"] for row in pack["fixture_identities"]},
        declared_capabilities=declared,
        writable_root_ids={row["root_id"] for row in pack["writable_roots"]},
    )
    binding = {row["task_id"]: row["task_sha256"] for row in pack["task_definition_sha256s"]}
    if binding.get(task["task_id"]) != task["task_sha256"]:
        raise ValidationError("selected task definition is not bound by the task pack")


def _provider_bindings(pack: dict[str, Any]) -> dict[str, Any]:
    references = pack["provider_references"]
    by_kind: dict[str, list[str]] = {kind: [] for kind in PROVIDER_KINDS}
    for reference in references:
        by_kind[reference["provider_kind"]].append(reference["provider_sha256"])
    return {
        "agent_adapter": by_kind["agent_adapter"][0],
        "environment_adapter": by_kind["environment_adapter"][0],
        "event_normalizer": by_kind["event_normalizer"][0],
        "oracle_providers": sorted(by_kind["oracle_provider"]),
    }


def validate_trial_identity(
    trial: dict[str, Any], pack: dict[str, Any], task: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "trial_id",
        "pack_id",
        "pack_sha256",
        "task_id",
        "task_sha256",
        "fixture_id",
        "fixture_sha256",
        "documentation_snapshot_id",
        "documentation_snapshot_sha256",
        "provider_bindings",
        "model_identity_sha256",
        "runtime_identity_sha256",
        "prompt_sha256",
        "decoding_policy_sha256",
        "tool_policy_sha256",
        "context_policy_sha256",
        "budget_policy_sha256",
        "scoring_policy_sha256",
        "replicate_index",
        "seed",
        "trial_sha256",
    }
    _exact(trial, fields, "task trial identity")
    if trial["schema_version"] != SCHEMA_TRIAL_IDENTITY:
        raise ValidationError("unsupported task trial identity schema")
    _text(trial["trial_id"], "trial_id")
    if trial["pack_id"] != pack["pack_id"] or trial["pack_sha256"] != pack["pack_sha256"]:
        raise ValidationError("trial does not bind the admitted task pack")
    if trial["task_id"] != task["task_id"] or trial["task_sha256"] != task["task_sha256"]:
        raise ValidationError("trial does not bind the admitted task definition")
    if trial["fixture_id"] != task["fixture_id"]:
        raise ValidationError("trial fixture_id does not match the task definition")
    fixture_by_id = {row["fixture_id"]: row["artifact_sha256"] for row in pack["fixture_identities"]}
    if trial["fixture_sha256"] != fixture_by_id.get(task["fixture_id"]):
        raise ValidationError("trial fixture_sha256 does not match the task pack")
    _text(trial["documentation_snapshot_id"], "documentation_snapshot_id")
    _sha(trial["documentation_snapshot_sha256"], "documentation_snapshot_sha256")
    bindings = _exact(
        trial["provider_bindings"],
        {"agent_adapter", "environment_adapter", "event_normalizer", "oracle_providers"},
        "trial provider_bindings",
    )
    for field in ("agent_adapter", "environment_adapter", "event_normalizer"):
        _sha(bindings[field], f"provider_bindings {field}")
    _sorted_unique_shas(bindings["oracle_providers"], "provider_bindings oracle_providers", allow_empty=False)
    if bindings != _provider_bindings(pack):
        raise ValidationError("trial provider_bindings do not match the task pack")
    _optional_sha(trial["model_identity_sha256"], "model_identity_sha256")
    for field in (
        "runtime_identity_sha256",
        "prompt_sha256",
        "decoding_policy_sha256",
        "tool_policy_sha256",
        "context_policy_sha256",
        "budget_policy_sha256",
        "scoring_policy_sha256",
    ):
        _sha(trial[field], field)
    replicate_index = _integer(trial["replicate_index"], "replicate_index")
    if replicate_index >= pack["replication_policy"]["replicate_count"]:
        raise ValidationError("replicate_index exceeds the frozen replication policy")
    seed = trial["seed"]
    if seed is not None:
        _integer(seed, "seed")
    seed_policy = pack["replication_policy"]["seed_policy"]
    if seed_policy == "required" and seed is None:
        raise ValidationError("trial seed is required")
    if seed_policy == "unsupported" and seed is not None:
        raise ValidationError("trial seed must be unavailable for an unsupported provider")
    _check_self_hash(trial, "trial_sha256", "task trial identity")
    return trial


def validate_task_events(
    events: list[dict[str, Any]], trial: dict[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        raise ValidationError("task events must be a list")
    fields = {
        "schema_version",
        "trial_id",
        "trial_sha256",
        "task_id",
        "attempt",
        "sequence",
        "observed_at_ns",
        "event_class",
        "target",
        "normalized_arguments_sha256",
        "adapter_id",
        "adapter_sha256",
        "input_artifact_sha256s",
        "output_artifact_sha256s",
        "exit_status",
        "observation_sha256",
        "parent_action_sequence",
        "causal_correlation_id",
        "redaction",
        "sensitivity",
        "native_receipt_sha256",
        "event_sha256",
    }
    expected_sequence = 1
    prior_time = -1
    seen_hashes: set[str] = set()
    admitted_adapter_hashes = {
        trial["provider_bindings"]["agent_adapter"],
        trial["provider_bindings"]["environment_adapter"],
        trial["provider_bindings"]["event_normalizer"],
        *trial["provider_bindings"]["oracle_providers"],
    }
    for event in events:
        _exact(event, fields, "task event")
        if event["schema_version"] != SCHEMA_TASK_EVENT:
            raise ValidationError("unsupported task event schema")
        _text(event["trial_id"], "task event trial_id")
        _sha(event["trial_sha256"], "task event trial_sha256")
        if (
            event["trial_id"] != trial["trial_id"]
            or event["trial_sha256"] != trial["trial_sha256"]
            or event["task_id"] != trial["task_id"]
        ):
            raise ValidationError("task event trial or task identity mismatch")
        _integer(event["attempt"], "event attempt", 1)
        if event["sequence"] != expected_sequence:
            raise ValidationError("task event sequence must be contiguous and start at one")
        expected_sequence += 1
        observed_at = _integer(event["observed_at_ns"], "observed_at_ns")
        if observed_at < prior_time:
            raise ValidationError("task event observed_at_ns must be monotonic")
        prior_time = observed_at
        if event["event_class"] not in EVENT_CLASSES:
            raise ValidationError("unsupported task event class")
        _text(event["target"], "task event target")
        _sha(event["normalized_arguments_sha256"], "normalized_arguments_sha256")
        _text(event["adapter_id"], "task event adapter_id")
        _sha(event["adapter_sha256"], "task event adapter_sha256")
        if event["adapter_sha256"] not in admitted_adapter_hashes:
            raise ValidationError("task event adapter_sha256 is not admitted by the trial")
        _sorted_unique_shas(event["input_artifact_sha256s"], "input_artifact_sha256s")
        _sorted_unique_shas(event["output_artifact_sha256s"], "output_artifact_sha256s")
        if event["exit_status"] is not None:
            if isinstance(event["exit_status"], bool) or not isinstance(event["exit_status"], int):
                raise ValidationError("exit_status must be an integer or null")
        _optional_sha(event["observation_sha256"], "observation_sha256")
        parent = event["parent_action_sequence"]
        if parent is not None and (_integer(parent, "parent_action_sequence", 1) >= event["sequence"]):
            raise ValidationError("parent_action_sequence must identify an earlier event")
        if event["causal_correlation_id"] is not None:
            _text(event["causal_correlation_id"], "causal_correlation_id")
        if event["redaction"] not in {"none", "redacted"}:
            raise ValidationError("unsupported event redaction")
        if event["sensitivity"] not in {"public", "internal", "secret"}:
            raise ValidationError("unsupported event sensitivity")
        _sha(event["native_receipt_sha256"], "native_receipt_sha256")
        _check_self_hash(event, "event_sha256", "task event")
        if event["event_sha256"] in seen_hashes:
            raise ValidationError("duplicate task event hash")
        seen_hashes.add(event["event_sha256"])
    return events


def validate_oracle_results(
    results: list[dict[str, Any]],
    task: dict[str, Any],
    pack: dict[str, Any],
    trial: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(results, list) or not results:
        raise ValidationError("oracle results must be a non-empty list")
    fields = {
        "schema_version",
        "trial_id",
        "trial_sha256",
        "task_id",
        "assertion_id",
        "oracle_class",
        "required",
        "critical",
        "provider_id",
        "provider_sha256",
        "parameters_sha256",
        "input_sha256s",
        "expected_predicate_sha256",
        "observed_value_sha256",
        "verdict",
        "missing_evidence",
        "violation_id",
        "result_sha256",
    }
    assertion_by_id = {row["assertion_id"]: row for row in task["assertions"]}
    provider_by_id = {row["provider_id"]: row for row in pack["provider_references"]}
    seen: set[str] = set()
    for result in results:
        _exact(result, fields, "oracle result")
        if result["schema_version"] != SCHEMA_ORACLE_RESULT:
            raise ValidationError("unsupported oracle result schema")
        _text(result["trial_id"], "oracle trial_id")
        if trial is not None and result["trial_id"] != trial["trial_id"]:
            raise ValidationError("oracle result trial_id mismatch")
        _sha(result["trial_sha256"], "oracle trial_sha256")
        if trial is not None and result["trial_sha256"] != trial["trial_sha256"]:
            raise ValidationError("oracle result trial_sha256 mismatch")
        if result["task_id"] != task["task_id"]:
            raise ValidationError("oracle result task_id mismatch")
        assertion_id = _text(result["assertion_id"], "oracle assertion_id")
        if assertion_id in seen or assertion_id not in assertion_by_id:
            raise ValidationError(f"unknown or duplicate oracle assertion_id:{assertion_id}")
        seen.add(assertion_id)
        assertion = assertion_by_id[assertion_id]
        for result_field, assertion_field in (
            ("oracle_class", "assertion_class"),
            ("required", "required"),
            ("critical", "critical"),
            ("provider_id", "provider_id"),
            ("parameters_sha256", "parameters_sha256"),
        ):
            if result[result_field] != assertion[assertion_field]:
                raise ValidationError(f"oracle result does not match assertion:{result_field}")
        provider = provider_by_id.get(result["provider_id"])
        if provider is None or provider["provider_kind"] != "oracle_provider":
            raise ValidationError("oracle result provider is not an admitted oracle_provider")
        if result["provider_sha256"] != provider["provider_sha256"]:
            raise ValidationError("oracle result provider_sha256 mismatch")
        _sorted_unique_shas(result["input_sha256s"], "oracle input_sha256s", allow_empty=False)
        _sha(result["expected_predicate_sha256"], "expected_predicate_sha256")
        _optional_sha(result["observed_value_sha256"], "observed_value_sha256")
        if result["verdict"] not in ORACLE_VERDICTS:
            raise ValidationError("unsupported oracle verdict")
        if not isinstance(result["missing_evidence"], bool):
            raise ValidationError("missing_evidence must be boolean")
        if result["missing_evidence"] and result["verdict"] not in {"unavailable", "invalid"}:
            raise ValidationError("missing evidence must be unavailable or invalid")
        if not result["missing_evidence"] and result["verdict"] == "unavailable":
            raise ValidationError("unavailable oracle result must declare missing evidence")
        if result["violation_id"] is not None:
            _text(result["violation_id"], "violation_id")
            if result["verdict"] != "fail" or result["oracle_class"] not in {
                "prohibition",
                "critical_safety",
            }:
                raise ValidationError("violation_id is valid only for failed safety or prohibition results")
        _check_self_hash(result, "result_sha256", "oracle result")
    if seen != set(assertion_by_id):
        raise ValidationError("oracle results must contain exactly one result for every task assertion")
    if results != sorted(results, key=lambda row: row["assertion_id"]):
        raise ValidationError("oracle results must be sorted by assertion_id")
    return results


def validate_efficiency(
    efficiency: dict[str, Any], trial: dict[str, Any] | None = None
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "trial_id",
        "trial_sha256",
        "wall_time_ms",
        "active_time_ms",
        "time_to_first_valid_action_ms",
        "time_to_first_successful_verification_ms",
        *TOKEN_FIELDS,
        "context_tokens",
        "context_items",
        "tool_calls",
        "successful_tool_calls",
        "failed_actions",
        "retries",
        "provider_cost",
        "provider_cost_currency",
        "token_accounting",
        "tokenizer_policy_sha256",
        "measured_resources",
        "source_receipt_sha256s",
        "efficiency_sha256",
    }
    _exact(efficiency, fields, "task efficiency")
    if efficiency["schema_version"] != SCHEMA_EFFICIENCY:
        raise ValidationError("unsupported task efficiency schema")
    if trial is not None and efficiency["trial_id"] != trial["trial_id"]:
        raise ValidationError("task efficiency trial_id mismatch")
    _text(efficiency["trial_id"], "task efficiency trial_id")
    _sha(efficiency["trial_sha256"], "task efficiency trial_sha256")
    if trial is not None and efficiency["trial_sha256"] != trial["trial_sha256"]:
        raise ValidationError("task efficiency trial_sha256 mismatch")
    for field in (
        "wall_time_ms",
        "active_time_ms",
        "time_to_first_valid_action_ms",
        "time_to_first_successful_verification_ms",
        *TOKEN_FIELDS,
        "context_tokens",
        "context_items",
        "tool_calls",
        "successful_tool_calls",
        "failed_actions",
        "retries",
    ):
        _optional_integer(efficiency[field], field)
    wall = efficiency["wall_time_ms"]
    for field in (
        "active_time_ms",
        "time_to_first_valid_action_ms",
        "time_to_first_successful_verification_ms",
    ):
        if wall is not None and efficiency[field] is not None and efficiency[field] > wall:
            raise ValidationError(f"{field} cannot exceed wall_time_ms")
    if (
        efficiency["tool_calls"] is not None
        and efficiency["successful_tool_calls"] is not None
        and efficiency["successful_tool_calls"] > efficiency["tool_calls"]
    ):
        raise ValidationError("successful_tool_calls cannot exceed tool_calls")
    cost = _optional_nonnegative(efficiency["provider_cost"], "provider_cost")
    currency = efficiency["provider_cost_currency"]
    if (cost is None) != (currency is None):
        raise ValidationError("provider_cost and provider_cost_currency must be jointly available")
    if currency is not None:
        _text(currency, "provider_cost_currency")
    token_accounting = _exact(efficiency["token_accounting"], set(TOKEN_FIELDS), "token_accounting")
    uses_tokenizer = False
    for field in TOKEN_FIELDS:
        source = token_accounting[field]
        if source not in {"provider_reported", "registered_tokenizer", "unavailable"}:
            raise ValidationError(f"unsupported token accounting source:{field}")
        if source == "unavailable" and efficiency[field] is not None:
            raise ValidationError(f"{field} must be null when unavailable")
        if source != "unavailable" and efficiency[field] is None:
            raise ValidationError(f"{field} requires a value when token accounting is available")
        uses_tokenizer = uses_tokenizer or source == "registered_tokenizer"
    tokenizer_policy = _optional_sha(efficiency["tokenizer_policy_sha256"], "tokenizer_policy_sha256")
    if uses_tokenizer != (tokenizer_policy is not None):
        raise ValidationError("registered tokenizer accounting requires exactly one tokenizer policy identity")
    resources = efficiency["measured_resources"]
    if not isinstance(resources, list):
        raise ValidationError("measured_resources must be a list")
    seen_resources: set[str] = set()
    for resource in resources:
        _exact(resource, {"resource_id", "unit", "value", "source_sha256"}, "measured resource")
        resource_id = _text(resource["resource_id"], "resource_id")
        if resource_id in seen_resources:
            raise ValidationError(f"duplicate measured resource:{resource_id}")
        seen_resources.add(resource_id)
        _text(resource["unit"], "measured resource unit")
        _finite_nonnegative(resource["value"], "measured resource value")
        _sha(resource["source_sha256"], "measured resource source_sha256")
    if resources != sorted(resources, key=lambda row: row["resource_id"]):
        raise ValidationError("measured_resources must be sorted by resource_id")
    _sorted_unique_shas(efficiency["source_receipt_sha256s"], "source_receipt_sha256s", allow_empty=False)
    _check_self_hash(efficiency, "efficiency_sha256", "task efficiency")
    return efficiency


def _fraction(results: list[dict[str, Any]]) -> float:
    if not results:
        return 1.0
    return sum(result["verdict"] == "pass" for result in results) / len(results)


def _required_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    required = [result for result in results if result["required"]]
    return {
        "total": len(required),
        "passed": sum(result["verdict"] == "pass" for result in required),
        "failed": sum(result["verdict"] == "fail" for result in required),
        "unavailable": sum(result["verdict"] == "unavailable" for result in required),
        "invalid": sum(result["verdict"] == "invalid" for result in required),
    }


def score_task(
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    oracle_results: list[dict[str, Any]],
    efficiency: dict[str, Any],
) -> dict[str, Any]:
    validate_task_pack(pack)
    _validate_bound_task(pack, task)
    validate_trial_identity(trial, pack, task)
    validate_oracle_results(oracle_results, task, pack, trial)
    validate_efficiency(efficiency, trial)
    required = [result for result in oracle_results if result["required"]]
    completion = [result for result in required if result["oracle_class"] in COMPLETION_CLASSES]
    verified_actions = [result for result in required if result["oracle_class"] == "semantic_action"]
    terminal_states = [result for result in required if result["oracle_class"] == "terminal_state"]
    cleanup = [result for result in required if result["oracle_class"] == "cleanup"]
    critical = [result for result in oracle_results if result["critical"]]

    invalid_reasons: list[str] = []
    for result in oracle_results:
        if result["verdict"] == "invalid":
            invalid_reasons.append(f"oracle_invalid:{result['assertion_id']}")
        if result["oracle_class"] == "initial_state" and result["verdict"] != "pass":
            invalid_reasons.append(f"initial_state_not_admitted:{result['assertion_id']}")
        if (
            result["required"]
            and result["missing_evidence"]
            and task["missing_evidence_policy"] == "invalid_trial"
        ):
            invalid_reasons.append(f"required_evidence_missing:{result['assertion_id']}")
    invalid_reasons = sorted(set(invalid_reasons))
    invalid_trial = bool(invalid_reasons)

    critical_safety_violation = any(result["verdict"] == "fail" for result in critical)
    violations = sorted(
        {
            result["violation_id"] or result["assertion_id"]
            for result in oracle_results
            if result["verdict"] == "fail"
            and result["oracle_class"] in {"prohibition", "critical_safety"}
        }
    )
    noncritical_violation_count = sum(
        result["verdict"] == "fail"
        and result["oracle_class"] in {"prohibition", "critical_safety"}
        and not result["critical"]
        for result in oracle_results
    )
    cleanup_passed = bool(cleanup) and all(result["verdict"] == "pass" for result in cleanup)
    task_success = (
        not invalid_trial
        and all(result["verdict"] == "pass" for result in required)
        and all(result["verdict"] == "pass" for result in critical)
        and cleanup_passed
    )

    score = {
        "schema_version": SCHEMA_TASK_SCORE,
        "result_family": RESULT_FAMILY,
        "trial_sha256": trial["trial_sha256"],
        "task_sha256": task["task_sha256"],
        "scoring_policy_sha256": trial["scoring_policy_sha256"],
        "oracle_result_sha256s": [result["result_sha256"] for result in oracle_results],
        "efficiency": efficiency,
        "task_success": task_success,
        "requirement_completion": _fraction(completion),
        "verified_action_completion": _fraction(verified_actions),
        "terminal_state_completion": _fraction(terminal_states),
        "critical_safety_violation": critical_safety_violation,
        "noncritical_violation_count": noncritical_violation_count,
        "violation_inventory": violations,
        "invalid_trial": invalid_trial,
        "invalid_reason_codes": invalid_reasons,
        "cleanup_passed": cleanup_passed,
        "required_oracle_counts": _required_counts(oracle_results),
        "score_sha256": "0" * 64,
    }
    score["score_sha256"] = self_hash(score, "score_sha256")
    return validate_task_score(score)


def validate_task_score(score: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "result_family",
        "trial_sha256",
        "task_sha256",
        "scoring_policy_sha256",
        "oracle_result_sha256s",
        "efficiency",
        "task_success",
        "requirement_completion",
        "verified_action_completion",
        "terminal_state_completion",
        "critical_safety_violation",
        "noncritical_violation_count",
        "violation_inventory",
        "invalid_trial",
        "invalid_reason_codes",
        "cleanup_passed",
        "required_oracle_counts",
        "score_sha256",
    }
    _exact(score, fields, "task score")
    if score["schema_version"] != SCHEMA_TASK_SCORE or score["result_family"] != RESULT_FAMILY:
        raise ValidationError("unsupported task score family")
    for field in ("trial_sha256", "task_sha256", "scoring_policy_sha256"):
        _sha(score[field], field)
    oracle_hashes = score["oracle_result_sha256s"]
    if not isinstance(oracle_hashes, list) or not oracle_hashes:
        raise ValidationError("oracle_result_sha256s must be a non-empty list")
    for value in oracle_hashes:
        _sha(value, "oracle_result_sha256s")
    if len(oracle_hashes) != len(set(oracle_hashes)):
        raise ValidationError("oracle_result_sha256s must not contain duplicates")
    validate_efficiency(score["efficiency"])
    for field in ("requirement_completion", "verified_action_completion", "terminal_state_completion"):
        _unit(score[field], field)
    for field in ("task_success", "critical_safety_violation", "invalid_trial", "cleanup_passed"):
        if not isinstance(score[field], bool):
            raise ValidationError(f"{field} must be boolean")
    _integer(score["noncritical_violation_count"], "noncritical_violation_count")
    _sorted_unique_texts(score["violation_inventory"], "violation_inventory")
    _sorted_unique_texts(score["invalid_reason_codes"], "invalid_reason_codes")
    counts = _exact(
        score["required_oracle_counts"],
        {"total", "passed", "failed", "unavailable", "invalid"},
        "required_oracle_counts",
    )
    for field in counts:
        _integer(counts[field], f"required_oracle_counts {field}")
    if counts["total"] != counts["passed"] + counts["failed"] + counts["unavailable"] + counts["invalid"]:
        raise ValidationError("required_oracle_counts do not sum to total")
    expected_success = (
        not score["invalid_trial"]
        and not score["critical_safety_violation"]
        and score["cleanup_passed"]
        and counts["passed"] == counts["total"]
    )
    if score["task_success"] != expected_success:
        raise ValidationError("task_success violates correctness or safety precedence")
    _check_self_hash(score, "score_sha256", "task score")
    return score


PAIR_INVARIANT_FIELDS = (
    "pack_id",
    "pack_sha256",
    "task_id",
    "task_sha256",
    "fixture_id",
    "fixture_sha256",
    "provider_bindings",
    "model_identity_sha256",
    "runtime_identity_sha256",
    "prompt_sha256",
    "decoding_policy_sha256",
    "tool_policy_sha256",
    "context_policy_sha256",
    "budget_policy_sha256",
    "scoring_policy_sha256",
    "replicate_index",
    "seed",
)


def _validate_trial_record(trial: dict[str, Any]) -> None:
    fields = {
        "schema_version",
        "trial_id",
        "pack_id",
        "pack_sha256",
        "task_id",
        "task_sha256",
        "fixture_id",
        "fixture_sha256",
        "documentation_snapshot_id",
        "documentation_snapshot_sha256",
        "provider_bindings",
        "model_identity_sha256",
        "runtime_identity_sha256",
        "prompt_sha256",
        "decoding_policy_sha256",
        "tool_policy_sha256",
        "context_policy_sha256",
        "budget_policy_sha256",
        "scoring_policy_sha256",
        "replicate_index",
        "seed",
        "trial_sha256",
    }
    _exact(trial, fields, "task trial identity")
    if trial["schema_version"] != SCHEMA_TRIAL_IDENTITY:
        raise ValidationError("unsupported task trial identity schema")
    _check_self_hash(trial, "trial_sha256", "task trial identity")


def compare_task_scores(
    pair_id: str,
    before_trial: dict[str, Any],
    before_score: dict[str, Any],
    after_trial: dict[str, Any],
    after_score: dict[str, Any],
) -> dict[str, Any]:
    _text(pair_id, "pair_id")
    _validate_trial_record(before_trial)
    _validate_trial_record(after_trial)
    validate_task_score(before_score)
    validate_task_score(after_score)
    if before_score["trial_sha256"] != before_trial["trial_sha256"]:
        raise ValidationError("before score does not bind before trial")
    if after_score["trial_sha256"] != after_trial["trial_sha256"]:
        raise ValidationError("after score does not bind after trial")
    for label, trial, score in (
        ("before", before_trial, before_score),
        ("after", after_trial, after_score),
    ):
        if score["task_sha256"] != trial["task_sha256"]:
            raise ValidationError(f"{label} score does not bind its task")
        if score["scoring_policy_sha256"] != trial["scoring_policy_sha256"]:
            raise ValidationError(f"{label} score does not bind its scoring policy")
        if (
            score["efficiency"]["trial_id"] != trial["trial_id"]
            or score["efficiency"]["trial_sha256"] != trial["trial_sha256"]
        ):
            raise ValidationError(f"{label} score efficiency does not bind its trial")
    before_invariant = {field: before_trial[field] for field in PAIR_INVARIANT_FIELDS}
    after_invariant = {field: after_trial[field] for field in PAIR_INVARIANT_FIELDS}
    if before_invariant != after_invariant:
        raise ValidationError("paired trials differ outside the documentation snapshot")
    if (
        before_trial["documentation_snapshot_id"] == after_trial["documentation_snapshot_id"]
        or before_trial["documentation_snapshot_sha256"] == after_trial["documentation_snapshot_sha256"]
    ):
        raise ValidationError("paired trials must bind distinct documentation snapshots")
    pair_valid = not before_score["invalid_trial"] and not after_score["invalid_trial"]
    classification: str | None = None
    if pair_valid:
        classification = {
            (False, True): "improvement",
            (True, False): "regression",
            (True, True): "preserved_success",
            (False, False): "preserved_failure",
        }[(before_score["task_success"], after_score["task_success"])]
    efficiency_comparable = (
        classification == "preserved_success"
        and not before_score["critical_safety_violation"]
        and not after_score["critical_safety_violation"]
    )
    deltas: dict[str, float | int | None] = {}
    for field in EFFICIENCY_VALUE_FIELDS:
        before_value = before_score["efficiency"][field]
        after_value = after_score["efficiency"][field]
        deltas[field] = (
            after_value - before_value
            if efficiency_comparable and before_value is not None and after_value is not None
            else None
        )
    comparison = {
        "schema_version": SCHEMA_PAIRED_COMPARISON,
        "result_family": RESULT_FAMILY,
        "pair_id": pair_id,
        "task_id": before_trial["task_id"],
        "replicate_index": before_trial["replicate_index"],
        "pair_invariant_sha256": sha256_json(before_invariant),
        "before_snapshot_id": before_trial["documentation_snapshot_id"],
        "before_snapshot_sha256": before_trial["documentation_snapshot_sha256"],
        "after_snapshot_id": after_trial["documentation_snapshot_id"],
        "after_snapshot_sha256": after_trial["documentation_snapshot_sha256"],
        "before_score_sha256": before_score["score_sha256"],
        "after_score_sha256": after_score["score_sha256"],
        "pair_valid": pair_valid,
        "classification": classification,
        "efficiency_comparable": efficiency_comparable,
        "efficiency_deltas": deltas,
        "comparison_sha256": "0" * 64,
    }
    comparison["comparison_sha256"] = self_hash(comparison, "comparison_sha256")
    return validate_paired_comparison(comparison)


def validate_paired_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "result_family",
        "pair_id",
        "task_id",
        "replicate_index",
        "pair_invariant_sha256",
        "before_snapshot_id",
        "before_snapshot_sha256",
        "after_snapshot_id",
        "after_snapshot_sha256",
        "before_score_sha256",
        "after_score_sha256",
        "pair_valid",
        "classification",
        "efficiency_comparable",
        "efficiency_deltas",
        "comparison_sha256",
    }
    _exact(comparison, fields, "paired task comparison")
    if comparison["schema_version"] != SCHEMA_PAIRED_COMPARISON or comparison["result_family"] != RESULT_FAMILY:
        raise ValidationError("unsupported paired task comparison family")
    for field in ("pair_id", "task_id", "before_snapshot_id", "after_snapshot_id"):
        _text(comparison[field], field)
    _integer(comparison["replicate_index"], "replicate_index")
    for field in (
        "pair_invariant_sha256",
        "before_snapshot_sha256",
        "after_snapshot_sha256",
        "before_score_sha256",
        "after_score_sha256",
    ):
        _sha(comparison[field], field)
    if not isinstance(comparison["pair_valid"], bool) or not isinstance(comparison["efficiency_comparable"], bool):
        raise ValidationError("pair_valid and efficiency_comparable must be booleans")
    if comparison["pair_valid"]:
        if comparison["classification"] not in {
            "improvement",
            "regression",
            "preserved_success",
            "preserved_failure",
        }:
            raise ValidationError("valid pair requires one registered classification")
    elif comparison["classification"] is not None:
        raise ValidationError("invalid pair cannot receive an outcome classification")
    if comparison["efficiency_comparable"] and comparison["classification"] != "preserved_success":
        raise ValidationError("efficiency is comparable only for preserved safe success")
    deltas = _exact(comparison["efficiency_deltas"], set(EFFICIENCY_VALUE_FIELDS), "efficiency_deltas")
    if not comparison["efficiency_comparable"] and any(value is not None for value in deltas.values()):
        raise ValidationError("non-comparable pair cannot report efficiency deltas")
    for field, value in deltas.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValidationError(f"efficiency delta must be finite or null:{field}")
    _check_self_hash(comparison, "comparison_sha256", "paired task comparison")
    return comparison


def build_task_replay(
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    events: list[dict[str, Any]],
    oracle_results: list[dict[str, Any]],
    score: dict[str, Any],
) -> dict[str, Any]:
    validate_task_pack(pack)
    _validate_bound_task(pack, task)
    validate_trial_identity(trial, pack, task)
    validate_task_events(events, trial)
    validate_oracle_results(oracle_results, task, pack, trial)
    validate_task_score(score)
    if score["trial_sha256"] != trial["trial_sha256"] or score["task_sha256"] != task["task_sha256"]:
        raise ValidationError("task score identity mismatch during replay construction")
    if score["scoring_policy_sha256"] != trial["scoring_policy_sha256"]:
        raise ValidationError("task score policy mismatch during replay construction")
    if (
        score["efficiency"]["trial_id"] != trial["trial_id"]
        or score["efficiency"]["trial_sha256"] != trial["trial_sha256"]
    ):
        raise ValidationError("task score efficiency mismatch during replay construction")
    if score["oracle_result_sha256s"] != [result["result_sha256"] for result in oracle_results]:
        raise ValidationError("task score oracle inventory mismatch during replay construction")
    recomputed_score = score_task(
        pack,
        task,
        trial,
        oracle_results,
        score["efficiency"],
    )
    if canonical_json(score) != canonical_json(recomputed_score):
        raise ValidationError("task score differs from deterministic evidence recomputation")
    replay = {
        "schema_version": SCHEMA_TASK_REPLAY,
        "result_family": RESULT_FAMILY,
        "pack_sha256": pack["pack_sha256"],
        "task_sha256": task["task_sha256"],
        "trial_sha256": trial["trial_sha256"],
        "ordered_event_sha256s": [event["event_sha256"] for event in events],
        "ordered_oracle_result_sha256s": [result["result_sha256"] for result in oracle_results],
        "efficiency_sha256": score["efficiency"]["efficiency_sha256"],
        "task_score_sha256": score["score_sha256"],
        "replay_sha256": "0" * 64,
    }
    replay["replay_sha256"] = self_hash(replay, "replay_sha256")
    return validate_task_replay(replay)


def validate_task_replay(replay: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "result_family",
        "pack_sha256",
        "task_sha256",
        "trial_sha256",
        "ordered_event_sha256s",
        "ordered_oracle_result_sha256s",
        "efficiency_sha256",
        "task_score_sha256",
        "replay_sha256",
    }
    _exact(replay, fields, "task replay")
    if replay["schema_version"] != SCHEMA_TASK_REPLAY or replay["result_family"] != RESULT_FAMILY:
        raise ValidationError("unsupported task replay family")
    for field in (
        "pack_sha256",
        "task_sha256",
        "trial_sha256",
        "efficiency_sha256",
        "task_score_sha256",
    ):
        _sha(replay[field], field)
    for field in ("ordered_event_sha256s", "ordered_oracle_result_sha256s"):
        values = replay[field]
        if not isinstance(values, list):
            raise ValidationError(f"{field} must be a list")
        for value in values:
            _sha(value, field)
        if len(values) != len(set(values)):
            raise ValidationError(f"{field} must not contain duplicates")
    _check_self_hash(replay, "replay_sha256", "task replay")
    return replay
