"""Promote a benchmark run to the production output directory, with human edits.

Run:
  python -m scripts.promote_benchmark --source output/benchmarks/<dir> \
      [--edits <file.json>] [--rerun-editorial --confirm-live]

1. Validates every edit first (each ``find`` must occur exactly once in its
   scene's narration); any problem aborts before anything is written.
2. Archives the current output/ (everything except benchmarks/) into
   output/benchmarks/archive-<timestamp>/ unless identical content is already
   archived; refuses to continue if archiving fails.
3. Copies the benchmark artifacts into output/ (story_brief.json is also
   written as research.json, which the GUI and pipeline load).
4. Applies the edits (author ``human_editorial``), re-runs the deterministic
   review offline and re-merges the existing editorial assessment, marking
   findings on edited scenes as ``addressed_by_human_edit``.
5. Invalidates narration, images, timeline, video, thumbnail and any approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.narrative import (
    atomic_write_json,
    deterministic_review,
    invalidate_downstream,
    load_json,
    merge_editorial_review,
    screen_editorial_findings,
    stable_hash,
    validate_script,
)

ARTIFACTS = ("story_brief.json", "character_bible.json", "story_outline.json", "script.json",
             "narrative_review.json")
EDIT_AUTHOR = "human_editorial"
ARCHIVE_MANIFEST = "archive_manifest.json"


class PromotionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _output_files(output: Path) -> dict[str, str]:
    """Relative path -> sha256 for everything in output/ except benchmarks/."""
    files = {}
    for path in sorted(output.rglob("*")):
        relative = path.relative_to(output)
        if relative.parts[0] == "benchmarks" or not path.is_file():
            continue
        files[relative.as_posix()] = _sha256(path)
    return files


def archive_output(output: Path) -> Path | None:
    """Archive output/ once per distinct content; return the archive holding it."""
    files = _output_files(output)
    if not files:
        return None
    benchmarks = output / "benchmarks"
    for manifest_path in sorted(benchmarks.glob(f"archive-*/{ARCHIVE_MANIFEST}")):
        if (load_json(manifest_path) or {}).get("files") == files:
            print(f"   Current output/ is already archived in {manifest_path.parent}")
            return manifest_path.parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = benchmarks / f"archive-{stamp}"
    try:
        for relative in files:
            target = archive / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output / relative, target)
        copied = {relative: _sha256(archive / relative) for relative in files}
        if copied != files:
            raise PromotionError("archive verification failed: copied files differ")
        atomic_write_json(archive / ARCHIVE_MANIFEST, {"archived_at": _now(), "files": files})
    except (OSError, PromotionError) as exc:
        raise PromotionError(f"could not archive output/ ({exc}); nothing was promoted") from exc
    print(f"   Archived current output/ to {archive}")
    return archive


def _scene_index(script: dict) -> dict:
    index = {}
    for position, scene in enumerate(script.get("sections", [])):
        index[scene.get("outline_scene_id")] = position
        index[str(scene.get("id"))] = position
        index[scene.get("id")] = position
    return index


def apply_edits(script: dict, edits: list[dict]) -> tuple[dict, list[dict]]:
    """Return an edited copy and the applied-edit records; raise before any change."""
    if not isinstance(edits, list):
        raise PromotionError("edits file must contain a JSON list of {scene_id, find, replace}")
    edited = json.loads(json.dumps(script))
    index = _scene_index(edited)
    applied, problems = [], []
    for number, edit in enumerate(edits, 1):
        scene_id = edit.get("scene_id") if isinstance(edit, dict) else None
        find = edit.get("find") if isinstance(edit, dict) else None
        replace = edit.get("replace") if isinstance(edit, dict) else None
        if scene_id not in index or not isinstance(find, str) or not find \
                or not isinstance(replace, str):
            problems.append(f"edit {number}: needs a known scene_id and string find/replace")
            continue
        scene = edited["sections"][index[scene_id]]
        occurrences = scene["narration"].count(find)
        if occurrences != 1:
            problems.append(f"edit {number} ({scene_id}): find occurs {occurrences} times "
                            f"(must be exactly once): {find[:60]!r}")
            continue
        scene["narration"] = scene["narration"].replace(find, replace)
        applied.append({"scene_id": scene_id, "section_id": scene["id"], "find": find,
                        "replace": replace, "author": EDIT_AUTHOR, "applied_at": _now()})
    if problems:
        raise PromotionError("no change made; " + "; ".join(problems))
    return edited, applied


def rereview(script: dict, brief: dict, bible: dict, outline: dict, previous: dict,
             edits: list[dict], live_editorial: bool = False) -> dict:
    """Deterministic review again, re-merged with the existing (or a fresh) editorial review."""
    target = float(script.get("target_duration_minutes") or brief.get("target_duration_minutes") or 15)
    report = deterministic_review(script, target, outline, previous.get("repair_attempts"), brief)
    edited_ids = {edit["section_id"] for edit in edits}
    if live_editorial:
        from agents.scriptwriter import _editorial_review
        print("   -> Running a live editorial review of the promoted screenplay...")
        merged = merge_editorial_review(
            report, _editorial_review(script, brief, bible, outline, report), bible)
    else:
        assessment = json.loads(json.dumps(previous.get("editorial_assessment") or {})) or None
        addressed = []
        if assessment:
            # Discard findings contradicting deterministic facts first, so they
            # are never reported as addressed by an edit.
            kept, discarded = screen_editorial_findings(assessment.get("findings", []), report, bible)
            remaining = []
            for finding in kept:
                if edited_ids & set(finding.get("scene_ids") or []):
                    finding["addressed_by_human_edit"] = True
                    addressed.append(finding)
                else:
                    remaining.append(finding)
            # Addressed findings stay visible but no longer drive the status.
            merged = merge_editorial_review(report, dict(assessment, findings=remaining), bible)
            merged["editorial_assessment"]["findings"] = remaining + addressed
            merged["discarded_editorial_findings"] = discarded
        else:
            merged = merge_editorial_review(report, None)
        merged["addressed_editorial_findings"] = addressed
        merged["editorial_review_stale"] = bool(edits)
    merged["editorial_edits"] = edits
    merged["promoted_from_benchmark"] = previous.get("source_hash")
    merged["source_hash"] = stable_hash(script)
    return merged


def promote(source, *, output=None, edits_path=None, live_editorial: bool = False) -> dict:
    source = Path(source)
    output = Path(output or config.OUTPUT_DIR)
    missing = [name for name in ARTIFACTS if not (source / name).is_file()]
    if missing:
        raise PromotionError(f"{source} is missing: {', '.join(missing)}")
    if source.resolve() == output.resolve():
        raise PromotionError("--source must be a benchmark directory, not output/ itself")
    artifacts = {name: load_json(source / name) for name in ARTIFACTS}
    if any(value is None for value in artifacts.values()):
        raise PromotionError("a benchmark artifact is not valid JSON")
    edits = []
    if edits_path:
        edits = json.loads(Path(edits_path).read_text(encoding="utf-8"))
    script, applied = apply_edits(artifacts["script.json"], edits)
    bible, outline = artifacts["character_bible.json"], artifacts["story_outline.json"]
    script = validate_script(script, bible, outline)
    if applied:
        script["editorial_edits"] = applied
    review = rereview(script, artifacts["story_brief.json"], bible, outline,
                      artifacts["narrative_review.json"], applied, live_editorial)

    output.mkdir(parents=True, exist_ok=True)
    archive = archive_output(output)
    for name in ("story_brief.json", "character_bible.json", "story_outline.json"):
        atomic_write_json(output / name, artifacts[name])
    atomic_write_json(output / "research.json", artifacts["story_brief.json"])
    atomic_write_json(output / "script.json", script)
    atomic_write_json(output / "narrative_review.json", review)
    removed = invalidate_downstream("script_changed", str(output))
    for name in ("review_approval.json", "review_data.json"):
        if (output / name).is_file():
            (output / name).unlink()
            removed.append(str(output / name))
    return {"archive": str(archive) if archive else None, "edits": applied,
            "status": review["status"], "removed": removed, "word_count": review["word_count"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Promote a benchmark run to output/.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--edits", help="JSON list of {scene_id, find, replace}")
    parser.add_argument("--output-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--rerun-editorial", action="store_true",
                        help="Run one live editorial review after the edits (uses Gemini quota).")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args(argv)
    if args.rerun_editorial and not args.confirm_live:
        parser.error("--rerun-editorial requires --confirm-live; no API call was made")
    try:
        result = promote(args.source, output=args.output_dir, edits_path=args.edits,
                         live_editorial=args.rerun_editorial)
    except PromotionError as exc:
        print(f"Promotion aborted: {exc}", file=sys.stderr)
        return 1
    print(f"Promoted {args.source} -> {args.output_dir or config.OUTPUT_DIR}")
    print(f"  edits applied: {len(result['edits'])}; narrative status: {result['status']}; "
          f"words: {result['word_count']}")
    if result["archive"]:
        print(f"  previous output archived in: {result['archive']}")
    print(f"  invalidated downstream files: {len(result['removed'])}")
    print("Next: python -m scripts.render_preflight")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
