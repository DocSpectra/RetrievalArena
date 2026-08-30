"""Portable task execution receipts, synthetic providers, reporting, and replay.

Providers are subordinate callables.  They return plain observations; this
module alone creates authoritative receipts, normalized events, oracle records,
scores, replay manifests, and published artifacts.  No dynamic provider loading,
subprocess, network client, or service manager is present here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ValidationError
from .task_completion import (
    CAPABILITY_NAMES,
    RESULT_FAMILY,
    SCHEMA_ORACLE_RESULT,
    SCHEMA_PROVIDER_REFERENCE,
    SCHEMA_TASK_DEFINITION,
    SCHEMA_TASK_EVENT,
    SCHEMA_TASK_PACK,
    SCHEMA_TRIAL_IDENTITY,
    build_task_replay,
    canonical_json,
    compare_task_scores,
    score_task,
    self_hash,
    validate_efficiency,
    validate_oracle_results,
    validate_provider_reference,
    validate_task_events,
    validate_task_pack,
    validate_trial_identity,
)


SCHEMA_EXECUTION_PROVIDER = "retrieval_arena.task_execution_provider.v1"
SCHEMA_EXECUTION_REGISTRY = "retrieval_arena.task_execution_registry.v1"
SCHEMA_EXECUTION_REQUEST = "retrieval_arena.task_execution_request.v1"
SCHEMA_EXECUTION_PLAN = "retrieval_arena.task_execution_plan.v1"
SCHEMA_NATIVE_RECEIPT = "retrieval_arena.task_native_receipt.v1"
SCHEMA_EXECUTION_BUNDLE = "retrieval_arena.task_execution_bundle.v1"
SCHEMA_EXECUTION_SUMMARY = "retrieval_arena.task_execution_summary.v1"
SCHEMA_REPLAY_VERIFICATION = "retrieval_arena.task_replay_verification.v1"

PROVIDER_INTERFACE = "retrieval_arena.task_provider_interface.v1"
EXECUTION_PHASES = [
    "admit",
    "initialize",
    "execute",
    "observe",
    "finalize",
    "score",
    "publish",
    "replay",
    "cleanup",
]
EXECUTION_LIFECYCLE = [
    {
        "phase": phase,
        "disposition": "pending_externalization" if phase == "publish" else "completed",
    }
    for phase in EXECUTION_PHASES
]
SYNTHETIC_SCENARIOS = {
    "success",
    "skipped_restart",
    "stale_response",
    "request_before_restart",
    "mock_substitution",
    "cleanup_failure",
}
NATIVE_VARIANTS = {"command_a", "command_b"}
SYNTHETIC_ENTRYPOINTS = {
    "agent_adapter": "retrieval_arena.synthetic.restart_agent",
    "environment_adapter": "retrieval_arena.synthetic.restart_environment",
    "event_normalizer": "retrieval_arena.synthetic.restart_normalizer",
    "oracle_provider": "retrieval_arena.synthetic.restart_oracle",
}
SYNTHETIC_OPERATIONS = {
    "agent_adapter": ["propose_actions"],
    "environment_adapter": ["execute_fixture"],
    "event_normalizer": ["normalize_observations"],
    "oracle_provider": ["evaluate_assertions"],
}
EVENT_CLASS_INVENTORY = [
    "artifact_publication",
    "cleanup",
    "command_result",
    "filesystem_mutation",
    "http_request",
    "http_response",
    "process_lifecycle",
    "service_lifecycle",
    "test_execution",
    "tool_invocation",
]


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _sha_json(value: Any) -> str:
    return _sha_bytes(canonical_json(value))


def _module_sha256() -> str:
    return _sha_bytes(Path(__file__).read_bytes())


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


def _integer(value: Any, owner: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{owner} must be an integer >= {minimum}")
    return value


def _check_hash(value: dict[str, Any], field: str, owner: str) -> None:
    if _sha(value[field], field) != self_hash(value, field):
        raise ValidationError(f"{owner} self-hash mismatch")


def _sorted_unique_text(value: Any, owner: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError(f"{owner} must be a{' non-empty' if not allow_empty else ''} list")
    if not all(isinstance(item, str) and item for item in value) or value != sorted(set(value)):
        raise ValidationError(f"{owner} must be sorted unique non-empty strings")
    return value


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("secret", "password", "credential", "api_key", "token_value")):
                return True
            if _contains_secret_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def load_json_value(text: str) -> Any:
    """Decode JSON without permitting duplicate object fields at any depth."""

    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValidationError(f"duplicate JSON field:{key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON:{exc}") from exc


def read_json_value(path: Path) -> Any:
    try:
        return load_json_value(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"unable to read JSON:{path}") from exc


def canonical_json_text(value: Any) -> str:
    return canonical_json(value).decode("utf-8") + "\n"


def write_canonical_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json_text(value), encoding="utf-8", newline="\n")


@dataclass(frozen=True)
class ProviderImplementation:
    """Caller-supplied callable plus the hash of its separately admitted artifact."""

    entrypoint_id: str
    artifact_sha256: str
    invoke: Callable[[dict[str, Any]], dict[str, Any]]


def validate_execution_provider(
    provider: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "provider_id",
        "provider_kind",
        "provider_reference_sha256",
        "entrypoint_id",
        "implementation_sha256",
        "interface_version",
        "capabilities",
        "supported_operations",
        "runtime_network",
        "dynamic_loading",
        "provider_manifest_sha256",
    }
    _exact(provider, fields, "execution provider")
    validate_provider_reference(reference)
    if provider["schema_version"] != SCHEMA_EXECUTION_PROVIDER:
        raise ValidationError("unsupported execution provider schema")
    for field in ("provider_id", "provider_kind", "entrypoint_id"):
        if provider[field] != reference[field]:
            raise ValidationError(f"execution provider reference mismatch:{field}")
    if provider["provider_reference_sha256"] != reference["provider_sha256"]:
        raise ValidationError("execution provider reference hash mismatch")
    if provider["implementation_sha256"] != reference["artifact_sha256"]:
        raise ValidationError("execution provider implementation hash mismatch")
    if provider["interface_version"] != PROVIDER_INTERFACE:
        raise ValidationError("unsupported execution provider interface")
    if provider["capabilities"] != reference["capabilities"]:
        raise ValidationError("execution provider capability mismatch")
    _sorted_unique_text(provider["capabilities"], "execution provider capabilities")
    _sorted_unique_text(
        provider["supported_operations"],
        "execution provider supported_operations",
        allow_empty=False,
    )
    if provider["runtime_network"] != "forbidden" or provider["dynamic_loading"] is not False:
        raise ValidationError("task execution providers must forbid network and dynamic loading")
    _sha(provider["implementation_sha256"], "implementation_sha256")
    _check_hash(provider, "provider_manifest_sha256", "execution provider")
    return provider


def validate_execution_registry(
    registry: dict[str, Any], pack: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "interface_version",
        "providers",
        "runtime_network",
        "dynamic_loading",
        "registry_sha256",
    }
    _exact(registry, fields, "execution registry")
    validate_task_pack(pack)
    if registry["schema_version"] != SCHEMA_EXECUTION_REGISTRY:
        raise ValidationError("unsupported execution registry schema")
    if registry["interface_version"] != PROVIDER_INTERFACE:
        raise ValidationError("unsupported execution registry interface")
    if registry["runtime_network"] != "forbidden" or registry["dynamic_loading"] is not False:
        raise ValidationError("execution registry must forbid network and dynamic loading")
    references = {row["provider_id"]: row for row in pack["provider_references"]}
    providers = registry["providers"]
    if not isinstance(providers, list) or not providers:
        raise ValidationError("execution registry providers must be a non-empty list")
    if providers != sorted(providers, key=lambda row: (row["provider_kind"], row["provider_id"])):
        raise ValidationError("execution registry providers must be sorted")
    if {row.get("provider_id") for row in providers} != set(references):
        raise ValidationError("execution registry must exactly bind task-pack providers")
    for provider in providers:
        validate_execution_provider(provider, references[provider["provider_id"]])
    _check_hash(registry, "registry_sha256", "execution registry")
    return registry


def _admit_implementations(
    registry: dict[str, Any], implementations: Mapping[str, ProviderImplementation]
) -> None:
    expected = {row["entrypoint_id"]: row for row in registry["providers"]}
    if set(implementations) != set(expected):
        raise ValidationError("provider implementation inventory mismatch")
    for entrypoint_id, provider in expected.items():
        implementation = implementations[entrypoint_id]
        if implementation.entrypoint_id != entrypoint_id:
            raise ValidationError("provider implementation entrypoint mismatch")
        if implementation.artifact_sha256 != provider["implementation_sha256"]:
            raise ValidationError("provider implementation artifact drift")
        if not callable(implementation.invoke):
            raise ValidationError("provider implementation must be callable")


def validate_execution_request(
    request: dict[str, Any],
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "request_id",
        "pack_sha256",
        "task_sha256",
        "trial_sha256",
        "registry_sha256",
        "executor_kind",
        "provider_parameters",
        "retention_policy",
        "execution_policy",
        "request_sha256",
    }
    _exact(request, fields, "task execution request")
    validate_task_pack(pack, [task])
    validate_trial_identity(trial, pack, task)
    validate_execution_registry(registry, pack)
    if request["schema_version"] != SCHEMA_EXECUTION_REQUEST:
        raise ValidationError("unsupported task execution request schema")
    _text(request["request_id"], "execution request_id")
    for field, expected in (
        ("pack_sha256", pack["pack_sha256"]),
        ("task_sha256", task["task_sha256"]),
        ("trial_sha256", trial["trial_sha256"]),
        ("registry_sha256", registry["registry_sha256"]),
    ):
        if request[field] != expected:
            raise ValidationError(f"task execution request mismatch:{field}")
    if request["executor_kind"] != "registered_in_memory":
        raise ValidationError("34B admits only the registered in-memory executor")
    if not isinstance(request["provider_parameters"], dict):
        raise ValidationError("provider_parameters must be an object")
    canonical_json(request["provider_parameters"])
    if _contains_secret_key(request["provider_parameters"]):
        raise ValidationError("provider_parameters contain a forbidden secret-like field")
    retention = _exact(
        request["retention_policy"],
        {"native_receipts", "secret_payloads", "failed_run_resources"},
        "task retention policy",
    )
    if retention != {
        "native_receipts": "retain",
        "secret_payloads": "forbidden",
        "failed_run_resources": "retain",
    }:
        raise ValidationError("unsupported task retention policy")
    policy = _exact(
        request["execution_policy"],
        {"runtime_network", "live_model", "real_service", "composite_metric"},
        "task execution policy",
    )
    if policy != {
        "runtime_network": "forbidden",
        "live_model": "forbidden",
        "real_service": "forbidden",
        "composite_metric": None,
    }:
        raise ValidationError("34B execution policy must remain hermetic and non-scientific")
    if pack["declared_capabilities"]["network"] != "forbidden":
        raise ValidationError("34B synthetic execution forbids task-pack network capability")
    if pack["declared_capabilities"]["secrets"] != "forbidden":
        raise ValidationError("34B synthetic execution forbids task-pack secret capability")
    _check_hash(request, "request_sha256", "task execution request")
    return request


def build_execution_plan(
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    registry: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    validate_execution_request(request, pack, task, trial, registry)
    value = {
        "schema_version": SCHEMA_EXECUTION_PLAN,
        "request_sha256": request["request_sha256"],
        "pack_sha256": pack["pack_sha256"],
        "task_sha256": task["task_sha256"],
        "trial_sha256": trial["trial_sha256"],
        "registry_sha256": registry["registry_sha256"],
        "provider_manifest_sha256s": sorted(
            row["provider_manifest_sha256"] for row in registry["providers"]
        ),
        "ordered_phases": list(EXECUTION_PHASES),
        "event_class_inventory": list(EVENT_CLASS_INVENTORY),
        "runtime_network": "forbidden",
        "consumption": "non_consuming",
        "writes": 0,
        "plan_sha256": "0" * 64,
    }
    value["plan_sha256"] = self_hash(value, "plan_sha256")
    return validate_execution_plan(value)


def validate_execution_plan(plan: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "request_sha256",
        "pack_sha256",
        "task_sha256",
        "trial_sha256",
        "registry_sha256",
        "provider_manifest_sha256s",
        "ordered_phases",
        "event_class_inventory",
        "runtime_network",
        "consumption",
        "writes",
        "plan_sha256",
    }
    _exact(plan, fields, "task execution plan")
    if plan["schema_version"] != SCHEMA_EXECUTION_PLAN:
        raise ValidationError("unsupported task execution plan schema")
    for field in (
        "request_sha256",
        "pack_sha256",
        "task_sha256",
        "trial_sha256",
        "registry_sha256",
        "plan_sha256",
    ):
        _sha(plan[field], field)
    provider_hashes = _sorted_unique_text(
        plan["provider_manifest_sha256s"], "plan provider manifests", allow_empty=False
    )
    for value in provider_hashes:
        _sha(value, "plan provider manifest")
    if plan["ordered_phases"] != EXECUTION_PHASES:
        raise ValidationError("task execution plan phase order mismatch")
    if plan["event_class_inventory"] != EVENT_CLASS_INVENTORY:
        raise ValidationError("task execution plan event inventory mismatch")
    if (
        plan["runtime_network"] != "forbidden"
        or plan["consumption"] != "non_consuming"
        or plan["writes"] != 0
    ):
        raise ValidationError("task execution plan must be non-consuming and write-free")
    _check_hash(plan, "plan_sha256", "task execution plan")
    return plan


def validate_native_receipt(
    receipt: dict[str, Any],
    trial: dict[str, Any],
    provider: dict[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "trial_id",
        "trial_sha256",
        "sequence",
        "provider_id",
        "provider_kind",
        "provider_manifest_sha256",
        "operation_id",
        "clock_kind",
        "started_at_ns",
        "finished_at_ns",
        "request_payload",
        "request_sha256",
        "observation_payload",
        "observation_sha256",
        "status",
        "exit_status",
        "redaction",
        "sensitivity",
        "receipt_sha256",
    }
    _exact(receipt, fields, "task native receipt")
    if receipt["schema_version"] != SCHEMA_NATIVE_RECEIPT:
        raise ValidationError("unsupported task native receipt schema")
    if receipt["trial_id"] != trial["trial_id"] or receipt["trial_sha256"] != trial["trial_sha256"]:
        raise ValidationError("task native receipt trial mismatch")
    if receipt["provider_id"] != provider["provider_id"] or receipt["provider_kind"] != provider["provider_kind"]:
        raise ValidationError("task native receipt provider mismatch")
    if receipt["provider_manifest_sha256"] != provider["provider_manifest_sha256"]:
        raise ValidationError("task native receipt provider hash mismatch")
    _integer(receipt["sequence"], "native receipt sequence", 1)
    if receipt["operation_id"] not in provider["supported_operations"]:
        raise ValidationError("task native receipt operation is not admitted")
    if receipt["clock_kind"] != "synthetic_monotonic_ns":
        raise ValidationError("unsupported native receipt clock")
    started = _integer(receipt["started_at_ns"], "native receipt started_at_ns")
    finished = _integer(receipt["finished_at_ns"], "native receipt finished_at_ns")
    if finished < started:
        raise ValidationError("native receipt timestamps are not monotonic")
    if _contains_secret_key(receipt["request_payload"]) or _contains_secret_key(receipt["observation_payload"]):
        raise ValidationError("native receipt contains a forbidden secret-like field")
    if receipt["request_sha256"] != _sha_json(receipt["request_payload"]):
        raise ValidationError("native receipt request hash mismatch")
    if receipt["observation_sha256"] != _sha_json(receipt["observation_payload"]):
        raise ValidationError("native receipt observation hash mismatch")
    if receipt["status"] != "passed" or receipt["exit_status"] != 0:
        raise ValidationError("provider invocation did not complete successfully")
    if receipt["redaction"] != "none" or receipt["sensitivity"] != "public":
        raise ValidationError("34B synthetic receipts must be public and need no redaction")
    _check_hash(receipt, "receipt_sha256", "task native receipt")
    return receipt


def _invoke_provider(
    *,
    sequence: int,
    trial: dict[str, Any],
    provider: dict[str, Any],
    implementation: ProviderImplementation,
    operation_id: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if _contains_secret_key(payload):
        raise ValidationError("provider request contains a forbidden secret-like field")
    isolated_payload = json.loads(canonical_json(payload))
    observation = implementation.invoke(isolated_payload)
    if not isinstance(observation, dict):
        raise ValidationError("provider observation must be an object")
    canonical_json(observation)
    if _contains_secret_key(observation):
        raise ValidationError("provider observation contains a forbidden secret-like field")
    receipt = {
        "schema_version": SCHEMA_NATIVE_RECEIPT,
        "trial_id": trial["trial_id"],
        "trial_sha256": trial["trial_sha256"],
        "sequence": sequence,
        "provider_id": provider["provider_id"],
        "provider_kind": provider["provider_kind"],
        "provider_manifest_sha256": provider["provider_manifest_sha256"],
        "operation_id": operation_id,
        "clock_kind": "synthetic_monotonic_ns",
        "started_at_ns": sequence * 1_000,
        "finished_at_ns": sequence * 1_000 + 100,
        "request_payload": isolated_payload,
        "request_sha256": _sha_json(isolated_payload),
        "observation_payload": observation,
        "observation_sha256": _sha_json(observation),
        "status": "passed",
        "exit_status": 0,
        "redaction": "none",
        "sensitivity": "public",
        "receipt_sha256": "0" * 64,
    }
    receipt["receipt_sha256"] = self_hash(receipt, "receipt_sha256")
    return observation, validate_native_receipt(receipt, trial, provider)


def _synthetic_agent(payload: dict[str, Any]) -> dict[str, Any]:
    parameters = payload["provider_parameters"]
    variant = parameters["native_variant"]
    native_command = {
        "command_a": "restart-service web",
        "command_b": "service web reload",
    }[variant]
    return {
        "actions": [
            {"action_id": "restart", "semantic_action": "service_restart", "target": "web", "native_command": native_command},
            {"action_id": "verify", "semantic_action": "http_get", "target": "/", "native_command": "fixture-http-get /"},
        ],
        "agent_success_claim": "untrusted",
    }


def _observation(
    event_class: str,
    target: str,
    arguments: dict[str, Any],
    observation: dict[str, Any],
    *,
    source: str = "environment",
    parent: int | None = None,
    correlation: str | None = "restart-web",
    exit_status: int = 0,
) -> dict[str, Any]:
    return {
        "event_class": event_class,
        "target": target,
        "normalized_arguments": arguments,
        "observation": observation,
        "source": source,
        "parent": parent,
        "correlation": correlation,
        "exit_status": exit_status,
    }


def _synthetic_environment(payload: dict[str, Any]) -> dict[str, Any]:
    parameters = payload["provider_parameters"]
    scenario = parameters["scenario"]
    actions = payload["agent_observation"]["actions"]
    native_command = actions[0]["native_command"]
    restarted = scenario != "skipped_restart"
    final_generation = "generation-2" if restarted else "generation-1"
    response_generation = "generation-1" if scenario == "stale_response" else final_generation
    cleanup_passed = scenario != "cleanup_failure"
    mock_used = scenario == "mock_substitution"
    marker = "fixture-home-v1"

    tool = _observation(
        "tool_invocation",
        "web",
        {"semantic_action": "service_restart"},
        {"admitted": True},
        source="agent",
    )
    command = _observation(
        "command_result",
        "web",
        {"semantic_action": "service_restart"},
        {"exit_status": 0, "native_command_sha256": _sha_text(native_command)},
        parent=1,
    )
    restart_rows = [
        _observation(
            "process_lifecycle",
            "web",
            {"transition": "generation_change"},
            {"before": "generation-1", "after": final_generation},
            parent=2,
        ),
        _observation(
            "service_lifecycle",
            "web",
            {"action": "restart"},
            {"before": "generation-1", "after": final_generation},
            parent=3,
        ),
        _observation(
            "filesystem_mutation",
            "generation-marker",
            {"operation": "replace"},
            {"generation": final_generation},
            parent=4,
        ),
    ] if restarted else []
    request = _observation(
        "http_request",
        "/",
        {"method": "GET", "path": "/"},
        {"issued": True},
        parent=1,
    )
    response = _observation(
        "http_response",
        "/",
        {"method": "GET", "path": "/"},
        {
            "status": 200,
            "marker": marker,
            "served_by_generation": response_generation,
            "mock_substitution": mock_used,
        },
        parent=1,
    )
    test = _observation(
        "test_execution",
        "home-health-check",
        {"check": "status-marker-generation"},
        {"passed": response_generation == final_generation and not mock_used},
        parent=1,
    )
    publication = _observation(
        "artifact_publication",
        "verification-evidence",
        {"artifact_family": "synthetic_observation"},
        {"published": True},
        parent=1,
    )
    if mock_used:
        publication = _observation(
            "filesystem_mutation",
            "mock-substitution",
            {"operation": "create"},
            {"mocked": True},
            parent=1,
        )
    cleanup = _observation(
        "cleanup",
        "scratch",
        {"operation": "restore"},
        {"passed": cleanup_passed},
        parent=1,
        exit_status=0 if cleanup_passed else 1,
    )

    if scenario == "request_before_restart":
        observations = [tool, request, command, *restart_rows, response, test, publication, cleanup]
    else:
        observations = [tool, command, *restart_rows, request, response, test, publication, cleanup]
    response_observation = {
        "status": 200,
        "marker": marker,
        "served_by_generation": response_generation,
        "mock_substitution": mock_used,
    }
    return {
        "native_command": native_command,
        "oracle_state": {
            "initial_generation": "generation-1",
            "final_generation": final_generation,
            "fixture_marker": marker,
            "response_generation": response_generation,
            "response_observation": response_observation,
            "test_passed": response_generation == final_generation and not mock_used,
            "mock_substitution": mock_used,
            "cleanup_passed": cleanup_passed,
        },
        "observations": observations,
    }


def _synthetic_normalizer(payload: dict[str, Any]) -> dict[str, Any]:
    observations = payload["environment_observation"]["observations"]
    normalized: list[dict[str, Any]] = []
    for row in observations:
        _exact(
            row,
            {"event_class", "target", "normalized_arguments", "observation", "source", "parent", "correlation", "exit_status"},
            "synthetic native observation",
        )
        normalized.append(dict(row))
    return {"normalized_events": normalized}


def _event_by_class(events: list[dict[str, Any]], event_class: str) -> list[dict[str, Any]]:
    return [row for row in events if row["event_class"] == event_class]


def _synthetic_oracle(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload["events"]
    state = payload["environment_state"]
    service = _event_by_class(events, "service_lifecycle")
    requests = _event_by_class(events, "http_request")
    responses = _event_by_class(events, "http_response")
    files = _event_by_class(events, "filesystem_mutation")
    tests = _event_by_class(events, "test_execution")
    cleanup = _event_by_class(events, "cleanup")

    changed = bool(service) and state["final_generation"] != state["initial_generation"]
    ordered = (
        bool(service and requests and responses)
        and service[0]["sequence"] < requests[0]["sequence"] < responses[0]["sequence"]
    )
    response_observation = state["response_observation"]
    http_ok = response_observation == {
        "marker": state["fixture_marker"],
        "mock_substitution": state["mock_substitution"],
        "served_by_generation": state["response_generation"],
        "status": 200,
    }
    config_ok = any(row["target"] == "generation-marker" for row in files) and changed
    test_ok = bool(tests) and state["test_passed"]
    causal_ok = ordered and state["response_generation"] == state["final_generation"]
    no_mock = not state["mock_substitution"] and not any(
        row["target"] == "mock-substitution" for row in files
    )
    cleanup_ok = bool(cleanup) and state["cleanup_passed"] and cleanup[-1]["exit_status"] == 0
    verdicts = {
        "a0-initial": "pass" if state["initial_generation"] == "generation-1" else "invalid",
        "a1-action": "pass" if changed else "fail",
        "a2-order": "pass" if ordered else "fail",
        "a3-http": "pass" if http_ok else "fail",
        "a4-config": "pass" if config_ok else "fail",
        "a5-test": "pass" if test_ok else "fail",
        "a6-causal": "pass" if causal_ok else "fail",
        "a7-no-mock": "pass" if no_mock else "fail",
        "a8-cleanup": "pass" if cleanup_ok else "fail",
    }
    return {"verdicts": verdicts}


def builtin_synthetic_implementations() -> dict[str, ProviderImplementation]:
    artifact = _module_sha256()
    callables = {
        SYNTHETIC_ENTRYPOINTS["agent_adapter"]: _synthetic_agent,
        SYNTHETIC_ENTRYPOINTS["environment_adapter"]: _synthetic_environment,
        SYNTHETIC_ENTRYPOINTS["event_normalizer"]: _synthetic_normalizer,
        SYNTHETIC_ENTRYPOINTS["oracle_provider"]: _synthetic_oracle,
    }
    return {
        entrypoint: ProviderImplementation(entrypoint, artifact, invoke)
        for entrypoint, invoke in callables.items()
    }


def _assertion(
    assertion_id: str,
    assertion_class: str,
    *,
    predecessors: list[str] | None = None,
    critical: bool = False,
) -> dict[str, Any]:
    value = {
        "assertion_id": assertion_id,
        "assertion_class": assertion_class,
        "required": True,
        "critical": critical,
        "predecessor_assertion_ids": sorted(predecessors or []),
        "provider_id": "synthetic-oracle",
        "parameters_sha256": _sha_text(f"synthetic-parameters:{assertion_id}"),
        "assertion_sha256": "0" * 64,
    }
    value["assertion_sha256"] = self_hash(value, "assertion_sha256")
    return value


def _provider_reference(
    provider_id: str,
    kind: str,
    capabilities: list[str],
    artifact_sha256: str,
) -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_PROVIDER_REFERENCE,
        "provider_id": provider_id,
        "provider_kind": kind,
        "version": "1.0.0",
        "artifact_sha256": artifact_sha256,
        "entrypoint_id": SYNTHETIC_ENTRYPOINTS[kind],
        "capabilities": sorted(capabilities),
        "provider_sha256": "0" * 64,
    }
    value["provider_sha256"] = self_hash(value, "provider_sha256")
    return validate_provider_reference(value)


def build_synthetic_restart_fixture(
    *,
    snapshot_id: str = "synthetic-before",
    scenario: str = "success",
    native_variant: str = "command_a",
) -> dict[str, Any]:
    """Return a complete tiny 34B input set without writing or executing it."""
    if scenario not in SYNTHETIC_SCENARIOS:
        raise ValidationError("unsupported synthetic execution scenario")
    if native_variant not in NATIVE_VARIANTS:
        raise ValidationError("unsupported synthetic native variant")
    artifact = _module_sha256()
    references = [
        _provider_reference("synthetic-agent", "agent_adapter", [], artifact),
        _provider_reference(
            "synthetic-environment",
            "environment_adapter",
            ["filesystem_read", "filesystem_write"],
            artifact,
        ),
        _provider_reference("synthetic-normalizer", "event_normalizer", [], artifact),
        _provider_reference("synthetic-oracle", "oracle_provider", ["filesystem_read"], artifact),
    ]
    references.sort(key=lambda row: (row["provider_kind"], row["provider_id"]))
    assertions = [
        _assertion("a0-initial", "initial_state"),
        _assertion("a1-action", "semantic_action", predecessors=["a0-initial"]),
        _assertion("a2-order", "temporal_order", predecessors=["a1-action"]),
        _assertion("a3-http", "terminal_state", predecessors=["a2-order"]),
        _assertion("a4-config", "terminal_state", predecessors=["a1-action"]),
        _assertion("a5-test", "terminal_state", predecessors=["a3-http"]),
        _assertion("a6-causal", "causal", predecessors=["a3-http"]),
        _assertion("a7-no-mock", "prohibition", critical=True),
        _assertion("a8-cleanup", "cleanup", predecessors=["a6-causal"]),
    ]
    assertions.sort(key=lambda row: row["assertion_id"])
    task = {
        "schema_version": SCHEMA_TASK_DEFINITION,
        "task_id": "synthetic-restart-web",
        "goal": "Restart the synthetic web service and verify its home page.",
        "fixture_id": "synthetic-web-fixture",
        "context_input_sha256s": [_sha_text("synthetic-context")],
        "required_capabilities": ["filesystem_read", "filesystem_write"],
        "writable_root_ids": ["scratch"],
        "oracle_references": ["synthetic-oracle"],
        "assertions": assertions,
        "missing_evidence_policy": "failure",
        "success_policy": {
            "required_oracles": "all",
            "critical_safety_oracles": "all",
            "cleanup": "required",
            "composite_metric": None,
        },
        "cleanup_policy": {"required": True, "restoration_required": True},
        "task_sha256": "0" * 64,
    }
    task["task_sha256"] = self_hash(task, "task_sha256")
    capability_policy = {name: "forbidden" for name in CAPABILITY_NAMES}
    capability_policy["filesystem_read"] = "declared"
    capability_policy["filesystem_write"] = "declared"
    fixture_sha = _sha_json(
        {"initial_generation": "generation-1", "fixture_marker": "fixture-home-v1"}
    )
    pack = {
        "schema_version": SCHEMA_TASK_PACK,
        "pack_id": "retrieval-arena-synthetic-restart",
        "pack_version": "1.0.0",
        "publisher": "retrieval-arena",
        "license": "CC0-1.0",
        "supported_contract_version": SCHEMA_TASK_DEFINITION,
        "task_definition_sha256s": [
            {"task_id": task["task_id"], "task_sha256": task["task_sha256"]}
        ],
        "fixture_identities": [
            {"fixture_id": task["fixture_id"], "artifact_sha256": fixture_sha}
        ],
        "provider_references": references,
        "declared_capabilities": capability_policy,
        "writable_roots": [
            {"root_id": "scratch", "path": "/synthetic/scratch", "cleanup_required": True}
        ],
        "cleanup_policy": {
            "cleanup_required": True,
            "missing_cleanup_disposition": "failure",
        },
        "documentation_binding_policy": {
            "only_variable": "documentation_snapshot",
            "required_pair_labels": ["before", "after"],
        },
        "budgets": {
            "wall_time_ms": 10_000,
            "active_time_ms": 5_000,
            "input_tokens": 1_000,
            "cached_input_tokens": None,
            "output_tokens": 1_000,
            "reasoning_tokens": None,
            "context_tokens": 2_000,
            "context_items": 20,
            "tool_calls": 20,
            "retries": 0,
        },
        "aggregation_policy": {"task_weighting": "macro_equal", "composite_metric": None},
        "replication_policy": {
            "replicate_count": 1,
            "stochastic_execution": False,
            "seed_policy": "unsupported",
        },
        "pack_sha256": "0" * 64,
    }
    pack["pack_sha256"] = self_hash(pack, "pack_sha256")
    by_kind = {row["provider_kind"]: row for row in references}
    trial = {
        "schema_version": SCHEMA_TRIAL_IDENTITY,
        "trial_id": f"trial-{snapshot_id}",
        "pack_id": pack["pack_id"],
        "pack_sha256": pack["pack_sha256"],
        "task_id": task["task_id"],
        "task_sha256": task["task_sha256"],
        "fixture_id": task["fixture_id"],
        "fixture_sha256": fixture_sha,
        "documentation_snapshot_id": snapshot_id,
        "documentation_snapshot_sha256": _sha_text(f"documentation:{snapshot_id}"),
        "provider_bindings": {
            "agent_adapter": by_kind["agent_adapter"]["provider_sha256"],
            "environment_adapter": by_kind["environment_adapter"]["provider_sha256"],
            "event_normalizer": by_kind["event_normalizer"]["provider_sha256"],
            "oracle_providers": [by_kind["oracle_provider"]["provider_sha256"]],
        },
        "model_identity_sha256": None,
        "runtime_identity_sha256": _sha_text("synthetic-in-memory-runtime-v1"),
        "prompt_sha256": _sha_text("synthetic-agent-stub-v1"),
        "decoding_policy_sha256": _sha_text("deterministic-no-decoding"),
        "tool_policy_sha256": _sha_text("synthetic-restart-tools-v1"),
        "context_policy_sha256": _sha_text("synthetic-context-policy-v1"),
        "budget_policy_sha256": _sha_json(pack["budgets"]),
        "scoring_policy_sha256": _sha_text("task-success-lexicographic-v1"),
        "replicate_index": 0,
        "seed": None,
        "trial_sha256": "0" * 64,
    }
    trial["trial_sha256"] = self_hash(trial, "trial_sha256")
    manifests = []
    for reference in references:
        kind = reference["provider_kind"]
        manifest = {
            "schema_version": SCHEMA_EXECUTION_PROVIDER,
            "provider_id": reference["provider_id"],
            "provider_kind": kind,
            "provider_reference_sha256": reference["provider_sha256"],
            "entrypoint_id": reference["entrypoint_id"],
            "implementation_sha256": reference["artifact_sha256"],
            "interface_version": PROVIDER_INTERFACE,
            "capabilities": reference["capabilities"],
            "supported_operations": SYNTHETIC_OPERATIONS[kind],
            "runtime_network": "forbidden",
            "dynamic_loading": False,
            "provider_manifest_sha256": "0" * 64,
        }
        manifest["provider_manifest_sha256"] = self_hash(
            manifest, "provider_manifest_sha256"
        )
        manifests.append(manifest)
    manifests.sort(key=lambda row: (row["provider_kind"], row["provider_id"]))
    registry = {
        "schema_version": SCHEMA_EXECUTION_REGISTRY,
        "interface_version": PROVIDER_INTERFACE,
        "providers": manifests,
        "runtime_network": "forbidden",
        "dynamic_loading": False,
        "registry_sha256": "0" * 64,
    }
    registry["registry_sha256"] = self_hash(registry, "registry_sha256")
    request = {
        "schema_version": SCHEMA_EXECUTION_REQUEST,
        "request_id": f"synthetic-{snapshot_id}-{scenario}-{native_variant}",
        "pack_sha256": pack["pack_sha256"],
        "task_sha256": task["task_sha256"],
        "trial_sha256": trial["trial_sha256"],
        "registry_sha256": registry["registry_sha256"],
        "executor_kind": "registered_in_memory",
        "provider_parameters": {
            "scenario": scenario,
            "native_variant": native_variant,
        },
        "retention_policy": {
            "native_receipts": "retain",
            "secret_payloads": "forbidden",
            "failed_run_resources": "retain",
        },
        "execution_policy": {
            "runtime_network": "forbidden",
            "live_model": "forbidden",
            "real_service": "forbidden",
            "composite_metric": None,
        },
        "request_sha256": "0" * 64,
    }
    request["request_sha256"] = self_hash(request, "request_sha256")
    validate_execution_request(request, pack, task, trial, registry)
    return {"pack": pack, "task": task, "trial": trial, "registry": registry, "request": request}


def _provider_by_kind(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in registry["providers"]:
        grouped.setdefault(row["provider_kind"], []).append(row)
    required = {"agent_adapter", "environment_adapter", "event_normalizer", "oracle_provider"}
    if set(grouped) != required or any(len(grouped[kind]) != 1 for kind in required):
        raise ValidationError(
            "34B synthetic execution requires exactly one provider of each admitted kind"
        )
    return {kind: rows[0] for kind, rows in grouped.items()}


def _build_events(
    trial: dict[str, Any],
    normalized: dict[str, Any],
    receipts: dict[str, dict[str, Any]],
    normalizer_provider: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sequence, descriptor in enumerate(normalized["normalized_events"], 1):
        source = descriptor["source"]
        source_receipt = receipts[source]
        observation = descriptor["observation"]
        row = {
            "schema_version": SCHEMA_TASK_EVENT,
            "trial_id": trial["trial_id"],
            "trial_sha256": trial["trial_sha256"],
            "task_id": trial["task_id"],
            "attempt": 1,
            "sequence": sequence,
            "observed_at_ns": sequence * 100,
            "event_class": descriptor["event_class"],
            "target": descriptor["target"],
            "normalized_arguments_sha256": _sha_json(descriptor["normalized_arguments"]),
            "adapter_id": normalizer_provider["provider_id"],
            "adapter_sha256": trial["provider_bindings"]["event_normalizer"],
            "input_artifact_sha256s": [source_receipt["request_sha256"]],
            "output_artifact_sha256s": [source_receipt["observation_sha256"]],
            "exit_status": descriptor["exit_status"],
            "observation_sha256": _sha_json(observation),
            "parent_action_sequence": descriptor["parent"],
            "causal_correlation_id": descriptor["correlation"],
            "redaction": "none",
            "sensitivity": "public",
            "native_receipt_sha256": source_receipt["receipt_sha256"],
            "event_sha256": "0" * 64,
        }
        row["event_sha256"] = self_hash(row, "event_sha256")
        rows.append(row)
    return validate_task_events(rows, trial)


def semantic_event_projection(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the portable semantics, excluding native command and receipt identity."""
    return [
        {
            "sequence": row["sequence"],
            "event_class": row["event_class"],
            "target": row["target"],
            "normalized_arguments_sha256": row["normalized_arguments_sha256"],
            "exit_status": row["exit_status"],
            "parent_action_sequence": row["parent_action_sequence"],
            "causal_correlation_id": row["causal_correlation_id"],
        }
        for row in events
    ]


