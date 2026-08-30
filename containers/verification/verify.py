from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


SOURCE_ROOT = Path("/workspace/source")
OPERATOR_ROOT = Path(__file__).resolve().parent
COMMANDS = {
    "focused": (
        "unit_tests.test_dual_profile",
        "unit_tests.test_review_packets",
        "unit_tests.test_profile_evaluation",
        "unit_tests.test_verification_container_contract",
        "unit_tests.test_profile_operator_admission",
        "unit_tests.test_verification_witness",
        "unit_tests.test_review_packet_operator",
        "unit_tests.test_task_completion",
        "unit_tests.test_task_execution",
    ),
    "all": None,
    "evaluate-profile": (),
    "review-packet": (),
}


def _suite(command: str) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    names = COMMANDS[command]
    if names is None:
        return loader.discover(str(SOURCE_ROOT / "unit_tests"), pattern="test_*.py")
    return loader.loadTestsFromNames(names)


def _case_ids(suite: unittest.TestSuite) -> list[str]:
    """Return leaf IDs from the exact suite instance supplied to the runner."""
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(_case_ids(item))
        else:
            result.append(item.id())
    if len(result) != len(set(result)):
        raise RuntimeError("focused-suite-case-id-duplicate")
    return result


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
        sys.path.insert(0, str(OPERATOR_ROOT))
        from evaluate_profile import main as evaluate_profile_main
        return evaluate_profile_main()
    if argv[1] == "review-packet":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from review_packet import main as review_packet_main
        return review_packet_main()
    scratch = SOURCE_ROOT / ".tmp_unit_tests"
    html_scratch = SOURCE_ROOT / "unit_tests" / "_tmp_html_report"
    if not scratch.is_dir() or not html_scratch.is_dir():
        print("declared-test-scratch-missing", file=sys.stderr)
        return 2

    suite = _suite(argv[1])
    case_ids = _case_ids(suite) if argv[1] == "focused" else []
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if argv[1] == "focused" and result.wasSuccessful():
        # Keep the ordinary unittest exit contract while emitting one canonical
        # machine-readable witness on stdout.  The runner writes its prose to
        # stderr, so consumers never need to parse it.
        from retrieval_arena.verification_witness import canonical, witness_from_environment

        print(canonical(witness_from_environment(
            focused_tests_run=result.testsRun,
            focused_test_case_ids=case_ids,
        )).decode("utf-8"))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
