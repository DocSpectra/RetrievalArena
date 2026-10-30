from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VerificationContainerContractTests(unittest.TestCase):
    def test_build_network_is_disabled_and_provisioned_identity_is_checked(self):
        for relative in ("scripts/build-verification.ps1", "scripts/build-verification.sh"):
            text = self.text(relative)
            for token in ("--network=none", "verification-provisioned-image-mutable",
                          "verification-provisioned-image-mismatch", "PROVISIONED=true"):
                self.assertIn(token, text)
        self.assertIn("m.version(n)==v", self.text("containers/verification/Dockerfile"))

    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_base_and_python_dependencies_are_immutable(self):
        dockerfile = self.text("containers/verification/Dockerfile")
        lock = self.text("containers/verification/requirements.lock")
        self.assertRegex(dockerfile, r"mcr\.microsoft\.com/.+@sha256:[0-9a-f]{64}")
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("--only-binary=:all:", dockerfile)
        requirements = [line for line in lock.splitlines() if line and not line.startswith("#")]
        self.assertGreaterEqual(len(requirements), 4)
        for requirement in requirements:
            self.assertRegex(requirement, r"^[a-z0-9-]+==[^ ]+ --hash=sha256:[0-9a-f]{64}$")

    def test_runtime_adapters_enforce_same_isolation(self):
        for relative in ("scripts/run-verification.ps1", "scripts/run-verification.sh"):
            adapter = self.text(relative)
            for required in (
                "--network",
                "none",
                "--read-only",
                "no-new-privileges",
                "--cap-drop",
                "readonly",
                "PYTHONHASHSEED=0",
                "verification-receipt-self-hash-invalid",
                "verification-image-label-mismatch",
                "verification-mounted-source-identity-mismatch",
                "--memory",
                "--cpus",
                "TMPDIR=/tmp",
            ):
                self.assertIn(required, adapter)
            self.assertIn("sha256:", adapter)
            self.assertIn("linux/amd64", adapter)

    def test_focused_adapter_branch_has_operator_resource_bounds(self):
        powershell = self.text("scripts/run-verification.ps1")
        shell = self.text("scripts/run-verification.sh")
        # These occur in the focused/all branch after its isolated source mount.
        self.assertGreaterEqual(powershell.count("'--pids-limit','512','--memory','2g','--cpus','2'"), 2)
        self.assertIn("--memory 2g --cpus 2", shell)
        self.assertIn('operator_memory=2g', shell)
        self.assertIn('operator_memory=8g', shell)
        self.assertIn('operator_cpus=2', shell)
        self.assertIn('[ "$command_name" != measure-phase-navigation ] || operator_cpus=4', shell)
        self.assertIn('--memory "$operator_memory" --cpus "$operator_cpus"', shell)
        self.assertIn("if ($Command -in @('measure-phase-navigation','compare-support-access','compare-support-access-corpora')) { '8g' }", powershell)
        self.assertIn('[ "$command_name" != compare-support-access ] && [ "$command_name" != compare-support-access-corpora ] || operator_memory=8g', shell)
        self.assertIn('[ "$command_name" != compare-support-access ] && [ "$command_name" != compare-support-access-corpora ] || operator_cpus=4', shell)
        self.assertIn("$arguments[$cpuIndex + 1] = '4'", powershell)

    def test_posix_adapters_cover_linux_and_macos_hash_tools_and_pretty_receipts(self):
        build = self.text("scripts/build-verification.sh")
        run = self.text("scripts/run-verification.sh")
        self.assertIn("sha256sum", build)
        self.assertIn("shasum -a 256", build)
        self.assertIn("[[:space:]]*:[[:space:]]*", run)

    def test_command_registry_is_closed(self):
        runner = self.text("containers/verification/verify.py")
        self.assertIn('\"focused\":', runner)
        self.assertIn('\"all\":', runner)
        self.assertIn('\"evaluate-profile\":', runner)
        self.assertIn('\"review-packet\":', runner)
        self.assertIn('\"candidate-retrieve-prepare\":', runner)
        self.assertIn('\"candidate-retrieve-finalize\":', runner)
        self.assertIn('\"measure-docstyle\":', runner)
        self.assertIn('\"measure-graph-spectra\":', runner)
        self.assertIn('\"measure-magnetic-attribution\":', runner)
        self.assertIn('\"audit-trace-provenance\":', runner)
        self.assertIn('\"compare-answer-elements\":', runner)
        self.assertIn("argv[1] not in COMMANDS", runner)
        self.assertNotIn("subprocess", runner)
        self.assertIn("sys.path.insert(0, str(OPERATOR_ROOT))", runner)
        self.assertIn("focused_verification_witness", self.text("retrieval_arena/verification_witness.py"))

    def test_operator_module_is_present_in_the_immutable_build_context(self):
        dockerignore = self.text(".dockerignore")
        dockerfile = self.text("containers/verification/Dockerfile")
        operator = "containers/verification/evaluate_profile.py"
        self.assertIn(f"!{operator}", dockerignore)
        self.assertIn(f"COPY {operator} /opt/retrieval-arena/evaluate_profile.py", dockerfile)
        review_operator = "containers/verification/review_packet.py"
        self.assertIn(f"!{review_operator}", dockerignore)
        self.assertIn(f"COPY {review_operator} /opt/retrieval-arena/review_packet.py", dockerfile)
        candidate_operator = "containers/verification/candidate_retrieval.py"
        self.assertIn(f"!{candidate_operator}", dockerignore)
        self.assertIn(f"COPY {candidate_operator} /opt/retrieval-arena/candidate_retrieval.py", dockerfile)
        docstyle_operator = "containers/verification/docstyle_measurement.py"
        self.assertIn(f"!{docstyle_operator}", dockerignore)
        self.assertIn(f"COPY {docstyle_operator} /opt/retrieval-arena/docstyle_measurement.py", dockerfile)
        spectra_operator = "containers/verification/graph_spectra.py"
        self.assertIn(f"!{spectra_operator}", dockerignore)
        self.assertIn(f"COPY {spectra_operator} /opt/retrieval-arena/graph_spectra.py", dockerfile)
        attribution_operator = "containers/verification/magnetic_attribution.py"
        self.assertIn(f"!{attribution_operator}", dockerignore)
        self.assertIn(f"COPY {attribution_operator} /opt/retrieval-arena/magnetic_attribution.py", dockerfile)
        provenance_operator = "containers/verification/trace_provenance.py"
        self.assertIn(f"!{provenance_operator}", dockerignore)
        self.assertIn(f"COPY {provenance_operator} /opt/retrieval-arena/trace_provenance.py", dockerfile)
        element_operator = "containers/verification/answer_element_comparison.py"
        self.assertIn(f"!{element_operator}", dockerignore)
        self.assertIn(f"COPY {element_operator} /opt/retrieval-arena/answer_element_comparison.py", dockerfile)

    def test_review_packet_operator_is_atomic_and_receipted(self):
        operator = self.text("containers/verification/review_packet.py")
        for required in (
            "retrieval_arena.review_packet_operator_request.v1",
            "retrieval_arena.review_packet_operator_receipt.v1",
            "review-packet-input-inventory-mismatch",
            "review-packet-output-not-fresh",
            "receipt_sha256",
            "output_inventory_sha256",
            "partial_outputs_published",
            "STAGING",
        ):
            self.assertIn(required, operator)

    def test_registered_profile_operator_is_mount_bound_and_self_hashing(self):
        operator = self.text("containers/verification/evaluate_profile.py")
        dockerfile = self.text("containers/verification/Dockerfile")
        for required in (
            "retrieval_arena.profile_evaluation_request.v1",
            "retrieval_arena.profile_evaluation_operator_receipt.v1",
            "operator-symlink-rejected",
            "operator-input-inventory-mismatch",
            "operator-output-root-not-fresh",
            "receipt_sha256",
            '"completed"',
            "container_ephemeral_tmpfs",
            "/workspace/input",
            "/workspace/output",
        ):
            self.assertIn(required, operator)
        self.assertIn("evaluate_profile.py", dockerfile)

    def test_build_receipts_are_self_hashed_v2(self):
        for relative in ("scripts/build-verification.ps1", "scripts/build-verification.sh"):
            build = self.text(relative)
            self.assertIn("retrieval_arena.verification_image_receipt.v2", build)
            self.assertIn("receipt_sha256", build)

    def test_build_and_run_refuse_untracked_or_dirty_source(self):
        for relative in (
            "scripts/build-verification.ps1", "scripts/build-verification.sh",
            "scripts/run-verification.ps1", "scripts/run-verification.sh",
        ):
            self.assertIn("status --porcelain", self.text(relative))
            self.assertIn("verification-source-not-clean", self.text(relative))


if __name__ == "__main__":
    unittest.main()
