"""Admitted negative provider used to prove runtime capability enforcement."""


def restart_agent(payload):
    probe = payload["provider_parameters"]["probe"]
    if probe == "filesystem":
        open("forbidden-provider-read.txt", "r")
    if probe == "process":
        system_call("forbidden-provider-process")
    if probe == "network":
        open_socket()
    if probe == "exception":
        raise ValueError("synthetic-provider-failure")
    if probe == "malformed":
        return ["not", "an", "object"]
    return {"actions": [], "agent_success_claim": "untrusted"}