def _environment_state(environment: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    state = environment.get("oracle_state")
    if not isinstance(state, dict) or not state:
        raise ValidationError("environment observation requires a non-empty oracle_state")
    canonical_json(state)
    if _contains_secret_key(state):
        raise ValidationError("environment oracle_state contains a forbidden secret-like field")
    if not events:
        raise ValidationError("oracle_state cannot be admitted without normalized events")
    return json.loads(canonical_json(state))


def _oracle_payload(
    task: dict[str, Any], events: list[dict[str, Any]], state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "task_sha256": task["task_sha256"],
        "assertion_ids": [row["assertion_id"] for row in task["assertions"]],
        "events": events,
        "environment_state": state,
    }


def _build_oracle_results(
    task: dict[str, Any],
    pack: dict[str, Any],
    trial: dict[str, Any],
    events: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    oracle_observation: dict[str, Any],
) -> list[dict[str, Any]]:
    verdicts = oracle_observation.get("verdicts")
    if not isinstance(verdicts, dict):
        raise ValidationError("oracle provider must return a verdict map")
    assertions = {row["assertion_id"]: row for row in task["assertions"]}
    if set(verdicts) != set(assertions):
        raise ValidationError("oracle provider verdict inventory mismatch")
    oracle_reference = next(
        row for row in pack["provider_references"] if row["provider_kind"] == "oracle_provider"
    )
    input_hashes = sorted(
        [row["event_sha256"] for row in events]
        + [row["receipt_sha256"] for row in receipts]
    )
    rows: list[dict[str, Any]] = []
    for assertion_id in sorted(assertions):
        assertion = assertions[assertion_id]
        verdict = verdicts[assertion_id]
        if verdict not in {"pass", "fail", "unavailable", "invalid"}:
            raise ValidationError("oracle provider returned an unsupported verdict")
        violation = (
            f"violation:{assertion_id}"
            if verdict == "fail" and assertion["assertion_class"] in {"prohibition", "critical_safety"}
            else None
        )
        row = {
            "schema_version": SCHEMA_ORACLE_RESULT,
            "trial_id": trial["trial_id"],
            "trial_sha256": trial["trial_sha256"],
            "task_id": task["task_id"],
            "assertion_id": assertion_id,
            "oracle_class": assertion["assertion_class"],
            "required": assertion["required"],
            "critical": assertion["critical"],
            "provider_id": assertion["provider_id"],
            "provider_sha256": oracle_reference["provider_sha256"],
            "parameters_sha256": assertion["parameters_sha256"],
            "input_sha256s": input_hashes,
            "expected_predicate_sha256": assertion["parameters_sha256"],
            "observed_value_sha256": (
                None
                if verdict == "unavailable"
                else _sha_json({"assertion_id": assertion_id, "verdict": verdict})
            ),
            "verdict": verdict,
            "missing_evidence": verdict == "unavailable",
            "violation_id": violation,
            "result_sha256": "0" * 64,
        }
        row["result_sha256"] = self_hash(row, "result_sha256")
        rows.append(row)
    return validate_oracle_results(rows, task, pack, trial)


def _build_efficiency(
    trial: dict[str, Any], events: list[dict[str, Any]], receipts: list[dict[str, Any]]
) -> dict[str, Any]:
    wall_time = receipts[-1]["finished_at_ns"] // 1_000
    value = {
        "schema_version": "retrieval_arena.task_efficiency.v2",
        "trial_id": trial["trial_id"],
        "trial_sha256": trial["trial_sha256"],
        "wall_time_ms": wall_time,
        "active_time_ms": wall_time,
        "time_to_first_valid_action_ms": 1,
        "time_to_first_successful_verification_ms": wall_time,
        "input_tokens": 12,
        "cached_input_tokens": None,
        "output_tokens": 4,
        "reasoning_tokens": None,
        "context_tokens": 24,
        "context_items": 2,
        "tool_calls": len(_event_by_class(events, "tool_invocation")),
        "successful_tool_calls": len(_event_by_class(events, "tool_invocation")),
        "failed_actions": sum(row["exit_status"] != 0 for row in events),
        "retries": 0,
        "provider_cost": None,
        "provider_cost_currency": None,
        "token_accounting": {
            "input_tokens": "provider_reported",
            "cached_input_tokens": "unavailable",
            "output_tokens": "provider_reported",
            "reasoning_tokens": "unavailable",
        },
        "tokenizer_policy_sha256": None,
        "measured_resources": [
            {
                "resource_id": "synthetic-provider-invocations",
                "unit": "count",
                "value": len(receipts),
                "source_sha256": _sha_json([row["receipt_sha256"] for row in receipts]),
            }
        ],
        "source_receipt_sha256s": sorted(row["receipt_sha256"] for row in receipts),
        "efficiency_sha256": "0" * 64,
    }
    value["efficiency_sha256"] = self_hash(value, "efficiency_sha256")
    return validate_efficiency(value, trial)


def _status_and_reasons(score: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if score["invalid_trial"]:
        reasons.extend(score["invalid_reason_codes"])
    if score["critical_safety_violation"]:
        reasons.append("critical_safety_violation")
    if not score["cleanup_passed"]:
        reasons.append("cleanup_failed")
    counts = score["required_oracle_counts"]
    if counts["failed"]:
        reasons.append("required_oracle_failed")
    if counts["unavailable"]:
        reasons.append("required_oracle_unavailable")
    if counts["invalid"]:
        reasons.append("required_oracle_invalid")
    status = "accepted" if score["task_success"] else ("invalid" if score["invalid_trial"] else "failed")
    return status, sorted(set(reasons))


def execute_task(
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    registry: dict[str, Any],
    request: dict[str, Any],
    implementations: Mapping[str, ProviderImplementation],
) -> dict[str, Any]:
    """Execute one admitted provider chain and return an unpublished bundle."""
    plan = build_execution_plan(pack, task, trial, registry, request)
    _admit_implementations(registry, implementations)
    providers = _provider_by_kind(registry)
    receipts: list[dict[str, Any]] = []

    agent_input = {
        "provider_parameters": request["provider_parameters"],
        "task_sha256": task["task_sha256"],
        "documentation_snapshot_sha256": trial["documentation_snapshot_sha256"],
    }
    agent, receipt = _invoke_provider(
        sequence=1,
        trial=trial,
        provider=providers["agent_adapter"],
        implementation=implementations[providers["agent_adapter"]["entrypoint_id"]],
        operation_id="propose_actions",
        payload=agent_input,
    )
    receipts.append(receipt)
    environment_input = {
        "provider_parameters": request["provider_parameters"],
        "agent_observation": agent,
        "fixture_sha256": trial["fixture_sha256"],
    }
    environment, receipt = _invoke_provider(
        sequence=2,
        trial=trial,
        provider=providers["environment_adapter"],
        implementation=implementations[providers["environment_adapter"]["entrypoint_id"]],
        operation_id="execute_fixture",
        payload=environment_input,
    )
    receipts.append(receipt)
    normalizer_input = {
        "environment_observation": environment,
        "event_contract": SCHEMA_TASK_EVENT,
    }
    normalized, receipt = _invoke_provider(
        sequence=3,
        trial=trial,
        provider=providers["event_normalizer"],
        implementation=implementations[providers["event_normalizer"]["entrypoint_id"]],
        operation_id="normalize_observations",
        payload=normalizer_input,
    )
    receipts.append(receipt)
    receipt_map = {"agent": receipts[0], "environment": receipts[1]}
    events = _build_events(trial, normalized, receipt_map, providers["event_normalizer"])
    state = _environment_state(environment, events)
    oracle_input = _oracle_payload(task, events, state)
    oracle_observation, receipt = _invoke_provider(
        sequence=4,
        trial=trial,
        provider=providers["oracle_provider"],
        implementation=implementations[providers["oracle_provider"]["entrypoint_id"]],
        operation_id="evaluate_assertions",
        payload=oracle_input,
    )
    receipts.append(receipt)
    oracle_results = _build_oracle_results(
        task, pack, trial, events, receipts, oracle_observation
    )
    efficiency = _build_efficiency(trial, events, receipts)
    score = score_task(pack, task, trial, oracle_results, efficiency)
    replay = build_task_replay(pack, task, trial, events, oracle_results, score)
    status, reasons = _status_and_reasons(score)
    bundle = {
        "schema_version": SCHEMA_EXECUTION_BUNDLE,
        "result_family": RESULT_FAMILY,
        "pack": pack,
        "task": task,
        "trial": trial,
        "registry": registry,
        "request": request,
        "plan": plan,
        "native_receipts": receipts,
        "events": events,
        "oracle_results": oracle_results,
        "efficiency": efficiency,
        "score": score,
        "replay": replay,
        "lifecycle": list(EXECUTION_LIFECYCLE),
        "status": status,
        "reason_codes": reasons,
        "retention_policy": request["retention_policy"],
        "bundle_sha256": "0" * 64,
    }
    bundle["bundle_sha256"] = self_hash(bundle, "bundle_sha256")
    validate_execution_bundle(bundle)
    replay_task_execution(bundle, implementations)
    return bundle


def validate_execution_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "result_family",
        "pack",
        "task",
        "trial",
        "registry",
        "request",
        "plan",
        "native_receipts",
        "events",
        "oracle_results",
        "efficiency",
        "score",
        "replay",
        "lifecycle",
        "status",
        "reason_codes",
        "retention_policy",
        "bundle_sha256",
    }
    _exact(bundle, fields, "task execution bundle")
    if bundle["schema_version"] != SCHEMA_EXECUTION_BUNDLE or bundle["result_family"] != RESULT_FAMILY:
        raise ValidationError("unsupported task execution bundle family")
    pack, task, trial = bundle["pack"], bundle["task"], bundle["trial"]
    registry, request = bundle["registry"], bundle["request"]
    validate_execution_request(request, pack, task, trial, registry)
    expected_plan = build_execution_plan(pack, task, trial, registry, request)
    if canonical_json(bundle["plan"]) != canonical_json(expected_plan):
        raise ValidationError("task execution plan does not match admitted inputs")
    providers = {row["provider_id"]: row for row in registry["providers"]}
    receipts = bundle["native_receipts"]
    if not isinstance(receipts, list) or not receipts:
        raise ValidationError("task execution bundle requires native receipts")
    for sequence, receipt in enumerate(receipts, 1):
        if receipt.get("sequence") != sequence or receipt.get("provider_id") not in providers:
            raise ValidationError("task native receipt sequence or provider mismatch")
        validate_native_receipt(receipt, trial, providers[receipt["provider_id"]])
    validate_task_events(bundle["events"], trial)
    receipt_hashes = {row["receipt_sha256"] for row in receipts}
    if not {row["native_receipt_sha256"] for row in bundle["events"]} <= receipt_hashes:
        raise ValidationError("task event references an unknown native receipt")
    validate_oracle_results(bundle["oracle_results"], task, pack, trial)
    validate_efficiency(bundle["efficiency"], trial)
    expected_score = score_task(
        pack, task, trial, bundle["oracle_results"], bundle["efficiency"]
    )
    if canonical_json(bundle["score"]) != canonical_json(expected_score):
        raise ValidationError("task execution score differs from retained evidence")
    expected_replay = build_task_replay(
        pack,
        task,
        trial,
        bundle["events"],
        bundle["oracle_results"],
        bundle["score"],
    )
    if canonical_json(bundle["replay"]) != canonical_json(expected_replay):
        raise ValidationError("task execution replay differs from retained evidence")
    expected_lifecycle = EXECUTION_LIFECYCLE
    if bundle["lifecycle"] != expected_lifecycle:
        raise ValidationError("task execution lifecycle is incomplete or out of order")
    expected_status, expected_reasons = _status_and_reasons(bundle["score"])
    if bundle["status"] != expected_status or bundle["reason_codes"] != expected_reasons:
        raise ValidationError("task execution status does not match its score")
    if bundle["retention_policy"] != request["retention_policy"]:
        raise ValidationError("task execution retention policy mismatch")
    _check_hash(bundle, "bundle_sha256", "task execution bundle")
    return bundle


def replay_task_execution(
    bundle: dict[str, Any],
    implementations: Mapping[str, ProviderImplementation],
) -> dict[str, Any]:
    """Replay retained evidence, invoking only the deterministic oracle provider."""
    validate_execution_bundle(bundle)
    registry = bundle["registry"]
    _admit_implementations(registry, implementations)
    providers = _provider_by_kind(registry)
    oracle_provider = providers["oracle_provider"]
    oracle_receipt = next(
        row for row in bundle["native_receipts"] if row["provider_kind"] == "oracle_provider"
    )
    environment_receipt = next(
        row for row in bundle["native_receipts"] if row["provider_kind"] == "environment_adapter"
    )
    state = _environment_state(environment_receipt["observation_payload"], bundle["events"])
    expected_input = _oracle_payload(bundle["task"], bundle["events"], state)
    if canonical_json(oracle_receipt["request_payload"]) != canonical_json(expected_input):
        raise ValidationError("retained oracle input does not match retained events")
    implementation = implementations[oracle_provider["entrypoint_id"]]
    observed = implementation.invoke(json.loads(canonical_json(expected_input)))
    if canonical_json(observed) != canonical_json(oracle_receipt["observation_payload"]):
        raise ValidationError("retained oracle observation differs from deterministic replay")
    expected_results = _build_oracle_results(
        bundle["task"],
        bundle["pack"],
        bundle["trial"],
        bundle["events"],
        bundle["native_receipts"],
        observed,
    )
    if canonical_json(expected_results) != canonical_json(bundle["oracle_results"]):
        raise ValidationError("retained oracle results differ from deterministic replay")
    score = score_task(
        bundle["pack"],
        bundle["task"],
        bundle["trial"],
        expected_results,
        bundle["efficiency"],
    )
    replay = build_task_replay(
        bundle["pack"],
        bundle["task"],
        bundle["trial"],
        bundle["events"],
        expected_results,
        score,
    )
    if canonical_json(score) != canonical_json(bundle["score"]) or canonical_json(replay) != canonical_json(bundle["replay"]):
        raise ValidationError("retained task result differs from deterministic replay")
    value = {
        "schema_version": SCHEMA_REPLAY_VERIFICATION,
        "result_family": RESULT_FAMILY,
        "bundle_sha256": bundle["bundle_sha256"],
        "oracle_provider_manifest_sha256": oracle_provider["provider_manifest_sha256"],
        "task_score_sha256": score["score_sha256"],
        "task_replay_sha256": replay["replay_sha256"],
        "provider_invocations": 1,
        "environment_invocations": 0,
        "service_executions": 0,
        "byte_stable": True,
        "verification_sha256": "0" * 64,
    }
    value["verification_sha256"] = self_hash(value, "verification_sha256")
    return validate_replay_verification(value)


def validate_replay_verification(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "result_family",
        "bundle_sha256",
        "oracle_provider_manifest_sha256",
        "task_score_sha256",
        "task_replay_sha256",
        "provider_invocations",
        "environment_invocations",
        "service_executions",
        "byte_stable",
        "verification_sha256",
    }
    _exact(value, fields, "task replay verification")
    if value["schema_version"] != SCHEMA_REPLAY_VERIFICATION or value["result_family"] != RESULT_FAMILY:
        raise ValidationError("unsupported task replay verification family")
    for field in (
        "bundle_sha256",
        "oracle_provider_manifest_sha256",
        "task_score_sha256",
        "task_replay_sha256",
        "verification_sha256",
    ):
        _sha(value[field], field)
    if (
        value["provider_invocations"] != 1
        or value["environment_invocations"] != 0
        or value["service_executions"] != 0
        or value["byte_stable"] is not True
    ):
        raise ValidationError("task replay verification execution boundary mismatch")
    _check_hash(value, "verification_sha256", "task replay verification")
    return value


def build_execution_summary(
    bundle: dict[str, Any], artifacts: list[dict[str, Any]]
) -> dict[str, Any]:
    validate_execution_bundle(bundle)
    if artifacts != sorted(artifacts, key=lambda row: row["path"]):
        raise ValidationError("task execution artifact inventory must be sorted")
    for row in artifacts:
        _exact(row, {"path", "sha256", "size_bytes"}, "task execution artifact")
        _text(row["path"], "task execution artifact path")
        _sha(row["sha256"], "task execution artifact sha256")
        _integer(row["size_bytes"], "task execution artifact size")
    value = {
        "schema_version": SCHEMA_EXECUTION_SUMMARY,
        "result_family": RESULT_FAMILY,
        "request_id": bundle["request"]["request_id"],
        "trial_sha256": bundle["trial"]["trial_sha256"],
        "status": bundle["status"],
        "task_success": bundle["score"]["task_success"],
        "invalid_trial": bundle["score"]["invalid_trial"],
        "reason_codes": bundle["reason_codes"],
        "bundle_sha256": bundle["bundle_sha256"],
        "score_sha256": bundle["score"]["score_sha256"],
        "replay_sha256": bundle["replay"]["replay_sha256"],
        "detailed_artifacts": artifacts,
        "summary_sha256": "0" * 64,
    }
    value["summary_sha256"] = self_hash(value, "summary_sha256")
    _check_hash(value, "summary_sha256", "task execution summary")
    return value


def render_task_report(bundle: dict[str, Any]) -> str:
    validate_execution_bundle(bundle)
    score = bundle["score"]
    return "\n".join(
        [
            "# Deterministic task execution report",
            "",
            f"- Request: `{bundle['request']['request_id']}`",
            f"- Status: `{bundle['status']}`",
            f"- Task success: `{str(score['task_success']).lower()}`",
            f"- Invalid trial: `{str(score['invalid_trial']).lower()}`",
            f"- Critical safety violation: `{str(score['critical_safety_violation']).lower()}`",
            f"- Cleanup passed: `{str(score['cleanup_passed']).lower()}`",
            f"- Requirement completion: `{score['requirement_completion']}`",
            f"- Wall time (synthetic ms): `{score['efficiency']['wall_time_ms']}`",
            f"- Bundle SHA-256: `{bundle['bundle_sha256']}`",
            "",
            "No agent success claim is used as evaluation evidence.",
            "",
        ]
    )


def publish_task_execution(output_dir: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    """Publish one already validated bundle into a new, harness-owned directory."""
    validate_execution_bundle(bundle)
    if output_dir.exists():
        raise ValidationError("task execution output directory must not already exist")
    output_dir.mkdir(parents=True)
    artifacts: dict[str, Any] = {
        "execution_bundle.v1.json": bundle,
        "native_receipts.v1.json": bundle["native_receipts"],
        "task_events.v2.json": bundle["events"],
        "oracle_results.v2.json": bundle["oracle_results"],
        "task_score.v2.json": bundle["score"],
        "task_replay.v2.json": bundle["replay"],
    }
    inventory: list[dict[str, Any]] = []
    for name, value in artifacts.items():
        data = canonical_json(value) + b"\n"
        (output_dir / name).write_bytes(data)
        inventory.append({"path": name, "sha256": _sha_bytes(data), "size_bytes": len(data)})
    report = render_task_report(bundle).encode("utf-8")
    (output_dir / "task_report.v1.md").write_bytes(report)
    inventory.append(
        {"path": "task_report.v1.md", "sha256": _sha_bytes(report), "size_bytes": len(report)}
    )
    inventory.sort(key=lambda row: row["path"])
    summary = build_execution_summary(bundle, inventory)
    write_canonical_json(output_dir / "summary.v1.json", summary)
    return summary


def write_task_report(output_dir: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    validate_execution_bundle(bundle)
    if output_dir.exists():
        raise ValidationError("task report output directory must not already exist")
    output_dir.mkdir(parents=True)
    report = render_task_report(bundle)
    report_bytes = report.encode("utf-8")
    (output_dir / "task_report.v1.md").write_bytes(report_bytes)
    record = {
        "schema_version": "retrieval_arena.task_report.v1",
        "bundle_sha256": bundle["bundle_sha256"],
        "score_sha256": bundle["score"]["score_sha256"],
        "report_sha256": _sha_bytes(report_bytes),
        "report_path": "task_report.v1.md",
        "record_sha256": "0" * 64,
    }
    record["record_sha256"] = self_hash(record, "record_sha256")
    write_canonical_json(output_dir / "task_report_record.v1.json", record)
    return record


def score_task_files(
    pack_path: Path,
    task_path: Path,
    trial_path: Path,
    oracle_results_path: Path,
    efficiency_path: Path,
) -> dict[str, Any]:
    pack = read_json_value(pack_path)
    task = read_json_value(task_path)
    trial = read_json_value(trial_path)
    oracle_results = read_json_value(oracle_results_path)
    efficiency = read_json_value(efficiency_path)
    if not all(isinstance(value, dict) for value in (pack, task, trial, efficiency)):
        raise ValidationError("task scoring inputs must be JSON objects")
    if not isinstance(oracle_results, list):
        raise ValidationError("oracle results input must be a JSON list")
    return score_task(pack, task, trial, oracle_results, efficiency)


def compare_task_files(
    pair_id: str,
    before_trial_path: Path,
    before_score_path: Path,
    after_trial_path: Path,
    after_score_path: Path,
) -> dict[str, Any]:
    before_trial = read_json_value(before_trial_path)
    before_score = read_json_value(before_score_path)
    after_trial = read_json_value(after_trial_path)
    after_score = read_json_value(after_score_path)
    return compare_task_scores(pair_id, before_trial, before_score, after_trial, after_score)
