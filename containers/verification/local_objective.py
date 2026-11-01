"""Offline immutable local-objective entry point."""
from pathlib import Path
from retrieval_arena.local_objective_audit import run

def main():
    run(Path('/workspace/input'), Path('/workspace/output'))
    return 0
