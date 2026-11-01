from pathlib import Path
from retrieval_arena.block_phase_audit import run

def main():
    run(Path('/workspace/input'),Path('/workspace/output'))
    return 0
