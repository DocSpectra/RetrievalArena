from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import build_regression_audit
from .config import load_config
from .drift import compare_retrieval_runs
from .errors import ValidationError
from .harness import run_config
from .hashing import sha256_file
from .manifests import canonical_manifest_json, read_manifest, write_manifest
from .measurements import collect_systems_measurements, environment_metadata


PILOT_PLAN_SCHEMA_VERSION = "retrieval_arena.bigdata2026_pilot_plan.v1"
PILOT_RUN_SCHEMA_VERSION = "retrieval_arena.bigdata2026_pilot_run.v1"
VALID_STAGES = (
    "snapshot_compare",
    "retrieval_before",
    "retrieval_after",
    "drift",
    "audit",
    "measurements",
    "baseline_bundle",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_pilot_plan(plan_path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid BigData pilot plan JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError("BigData pilot plan must be a JSON object.")
    return resolve_pilot_plan(raw, plan_path)


def resolve_pilot_plan(raw: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    if raw.get("schema_version") != PILOT_PLAN_SCHEMA_VERSION:
        raise ValidationError(f"schema_version must be {PILOT_PLAN_SCHEMA_VERSION}.")
    base = plan_path.resolve().parent
    plan = dict(raw)
    plan["plan_path"] = str(plan_path.resolve())
    for field in ("pilot_id", "corpus_id", "query_set_id"):
        _required_str(plan, field)

    snapshot_pair = _required_mapping(plan, "snapshot_pair")
    for side in ("before", "after"):
        item = _required_mapping(snapshot_pair, side)
        _required_str(item, "snapshot_id")
        item["source_descriptor"] = str(_resolve_path(_required_str(item, "source_descriptor"), base))
    snapshot_pair["comparison_plan"] = str(_resolve_path(_required_str(snapshot_pair, "comparison_plan"), base))

    retrieval = _required_mapping(plan, "retrieval")
    retrieval["before_config"] = str(_resolve_path(_required_str(retrieval, "before_config"), base))
    retrieval["after_config"] = str(_resolve_path(_required_str(retrieval, "after_config"), base))
    selected_tests = retrieval.get("selected_tests", [])
    if not isinstance(selected_tests, list) or not all(isinstance(item, str) and item for item in selected_tests):
        raise ValidationError("retrieval.selected_tests must be a list of non-empty strings.")
    _required_str(retrieval, "selected_dataset")

    comparisons = plan.get("comparisons")
    if not isinstance(comparisons, list) or not comparisons:
        raise ValidationError("comparisons must be a non-empty list.")
    seen_comparisons: set[str] = set()
    for item in comparisons:
        if not isinstance(item, dict):
            raise ValidationError("Each comparison must be a mapping.")
        comparison_id = _required_str(item, "comparison_id")
        if comparison_id in seen_comparisons:
            raise ValidationError(f"Duplicate comparison_id: {comparison_id}")
        seen_comparisons.add(comparison_id)
        _validate_selector(_required_mapping(item, "before_run_selector"), "before_run_selector")
        _validate_selector(_required_mapping(item, "after_run_selector"), "after_run_selector")

    plan["output_root"] = str(_resolve_path(_required_str(plan, "output_root"), base))
    plan["calibration_store"] = str(_resolve_path(_required_str(plan, "calibration_store"), base))
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValidationError("stages must be a non-empty list.")
    unknown = [stage for stage in stages if stage not in VALID_STAGES]
    if unknown:
        raise ValidationError(f"Unknown BigData pilot stages: {unknown}")
    plan["stages"] = stages

    reuse = plan.get("reuse_policy", {})
    if not isinstance(reuse, dict):
        raise ValidationError("reuse_policy must be a mapping when present.")
    for key in ("reuse_imports", "reuse_prepared_snapshots", "reuse_retrieval_runs", "overwrite_reports"):
        if key in reuse and not isinstance(reuse[key], bool):
            raise ValidationError(f"reuse_policy.{key} must be boolean when present.")
    plan["reuse_policy"] = reuse

    baseline = _required_mapping(plan, "baseline_bundle")
    if baseline.get("include_raw") is True:
        raise ValidationError("baseline_bundle.include_raw must not be true; raw artifacts are excluded.")
    baseline["include_raw"] = False
    plan["baseline_bundle"] = baseline
    return plan


def resolve_stage_list(plan_stages: list[str], *, stage: str | None = None, from_stage: str | None = None) -> list[str]:
    if stage and from_stage:
        raise ValidationError("--stage and --from-stage cannot be used together.")
    for value, label in ((stage, "--stage"), (from_stage, "--from-stage")):
        if value and value not in plan_stages:
            raise ValidationError(f"{label} value is not present in the pilot plan stages: {value}")
    if stage:
        index = plan_stages.index(stage)
        required = [item for item in VALID_STAGES[: VALID_STAGES.index(stage) + 1] if item in plan_stages[: index + 1]]
        return required
    if from_stage:
        return plan_stages[plan_stages.index(from_stage) :]
    return list(plan_stages)


def select_run_from_experiment_manifest(manifest_path: Path, selector: dict[str, Any]) -> Path:
    manifest = read_manifest(manifest_path, verify_hash=False)
    experiment_dir = manifest_path.resolve().parent
    matches = []
    for item in manifest.get("run_manifests", []):
        if not isinstance(item, dict):
            continue
        if item.get("dataset") != selector.get("dataset") or item.get("test") != selector.get("test"):
            continue
        if selector.get("run_id") and item.get("run_id") != selector.get("run_id"):
            continue
        matches.append(item)
    label = f"dataset={selector.get('dataset')} test={selector.get('test')}"
    if selector.get("run_id"):
        label += f" run_id={selector.get('run_id')}"
    if not matches:
        raise ValidationError(f"No run matched selector in {manifest_path}: {label}")
    if len(matches) > 1:
        raise ValidationError(f"Ambiguous run selector in {manifest_path}: {label}")
    item = matches[0]
    path_value = item.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise ValidationError(f"Experiment manifest run entry missing path for selector: {label}")
    run_manifest = Path(path_value)
    if not run_manifest.is_absolute():
        run_manifest = experiment_dir / run_manifest
    return run_manifest.resolve().parent


def bigdata_pilot(
    plan_path: Path,
    *,
    dry_run: bool = False,
    stage: str | None = None,
    from_stage: str | None = None,
    force: bool = False,
    no_baseline_bundle: bool = False,
    created_at: str | None = None,
) -> dict[str, Any]:
    plan = load_pilot_plan(plan_path)
    stages_to_run = resolve_stage_list(plan["stages"], stage=stage, from_stage=from_stage)
    if no_baseline_bundle:
        stages_to_run = [item for item in stages_to_run if item != "baseline_bundle"]
    timestamp = created_at or utc_now_iso()
    output_root = Path(plan["output_root"])
    stage_records: list[dict[str, Any]] = []
    runtime_metrics: list[dict[str, Any]] = []
    context = _initial_context(plan, plan_path)

    if dry_run:
        manifest = _build_pilot_manifest(
            plan,
            plan_path,
            created_at=timestamp,
            stage_records=[_dry_stage_record(name) for name in stages_to_run],
            context=context,
            baseline_ref=None,
        )
        return {
            "ok": True,
            "summary": f"BigData pilot {plan['pilot_id']} dry-run: {len(stages_to_run)} stages resolved, {len(plan['comparisons'])} run comparison(s)",
            "dry_run": True,
            "plan": plan,
            "stages": stages_to_run,
            "manifest": manifest,
            "written_artifacts": [],
        }

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "plan_resolved.json").write_text(canonical_manifest_json(plan), encoding="utf-8")
    baseline_ref: dict[str, Any] | None = None
    failed = False
    for stage_name in stages_to_run:
        if failed:
            stage_records.append(_skipped_stage_record(stage_name))
            continue
        started_at = utc_now_iso()
        start = datetime.now(timezone.utc)
        try:
            record = _run_stage(stage_name, plan, context, force=force, runtime_metrics=runtime_metrics)
            record.update({"stage": stage_name, "status": "completed", "started_at": started_at, "completed_at": utc_now_iso()})
            if stage_name == "baseline_bundle":
                baseline_ref = record.get("baseline_bundle")
        except Exception as exc:
            record = {
                "stage": stage_name,
                "status": "failed",
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                "input_artifacts": [],
                "output_artifacts": [],
                "manifest_references": [],
                "unavailable_optional_inputs": [],
                "error": str(exc),
            }
            failed = True
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        runtime_metrics.append({"stage_name": stage_name, "started_at": started_at, "completed_at": record["completed_at"], "duration_seconds": duration, "status": record["status"], "artifact_references": record.get("output_artifacts", [])})
        stage_records.append(record)
        manifest = _build_pilot_manifest(plan, plan_path, created_at=timestamp, stage_records=stage_records, context=context, baseline_ref=baseline_ref)
        write_manifest(output_root / "pilot_manifest.json", manifest)
        if failed:
            raise ValidationError(f"BigData pilot stage failed: {stage_name}: {record['error']}")

    manifest = _build_pilot_manifest(plan, plan_path, created_at=timestamp, stage_records=stage_records, context=context, baseline_ref=baseline_ref)
    written_manifest = write_manifest(output_root / "pilot_manifest.json", manifest)
    if baseline_ref is not None:
        shutil.copy2(output_root / "pilot_manifest.json", Path(plan["calibration_store"]) / "pilot_manifest.json")
    return {
        "ok": True,
        "summary": f"BigData pilot {plan['pilot_id']} completed: {len(stages_to_run)} stages, {len(plan['comparisons'])} run comparison(s), baseline {'refreshed' if baseline_ref else 'not refreshed'}",
        "dry_run": False,
        "manifest": written_manifest,
        "manifest_path": output_root / "pilot_manifest.json",
        "written_artifacts": [str(output_root / "pilot_manifest.json"), str(output_root / "plan_resolved.json")],
    }


def assemble_baseline_bundle(output_root: Path, calibration_store: Path, *, pilot_id: str) -> dict[str, Any]:
    if calibration_store.exists():
        shutil.rmtree(calibration_store)
    calibration_store.mkdir(parents=True)
    copied: list[str] = []
    for item in sorted(output_root.iterdir(), key=lambda path: path.name):
        if item.name == "raw":
            continue
        target = calibration_store / item.name
        if item.is_dir():
            _copytree_excluding_raw(item, target)
        elif item.is_file():
            shutil.copy2(item, target)
        if target.exists():
            copied.append(str(target))
    baseline_md = calibration_store / "BASELINE.md"
    baseline_md.write_text(
        "\n".join(
            [
                f"# BigData Pilot Baseline: {pilot_id}",
                "",
                "This calibration store contains the current selected BigData pilot baseline.",
                "Raw source trees and copied corpus content are excluded by policy.",
                "No efficiency score, overall scale score, or composite systems metric is included.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    copied.append(str(baseline_md))
    return {"path": str(calibration_store.resolve()), "copied_artifacts": copied, "include_raw": False}


def _run_stage(stage_name: str, plan: dict[str, Any], context: dict[str, Any], *, force: bool, runtime_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    output_root = Path(plan["output_root"])
    if stage_name == "snapshot_compare":
        from .corpus.comparison import run_corpus_snapshot_comparison

        result = run_corpus_snapshot_comparison(Path(plan["snapshot_pair"]["comparison_plan"]))
        context["snapshot_comparison_manifest"] = Path(result["manifest_path"])
        context["snapshot_diff_json"] = Path(result["manifest_path"]).parent / "snapshot_diff.json"
        context["snapshot_diff_md"] = Path(result["manifest_path"]).parent / "snapshot_diff.md"
        manifest = result["manifest"]
        context["before_snapshot_dir"] = Path(manifest["before"]["snapshot_manifest_dir"])
        context["after_snapshot_dir"] = Path(manifest["after"]["snapshot_manifest_dir"])
        return _record([plan["snapshot_pair"]["comparison_plan"]], result["written_artifacts"], [str(result["manifest_path"])])
    if stage_name in {"retrieval_before", "retrieval_after"}:
        side = "before" if stage_name.endswith("before") else "after"
        config_path = Path(plan["retrieval"][f"{side}_config"])
        if not force and _experiment_manifest_path(config_path).exists() and plan["reuse_policy"].get("reuse_retrieval_runs"):
            manifest_path = _experiment_manifest_path(config_path)
        else:
            run_config(config_path)
            manifest_path = _experiment_manifest_path(config_path)
        context[f"{side}_experiment_manifest"] = manifest_path
        return _record([str(config_path)], [str(manifest_path)], [str(manifest_path)])
    if stage_name == "drift":
        _ensure_runs_selected(plan, context)
        outputs = []
        for comparison in context["selected_comparisons"]:
            out_dir = output_root / "comparisons" / comparison["comparison_id"]
            report = compare_retrieval_runs(comparison["before_run_dir"], comparison["after_run_dir"], out_dir=out_dir)
            comparison["drift_jsonl"] = out_dir / "retrieval_drift.jsonl"
            comparison["drift_summary_json"] = out_dir / "retrieval_drift_summary.json"
            outputs.extend(report["written_artifacts"])
        return _record([], outputs, [path for item in context["selected_comparisons"] for path in (str(item["drift_summary_json"]),)])
    if stage_name == "audit":
        _ensure_runs_selected(plan, context)
        outputs = []
        for comparison in context["selected_comparisons"]:
            out_dir = output_root / "comparisons" / comparison["comparison_id"]
            report = build_regression_audit(
                comparison["before_run_dir"],
                comparison["after_run_dir"],
                out_dir,
                drift_jsonl=comparison.get("drift_jsonl"),
                drift_summary_json=comparison.get("drift_summary_json"),
                snapshot_diff_json=context.get("snapshot_diff_json"),
            )
            comparison["audit_jsonl"] = out_dir / "regression_audit.jsonl"
            comparison["audit_summary_json"] = out_dir / "regression_audit_summary.json"
            outputs.extend(report["written_artifacts"])
        return _record([], outputs, [path for item in context["selected_comparisons"] for path in (str(item["audit_summary_json"]),)])
    if stage_name == "measurements":
        _ensure_runs_selected(plan, context)
        outputs = []
        for comparison in context["selected_comparisons"]:
            out_dir = output_root / "comparisons" / comparison["comparison_id"]
            report = collect_systems_measurements(
                out_dir,
                snapshot_dir=context.get("after_snapshot_dir"),
                run_dir=comparison["after_run_dir"],
                snapshot_diff_json=context.get("snapshot_diff_json"),
                drift_jsonl=comparison.get("drift_jsonl"),
                drift_summary_json=comparison.get("drift_summary_json"),
                audit_jsonl=comparison.get("audit_jsonl"),
                audit_summary_json=comparison.get("audit_summary_json"),
                stage_runtime_metrics=runtime_metrics,
            )
            comparison["systems_measurements_json"] = out_dir / "systems_measurements.json"
            outputs.extend(report["written_artifacts"])
        return _record([], outputs, [path for item in context["selected_comparisons"] for path in (str(item["systems_measurements_json"]),)])
    if stage_name == "baseline_bundle":
        bundle = assemble_baseline_bundle(output_root, Path(plan["calibration_store"]), pilot_id=plan["pilot_id"])
        return {**_record([str(output_root)], bundle["copied_artifacts"], []), "baseline_bundle": bundle}
    raise ValidationError(f"Unsupported BigData pilot stage: {stage_name}")


def _ensure_runs_selected(plan: dict[str, Any], context: dict[str, Any]) -> None:
    if "selected_comparisons" in context:
        return
    before_manifest = context.get("before_experiment_manifest") or _experiment_manifest_path(Path(plan["retrieval"]["before_config"]))
    after_manifest = context.get("after_experiment_manifest") or _experiment_manifest_path(Path(plan["retrieval"]["after_config"]))
    selected = []
    for comparison in plan["comparisons"]:
        selected.append(
            {
                "comparison_id": comparison["comparison_id"],
                "before_run_dir": select_run_from_experiment_manifest(before_manifest, comparison["before_run_selector"]),
                "after_run_dir": select_run_from_experiment_manifest(after_manifest, comparison["after_run_selector"]),
            }
        )
    context["selected_comparisons"] = selected


def _experiment_manifest_path(config_path: Path) -> Path:
    config = load_config(config_path)
    return (config.output_dir / config.experiment_name / "experiment_manifest.json").resolve()


def _build_pilot_manifest(
    plan: dict[str, Any],
    plan_path: Path,
    *,
    created_at: str,
    stage_records: list[dict[str, Any]],
    context: dict[str, Any],
    baseline_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": PILOT_RUN_SCHEMA_VERSION,
        "created_at": created_at,
        "manifest_type": "bigdata2026_pilot_run",
        "pilot_id": plan["pilot_id"],
        "plan_path": str(plan_path.resolve()),
        "plan_hash": sha256_file(plan_path),
        "corpus_id": plan["corpus_id"],
        "query_set_id": plan["query_set_id"],
        "stage_status": _overall_status(stage_records),
        "stages": stage_records,
        "snapshot_pair": plan["snapshot_pair"],
        "retrieval_runs": _retrieval_run_refs(context),
        "comparisons": _comparison_refs(context),
        "baseline_bundle": baseline_ref or {"include_raw": False, "status": "not_refreshed"},
        "environment": environment_metadata(),
    }


def _retrieval_run_refs(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value)
        for key, value in sorted(context.items())
        if key.endswith("_experiment_manifest") and isinstance(value, Path)
    }


def _comparison_refs(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in context.get("selected_comparisons", []):
        rows.append({key: str(value) if isinstance(value, Path) else value for key, value in sorted(item.items())})
    return rows


def _overall_status(stage_records: list[dict[str, Any]]) -> str:
    if any(item["status"] == "failed" for item in stage_records):
        return "failed"
    if stage_records and all(item["status"] == "dry_run" for item in stage_records):
        return "dry_run"
    return "completed" if stage_records else "not_started"


def _dry_stage_record(stage_name: str) -> dict[str, Any]:
    return {
        "stage": stage_name,
        "status": "dry_run",
        "started_at": None,
        "completed_at": None,
        "input_artifacts": [],
        "output_artifacts": [],
        "manifest_references": [],
        "unavailable_optional_inputs": [],
    }


def _skipped_stage_record(stage_name: str) -> dict[str, Any]:
    record = _dry_stage_record(stage_name)
    record["status"] = "skipped"
    return record


def _record(inputs: list[str], outputs: list[str], manifests: list[str]) -> dict[str, Any]:
    return {
        "input_artifacts": [{"path": str(item)} for item in inputs],
        "output_artifacts": [{"path": str(item)} for item in outputs],
        "manifest_references": [{"path": str(item)} for item in manifests],
        "unavailable_optional_inputs": [],
    }


def _initial_context(plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    return {"plan_path": plan_path.resolve(), "pilot_id": plan["pilot_id"]}


def _copytree_excluding_raw(source: Path, target: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {"raw", "corpus"}}

    shutil.copytree(source, target, ignore=ignore)


def _validate_selector(selector: dict[str, Any], field: str) -> None:
    _required_str(selector, "dataset")
    _required_str(selector, "test")
    if "run_id" in selector and not isinstance(selector["run_id"], str):
        raise ValidationError(f"{field}.run_id must be a string when present.")


def _required_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"{key} is required and must be a mapping.")
    return value


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{key} is required.")
    return value


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()
