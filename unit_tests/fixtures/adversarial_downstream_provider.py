"""Negative downstream providers for terminal receipt and cleanup tests."""


def restart_normalizer(payload):
    raise ValueError("synthetic-normalizer-provider-exception")


def restart_oracle(payload):
    raise ValueError("synthetic-oracle-provider-exception")
