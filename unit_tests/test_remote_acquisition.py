from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import traceback
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from retrieval_arena.corpus import acquisition as a
from retrieval_arena.corpus.importers import import_corpus_source
from retrieval_arena.corpus.sources import CorpusSourceDescriptor, descriptor_from_dict
from retrieval_arena.errors import ValidationError
from retrieval_arena.hashing import sha256_json
from retrieval_arena.manifests import finalize_manifest


class RemoteAcquisitionTests(unittest.TestCase):
    def setUp(self):
        declared = Path(os.environ.get("TMPDIR", ""))
        expected = Path(__file__).resolve().parents[1] / ".tmp_unit_tests"
        if not declared.is_absolute() or declared != expected or not declared.is_dir() or declared.is_symlink():
            raise RuntimeError("acquisition-test-scratch-not-admitted")
        self.root = declared / ("remote-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.repo = self.root / "upstream"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "fixture-main")
        (self.repo / "docs").mkdir()
        (self.repo / "docs/readme.md").write_text("first\n")
        (self.repo / "docs/ignored.txt").write_text("skip\n")
        (self.repo / "outside.md").write_text("outside\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "first")
        self.commit = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "tag", "-a", "release", "-m", "annotated")
        self.remote = self.root / "remote.git"
        self.git(self.root, "clone", "--bare", str(self.repo), str(self.remote))
        self.d = CorpusSourceDescriptor("fixture", "snapshot", "git", self.root / "output",
            source_url=self.remote.as_uri(), requested_ref="refs/heads/fixture-main",
            docs_root="docs", include=("*.md",))

    def git(self, root, *args):
        env = os.environ.copy()
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
            "-C", str(root), *args], check=True, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True).stdout.strip()

    def acquire(self, d=None):
        return a.acquire_remote(d or self.d, capability="local-only", created_at="2026-09-04T00:00:00+00:00")

    def verify(self, result, d=None):
        return a.verify_acquisition(result.root, d or self.d, expected_commit=self.commit,
                                     expected_receipt_hash=result.receipt["manifest_hash"])

    def assert_no_success(self):
        self.assertFalse(list((self.root / "output").rglob(a.RECEIPT)))
        self.assertFalse(list((self.root / "output").rglob(".acquiring-*")))

    def rewrite_receipt(self, result, mutate):
        receipt = copy.deepcopy(result.receipt)
        mutate(receipt)
        receipt["identity_hash"] = sha256_json(receipt["identity"])
        receipt = finalize_manifest(receipt)
        a._write(result.root / a.RECEIPT, receipt)
        a._write(result.root / a.MANIFEST, a._manifest(receipt, result.root))

    def test_fixture_uses_declared_scratch_and_cleans_unique_child(self):
        self.assertEqual(self.root.parent, Path(os.environ["TMPDIR"]))
        self.assertTrue(self.root.name.startswith("remote-"))

    def test_full_branch_detached_clean_complete_and_selected_inventory(self):
        result = self.acquire()
        receipt = self.verify(result)
        self.assertEqual(receipt["identity"]["resolved_commit"], self.commit)
        self.assertEqual(receipt["identity"]["full_tree"], self.git(self.repo, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(receipt["identity"]["docs_tree"], self.git(self.repo, "rev-parse", "HEAD:docs"))
        self.assertEqual([r["path"] for r in receipt["identity"]["inventory"]], ["readme.md"])
        self.git(result.root / "source", "fsck", "--full")
        self.assertEqual(self.git(result.root / "source", "status", "--porcelain"), "")
        self.assertFalse((result.root / "source/.git/objects/info/alternates").exists())

    def test_annotated_full_tag_is_peeled_to_commit(self):
        d = replace(self.d, requested_ref="refs/tags/release")
        result = self.acquire(d)
        self.verify(result, d)
        self.assertEqual(result.receipt["identity"]["resolved_commit"], self.commit)

    def test_exact_commit_and_unambiguous_short_ref(self):
        for ref in (self.commit, "fixture-main", "release"):
            with self.subTest(ref=ref):
                d = replace(self.d, requested_ref=ref)
                self.verify(self.acquire(d), d)

    def test_moving_branch_creates_distinct_lineage_and_preserves_first(self):
        first = self.acquire()
        original = (first.root / a.RECEIPT).read_bytes()
        (self.repo / "docs/readme.md").write_text("second\n")
        self.git(self.repo, "commit", "-am", "second")
        second_commit = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "push", str(self.remote), "refs/heads/fixture-main")
        second = self.acquire()
        self.assertNotEqual(first.root, second.root)
        self.assertEqual(second.receipt["identity"]["resolved_commit"], second_commit)
        self.assertEqual((first.root / a.RECEIPT).read_bytes(), original)
        self.verify(first)

    def test_exact_reuse_is_byte_identical(self):
        first = self.acquire()
        original = (first.root / a.RECEIPT).read_bytes()
        second = self.acquire()
        self.assertTrue(second.reused)
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual((second.root / a.RECEIPT).read_bytes(), original)

    def test_checkout_is_independent_after_remote_removed(self):
        d = replace(self.d, requested_ref=self.commit)
        result = self.acquire(d)
        shutil.rmtree(self.remote)
        self.verify(result, d)

    def test_missing_documentation_root_and_non_tree_scope_rejected(self):
        for docs_root in ("missing", "outside.md"):
            with self.assertRaises(ValidationError):
                self.acquire(replace(self.d, docs_root=docs_root))
        self.assert_no_success()

    def test_exact_ancestor_commit_fetch_has_complete_history(self):
        (self.repo / "docs/readme.md").write_text("second\n")
        self.git(self.repo, "commit", "-am", "second")
        self.git(self.repo, "push", str(self.remote), "refs/heads/fixture-main")
        d = replace(self.d, requested_ref=self.commit)
        self.verify(self.acquire(d), d)

    def test_symlink_tree_entry_rejected(self):
        (self.repo / "docs/link.md").symlink_to("readme.md")
        self.git(self.repo, "add", "docs/link.md")
        self.git(self.repo, "commit", "-m", "symlink")
        self.git(self.repo, "push", str(self.remote), "refs/heads/fixture-main")
        with self.assertRaises(ValidationError):
            self.acquire()
        self.assert_no_success()

    def test_submodule_tree_entry_rejected(self):
        self.git(self.repo, "update-index", "--add", "--cacheinfo", "160000," + self.commit + ",submodule")
        self.git(self.repo, "commit", "-m", "gitlink")
        self.git(self.repo, "push", str(self.remote), "refs/heads/fixture-main")
        with self.assertRaises(ValidationError):
            self.acquire()
        self.assert_no_success()

    def test_inventory_numeric_type_mutation_rejected_even_when_rehashed(self):
        result = self.acquire()
        self.rewrite_receipt(result, lambda r: r["identity"]["inventory"][0].update({"size_bytes": 6.0}))
        a._write(result.root / "inventory.json", a._read(result.root / a.RECEIPT)["identity"]["inventory"])
        with self.assertRaises(ValidationError):
            a.verify_acquisition(result.root, self.d, expected_commit=self.commit)

    def test_raw_descriptor_cannot_hide_symlink_destination(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        d = descriptor_from_dict({**a.descriptor_payload(self.d), "destination_workspace": str(link)})
        with self.assertRaisesRegex(ValidationError, "root-link-rejected"):
            self.acquire(d)

    def test_dataset_preparation_revalidates_acquired_bytes(self):
        from retrieval_arena.corpus.dataset_export import prepare_dataset
        result = self.acquire()
        prepared = prepare_dataset(result.root / a.MANIFEST, self.root / "prepared")
        self.assertTrue(prepared["manifest_path"].is_file())
        (result.root / "documents/readme.md").write_text("mutation")
        with self.assertRaises(ValidationError):
            prepare_dataset(result.root / a.MANIFEST, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())

    def test_longpaths_is_command_and_repository_local(self):
        result = self.acquire()
        self.assertEqual(self.git(result.root / "source", "config", "--local", "core.longpaths"), "true")
        runtime = a.GitRuntime(shutil.which("git"))
        with patch.object(a.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            runtime.run(self.root, "--version")
        argv = run.call_args.args[0]
        self.assertIn("core.longpaths=true", argv)
        self.assertNotIn("--global", argv)
        self.assertEqual(run.call_args.kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)

    def test_invalid_missing_empty_and_unreachable_refs_fail_closed(self):
        for ref in (None, "", "HEAD", "--upload-pack=x", "bad..ref", "refs/heads/", "refs/other/x", "deadbee", "0" * 40):
            with self.subTest(ref=ref), self.assertRaises(ValidationError):
                self.acquire(replace(self.d, requested_ref=ref))
        self.assert_no_success()

    def test_absent_branch_never_falls_back_to_remote_default(self):
        with self.assertRaisesRegex(ValidationError, "ref-missing-or-ambiguous"):
            self.acquire(replace(self.d, requested_ref="refs/heads/absent"))
        self.assert_no_success()

    def test_short_branch_tag_collision_rejected_full_refs_work(self):
        self.git(self.repo, "tag", "fixture-main")
        self.git(self.repo, "push", str(self.remote), "refs/tags/fixture-main")
        with self.assertRaisesRegex(ValidationError, "ref-missing-or-ambiguous"):
            self.acquire(replace(self.d, requested_ref="fixture-main"))
        self.acquire()
        self.acquire(replace(self.d, requested_ref="refs/tags/fixture-main"))

    def test_credentials_query_and_unsafe_urls_never_reach_git(self):
        for url in ("https://user:fixture-secret@example.invalid/repo", "https://example.invalid/repo?token=fixture-secret",
                    "ssh://git@example.invalid/repo", "file://host/tmp/repo", "https://example.invalid/a/../b", ""):
            with self.subTest(url=url), patch.object(a.GitRuntime, "run") as run:
                with self.assertRaises(ValidationError) as caught:
                    self.acquire(replace(self.d, source_url=url))
                self.assertNotIn("fixture-secret", str(caught.exception))
                run.assert_not_called()

    def test_git_stderr_stdout_and_exception_secrets_are_not_retained(self):
        for outcome in (subprocess.CompletedProcess([], 1, b"fixture-secret", b"fixture-secret"),
                        subprocess.TimeoutExpired("fixture-secret", 1)):
            with self.subTest(outcome=type(outcome).__name__):
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch.object(a.subprocess, "run", **kwargs):
                    try:
                        a.GitRuntime(shutil.which("git")).run(self.root, "fetch")
                    except ValidationError:
                        self.assertNotIn("fixture-secret", traceback.format_exc())

    def test_remote_movement_during_capture_is_rejected(self):
        original = a._advertisement
        calls = 0
        def moving(*args):
            nonlocal calls
            calls += 1
            answer = original(*args)
            return answer if calls == 1 else (answer[0], "f" * 40)
        with patch.object(a, "_advertisement", side_effect=moving), self.assertRaisesRegex(ValidationError, "remote-changed"):
            self.acquire()
        self.assert_no_success()

    def test_url_and_requested_ref_substitution_rejected(self):
        result = self.acquire()
        other = self.root / "other.git"
        self.git(self.root, "clone", "--bare", str(self.remote), str(other))
        for d in (replace(self.d, source_url=other.as_uri()), replace(self.d, requested_ref=self.commit)):
            with self.assertRaisesRegex(ValidationError, "descriptor-mismatch"):
                self.verify(result, d)

    def test_dirty_tracked_untracked_ignored_checkout_rejected(self):
        result = self.acquire()
        for relative in ("docs/readme.md", "untracked.md", ".gitignore"):
            file = result.root / "source" / relative
            prior = file.read_bytes() if file.exists() else None
            file.write_text("mutation\n")
            with self.assertRaises(ValidationError):
                self.verify(result)
            if prior is None:
                file.unlink()
            else:
                file.write_bytes(prior)

    def test_wrong_commit_tree_and_documentation_tree_rejected(self):
        result = self.acquire()
        with self.assertRaises(ValidationError):
            a.verify_acquisition(result.root, self.d, expected_commit="f" * 40)
        for field in ("resolved_commit", "full_tree", "docs_tree"):
            self.rewrite_receipt(result, lambda r: r["identity"].update({field: "f" * 40}))
            with self.assertRaises(ValidationError):
                a.verify_acquisition(result.root, self.d, expected_commit=self.commit)

    def test_missing_objects_rejected(self):
        result = self.acquire()
        shutil.rmtree(result.root / "source/.git/objects")
        with self.assertRaises(ValidationError):
            self.verify(result)

    def test_alternates_and_symbolic_checkout_rejected(self):
        result = self.acquire()
        alternate = result.root / "source/.git/objects/info/alternates"
        alternate.parent.mkdir(exist_ok=True)
        alternate.write_text(str(self.remote / "objects"))
        with self.assertRaisesRegex(ValidationError, "extension-rejected"):
            self.verify(result)
        alternate.unlink()
        self.git(result.root / "source", "switch", "-c", "unexpected")
        with self.assertRaisesRegex(ValidationError, "checkout-not-detached"):
            self.verify(result)

    def test_receipt_descriptor_manifest_inventory_and_copy_mutation_rejected(self):
        result = self.acquire()
        for relative in (a.RECEIPT, "descriptor.json", a.MANIFEST, "inventory.json", "documents/readme.md"):
            file = result.root / relative
            prior = file.read_bytes()
            file.write_bytes(prior + b"mutation")
            with self.subTest(relative=relative), self.assertRaises(ValidationError):
                self.verify(result)
            file.write_bytes(prior)

    def test_rehashed_receipt_unknown_fields_and_policy_mutations_rejected(self):
        result = self.acquire()
        for field, value in (("strategy", "different"), ("reuse_disposition", "refresh"),
                             ("artifacts", {}), ("ref_interpretation", {"kind": "tag", "full_ref": "refs/tags/x"}),
                             ("extra", True)):
            self.rewrite_receipt(result, lambda r: r["identity"].update({field: value}))
            with self.assertRaises(ValidationError):
                a.verify_acquisition(result.root, self.d, expected_commit=self.commit)

    def test_interrupted_fetch_checkout_inventory_and_publication_clean_up(self):
        original = a.GitRuntime.run
        for command in ("fetch", "checkout", "ls-tree"):
            def interrupted(runtime, root, *args, **kwargs):
                if command in args:
                    raise KeyboardInterrupt()
                return original(runtime, root, *args, **kwargs)
            with self.subTest(command=command), patch.object(a.GitRuntime, "run", interrupted), self.assertRaises(KeyboardInterrupt):
                self.acquire()
            self.assert_no_success()
        with patch.object(a.Path, "rename", side_effect=OSError("fixture-secret")), self.assertRaises(ValidationError):
            self.acquire()
        self.assert_no_success()

    def test_prepublication_copy_mutation_cannot_publish_success(self):
        original = a._write
        def mutate(path, value):
            original(path, value)
            if path.name == a.MANIFEST:
                (path.parent / "documents/readme.md").write_text("mutation")
        with patch.object(a, "_write", mutate), self.assertRaises(ValidationError):
            self.acquire()
        self.assert_no_success()

    def test_stale_markerless_and_partial_roots_never_reused_or_removed(self):
        parent = self.d.destination_workspace / self.d.corpus_id / self.d.snapshot_id
        final = parent / sha256_json({"descriptor": a.descriptor_payload(self.d), "commit": self.commit})
        final.mkdir(parents=True)
        sentinel = final / "unknown"
        sentinel.write_text("preserve")
        with self.assertRaises(ValidationError):
            self.acquire()
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assert_no_success()

    def test_cleanup_rejects_markerless_or_outside_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaises(ValidationError):
            a._cleanup(outside, self.root / "output", {})
        self.assertTrue(outside.exists())

    def test_disabled_evaluator_import_and_network_capability_rejected(self):
        with self.assertRaisesRegex(ValidationError, "capability-disabled"):
            import_corpus_source(self.d)
        with self.assertRaisesRegex(ValidationError, "network-disabled"):
            self.acquire(replace(self.d, source_url="https://example.invalid/repo"))
        with patch.dict(os.environ, {"RA_NETWORK_DISABLED": "1"}), self.assertRaisesRegex(ValidationError, "network-disabled"):
            a.acquire_remote(self.d, capability="host-network")
        self.assertFalse(self.d.destination_workspace.exists())

    def test_unsafe_descriptor_scope_rejected_before_roots(self):
        for d in (replace(self.d, corpus_id="../escape"), replace(self.d, snapshot_id="/escape"),
                  replace(self.d, docs_root="../docs"), replace(self.d, docs_root="/docs"),
                  replace(self.d, include=("../*",))):
            with self.assertRaises(ValidationError):
                self.acquire(d)
        raw = {**a.descriptor_payload(self.d), "destination_workspace": str(self.d.destination_workspace), "docs_root": "/docs"}
        with self.assertRaises(ValidationError):
            descriptor_from_dict(raw)
        self.assertFalse(self.d.destination_workspace.exists())

    def test_symlink_root_cannot_redirect_publication_or_cleanup(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValidationError, "root-link-rejected"):
            self.acquire(replace(self.d, destination_workspace=link))
        self.assertEqual(list(target.iterdir()), [])

    def test_remote_import_v2_references_exact_receipt_and_materialization(self):
        result = import_corpus_source(self.d, acquisition_capability="local-only")
        manifest = result["manifest"]
        self.assertEqual(manifest["schema_version"], a.IMPORT_SCHEMA)
        self.assertEqual(manifest["acquisition_receipt_hash"], result["receipt"]["manifest_hash"])
        self.assertEqual(Path(manifest["materialized_source_path"]).parent, result["manifest_path"].parent)

    def test_cli_help_and_controlled_acquisition(self):
        from retrieval_arena.cli import main
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["corpus", "acquire", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--transport", output.getvalue())
        descriptor = self.root / "descriptor.json"
        descriptor.write_text(json.dumps({**a.descriptor_payload(self.d), "destination_workspace": str(self.d.destination_workspace)}))
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["corpus", "acquire", "--descriptor", str(descriptor)]), 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["status"], "success")
        self.assertTrue(Path(summary["receipt"]).is_file())

    def test_repository_config_injection_rejected_before_git_execution(self):
        result = self.acquire()
        config = result.root / "source/.git/config"
        config.write_text(config.read_text() + '\n[include]\n path = /untrusted\n')
        with patch.object(a.GitRuntime, "run") as run, self.assertRaisesRegex(ValidationError, "git-config-invalid"):
            self.verify(result)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
