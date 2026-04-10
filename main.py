import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import classifier
import dashboard_state
import evaluator
import nlu_context
import orchestrator
import response_generator

load_dotenv()

TASK_DESCRIPTION = """
Analyse this codebase in full. Identify the most impactful improvement you can make right now 
— whether that is a missing feature, a broken connection, a security issue, or a structural improvement. 
Implement that improvement completely. Then report back with exactly what you changed and why.
"""

# Parent of this project folder (e.g. the application repo); adjust if your layout differs.
PROJECT_ROOT = ".."

SKIP_DIR_NAMES = frozenset({".git", "__pycache__", "node_modules", "venv", ".venv"})
SKIP_FILE_NAMES = frozenset({".env"})


def _print_section(title: str, payload: Any) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _progress(msg: str) -> None:
    print(f"\n>>> {msg}")


def resolve_project_root() -> Path:
    base = Path(__file__).resolve().parent
    return (base / PROJECT_ROOT).resolve()


def load_file_tree(project_root: Path) -> dict[str, str]:
    tree: dict[str, str] = {}
    root_str = str(project_root.resolve())

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]

        for name in filenames:
            if name.endswith(".pyc") or name in SKIP_FILE_NAMES:
                continue
            full = Path(dirpath) / name
            try:
                rel = full.resolve().relative_to(project_root.resolve())
            except ValueError:
                continue
            rel_key = rel.as_posix()
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tree[rel_key] = text

    _progress(f"Loaded {len(tree)} files from {root_str}")
    return tree


def _feedback_from_evaluator(ev: dict[str, Any]) -> str:
    parts: list[str] = []
    if ev.get("suggested_fix"):
        parts.append(f"suggested_fix: {ev['suggested_fix']}")
    issues = ev.get("issues_found") or []
    if issues:
        parts.append("issues_found: " + json.dumps(issues, ensure_ascii=False))
    if ev.get("review_notes"):
        parts.append(f"review_notes: {ev['review_notes']}")
    return "\n".join(parts) if parts else "Address all QA findings and resubmit complete files."


def _collect_proposed_paths(
    rg: dict[str, Any], ds: dict[str, Any]
) -> list[tuple[str, str, str]]:
    """Returns list of (relative_path, full_content, summary_or_reason) for apply order: impl then integration."""
    out: list[tuple[str, str, str]] = []
    for item in rg.get("files_written") or []:
        if not isinstance(item, dict):
            continue
        p = item.get("path")
        c = item.get("full_content")
        if isinstance(p, str) and isinstance(c, str):
            out.append((p, c, str(item.get("change_summary") or "")))
    for item in ds.get("files_updated") or []:
        if not isinstance(item, dict):
            continue
        p = item.get("path")
        c = item.get("full_content")
        if isinstance(p, str) and isinstance(c, str):
            out.append((p, str(c), str(item.get("reason") or "")))
    return out


