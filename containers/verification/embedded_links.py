"""Offline immutable operator entry point."""
from pathlib import Path
import sys
sys.path.insert(0,'/workspace/source')
from retrieval_arena.embedded_link_audit import run

def main():
    run(Path('/workspace/input'),Path('/workspace/output'))
    return 0
