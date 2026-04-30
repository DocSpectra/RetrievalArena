from __future__ import annotations

import subprocess
from pathlib import Path

from .config import TestConfig
from .errors import RetrievalArenaError, ValidationError


def _run_command(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        raise RetrievalArenaError("Command failed ({}):\n{}".format(" ".join(command), completed.stdout))


def build_image(test: TestConfig) -> None:
    if not test.build_context:
        return
    if not test.build_context.exists():
        raise ValidationError(f"Docker build_context not found for {test.name}: {test.build_context}")
    _run_command(["docker", "build", "-t", test.image, str(test.build_context)])


def run_container(test: TestConfig, input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _run_command(["docker", "run", "--rm", "-v", f"{input_dir.resolve()}:/input:ro", "-v", f"{output_dir.resolve()}:/output", test.image])