def _merge_final_summaries(entries: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for path, content, note in entries:
        merged[path] = {"path": path, "summary": note, "full_content": content}
    return list(merged.values())


def safe_write_file(project_root: Path, rel_path: str, content: str) -> None:
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Refusing unsafe path: {rel_path}")
    dest = (project_root / rel).resolve()
    root_resolved = project_root.resolve()
    try:
        dest.relative_to(root_resolved)
    except ValueError as e:
        raise ValueError(f"Path escapes project root: {rel_path}") from e
    print(f"  Writing: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


async def run_stage2_sequential(
    nlu_out: dict[str, Any],
    classifier_out: dict[str, Any],
    file_tree: dict[str, str],
    evaluator_feedback: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _progress("Stage 2a: implementation agent (response_generator)…")
    rg_input: dict[str, Any] = {
        "task_spec": nlu_out,
        "codebase_map": classifier_out,
        "file_tree": file_tree,
    }
    if evaluator_feedback:
        rg_input["evaluator_feedback"] = evaluator_feedback
    rg_out = await response_generator.run(rg_input)

    _progress("Stage 2b: integration agent (dashboard_state)…")
    ds_input: dict[str, Any] = {
        "proposed_changes": rg_out,
        "codebase_map": classifier_out,
    }
    if evaluator_feedback:
        ds_input["evaluator_feedback"] = evaluator_feedback
    ds_out = await dashboard_state.run(ds_input)
    return rg_out, ds_out


async def run_pipeline() -> None:
    project_root = resolve_project_root()
    _progress(f"Project root: {project_root}")

    file_tree = load_file_tree(project_root)
    if not file_tree:
        print("No files loaded; check PROJECT_ROOT and exclusions.")
        return

    task = TASK_DESCRIPTION.strip()

    _progress("Stage 1 (parallel): codebase analyst + requirements agent…")
    classifier_out, nlu_out = await asyncio.gather(
        classifier.run({"file_tree": file_tree}),
        nlu_context.run({"task": task, "file_tree": file_tree}),
    )
    _print_section("STAGE 1 — classifier (Codebase Analyst)", classifier_out)
    _print_section("STAGE 1 — nlu_context (Requirements & Context)", nlu_out)
    _progress("Stage 1 complete.")

    evaluator_feedback: str | None = None
    rg_out: dict[str, Any] = {}
    ds_out: dict[str, Any] = {}
    ev_out: dict[str, Any] = {}

    for attempt in range(2):
        rg_out, ds_out = await run_stage2_sequential(
            nlu_out, classifier_out, file_tree, evaluator_feedback
        )
        _print_section(
            f"STAGE 2 — response_generator (attempt {attempt + 1})",
            rg_out,
        )
        _print_section(
            f"STAGE 2 — dashboard_state (attempt {attempt + 1})",
            ds_out,
        )
        _progress(f"Stage 2 complete (attempt {attempt + 1}).")

        _progress("Stage 3: QA evaluator…")
        ev_out = await evaluator.run(
            {
                "original_task": task,
                "codebase_map": classifier_out,
                "implementation_agent": rg_out,
                "integration_agent": ds_out,
            }
        )
        _print_section("STAGE 3 — evaluator", ev_out)
        _progress("Stage 3 complete.")

        if ev_out.get("approved") is True:
            break
        if attempt == 0:
            evaluator_feedback = _feedback_from_evaluator(ev_out)
            _progress("QA not approved — re-running Stage 2 once with evaluator feedback…")
        else:
            _progress("QA still not approved after one retry; proceeding to orchestrator.")

    _progress("Stage 4: orchestrator (Engineering Lead)…")
    orch_out = await orchestrator.run(
        {
            "task": task,
            "codebase_map": classifier_out,
            "requirements_agent": nlu_out,
            "implementation_agent": rg_out,
            "integration_agent": ds_out,
            "evaluator": ev_out,
        }
    )
    _print_section("STAGE 4 — orchestrator", orch_out)
    _progress("Stage 4 complete.")

    proposed = _collect_proposed_paths(rg_out, ds_out)
    merged_list = _merge_final_summaries(proposed)

    _print_section(
        "PROPOSED FILE CHANGES (merged: integration overwrites same path)",
        [{"path": x[0], "summary": x[2]} for x in proposed],
    )
    _print_section(
        "QA RESULT",
        {
            "approved": ev_out.get("approved"),
            "confidence": ev_out.get("confidence"),
            "task_completed": ev_out.get("task_completed"),
        },
    )
    _print_section("REPORT TO DEVELOPER (orchestrator)", {"report": orch_out.get("report_to_developer")})

    answer = input('\nApply these changes? (y/n): ').strip().lower()
    if answer == "y":
        _progress("Applying file writes…")
        for path, content, _ in proposed:
            try:
                safe_write_file(project_root, path, content)
            except ValueError as e:
                print(f"  SKIP (unsafe path): {path} — {e}")
        print("Done.")
    else:
        print("No files were written.")


async def main() -> None:
    await run_pipeline()


if __name__ == "__main__":
    asyncio.run(main())
