"""Negative environment provider proving declared filesystem scope is bounded."""


def restart_environment(payload):
    if payload["operation"] == "cleanup_fixture":
        return {"observations": [], "cleanup_passed": True}
    open("outside-declared-root.txt", "r")
