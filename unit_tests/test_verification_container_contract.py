from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VerificationContainerContractTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_base_and_python_dependencies_are_immutable(self):
        dockerfile = self.text("containers/verification/Dockerfile")
        lock = self.text("containers/verification/requirements.lock")
        self.assertRegex(dockerfile, r"mcr\.microsoft\.com/.+@sha256:[0-9a-f]{64}")
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("--only-binary=:all:", dockerfile)
        requirements = [line for line in lock.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(requirements), 4)
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
        self.assertIn("argv[1] not in COMMANDS", runner)
        self.assertNotIn("subprocess", runner)
        self.assertIn("sys.path.insert(0, str(OPERATOR_ROOT))", runner)

    def test_operator_module_is_present_in_the_immutable_build_context(self):
        dockerignore = self.text(".dockerignore")
        dockerfile = self.text("containers/verification/Dockerfile")
        operator = "containers/verification/evaluate_profile.py"
        self.assertIn(f"!{operator}", dockerignore)
        self.assertIn(f"COPY {operator} /opt/retrieval-arena/evaluate_profile.py", dockerfile)

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
