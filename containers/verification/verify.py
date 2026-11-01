from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


SOURCE_ROOT = Path("/workspace/source")
OPERATOR_ROOT = Path(__file__).resolve().parent
COMMANDS = {
    "focused": (
        "unit_tests.test_block_phase",
        "unit_tests.test_embedded_selection_v2",
        "unit_tests.test_embedded_token_admission",
        "unit_tests.test_embedded_model_run",
        "unit_tests.test_embedded_refinement",
        "unit_tests.test_embedded_link_selection",
        "unit_tests.test_local_objective",
        "unit_tests.test_embedded_links",
        "unit_tests.test_ragas_scoring",
        "unit_tests.test_model_providers",
        "unit_tests.test_dual_profile",
        "unit_tests.test_review_packets",
        "unit_tests.test_profile_evaluation",
        "unit_tests.test_verification_container_contract",
        "unit_tests.test_profile_operator_admission",
        "unit_tests.test_verification_witness",
        "unit_tests.test_review_packet_operator",
        "unit_tests.test_task_completion",
        "unit_tests.test_task_execution",
        "unit_tests.test_remote_acquisition",
        "unit_tests.test_corpus_import",
        "unit_tests.test_candidate_retrieval",
        "unit_tests.test_rag_lexical_topk_runner",
        "unit_tests.test_rag_graph_ranked_expand_runner",
        "unit_tests.test_rag_llm_guided_search_runner",
        "unit_tests.test_rag_repo_search_agent_runner",
        "unit_tests.test_s057_reader_v2",
        "unit_tests.test_reachability_surface",
        "unit_tests.test_docstyle_measurement",
        "unit_tests.test_repo_lexicon",
        "unit_tests.test_pooled_support",
        "unit_tests.test_structural_target_v2",
        "unit_tests.test_magnetic_spectrum",
        "unit_tests.test_magnetic_attribution",
        "unit_tests.test_cycle_sampling",
        "unit_tests.test_phase_navigation",
        "unit_tests.test_support_access",
        "unit_tests.test_support_access_corpora",
        "unit_tests.test_page_representations",
        "unit_tests.test_page_roles",
        "unit_tests.test_complementary_context",
        "unit_tests.test_trace_provenance",
        "unit_tests.test_answer_element_comparison",
    ),
    "all": None,
    "evaluate-profile": (),
    "score-bertscore": (),
    "review-packet": (),
    "candidate-retrieve-prepare": (),
    "candidate-retrieve-finalize": (),
    "measure-docstyle": (),
    "measure-graph-spectra": (),
    "measure-magnetic-attribution": (),
    "audit-trace-provenance": (),
    "compare-answer-elements": (),
    "measure-phase-navigation": (),
    "score-phase-navigation": (),
    "compare-support-access": (),
    "compare-support-access-corpora": (),
    "export-page-representations": (),
    "page-roles": (),
    "compare-block-phase": (),
    "demonstrate-embedded-v2": (),
    "refine-embedded-v2": (),
    "qualify-local-objective": (),
    "audit-embedded-links": (),
    "generate-complementary-context": (),
    "evaluate-complementary-context": (),
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
    os.environ["RA_NETWORK_DISABLED"] = "1"
    sys.path.insert(0, str(SOURCE_ROOT))
    if argv[1] in {"evaluate-profile", "score-bertscore"}:
        sys.path.insert(0, str(OPERATOR_ROOT))
        from evaluate_profile import main as evaluate_profile_main
        return evaluate_profile_main(argv[1])
    if argv[1] == "review-packet":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from review_packet import main as review_packet_main
        return review_packet_main()
    if argv[1] in {"candidate-retrieve-prepare", "candidate-retrieve-finalize"}:
        sys.path.insert(0, str(OPERATOR_ROOT))
        from candidate_retrieval import main as candidate_retrieval_main
        return candidate_retrieval_main(argv[1])
    if argv[1] in {"generate-complementary-context", "evaluate-complementary-context"}:
        sys.path.insert(0, str(OPERATOR_ROOT))
        from complementary_context import main as complementary_main
        return complementary_main(argv[1])
    if argv[1] == "demonstrate-embedded-v2":
        from retrieval_arena.embedded_demonstrator import run
        run(Path("/workspace/input"), Path("/workspace/output"))
        return 0
    if argv[1] == "refine-embedded-v2":
        from retrieval_arena.embedded_refinement import run
        run(Path('/workspace/input'),Path('/workspace/output'))
        return 0
    if argv[1] == "compare-block-phase":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from block_phase import main as block_phase_main
        return block_phase_main()
    if argv[1] == "qualify-local-objective":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from local_objective import main as local_objective_main
        return local_objective_main()
    if argv[1] == "audit-embedded-links":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from embedded_links import main as embedded_links_main
        return embedded_links_main()
    if argv[1] == "page-roles":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from page_roles import main as page_roles_main
        return page_roles_main()
    if argv[1] == "export-page-representations":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from page_representations import main as page_representations_main
        return page_representations_main()
    if argv[1] == "measure-docstyle":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from docstyle_measurement import main as docstyle_measurement_main
        return docstyle_measurement_main()
    if argv[1] == "measure-graph-spectra":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from graph_spectra import main as graph_spectra_main
        return graph_spectra_main()
    if argv[1] == "measure-magnetic-attribution":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from magnetic_attribution import main as magnetic_attribution_main
        return magnetic_attribution_main()
    if argv[1] == "audit-trace-provenance":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from trace_provenance import main as trace_provenance_main
        return trace_provenance_main()
    if argv[1] == "compare-answer-elements":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from answer_element_comparison import main as answer_element_main
        return answer_element_main()
    if argv[1] == "compare-support-access":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from support_access import main as support_access_main
        return support_access_main()
    if argv[1] == "compare-support-access-corpora":
        sys.path.insert(0, str(OPERATOR_ROOT))
        from support_access_corpora import main as corpus_support_main
        return corpus_support_main()
    if argv[1] in {"measure-phase-navigation", "score-phase-navigation"}:
        sys.path.insert(0, str(OPERATOR_ROOT))
        from phase_navigation import main as phase_navigation_main
        return phase_navigation_main(argv[1])
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
