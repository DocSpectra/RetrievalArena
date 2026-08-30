"""Portable task execution receipts, synthetic providers, reporting, and replay.

Providers are exact admitted source artifacts. They return plain observations;
this module alone creates authoritative receipts, normalized events, oracle
records, scores, replay manifests, terminal failures, and publications. Runtime
capabilities are audit-enforced and provider imports are unavailable.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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


SCHEMA_EXECUTION_PROVIDER = "retrieval_arena.task_execution_provider.v2"
SCHEMA_EXECUTION_REGISTRY = "retrieval_arena.task_execution_registry.v2"
SCHEMA_EXECUTION_REQUEST = "retrieval_arena.task_execution_request.v2"
SCHEMA_EXECUTION_PLAN = "retrieval_arena.task_execution_plan.v2"
SCHEMA_NATIVE_RECEIPT = "retrieval_arena.task_native_receipt.v2"
SCHEMA_EXECUTION_BUNDLE = "retrieval_arena.task_execution_bundle.v2"
SCHEMA_EXECUTION_FAILURE = "retrieval_arena.task_execution_failure.v1"
SCHEMA_EXECUTION_SUMMARY = "retrieval_arena.task_execution_summary.v2"
SCHEMA_REPLAY_VERIFICATION = "retrieval_arena.task_replay_verification.v2"

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
SYNTHETIC_SCENARIOS = {
    "success",
    "skipped_restart",
    "stale_response",
    "request_before_restart",
    "mock_substitution",
    "cleanup_failure",
    "provider_exception",
    "cleanup_provider_exception",
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
    "environment_adapter": ["cleanup_fixture", "execute_fixture"],
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
    """Path to provider source; RA derives identity and loads admitted bytes."""

    artifact_path: Path


@dataclass(frozen=True)
class AdmittedProviderImplementation:
    artifact_path: str
    artifact_sha256: str
    artifact_bytes: bytes


class ProviderCapabilityError(RuntimeError):
    pass


_ACTIVE_PROVIDER_POLICY: ContextVar[dict[str, Any] | None] = ContextVar(
    "retrieval_arena_active_provider_policy", default=None
)


def _audit_capability(event: str, arguments: tuple[Any, ...]) -> str | None:
    if event == "open":
        mode = arguments[1] if len(arguments) > 1 else "r"
        flags = arguments[2] if len(arguments) > 2 else 0
        writes = (
            isinstance(mode, str)
            and any(marker in mode for marker in ("w", "a", "x", "+"))
        ) or (
            isinstance(flags, int)
            and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        )
        return "filesystem_write" if writes else "filesystem_read"
    if event.startswith("socket."):
        return "network"
    if event == "subprocess.Popen" or event == "os.system" or event.startswith("os.spawn") or event.startswith("os.posix_spawn"):
        return "process"
    if event in {"os.remove", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "os.link", "os.symlink"}:
        return "filesystem_write"
    if event.startswith("ctypes."):
        return "privileges"
    return None


def _provider_audit_hook(event: str, arguments: tuple[Any, ...]) -> None:
    policy = _ACTIVE_PROVIDER_POLICY.get()
    if policy is None:
        return
    capability = _audit_capability(event, arguments)
    if capability is None:
        return
    policy["used"].add(capability)
    if capability not in policy["allowed"]:
        policy["violation"] = capability
        raise ProviderCapabilityError(f"undeclared-provider-capability:{capability}")
    if capability in {"filesystem_read", "filesystem_write"}:
        raw_path = arguments[0] if arguments else None
        if not isinstance(raw_path, (str, bytes, os.PathLike)):
            policy["violation"] = f"{capability}_scope"
            raise ProviderCapabilityError(f"provider-filesystem-scope:{capability}")
        candidate = Path(os.fsdecode(raw_path))
        if not candidate.is_absolute():
            policy["violation"] = f"{capability}_scope"
            raise ProviderCapabilityError(f"provider-filesystem-scope:{capability}")
        resolved = candidate.resolve(strict=False)
        if not any(resolved == root or root in resolved.parents for root in policy["filesystem_roots"]):
            policy["violation"] = f"{capability}_scope"
            raise ProviderCapabilityError(f"provider-filesystem-scope:{capability}")


sys.addaudithook(_provider_audit_hook)


def _provider_artifact(path: Path) -> AdmittedProviderImplementation:
    candidate = path if path.is_absolute() else Path.cwd() / path
    if candidate.is_symlink():
        raise ValidationError("provider artifact must not be a symlink")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValidationError("provider artifact must be a regular non-symlink file")
    data = resolved.read_bytes()
    if not data:
        raise ValidationError("provider artifact must not be empty")
    return AdmittedProviderImplementation(str(resolved), _sha_bytes(data), data)


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
) -> dict[str, AdmittedProviderImplementation]:
    expected = {row["entrypoint_id"]: row for row in registry["providers"]}
    if set(implementations) != set(expected):
        raise ValidationError("provider implementation inventory mismatch")
    admitted: dict[str, AdmittedProviderImplementation] = {}
    for entrypoint_id, provider in expected.items():
        implementation = implementations[entrypoint_id]
        if not isinstance(implementation, ProviderImplementation):
            raise ValidationError("provider implementation must be a source artifact")
        artifact = _provider_artifact(implementation.artifact_path)
        if artifact.artifact_sha256 != provider["implementation_sha256"]:
            raise ValidationError("provider implementation artifact drift")
        admitted[entrypoint_id] = artifact
    return admitted


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
        "capabilities_used",
        "status",
        "exit_status",
        "failure",
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
    capabilities_used = _sorted_unique_text(
        receipt["capabilities_used"], "native receipt capabilities_used"
    )
    if receipt["status"] == "passed":
        if not set(capabilities_used) <= set(provider["capabilities"]):
            raise ValidationError("passed native receipt records an undeclared capability")
        if receipt["exit_status"] != 0 or receipt["failure"] is not None:
            raise ValidationError("passed provider receipt has failure state")
    elif receipt["status"] == "failed":
        if receipt["exit_status"] != 1:
            raise ValidationError("failed provider receipt requires exit status one")
        failure = _exact(
            receipt["failure"],
            {"reason_code", "error_class", "message_sha256"},
            "provider failure",
        )
        _text(failure["reason_code"], "provider failure reason_code")
        _text(failure["error_class"], "provider failure error_class")
        _sha(failure["message_sha256"], "provider failure message_sha256")
        undeclared = set(capabilities_used) - set(provider["capabilities"])
        if undeclared and failure["reason_code"] != "provider_capability_violation":
            raise ValidationError("undeclared capability requires capability-violation failure")
    else:
        raise ValidationError("unsupported provider receipt status")
    if receipt["redaction"] != "none" or receipt["sensitivity"] != "public":
        raise ValidationError("34B synthetic receipts must be public and need no redaction")
    _check_hash(receipt, "receipt_sha256", "task native receipt")
    return receipt


def _invoke_provider(
    *,
    sequence: int,
    trial: dict[str, Any],
    provider: dict[str, Any],
    implementation: AdmittedProviderImplementation,
    operation_id: str,
    payload: dict[str, Any],
    filesystem_roots: list[Path],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if _contains_secret_key(payload):
        raise ValidationError("provider request contains a forbidden secret-like field")
    isolated_payload = json.loads(canonical_json(payload))
    policy = {
        "allowed": set(provider["capabilities"]),
        "used": set(),
        "violation": None,
        "filesystem_roots": [root.resolve(strict=False) for root in filesystem_roots],
    }
    token = _ACTIVE_PROVIDER_POLICY.set(policy)
    observation: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    try:
        entrypoint_name = provider["entrypoint_id"].rsplit(".", 1)[-1]
        safe_builtins = {
            "ValueError": ValueError,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "isinstance": isinstance,
            "len": len,
            "list": list,
            "open": open,
            "set": set,
            "sorted": sorted,
            "str": str,
        }
        namespace: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "__name__": f"admitted_provider_{implementation.artifact_sha256}",
            "system_call": os.system,
            "open_socket": socket.socket,
        }
        code = compile(
            implementation.artifact_bytes,
            f"provider:{implementation.artifact_sha256}",
            "exec",
            dont_inherit=True,
        )
        exec(code, namespace, namespace)
        entrypoint = namespace.get(entrypoint_name)
        if not callable(entrypoint):
            raise ValidationError("admitted provider entrypoint is missing or not callable")
        candidate = entrypoint(isolated_payload)
        if policy["violation"] is not None:
            raise ProviderCapabilityError(
                f"undeclared-provider-capability:{policy['violation']}"
            )
        if not isinstance(candidate, dict):
            raise ValidationError("provider observation must be an object")
        canonical_json(candidate)
        if _contains_secret_key(candidate):
            raise ValidationError("provider observation contains a forbidden secret-like field")
        observation = candidate
    except Exception as exc:
        reason = (
            "provider_capability_violation"
            if isinstance(exc, ProviderCapabilityError)
            else "provider_contract_failure"
            if isinstance(exc, ValidationError)
            else "provider_exception"
        )
        failure = {
            "reason_code": reason,
            "error_class": type(exc).__name__,
            "message_sha256": _sha_text(str(exc)),
        }
    finally:
        _ACTIVE_PROVIDER_POLICY.reset(token)
    status = "passed" if failure is None else "failed"
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
        "capabilities_used": sorted(policy["used"]),
        "status": status,
        "exit_status": 0 if status == "passed" else 1,
        "failure": failure,
        "redaction": "none",
        "sensitivity": "public",
        "receipt_sha256": "0" * 64,
    }
    receipt["receipt_sha256"] = self_hash(receipt, "receipt_sha256")
    return observation, validate_native_receipt(receipt, trial, provider)


def _event_by_class(events: list[dict[str, Any]], event_class: str) -> list[dict[str, Any]]:
    return [row for row in events if row["event_class"] == event_class]


def builtin_synthetic_implementations() -> dict[str, ProviderImplementation]:
    artifact = Path(__file__).with_name("synthetic_task_providers.py")
    return {
        entrypoint: ProviderImplementation(artifact)
        for entrypoint in SYNTHETIC_ENTRYPOINTS.values()
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
    implementations: Mapping[str, ProviderImplementation] | None = None,
) -> dict[str, Any]:
    """Return a complete tiny 34B input set without writing or executing it."""
    if scenario not in SYNTHETIC_SCENARIOS:
        raise ValidationError("unsupported synthetic execution scenario")
    if native_variant not in NATIVE_VARIANTS:
        raise ValidationError("unsupported synthetic native variant")
    implementation_set = implementations or builtin_synthetic_implementations()
    if set(implementation_set) != set(SYNTHETIC_ENTRYPOINTS.values()):
        raise ValidationError("synthetic provider implementation inventory mismatch")
    artifact_by_kind = {
        kind: _provider_artifact(
            implementation_set[entrypoint].artifact_path
        ).artifact_sha256
        for kind, entrypoint in SYNTHETIC_ENTRYPOINTS.items()
    }
    references = [
        _provider_reference(
            "synthetic-agent", "agent_adapter", [], artifact_by_kind["agent_adapter"]
        ),
        _provider_reference(
            "synthetic-environment",
            "environment_adapter",
            ["filesystem_read", "filesystem_write"],
            artifact_by_kind["environment_adapter"],
        ),
        _provider_reference(
            "synthetic-normalizer",
            "event_normalizer",
            [],
            artifact_by_kind["event_normalizer"],
        ),
        _provider_reference(
            "synthetic-oracle",
            "oracle_provider",
            ["filesystem_read"],
            artifact_by_kind["oracle_provider"],
        ),
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


def _lifecycle(
    *,
    failed_phase: str | None,
    replay_completed: bool,
    cleanup_passed: bool,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    reached_failure = False
    for phase in EXECUTION_PHASES:
        if phase == "publish":
            disposition = "pending_externalization"
        elif phase == "cleanup":
            disposition = "completed" if cleanup_passed else "failed"
        elif phase == "replay" and failed_phase is None:
            disposition = "completed" if replay_completed else "pending"
        elif reached_failure:
            disposition = "not_reached"
        elif phase == failed_phase:
            disposition = "failed"
            reached_failure = True
        else:
            disposition = "completed"
        rows.append({"phase": phase, "disposition": disposition})
    return rows


def _cleanup_passed(receipt: dict[str, Any]) -> bool:
    observation = receipt["observation_payload"]
    return (
        receipt["status"] == "passed"
        and isinstance(observation, dict)
        and observation.get("cleanup_passed") is True
    )


def _failure_phase_from_receipts(receipts: list[dict[str, Any]]) -> str:
    failed = [row for row in receipts if row["status"] == "failed"]
    if not failed:
        raise ValidationError("task execution failure requires a failed provider receipt")
    if any(
        row["provider_kind"] == "agent_adapter"
        or (
            row["provider_kind"] == "environment_adapter"
            and row["operation_id"] == "execute_fixture"
        )
        for row in failed
    ):
        return "execute"
    if any(row["provider_kind"] == "event_normalizer" for row in failed):
        return "observe"
    failed_oracles = [row for row in failed if row["provider_kind"] == "oracle_provider"]
    if failed_oracles:
        passed_oracle_precedes_failure = any(
            row["provider_kind"] == "oracle_provider"
            and row["status"] == "passed"
            and row["sequence"] < failed_oracles[0]["sequence"]
            for row in receipts
        )
        return "replay" if passed_oracle_precedes_failure else "finalize"
    if any(row["operation_id"] == "cleanup_fixture" for row in failed):
        return "cleanup"
    raise ValidationError("failed provider receipt has no lifecycle phase")


def _failure_reasons_from_receipts(
    receipts: list[dict[str, Any]], cleanup_passed: bool
) -> list[str]:
    reasons = [
        row["failure"]["reason_code"]
        for row in receipts
        if row["status"] == "failed"
    ]
    if not cleanup_passed:
        reasons.append("cleanup_failed")
    return sorted(set(reasons))


def _build_terminal_failure(
    *,
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    registry: dict[str, Any],
    request: dict[str, Any],
    plan: dict[str, Any],
    receipts: list[dict[str, Any]],
    cleanup_receipt: dict[str, Any],
) -> dict[str, Any]:
    cleanup_ok = _cleanup_passed(cleanup_receipt)
    failed_phase = _failure_phase_from_receipts(receipts)
    reasons = _failure_reasons_from_receipts(receipts, cleanup_ok)
    efficiency = _build_efficiency(trial, [], receipts)
    value = {
        "schema_version": SCHEMA_EXECUTION_FAILURE,
        "result_family": RESULT_FAMILY,
        "pack": pack,
        "task": task,
        "trial": trial,
        "registry": registry,
        "request": request,
        "plan": plan,
        "native_receipts": receipts,
        "efficiency": efficiency,
        "failed_phase": failed_phase,
        "lifecycle": _lifecycle(
            failed_phase=failed_phase,
            replay_completed=False,
            cleanup_passed=cleanup_ok,
        ),
        "cleanup": {
            "attempted": True,
            "passed": cleanup_ok,
            "receipt_sha256": cleanup_receipt["receipt_sha256"],
        },
        "status": "rejected",
        "reason_codes": reasons,
        "retention_policy": request["retention_policy"],
        "failure_sha256": "0" * 64,
    }
    value["failure_sha256"] = self_hash(value, "failure_sha256")
    return validate_execution_failure(value)


def validate_execution_failure(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "result_family", "pack", "task", "trial", "registry",
        "request", "plan",
        "native_receipts", "efficiency", "failed_phase", "lifecycle", "cleanup",
        "status", "reason_codes", "retention_policy", "failure_sha256",
    }
    _exact(value, fields, "task execution failure")
    pack, task, trial = value["pack"], value["task"], value["trial"]
    registry, request, plan = value["registry"], value["request"], value["plan"]
    validate_execution_request(request, pack, task, trial, registry)
    if canonical_json(plan) != canonical_json(build_execution_plan(pack, task, trial, registry, request)):
        raise ValidationError("task execution failure plan mismatch")
    if value["schema_version"] != SCHEMA_EXECUTION_FAILURE or value["result_family"] != RESULT_FAMILY:
        raise ValidationError("unsupported task execution failure family")
    providers = {row["provider_id"]: row for row in registry["providers"]}
    receipts = value["native_receipts"]
    if not isinstance(receipts, list) or not receipts:
        raise ValidationError("task execution failure requires retained native receipts")
    for sequence, receipt in enumerate(receipts, 1):
        if receipt.get("sequence") != sequence or receipt.get("provider_id") not in providers:
            raise ValidationError("task execution failure receipt sequence mismatch")
        validate_native_receipt(receipt, trial, providers[receipt["provider_id"]])
    if not any(row["status"] == "failed" for row in receipts):
        raise ValidationError("task execution failure requires a failed provider receipt")
    validate_efficiency(value["efficiency"], trial)
    expected_efficiency = _build_efficiency(trial, [], receipts)
    if canonical_json(value["efficiency"]) != canonical_json(expected_efficiency):
        raise ValidationError("task execution failure efficiency differs from receipts")
    if value["failed_phase"] != _failure_phase_from_receipts(receipts):
        raise ValidationError("task execution failure phase differs from receipts")
    cleanup = _exact(
        value["cleanup"], {"attempted", "passed", "receipt_sha256"}, "failure cleanup"
    )
    if cleanup["attempted"] is not True or not isinstance(cleanup["passed"], bool):
        raise ValidationError("task execution failure cleanup state is invalid")
    cleanup_receipt = next(
        (row for row in receipts if row["receipt_sha256"] == cleanup["receipt_sha256"]), None
    )
    if cleanup_receipt is None or cleanup_receipt["operation_id"] != "cleanup_fixture":
        raise ValidationError("task execution failure cleanup receipt is missing")
    if cleanup["passed"] != _cleanup_passed(cleanup_receipt):
        raise ValidationError("task execution failure cleanup disposition mismatch")
    expected_lifecycle = _lifecycle(
        failed_phase=value["failed_phase"], replay_completed=False, cleanup_passed=cleanup["passed"]
    )
    if value["lifecycle"] != expected_lifecycle:
        raise ValidationError("task execution failure lifecycle mismatch")
    _sorted_unique_text(value["reason_codes"], "task execution failure reason_codes", allow_empty=False)
    expected_reasons = _failure_reasons_from_receipts(receipts, cleanup["passed"])
    if value["reason_codes"] != expected_reasons:
        raise ValidationError("task execution failure reasons differ from receipts")
    if value["status"] != "rejected" or value["retention_policy"] != request["retention_policy"]:
        raise ValidationError("task execution failure status or retention mismatch")
    _check_hash(value, "failure_sha256", "task execution failure")
    return value


def execute_task(
    pack: dict[str, Any],
    task: dict[str, Any],
    trial: dict[str, Any],
    registry: dict[str, Any],
    request: dict[str, Any],
    implementations: Mapping[str, ProviderImplementation],
) -> dict[str, Any]:
    """Execute one admitted chain, always retaining failures and cleanup."""
    plan = build_execution_plan(pack, task, trial, registry, request)
    admitted = _admit_implementations(registry, implementations)
    providers = _provider_by_kind(registry)
    filesystem_roots = [Path(row["path"]) for row in pack["writable_roots"]]
    receipts: list[dict[str, Any]] = []
    failed_phase: str | None = None
    agent: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    cleanup: dict[str, Any] | None = None
    try:
        agent, receipt = _invoke_provider(
            sequence=len(receipts) + 1,
            trial=trial,
            provider=providers["agent_adapter"],
            implementation=admitted[providers["agent_adapter"]["entrypoint_id"]],
            operation_id="propose_actions",
            payload={
                "provider_parameters": request["provider_parameters"],
                "task_sha256": task["task_sha256"],
                "documentation_snapshot_sha256": trial["documentation_snapshot_sha256"],
            },
            filesystem_roots=filesystem_roots,
        )
        receipts.append(receipt)
        if receipt["status"] == "failed":
            failed_phase = "execute"
        if failed_phase is None:
            environment, receipt = _invoke_provider(
                sequence=len(receipts) + 1,
                trial=trial,
                provider=providers["environment_adapter"],
                implementation=admitted[providers["environment_adapter"]["entrypoint_id"]],
                operation_id="execute_fixture",
                payload={
                    "operation": "execute_fixture",
                    "provider_parameters": request["provider_parameters"],
                    "agent_observation": agent,
                    "fixture_sha256": trial["fixture_sha256"],
                },
                filesystem_roots=filesystem_roots,
            )
            receipts.append(receipt)
            if receipt["status"] == "failed":
                failed_phase = "execute"
    finally:
        cleanup, cleanup_receipt = _invoke_provider(
            sequence=len(receipts) + 1,
            trial=trial,
            provider=providers["environment_adapter"],
            implementation=admitted[providers["environment_adapter"]["entrypoint_id"]],
            operation_id="cleanup_fixture",
            payload={
                "operation": "cleanup_fixture",
                "provider_parameters": request["provider_parameters"],
                "fixture_sha256": trial["fixture_sha256"],
                "environment_observation_sha256": _sha_json(environment),
            },
            filesystem_roots=filesystem_roots,
        )
        receipts.append(cleanup_receipt)
        if cleanup_receipt["status"] == "failed" and failed_phase is None:
            failed_phase = "cleanup"
    if failed_phase is not None:
        return _build_terminal_failure(
            pack=pack, task=task, trial=trial, registry=registry, request=request,
            plan=plan, receipts=receipts, cleanup_receipt=cleanup_receipt,
        )

    normalizer_input = {
        "environment_observation": environment,
        "cleanup_observation": cleanup,
        "event_contract": SCHEMA_TASK_EVENT,
    }
    normalized, receipt = _invoke_provider(
        sequence=len(receipts) + 1,
        trial=trial,
        provider=providers["event_normalizer"],
        implementation=admitted[providers["event_normalizer"]["entrypoint_id"]],
        operation_id="normalize_observations",
        payload=normalizer_input,
        filesystem_roots=filesystem_roots,
    )
    receipts.append(receipt)
    if receipt["status"] == "failed":
        return _build_terminal_failure(
            pack=pack, task=task, trial=trial, registry=registry, request=request,
            plan=plan, receipts=receipts, cleanup_receipt=cleanup_receipt,
        )
    receipt_map = {"agent": receipts[0], "environment": receipts[1], "cleanup": cleanup_receipt}
    events = _build_events(trial, normalized, receipt_map, providers["event_normalizer"])
    state = _environment_state(environment, events)
    oracle_input = _oracle_payload(task, events, state)
    oracle_observation, receipt = _invoke_provider(
        sequence=len(receipts) + 1,
        trial=trial,
        provider=providers["oracle_provider"],
        implementation=admitted[providers["oracle_provider"]["entrypoint_id"]],
        operation_id="evaluate_assertions",
        payload=oracle_input,
        filesystem_roots=filesystem_roots,
    )
    receipts.append(receipt)
    if receipt["status"] == "failed":
        return _build_terminal_failure(
            pack=pack, task=task, trial=trial, registry=registry, request=request,
            plan=plan, receipts=receipts, cleanup_receipt=cleanup_receipt,
        )
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
        "replay_receipt": None,
        "lifecycle": _lifecycle(
            failed_phase=None, replay_completed=False, cleanup_passed=score["cleanup_passed"]
        ),
        "status": status,
        "reason_codes": reasons,
        "retention_policy": request["retention_policy"],
        "bundle_sha256": "0" * 64,
    }
    bundle["bundle_sha256"] = self_hash(bundle, "bundle_sha256")
    _validate_execution_bundle(bundle, replay_completed=False)
    verification = _replay_task_execution(bundle, admitted)
    if verification["status"] == "failed":
        receipts.append(verification["receipt"])
        return _build_terminal_failure(
            pack=pack, task=task, trial=trial, registry=registry, request=request,
            plan=plan, receipts=receipts, cleanup_receipt=cleanup_receipt,
        )
    bundle["replay_receipt"] = verification["receipt"]
    bundle["lifecycle"] = _lifecycle(
        failed_phase=None, replay_completed=True, cleanup_passed=score["cleanup_passed"]
    )
    bundle["bundle_sha256"] = self_hash(bundle, "bundle_sha256")
    validate_execution_bundle(bundle)
    return bundle


def _validate_execution_bundle(
    bundle: dict[str, Any], *, replay_completed: bool
) -> dict[str, Any]:
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
        "replay_receipt",
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
    if any(row["status"] != "passed" for row in receipts):
        raise ValidationError("completed task execution bundle cannot contain failed receipts")
    replay_receipt = bundle["replay_receipt"]
    if replay_completed:
        if not isinstance(replay_receipt, dict):
            raise ValidationError("completed task execution bundle requires a replay receipt")
        oracle_provider = next(
            row for row in registry["providers"] if row["provider_kind"] == "oracle_provider"
        )
        if replay_receipt.get("sequence") != len(receipts) + 1:
            raise ValidationError("task replay receipt sequence mismatch")
        validate_native_receipt(replay_receipt, trial, oracle_provider)
        if replay_receipt["status"] != "passed":
            raise ValidationError("completed task execution replay receipt must pass")
    elif replay_receipt is not None:
        raise ValidationError("pending task execution cannot predeclare a replay receipt")
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
    expected_lifecycle = _lifecycle(
        failed_phase=None,
        replay_completed=replay_completed,
        cleanup_passed=bundle["score"]["cleanup_passed"],
    )
    if bundle["lifecycle"] != expected_lifecycle:
        raise ValidationError("task execution lifecycle is incomplete or out of order")
    expected_status, expected_reasons = _status_and_reasons(bundle["score"])
    if bundle["status"] != expected_status or bundle["reason_codes"] != expected_reasons:
        raise ValidationError("task execution status does not match its score")
    if bundle["retention_policy"] != request["retention_policy"]:
        raise ValidationError("task execution retention policy mismatch")
    _check_hash(bundle, "bundle_sha256", "task execution bundle")
    return bundle


def validate_execution_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return _validate_execution_bundle(bundle, replay_completed=True)


def _replay_task_execution(
    bundle: dict[str, Any],
    admitted: Mapping[str, AdmittedProviderImplementation],
) -> dict[str, Any]:
    registry = bundle["registry"]
    providers = _provider_by_kind(registry)
    oracle_provider = providers["oracle_provider"]
    oracle_receipt = next(
        row for row in bundle["native_receipts"] if row["provider_kind"] == "oracle_provider"
    )
    environment_receipt = next(
        row
        for row in bundle["native_receipts"]
        if row["provider_kind"] == "environment_adapter"
        and row["operation_id"] == "execute_fixture"
    )
    state = _environment_state(environment_receipt["observation_payload"], bundle["events"])
    expected_input = _oracle_payload(bundle["task"], bundle["events"], state)
    if canonical_json(oracle_receipt["request_payload"]) != canonical_json(expected_input):
        raise ValidationError("retained oracle input does not match retained events")
    observed, replay_receipt = _invoke_provider(
        sequence=len(bundle["native_receipts"]) + 1,
        trial=bundle["trial"],
        provider=oracle_provider,
        implementation=admitted[oracle_provider["entrypoint_id"]],
        operation_id="evaluate_assertions",
        payload=expected_input,
        filesystem_roots=[Path(row["path"]) for row in bundle["pack"]["writable_roots"]],
    )
    if replay_receipt["status"] == "failed":
        return {"status": "failed", "receipt": replay_receipt}
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
        "oracle_replay_receipt_sha256": replay_receipt["receipt_sha256"],
        "oracle_replay_receipt": replay_receipt,
        "verification_sha256": "0" * 64,
    }
    value["verification_sha256"] = self_hash(value, "verification_sha256")
    return {"status": "passed", "verification": validate_replay_verification(value), "receipt": replay_receipt}


def replay_task_execution(
    bundle: dict[str, Any],
    implementations: Mapping[str, ProviderImplementation],
) -> dict[str, Any]:
    """Replay retained evidence, invoking only the admitted deterministic oracle."""
    validate_execution_bundle(bundle)
    admitted = _admit_implementations(bundle["registry"], implementations)
    result = _replay_task_execution(bundle, admitted)
    if result["status"] == "failed":
        receipt = result["receipt"]
        raise ValidationError(
            f"oracle replay provider failed; receipt_sha256={receipt['receipt_sha256']}"
        )
    return result["verification"]


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
        "oracle_replay_receipt_sha256",
        "oracle_replay_receipt",
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
        "oracle_replay_receipt_sha256",
        "verification_sha256",
    ):
        _sha(value[field], field)
    receipt = value["oracle_replay_receipt"]
    if not isinstance(receipt, dict) or receipt.get("receipt_sha256") != value["oracle_replay_receipt_sha256"]:
        raise ValidationError("task replay verification receipt mismatch")
    if (
        receipt.get("schema_version") != SCHEMA_NATIVE_RECEIPT
        or receipt.get("provider_kind") != "oracle_provider"
        or receipt.get("provider_manifest_sha256")
        != value["oracle_provider_manifest_sha256"]
        or receipt.get("operation_id") != "evaluate_assertions"
        or receipt.get("status") != "passed"
        or receipt.get("exit_status") != 0
        or receipt.get("failure") is not None
    ):
        raise ValidationError("task replay verification receipt is not a passed oracle invocation")
    if receipt.get("request_sha256") != _sha_json(receipt.get("request_payload")):
        raise ValidationError("task replay verification request hash mismatch")
    if receipt.get("observation_sha256") != _sha_json(receipt.get("observation_payload")):
        raise ValidationError("task replay verification observation hash mismatch")
    _check_hash(receipt, "receipt_sha256", "task replay verification receipt")
    if (
        value["provider_invocations"] != 1
        or value["environment_invocations"] != 0
        or value["service_executions"] != 0
        or value["byte_stable"] is not True
    ):
        raise ValidationError("task replay verification execution boundary mismatch")
    _check_hash(value, "verification_sha256", "task replay verification")
    return value


def _validate_terminal_record(record: dict[str, Any]) -> str:
    if record.get("schema_version") == SCHEMA_EXECUTION_BUNDLE:
        validate_execution_bundle(record)
        return "completed_bundle"
    if record.get("schema_version") == SCHEMA_EXECUTION_FAILURE:
        validate_execution_failure(record)
        return "terminal_failure"
    raise ValidationError("unsupported task execution terminal record")


def build_execution_summary(
    record: dict[str, Any], artifacts: list[dict[str, Any]]
) -> dict[str, Any]:
    record_kind = _validate_terminal_record(record)
    if artifacts != sorted(artifacts, key=lambda row: row["path"]):
        raise ValidationError("task execution artifact inventory must be sorted")
    for row in artifacts:
        _exact(row, {"path", "sha256", "size_bytes"}, "task execution artifact")
        _text(row["path"], "task execution artifact path")
        _sha(row["sha256"], "task execution artifact sha256")
        _integer(row["size_bytes"], "task execution artifact size")
    completed = record_kind == "completed_bundle"
    terminal_sha256 = record["bundle_sha256"] if completed else record["failure_sha256"]
    value = {
        "schema_version": SCHEMA_EXECUTION_SUMMARY,
        "result_family": RESULT_FAMILY,
        "record_kind": record_kind,
        "request_id": record["request"]["request_id"],
        "trial_sha256": record["trial"]["trial_sha256"],
        "status": record["status"],
        "task_success": record["score"]["task_success"] if completed else False,
        "invalid_trial": record["score"]["invalid_trial"] if completed else True,
        "reason_codes": record["reason_codes"],
        "terminal_record_sha256": terminal_sha256,
        "score_sha256": record["score"]["score_sha256"] if completed else None,
        "replay_sha256": record["replay"]["replay_sha256"] if completed else None,
        "detailed_artifacts": artifacts,
        "summary_sha256": "0" * 64,
    }
    value["summary_sha256"] = self_hash(value, "summary_sha256")
    _check_hash(value, "summary_sha256", "task execution summary")
    return value


def render_task_report(record: dict[str, Any]) -> str:
    record_kind = _validate_terminal_record(record)
    if record_kind == "terminal_failure":
        return "\n".join(
            [
                "# Deterministic task execution failure report",
                "",
                f"- Request: `{record['request']['request_id']}`",
                f"- Status: `{record['status']}`",
                f"- Failed phase: `{record['failed_phase']}`",
                f"- Cleanup attempted: `{str(record['cleanup']['attempted']).lower()}`",
                f"- Cleanup passed: `{str(record['cleanup']['passed']).lower()}`",
                f"- Retained receipts: `{len(record['native_receipts'])}`",
                f"- Failure SHA-256: `{record['failure_sha256']}`",
                "",
                "Provider failure evidence and cleanup evidence are retained by RA.",
                "",
            ]
        )
    score = record["score"]
    return "\n".join(
        [
            "# Deterministic task execution report",
            "",
            f"- Request: `{record['request']['request_id']}`",
            f"- Status: `{record['status']}`",
            f"- Task success: `{str(score['task_success']).lower()}`",
            f"- Invalid trial: `{str(score['invalid_trial']).lower()}`",
            f"- Critical safety violation: `{str(score['critical_safety_violation']).lower()}`",
            f"- Cleanup passed: `{str(score['cleanup_passed']).lower()}`",
            f"- Requirement completion: `{score['requirement_completion']}`",
            f"- Wall time (synthetic ms): `{score['efficiency']['wall_time_ms']}`",
            f"- Bundle SHA-256: `{record['bundle_sha256']}`",
            "",
            "No agent success claim is used as evaluation evidence.",
            "",
        ]
    )


def publish_task_execution(output_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Publish one completed bundle or retained failure to a fresh owned root."""
    record_kind = _validate_terminal_record(record)
    if output_dir.exists():
        raise ValidationError("task execution output directory must not already exist")
    output_dir.mkdir(parents=True)
    if record_kind == "completed_bundle":
        artifacts: dict[str, Any] = {
            "execution_bundle.v2.json": record,
            "native_receipts.v2.json": record["native_receipts"],
            "task_events.v2.json": record["events"],
            "oracle_results.v2.json": record["oracle_results"],
            "task_score.v2.json": record["score"],
            "task_replay.v2.json": record["replay"],
        }
    else:
        artifacts = {
            "execution_failure.v1.json": record,
            "native_receipts.v2.json": record["native_receipts"],
            "task_efficiency.v2.json": record["efficiency"],
            "task_lifecycle.v1.json": record["lifecycle"],
        }
    inventory: list[dict[str, Any]] = []
    for name, value in artifacts.items():
        data = canonical_json(value) + b"\n"
        (output_dir / name).write_bytes(data)
        inventory.append({"path": name, "sha256": _sha_bytes(data), "size_bytes": len(data)})
    report = render_task_report(record).encode("utf-8")
    (output_dir / "task_report.v2.md").write_bytes(report)
    inventory.append(
        {"path": "task_report.v2.md", "sha256": _sha_bytes(report), "size_bytes": len(report)}
    )
    inventory.sort(key=lambda row: row["path"])
    summary = build_execution_summary(record, inventory)
    write_canonical_json(output_dir / "summary.v2.json", summary)
    return summary


def write_task_report(output_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    record_kind = _validate_terminal_record(record)
    if output_dir.exists():
        raise ValidationError("task report output directory must not already exist")
    output_dir.mkdir(parents=True)
    report = render_task_report(record)
    report_bytes = report.encode("utf-8")
    (output_dir / "task_report.v2.md").write_bytes(report_bytes)
    terminal_sha256 = record["bundle_sha256"] if record_kind == "completed_bundle" else record["failure_sha256"]
    report_record = {
        "schema_version": "retrieval_arena.task_report.v2",
        "record_kind": record_kind,
        "terminal_record_sha256": terminal_sha256,
        "score_sha256": record["score"]["score_sha256"] if record_kind == "completed_bundle" else None,
        "report_sha256": _sha_bytes(report_bytes),
        "report_path": "task_report.v2.md",
        "record_sha256": "0" * 64,
    }
    report_record["record_sha256"] = self_hash(report_record, "record_sha256")
    write_canonical_json(output_dir / "task_report_record.v2.json", report_record)
    return report_record


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
