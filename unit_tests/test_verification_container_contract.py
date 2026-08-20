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
        self.assertIn("argv[1] not in COMMANDS", runner)
        self.assertNotIn("subprocess", runner)


if __name__ == "__main__":
    unittest.main()
