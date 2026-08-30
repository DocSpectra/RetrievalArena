"""Hermetic provider artifact for PRE-S019q-34B qualification.

This file is executed by the RA provider runtime with a restricted builtins
table. The artifact performs no imports, filesystem access, process execution,
networking, or dynamic loading.
"""


def _observation(
    event_class,
    target,
    arguments,
    observation,
    *,
    source="environment",
    parent=None,
    correlation="restart-web",
    exit_status=0,
):
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


def restart_agent(payload):
    parameters = payload["provider_parameters"]
    if parameters["scenario"] == "provider_exception":
        raise ValueError("synthetic-agent-provider-exception")
    native_command = {
        "command_a": "restart-service web",
        "command_b": "service web reload",
    }[parameters["native_variant"]]
    return {
        "actions": [
            {
                "action_id": "restart",
                "semantic_action": "service_restart",
                "target": "web",
                "native_command": native_command,
            },
            {
                "action_id": "verify",
                "semantic_action": "http_get",
                "target": "/",
                "native_command": "fixture-http-get /",
            },
        ],
        "agent_success_claim": "untrusted",
    }


def restart_environment(payload):
    parameters = payload["provider_parameters"]
    scenario = parameters["scenario"]
    operation = payload["operation"]
    cleanup_passed = scenario != "cleanup_failure"
    if operation == "cleanup_fixture":
        if scenario == "cleanup_provider_exception":
            raise ValueError("synthetic-cleanup-provider-exception")
        return {
            "observations": [
                _observation(
                    "cleanup",
                    "scratch",
                    {"operation": "restore"},
                    {"passed": cleanup_passed},
                    source="cleanup",
                    parent=1,
                    exit_status=0 if cleanup_passed else 1,
                )
            ],
            "cleanup_passed": cleanup_passed,
        }
    if operation != "execute_fixture":
        raise ValueError("unsupported synthetic environment operation")

    actions = payload["agent_observation"]["actions"]
    native_command = actions[0]["native_command"]
    restarted = scenario != "skipped_restart"
    final_generation = "generation-2" if restarted else "generation-1"
    response_generation = "generation-1" if scenario == "stale_response" else final_generation
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
        {"exit_status": 0, "native_command": native_command},
        parent=1,
    )
    restart_rows = (
        [
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
        ]
        if restarted
        else []
    )
    request = _observation(
        "http_request",
        "/",
        {"method": "GET", "path": "/"},
        {"issued": True},
        parent=1,
    )
    response_observation = {
        "status": 200,
        "marker": marker,
        "served_by_generation": response_generation,
        "mock_substitution": mock_used,
    }
    response = _observation(
        "http_response",
        "/",
        {"method": "GET", "path": "/"},
        response_observation,
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
    observations = (
        [tool, request, command, *restart_rows, response, test, publication]
        if scenario == "request_before_restart"
        else [tool, command, *restart_rows, request, response, test, publication]
    )
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


def restart_normalizer(payload):
    observations = [
        *payload["environment_observation"]["observations"],
        *payload["cleanup_observation"]["observations"],
    ]
    required = {
        "event_class",
        "target",
        "normalized_arguments",
        "observation",
        "source",
        "parent",
        "correlation",
        "exit_status",
    }
    normalized = []
    for row in observations:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("synthetic native observation fields mismatch")
        normalized.append(dict(row))
    return {"normalized_events": normalized}


def _by_class(events, event_class):
    return [row for row in events if row["event_class"] == event_class]


def restart_oracle(payload):
    events = payload["events"]
    state = payload["environment_state"]
    service = _by_class(events, "service_lifecycle")
    requests = _by_class(events, "http_request")
    responses = _by_class(events, "http_response")
    files = _by_class(events, "filesystem_mutation")
    tests = _by_class(events, "test_execution")
    cleanup = _by_class(events, "cleanup")

    changed = bool(service) and state["final_generation"] != state["initial_generation"]
    ordered = (
        bool(service and requests and responses)
        and service[0]["sequence"] < requests[0]["sequence"] < responses[0]["sequence"]
    )
    http_ok = state["response_observation"] == {
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
    return {
        "verdicts": {
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
    }
