from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


SOURCE_ROOT = Path("/workspace/source")
COMMANDS = {
    "focused": (
        "unit_tests.test_dual_profile",
        "unit_tests.test_review_packets",
        "unit_tests.test_profile_evaluation",
        "unit_tests.test_verification_container_contract",
        "unit_tests.test_profile_operator_admission",
    ),
    "all": None,
    "evaluate-profile": (),
}


def _suite(command: str) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    names = COMMANDS[command]
    if names is None:
        return loader.discover(str(SOURCE_ROOT / "unit_tests"), pattern="test_*.py")
    return loader.loadTestsFromNames(names)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in COMMANDS:
        allowed = "|".join(sorted(COMMANDS))
        print(f"usage: verify.py <{allowed}>", file=sys.stderr)
        return 2
    if not SOURCE_ROOT.is_dir():
        print("source-root-missing", file=sys.stderr)
        return 2
    os.chdir(SOURCE_ROOT)
    sys.path.insert(0, str(SOURCE_ROOT))
    if argv[1] == "evaluate-profile":
        from evaluate_profile import main as evaluate_profile_main
        return evaluate_profile_main()
    scratch = SOURCE_ROOT / ".tmp_unit_tests"
    html_scratch = SOURCE_ROOT / "unit_tests" / "_tmp_html_report"
    if not scratch.is_dir() or not html_scratch.is_dir():
        print("declared-test-scratch-missing", file=sys.stderr)
        return 2

    result = unittest.TextTestRunner(verbosity=2).run(_suite(argv[1]))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
