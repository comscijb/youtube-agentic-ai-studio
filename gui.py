"""
YouTube AI Agent — Desktop GUI
Run:  python gui.py
Open: http://localhost:7070

Replaces pipeline.py + review/app.py entirely.
One page controls everything: generate, review, edit, upload.
"""
import os, sys, json, queue, threading, time, io, pickle, re, math, textwrap
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# Module-level imports from agents (sys.path is set above)
from agents.researcher import load_banned_topics, save_banned_topics
from agents.narrative import (
    atomic_write_json, invalidate_downstream, invalidate_scene_outputs, load_json,
    title_passes, validate_title,
)
from agents import voice_calibration
from studio import projects
from video.timeline import load_timeline

from flask import Flask, Response, jsonify, request, send_file, abort

import config

# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════════════

_log_queue  = queue.Queue()
_stop_event = threading.Event()   # set() to request pipeline stop

_state = {
    "running":        False,
    "stop_requested": False,
    "video_type":     "normal",
    "stages": {
        "research":  "pending",   # pending | running | done | error
        "script":    "pending",
        "music":     "pending",
        "narration": "pending",
        "visual_bible": "pending",
        "shots":     "pending",
        "video":     "pending",
        "shorts":    "pending",
    },
    "video_path":   None,
    "thumb_path":   None,
    "audio_path":   None,
    "image_map":    {},
    "script":       None,
    "research":     None,
    "character_bible": None,
    "story_outline": None,
    "narrative_review": None,
    "yt_url":       None,
    "error":        None,
    "upload_pct":   0,
    "uploading":    False,
    "imagegen_busy": False,
    "imagegen_message": "",
}


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class _Capture:
    """Redirect stdout into the log queue so pipeline prints appear in GUI."""
    def __init__(self, orig): self._orig = orig
    def write(self, t):
        if t.strip(): _log_queue.put(("log", t.rstrip()))
        self._orig.write(t)
    def flush(self): self._orig.flush()

def _log(msg, level="info"):
    _log_queue.put(("log", f"[{level.upper()}] {msg}"))

_RUN_STARTED: dict = {}


def _stage(name, status):
    _state["stages"][name] = status
    _log_queue.put(("stage", f"{name}:{status}"))
    from studio import stages as stage_graph
    stage = stage_graph.stage_for_step(name) or name
    if status == "running":
        _RUN_STARTED[stage] = time.time()
    elif status in ("done", "error") and stage in _RUN_STARTED:
        try:                                         # last run time and duration on the stage card
            projects.record_run(config.OUTPUT_DIR, stage, status, _RUN_STARTED.pop(stage), time.time())
        except (OSError, ValueError):
            pass
    if status == "done":
        try:
            projects.touch(config.OUTPUT_DIR)       # keeps project.json's stage status current
        except (OSError, ValueError) as exc:
            _log(f"Could not update project.json: {exc}", "warn")


# ══════════════════════════════════════════════════════════════════════════════
#  DESCRIPTION / CHAPTERS BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _ts(secs):
    h, m, s = secs//3600, (secs%3600)//60, secs%60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _build_description(script, research, video_type: str = "normal", output_dir=None):
    """Phase 3.6-C: normal episodes use the validated template (hook, chapters, CTAs, credits,
    hashtags); the legacy layout below stays for Shorts and when the template cannot be filled."""
    sections = script.get("sections", [])
    is_shorts = video_type == "shorts" or script.get("video_type") == "shorts"
    if not is_shorts:
        from agents import packaging
        from video import description
        folder = output_dir or config.OUTPUT_DIR
        try:
            return description.build(folder, script, research or {},
                                     load_json(os.path.join(folder, packaging.DESCRIPTION_PARTS_FILE)))
        except (description.DescriptionError, OSError, KeyError, ValueError) as exc:
            _log(f"Description template not used ({exc}); legacy layout instead", "warn")

    lines = []
    if research.get("hook_question"): lines += [research["hook_question"], ""]
    if script.get("description"):     lines += [script["description"], ""]
    lines += ["This episode is an original work of fiction. Characters and events are fictional.", ""]

    # Chapters only make sense for videos > ~2 min — never for Shorts
    if not is_shorts:
        timeline = load_timeline(os.path.join(output_dir or config.OUTPUT_DIR, "scene_timeline.json"))
        chapters = []
        if timeline and len(timeline.get("scenes", [])) == len(sections):
            chapters = [{"ts": _ts(int(scene["start"])), "title": scene.get("title", "")}
                        for scene in timeline["scenes"]]

        if len(chapters) >= 3:
            lines += ["─"*40, "⏱ CHAPTERS", "─"*40]
            for c in chapters: lines.append(f"{c['ts']} {c['title']}")
            lines.append("")

    if is_shorts:
        lines += ["What would you have done in their place?", ""]
    else:
        lines += ["Private Gravity presents original cinematic stories about the hidden forces that shape us.",
                  "What stayed with you after the ending?", ""]

    manifest = load_json(os.path.join(output_dir or config.OUTPUT_DIR, "asset_manifest.json")) or {}
    attributed = []
    for asset in manifest.get("assets", []):
        if asset.get("source_url") and asset.get("creator"):
            license_note = f" ({asset['license_url']})" if asset.get("license_url") else ""
            attributed.append(f"{asset['creator']} — {asset['source_url']}{license_note}")
    if attributed:
        lines += ["Visual credits:", *dict.fromkeys(attributed), ""]
    music_credit = (manifest.get("music") or {}).get("attribution") if (manifest.get("music") or {}).get("track") else ""
    if music_credit:
        lines += [f"Music: {music_credit}", ""]

    tags = script.get("tags", [])
    if tags: lines.append(" ".join(f"#{t.replace(' ','')}" for t in tags[:8]))
    if is_shorts: lines.append("#Shorts #YouTubeShorts")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE RUNNER  (runs in background thread)
# ══════════════════════════════════════════════════════════════════════════════

# Steps after the script; a run of only ["shots"] or ["visual_bible"] must reach them
# instead of stopping at the Story Brief / Narrative Development early exits.
_MEDIA_STEPS = ("narration", "music", "visual_bible", "shots", "video", "shorts")


def _run_pipeline(steps: list, topic_override: str = "", video_type: str = "normal",
                  focus_angle: str = "", narrative_type: str = "auto",
                  target_duration: float = 15, narrative_review: bool = True):
    orig_stdout = sys.stdout
    sys.stdout = _Capture(orig_stdout)
    _state["running"] = True
    _state["error"]   = None
    _state["stop_requested"] = False
    _stop_event.clear()
    from agents import gemini_client
    gemini_client.clear_call_records()

    def _check_stop(after_stage: str):
        """Raise if user clicked Stop."""
        if _stop_event.is_set():
            raise InterruptedError(f"Stopped by user after {after_stage}")

    def _complete_partial(label: str):
        _log(f"Partial pipeline complete after {label}. Review the saved artifacts before continuing.")
        _log_queue.put(("done", ""))

    try:
        Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
        if video_type == "normal":
            # Phase 3.7: the brief's target duration wins over the dropdown (and is kept in project.json).
            from agents import premise_brief
            try:
                pending = premise_brief.parse(topic_override) if premise_brief.is_brief(topic_override) \
                    else premise_brief.load(config.OUTPUT_DIR) if not topic_override.strip() else None
            except premise_brief.BriefError:
                pending = None
            wanted = premise_brief.target_minutes(pending, target_duration)
            if wanted and wanted != float(target_duration):
                _log(f"Target duration: {wanted:g} min from the premise brief (instead of {float(target_duration):g}).")
                target_duration = wanted
            if projects.is_project(config.OUTPUT_DIR):
                data = projects.load(config.OUTPUT_DIR)
                if data.get("target_duration_minutes") != target_duration:
                    data["target_duration_minutes"] = target_duration
                    projects.save(config.OUTPUT_DIR, data)
        Path(config.IMAGES_DIR).mkdir(exist_ok=True)

        from agents.researcher   import research_topic
        from agents.scriptwriter import write_script
        from video.narrator      import generate_narration
        from video.stock         import load_existing_images
        from video.creator       import create_video
        from video.music         import prepare_final_audio

        # ── Research ──────────────────────────────────────────
        if "research" in steps:
            _stage("research", "running")
            # Phase 3.7-A: a detailed brief is saved in the project and is authoritative; one
            # sentence stays the exact premise as before; an empty field uses the project's brief.
            from agents import premise_brief
            author = None
            if premise_brief.is_brief(topic_override):
                author = premise_brief.save(config.OUTPUT_DIR, premise_brief.parse(topic_override))
                topic_override = ""
            elif not topic_override.strip():
                author = premise_brief.load(config.OUTPUT_DIR)
            research = research_topic(
                channel_description = config.CHANNEL_DESCRIPTION,
                topic_override      = topic_override,
                focus_angle         = focus_angle,
                narrative_type      = narrative_type,
                target_duration_minutes = target_duration,
                premise_brief       = author,
            )
            invalidate_downstream("story_brief", config.OUTPUT_DIR)
            _state["research"] = research
            _state.update({
                "script": None, "character_bible": None, "story_outline": None,
                "narrative_review": None, "image_map": {}, "audio_path": None,
                "video_path": None, "thumb_path": None,
            })
            atomic_write_json(f"{config.OUTPUT_DIR}/research.json", research)
            if projects.is_project(config.OUTPUT_DIR):     # a new project gets its title-based name
                projects.activate(projects.retitle(config.OUTPUT_DIR, research["video_title"]))
                _remember_active(Path(config.OUTPUT_DIR).name)
            _stage("research", "done")
            _log(f"Topic: {research['video_title']}")
        else:
            try:
                with open(f"{config.OUTPUT_DIR}/research.json", encoding="utf-8") as f:
                    _state["research"] = json.load(f)
            except: pass

        _check_stop("research")
        research = _state["research"]
        if not research: raise ValueError("No research data — run Research step first.")
        if not any(step in steps for step in ("script",) + _MEDIA_STEPS):
            _complete_partial("Story Brief")
            return

        # ── Script ────────────────────────────────────────────
        if "script" in steps:
            _stage("script", "running")
            _log(f"Generating {'Shorts' if video_type == 'shorts' else 'normal'} script…")
            voice_calibration.ensure_measured(progress=lambda line: _log(str(line).strip()))
            script = write_script(
                research, video_type=video_type, output_dir=config.OUTPUT_DIR,
                target_duration_minutes=target_duration,
                narrative_review=narrative_review,
            )
            invalidate_downstream("script_changed", config.OUTPUT_DIR)
            _state["script"] = script
            _state.update({"image_map": {}, "audio_path": None,
                           "video_path": None, "thumb_path": None})
            _state["character_bible"] = load_json(os.path.join(config.OUTPUT_DIR, "character_bible.json"))
            _state["story_outline"] = load_json(os.path.join(config.OUTPUT_DIR, "story_outline.json"))
            _state["narrative_review"] = load_json(os.path.join(config.OUTPUT_DIR, "narrative_review.json"))
            _stage("script", "done")
            _log(f"Script: {len(script['sections'])} sections")
        else:
            try:
                with open(f"{config.OUTPUT_DIR}/script.json", encoding="utf-8") as f:
                    _state["script"] = json.load(f)
            except: pass
            _state["character_bible"] = load_json(os.path.join(config.OUTPUT_DIR, "character_bible.json"))
            _state["story_outline"] = load_json(os.path.join(config.OUTPUT_DIR, "story_outline.json"))
            _state["narrative_review"] = load_json(os.path.join(config.OUTPUT_DIR, "narrative_review.json"))

        _check_stop("script")
        script = _state["script"]
        if not script: raise ValueError("No script data — run Script step first.")
        review = _state.get("narrative_review") or {}
        if review:
            _log(
                "Narrative QA: "
                f"{review.get('word_count', 0)}/{review.get('target_word_count', 0)} words; "
                f"status={review.get('status', 'unknown')}; "
                f"blocking={len(review.get('blocking_issues', []))}; "
                f"major={len(review.get('major_issues', []))}."
            )
        unresolved = review.get("status") in ("structurally_invalid", "revision_required")
        downstream_requested = any(step in steps for step in _MEDIA_STEPS)
        # Generating a newly invalid script and rendering it in the same automatic run is unsafe.
        # A later media-only run is an explicit operator override and remains available.
        if "script" in steps and unresolved and downstream_requested:
            _log(
                "Automatic media generation stopped: narrative QA has unresolved blocking/major "
                "issues. Review the per-scene deficits, regenerate selected scenes, or explicitly "
                "run the desired downstream steps after accepting the risk.",
                "warn",
            )
            _state["description"] = _build_description(script, research, video_type=video_type)
            _complete_partial("Narrative QA gate")
            return
        if not any(step in steps for step in _MEDIA_STEPS):
            _state["description"] = _build_description(script, research, video_type=video_type)
            _complete_partial("Narrative Development")
            return

        # Images are the generated shots (Planos, after narration and the visual bible); the legacy
        # "images" step is an alias of "shots" (Phase 3.8-C) and stock stays a setting only.
        if "images" in steps:
            _log("The 'images' step is now 'shots' (Planos, after narration and the visual bible).", "warn")
        steps = list(dict.fromkeys("shots" if step == "images" else step for step in steps))
        _state["image_map"] = load_existing_images(script, config.OUTPUT_DIR)

        # ── Media stages on the graph (Phase 3.8-B) ───────────
        # Narration (Edge TTS) and the visual bible (Gemini) run together; reference candidates
        # start once the visual bible exists; music (CPU) mixes next to the shots (GPU); one GPU job.
        from studio import stages as stage_graph
        from video import shot_generation as shotgen
        from video.imagegen_comfyui import ComfyUnavailable, ImageGenerator
        selected = [stage_graph.stage_for_step(step) for step in steps]
        selected = [stage for stage in dict.fromkeys(selected) if stage and stage not in ("story_brief", "script")]
        if "shots" in selected:
            selected.append("references")            # candidates for unapproved characters, early
        out = config.OUTPUT_DIR
        mixed_path = os.path.join(out, "narration_mixed.mp3")

        def narration_file() -> str:
            path = os.path.join(out, "narration.mp3")
            if not os.path.exists(path):
                raise FileNotFoundError("narration.mp3 missing — run Narration step.")
            _state["audio_path"] = path
            return path

        def job_narration():
            _stage("narration", "running")
            _state["audio_path"] = generate_narration(script, out)
            _stage("narration", "done")

        def job_music():
            _stage("music", "running")
            _log("Preparing final audio (music, ducking, -14 LUFS loudness)…")
            prepare_final_audio(narration_file(), mixed_path, episode={**(research or {}), **(script or {})},
                                output_dir=out)
            _log("Audio ready")
            _stage("music", "done")

        def job_visual_bible():
            _stage("visual_bible", "running")
            shotgen.ensure_visual_bible(out)
            _stage("visual_bible", "done")

        def job_references():
            vb = load_json(os.path.join(out, "visual_bible.json"))      # read only: never builds it
            if not vb:
                return
            outline = load_json(os.path.join(out, "story_outline.json")) or {}
            missing = shotgen.characters_to_approve(vb, outline, out)
            if not missing:
                return
            record = load_json(os.path.join(out, shotgen.CANDIDATES_FILE)) or {}
            fresh = [cid for cid in missing if not [item for item in record.get(f"character:{cid}", [])
                                                    if shotgen.candidate_is_current(f"character:{cid}", item)]]
            if fresh:
                try:
                    shotgen.generate_reference_candidates(vb, out, ImageGenerator(out), characters=fresh,
                                                          progress=_imagegen_progress)
                except ComfyUnavailable as exc:
                    raise stage_graph.Blocked(str(exc)) from None
            raise stage_graph.Blocked("Approve a reference portrait for: " + ", ".join(missing)
                                      + " (Review & Edit → References), then run Shots")

        def job_shots():
            _stage("shots", "running")
            try:
                generator = ImageGenerator(out)
                # Phase 3.8-A: Gemini plans scene by scene while ComfyUI renders what is planned.
                summary = shotgen.plan_and_render(out, generator, progress=_imagegen_progress, stop=_stop_event,
                                                  on_status=lambda lanes: _state.update(lanes=lanes))
                if summary.get("stopped"):
                    raise InterruptedError("Stopped by user during shots")
                if not summary["failed"]:
                    shotgen.generate_thumbnail_image(out, generator)
                    _first_thumbnails(out, generator)
            except shotgen.ApprovalRequired as exc:
                _stage("shots", "pending")
                raise stage_graph.Blocked(str(exc)) from None
            except ComfyUnavailable as exc:
                _stage("shots", "error")
                raise stage_graph.Blocked(str(exc)) from None
            _stage("shots", "done")

        def job_video():
            audio = narration_file()
            if os.path.exists(mixed_path) and os.path.getmtime(mixed_path) >= os.path.getmtime(audio):
                audio = mixed_path                   # the current music mix
            thumb = os.path.join(out, "thumbnail.jpg")          # the selected variant (Review → Thumbnails)
            _state["thumb_path"] = thumb if os.path.exists(thumb) else None
            _stage("video", "running")
            image_map_int = {int(k): v for k, v in _state["image_map"].items()}
            from video.creator import RenderStopped
            try:
                _state["video_path"] = create_video(script, audio, out, image_map_int, video_type=video_type,
                                                    should_stop=_stop_event.is_set)
            except RenderStopped as exc:
                _stage("video", "interrompido")
                raise InterruptedError(str(exc)) from exc
            _stage("video", "done")

        def job_shorts():
            if video_type != "normal":
                return
            from video import shorts, shorts_render
            _stage("shorts", "running")
            plan = shorts.load(out)
            if not plan or not plan.get("segments"):
                plan = shorts.build_plan(out, script=_state.get("script"), brief=_state.get("research"))
                _log(f"Shorts: {len(plan['segments'])} cut(s) proposed — review them in Review & Edit")
            done = shorts_render.render_all(out, progress=lambda message: _log(message.strip()))
            _log(f"Shorts rendered: {len(done)}")
            _stage("shorts", "done")

        def lane_event(stage, status, lane):
            activity = _state.setdefault("lane_activity", {})
            busy = set(activity.get(lane, []))
            busy = busy | {stage} if status == "running" else busy - {stage}
            activity[lane] = sorted(busy)
            if status in ("running", "done", "blocked", "error", "skipped"):
                _log(f"[{lane}] {stage_graph.get(stage).label}: {status}")

        jobs = {"narration": job_narration, "music": job_music, "visual_bible": job_visual_bible,
                "references": job_references, "shots": job_shots, "video": job_video, "shorts": job_shorts}
        outcome = stage_graph.run([stage for stage in selected if stage in jobs], jobs, stop=_stop_event,
                                  on_event=lane_event)
        _state["lane_activity"] = {}
        error = outcome.first_error()
        if error is not None:
            raise error
        if _stop_event.is_set() or "stopped" in outcome.status.values():
            raise InterruptedError("Stopped by user")
        if not _state.get("video_path"):
            vp_short = os.path.join(out, "final_short.mp4")
            vp_norm = os.path.join(out, "final_video.mp4")
            vp = vp_short if os.path.exists(vp_short) else vp_norm
            _state["video_path"] = vp if os.path.exists(vp) else None
        blocked = [stage for stage, status in outcome.status.items() if status == "blocked"]
        if blocked:
            for stage in blocked:
                _log(str(outcome.errors[stage]), "warn")
            _complete_partial(", ".join(stage_graph.get(stage).label for stage in blocked)
                              + " (waiting for you: approve the references in Review & Edit, or start ComfyUI)")
            return

        # ── Build description ─────────────────────────────────
        if _state["research"] and _state["script"]:
            _state["description"] = _build_description(
                _state["script"], _state["research"], video_type=video_type)

        _log("Pipeline complete!", "info")
        _log_queue.put(("done", ""))

    except InterruptedError as e:
        _log(f"⏹ {e}", "warn")
        for s in _state["stages"]:
            if _state["stages"][s] == "running":
                _state["stages"][s] = "pending"
        _log_queue.put(("stopped", ""))

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        _state["error"] = str(e)
        for s in _state["stages"]:
            if _state["stages"][s] == "running":
                _state["stages"][s] = "error"
        _log(f"ERROR: {e}", "error")
        _log_queue.put(("error", str(e)))
    finally:
        _log(gemini_client.usage_line())
        _state["running"] = False
        sys.stdout = orig_stdout


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK APP
# ══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__, template_folder="gui_templates")


@app.route("/")
def index():
    with open(os.path.join(ROOT, "gui_templates", "index.html"), encoding="utf-8") as f:
        return f.read()


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.route("/api/stream")
def stream():
    def gen():
        while True:
            try:
                kind, msg = _log_queue.get(timeout=30)
                data = json.dumps({"kind": kind, "msg": msg})
                yield f"data: {data}\n\n"
                if kind in ("done", "error", "stopped"):
                    break
            except queue.Empty:
                yield "data: {\"kind\":\"ping\"}\n\n"
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── Pipeline control ──────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def run_pipeline():
    if _state["running"]:
        return jsonify({"ok": False, "error": "Pipeline already running"})
    if _state.get("draft"):
        return jsonify({"ok": False, "error": "Crie o projeto para começar (Novo projeto → Criar projeto e abrir)"}), 409
    body       = request.get_json(force=True) or {}
    steps       = body.get("steps", ["research","script","images","narration","music","video"])
    if not isinstance(steps, list) or not steps:
        return jsonify({"ok": False, "error": "Select at least one pipeline step"}), 400
    if "images" in steps:                           # the legacy step before narration (Phase 3.8-C)
        _log("The 'images' step is now 'shots' (Planos, after narration and the visual bible).", "warn")
        steps = list(dict.fromkeys("shots" if step == "images" else step for step in steps))
    topic       = body.get("topic", "").strip()
    focus_angle = body.get("focus_angle", "").strip()
    video_type  = body.get("video_type", "normal")
    narrative_type = body.get("narrative_type", "auto")
    narrative_review = bool(body.get("narrative_review", True))
    try:
        target_duration = float(body.get("target_duration", 15))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Target duration must be a number"}), 400
    from agents.narrative import NARRATIVE_TYPES
    if narrative_type != "auto" and narrative_type not in NARRATIVE_TYPES:
        return jsonify({"ok": False, "error": "Invalid narrative type"}), 400
    if video_type not in ("normal", "shorts"):
        return jsonify({"ok": False, "error": "Invalid video format"}), 400
    low, high = config.EPISODE_MINUTES
    if video_type == "normal" and not low <= target_duration <= high:
        return jsonify({"ok": False, "error": f"Regular episodes must target {low:g}-{high:g} minutes"}), 400
    guard = _regeneration_guard(steps, body)
    if guard:
        return guard
    for s in steps:
        if s in _state["stages"]: _state["stages"][s] = "pending"
    _state["video_type"] = video_type
    _stop_event.clear()
    t = threading.Thread(
        target=_run_pipeline,
        args=(steps, topic, video_type, focus_angle, narrative_type,
              target_duration, narrative_review), daemon=True)
    t.start()
    return jsonify({"ok": True})


# ── Projects ──────────────────────────────────────────────────────────────────

_STAGE_FOR_SIDEBAR = {"research": "story_brief", "script": "script", "narration": "narration",
                      "visual_bible": "visual_bible", "shots": "shots", "video": "video"}


def _reload_state_from_disk():
    """Point the GUI state at the artifacts of the current folder (project or legacy output/)."""
    out = Path(config.OUTPUT_DIR)
    research = load_json(out / "research.json") or load_json(out / "story_brief.json")
    script = load_json(out / "script.json")
    video = next((str(out / name) for name in ("final_short.mp4", "final_video.mp4") if (out / name).is_file()), None)
    statuses = {item["stage"]: item["status"] for item in projects.stage_status(out)}
    info = projects.load(out)
    _state.update({
        "research": research, "script": script,
        "character_bible": load_json(out / "character_bible.json"),
        "story_outline": load_json(out / "story_outline.json"),
        "narrative_review": load_json(out / "narrative_review.json"),
        "audio_path": str(out / "narration.mp3") if (out / "narration.mp3").is_file() else None,
        "video_path": video, "thumb_path": str(out / "thumbnail.jpg") if (out / "thumbnail.jpg").is_file() else None,
        "image_map": {}, "yt_url": None, "error": None,
        "video_type": info.get("video_type") or (script or {}).get("video_type") or "normal",
    })
    _state["description"] = _build_description(script, research, _state["video_type"]) if script and research else ""
    for name, stage in _STAGE_FOR_SIDEBAR.items():
        _state["stages"][name] = "done" if statuses.get(stage) == "done" else "pending"
    _state["stages"]["images"] = _state["stages"]["shots"]


def _running_stages() -> list[str]:
    """Stage ids running now (from the GUI step states), for "em execução" on the cards."""
    from studio import stages as stage_graph
    names = [name for name, status in _state["stages"].items() if status == "running"]
    return sorted({stage_graph.stage_for_step(name) or name for name in names})


GUI_STATE_FILE = Path(__file__).resolve().parent / "data" / "gui_state.json"


def _remember_active(slug: str | None) -> None:
    """The GUI's active project (data/gui_state.json); None = a blank "Novo projeto" draft."""
    try:
        atomic_write_json(Path(getattr(config, "GUI_STATE_FILE", GUI_STATE_FILE)), {"active": slug})
    except OSError:
        pass


def _remembered_active() -> str | None:
    state = load_json(Path(getattr(config, "GUI_STATE_FILE", GUI_STATE_FILE)))
    return state.get("active") if state else projects.active_slug()


def _enter_draft() -> None:
    """A blank "Novo projeto (não criado)": no project is loaded, so every panel shows its empty state
    (an empty scratch folder stands in for the project folder until the wizard creates one)."""
    import tempfile
    projects.activate(Path(tempfile.mkdtemp(prefix="pg-draft-")))
    _state["draft"] = True
    _reload_state_from_disk()


def _switch_project(path) -> None:
    """Make ``path`` the project every panel reads (server side), and remember it."""
    path = projects.activate(path)
    _state["draft"] = False
    projects.set_active(path.name)
    _remember_active(path.name)
    _reload_state_from_disk()


def startup() -> None:
    """Open the last project unless it is archived or gone; then the GUI starts on a blank draft and the
    home screen lists the projects."""
    slug = _remembered_active()
    try:
        path = projects.path_for(slug) if slug else None
    except projects.ProjectNotFound:
        path = None
    if path is None or projects.is_archived(path):
        _enter_draft()
    else:
        _switch_project(path)


@app.after_request
def _project_header(response):
    """Every API answer says which project it is about, so the GUI drops late answers for another one."""
    if request.path.startswith("/api/"):
        out = Path(config.OUTPUT_DIR)
        response.headers["X-Project-Slug"] = "" if _state.get("draft") or not projects.is_project(out) else out.name
    return response


def _current_summary() -> dict:
    from audio import tts
    out = Path(config.OUTPUT_DIR)
    if _state.get("draft"):
        return {"project": False, "draft": True, "slug": None, "title": "Novo projeto (não criado)", "stages": [],
                "next_step": None, "thumbnail": None, "channel_voice": tts.identity()}
    if projects.is_project(out):
        return {"project": True, **projects.summary(out), "running": _running_stages(), "channel_voice": tts.identity()}
    statuses = projects.stage_status(out)
    return {"project": False, "slug": None, "title": "output/ (legacy folder)", "stages": statuses,
            "next_step": projects.next_step(statuses), "thumbnail": None, "channel_voice": tts.identity()}


@app.route("/api/stages")
def stages_graph():
    """Phase 3.8-C: the one stage list (order, labels, dependencies, lanes) the GUI renders."""
    from studio import stages as stage_graph
    return jsonify({"ok": True, "stages": stage_graph.as_json(), "aliases": stage_graph.STEP_ALIASES})


@app.route("/api/projects")
def list_projects():
    legacy = Path(projects.LEGACY_DIR)
    return jsonify({
        "projects": [projects.summary(path) for path in projects.all_paths()],
        "active": None if _state.get("draft") else projects.active_slug(),
        "current": Path(config.OUTPUT_DIR).name if projects.is_project(config.OUTPUT_DIR) else None,
        "legacy_unmigrated": (legacy / "script.json").is_file() or (legacy / "story_brief.json").is_file(),
    })


@app.route("/api/projects/archive", methods=["POST"])
def archive_project_endpoint():
    """"Arquivar": move a published project to the HDD archive (dry_run lists the actions)."""
    from studio import archive
    body = request.get_json(force=True) or {}
    try:
        path = projects.path_for(str(body.get("slug", "")))
    except projects.ProjectNotFound as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    if _state["running"] or _state.get("imagegen_busy") or _state.get("archiving"):
        return jsonify({"ok": False, "error": "Wait for the running step to finish"}), 409
    if body.get("dry_run"):
        try:
            work = archive.archive_project(path, dry_run=True)
        except archive.ArchiveError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "target": work["target"], "megabytes": round(work["payload_bytes"] / 2**20),
                        "actions": len(work["actions"])})
    if archive.publication(path) is None:
        return jsonify({"ok": False, "error": "Only published projects can be archived from the GUI "
                                              "(use the CLI with --force --reason)"}), 400

    def work():
        _state["archiving"] = True
        try:
            archive.archive_project(path, log=lambda message: _log(message))
        except (archive.ArchiveError, OSError) as exc:
            _log(f"Archive of {path.name} failed: {exc}", "error")
        finally:
            _state["archiving"] = False
    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.route("/projects/<slug>/thumbnail")
def project_thumbnail(slug):
    try:
        path = projects.path_for(slug)
    except projects.ProjectNotFound:
        abort(404)
    for name in ("thumbnail.jpg", "thumbnail_bg.jpg"):
        if (path / name).is_file():
            return send_file(str(path / name), mimetype="image/jpeg")
    abort(404)


@app.route("/api/projects/current")
def current_project():
    return jsonify(_current_summary())


@app.route("/api/projects/open", methods=["POST"])
def open_project():
    if _state["running"] or _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running step to finish"}), 409
    slug = str((request.get_json(force=True) or {}).get("slug", ""))
    try:
        path = projects.path_for(slug)
    except projects.ProjectNotFound as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    if projects.is_archived(path):
        return jsonify({"ok": False, "archived": True, "slug": slug,
                        "error": "Este projeto está arquivado. Use “Restaurar” antes de abrir para edição."}), 409
    _switch_project(path)
    return jsonify({"ok": True, **_current_summary()})


@app.route("/api/projects/draft", methods=["POST"])
def draft_project():
    """"Novo projeto": a blank draft until the wizard creates the project."""
    if _state["running"] or _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running step to finish"}), 409
    _enter_draft()
    _remember_active(None)
    return jsonify({"ok": True, **_current_summary()})


@app.route("/api/projects/restore", methods=["POST"])
def restore_project_endpoint():
    """"Restaurar": copy an archived project back from the archive (a background job)."""
    from studio import archive
    slug = str((request.get_json(force=True) or {}).get("slug", ""))
    try:
        path = projects.path_for(slug)
    except projects.ProjectNotFound as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    if not projects.is_archived(path):
        return jsonify({"ok": False, "error": "This project is not archived"}), 400
    return _imagegen_run(lambda: archive.restore_project(slug, log=lambda line: _log(line)))


@app.route("/api/projects/new", methods=["POST"])
def new_project():
    if _state["running"] or _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running step to finish"}), 409
    body = request.get_json(force=True) or {}
    video_type = body.get("video_type", "normal") if body.get("video_type") in ("normal", "shorts") else "normal"
    try:
        target = float(body.get("target_duration", 15))
    except (TypeError, ValueError):
        target = 15.0
    from agents import premise_brief
    from agents.narrative import NARRATIVE_TYPES
    premise = str(body.get("premise", "")).strip()
    narrative_type = body.get("narrative_type", "auto")
    if narrative_type != "auto" and narrative_type not in NARRATIVE_TYPES:
        return jsonify({"ok": False, "error": "Invalid narrative type"}), 400
    brief = None
    if premise_brief.is_brief(premise):
        try:
            brief = premise_brief.parse(premise)
        except premise_brief.BriefError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        target = premise_brief.target_minutes(brief, target) or target
    low, high = config.EPISODE_MINUTES
    if video_type == "normal" and not low <= target <= high:
        return jsonify({"ok": False, "error": f"Regular episodes must target {low:g}-{high:g} minutes"}), 400
    path = projects.create(str(body.get("title", "") or (brief or {}).get("working_title", "")),
                           video_type=video_type, target_duration=target)
    # "Novo projeto" (Phase 3.8-C): the wizard's choices; the Story Brief stage uses them.
    data = projects.load(path)
    data.update(premise="" if brief else premise, focus_angle=str(body.get("focus_angle", "")).strip(),
                narrative_type=narrative_type, narrative_review=bool(body.get("narrative_review", True)))
    projects.save(path, data)
    if brief:
        premise_brief.save(path, brief)
    _switch_project(path)
    return jsonify({"ok": True, **_current_summary()})


@app.route("/api/premise-brief")
def premise_brief_info():
    """The template ("Inserir modelo") and the current project's brief, if any."""
    from agents import premise_brief
    path = Path(config.OUTPUT_DIR, premise_brief.MARKDOWN_FILE)
    return jsonify({"ok": True, "template": premise_brief.TEMPLATE, "max_chars": premise_brief.MAX_CHARS,
                    "current": path.read_text(encoding="utf-8") if path.is_file() else ""})


@app.route("/api/premise-brief/parse", methods=["POST"])
def premise_brief_parse():
    """Which fields the text provides (shown under the textarea); nothing is saved."""
    from agents import premise_brief
    text = str((request.get_json(force=True) or {}).get("text", ""))
    if not premise_brief.is_brief(text):
        return jsonify({"ok": True, "brief": False, "fields": []})
    try:
        brief = premise_brief.parse(text)
    except premise_brief.BriefError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "brief": True, "fields": [key for key in premise_brief.FIELDS if key in brief],
                    "notes": bool(brief.get("notes")), "target_minutes": premise_brief.target_minutes(brief)})


@app.route("/api/shorts")
def shorts_status():
    """Phase 3.7-B: the Shorts plan with the sentences it can be cut on."""
    from video import shorts
    output = Path(config.OUTPUT_DIR)
    words = shorts.word_timeline(output, _state.get("script"))
    sentence_list = shorts.sentences(words)
    plan = shorts.load(output) or {}
    audio = next((name for name in ("narration_mixed.mp3", "narration.mp3") if (output / name).is_file()), None)
    for item in plan.get("segments", []):
        video = output / shorts.FOLDER / f"{item['id']}.mp4"
        item["video"] = f"{shorts.FOLDER}/{item['id']}.mp4" if video.is_file() else None
    return jsonify({"ok": True, "plan": plan, "audio": bool(audio), "limits": shorts.limits(),
                    "sentences": [{key: item[key] for key in ("index", "start", "end", "text")} for item in sentence_list],
                    "busy": _state.get("imagegen_busy", False)})


@app.route("/api/shorts/plan", methods=["POST"])
def shorts_plan():
    """Gemini proposes the segments (validated by code); a new proposal replaces the plan."""
    from video import shorts
    force = bool((request.get_json(force=True) or {}).get("force"))
    if not shorts.word_timeline(Path(config.OUTPUT_DIR), _state.get("script")):
        return jsonify({"ok": False, "error": "Run the narration step first (the plan needs real timings)"}), 400

    def run():
        plan = shorts.build_plan(config.OUTPUT_DIR, script=_state.get("script"), brief=_state.get("research"),
                                 force=force)
        _imagegen_progress(f"Shorts: {len(plan['segments'])} segmento(s) propostos")
    return _imagegen_run(run)


@app.route("/api/shorts/save", methods=["POST"])
def shorts_save():
    from agents.narrative import NarrativeValidationError
    from video import shorts
    segments = (request.get_json(force=True) or {}).get("segments")
    try:
        plan = shorts.save(config.OUTPUT_DIR, segments, brief=_state.get("research"))
    except (NarrativeValidationError, KeyError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "plan": plan})


@app.route("/api/shorts/render", methods=["POST"])
def shorts_render():
    """Render every included Short of the plan (1080x1920), or one (``id``)."""
    from video import shorts, shorts_render as renderer
    if not (shorts.load(config.OUTPUT_DIR) or {}).get("segments"):
        return jsonify({"ok": False, "error": "No Shorts plan yet"}), 400
    only = (request.get_json(force=True) or {}).get("id")

    def run():
        done = renderer.render_all(config.OUTPUT_DIR, progress=_imagegen_progress, only=only)
        _imagegen_progress(f"Shorts renderizados: {len(done)}")
    return _imagegen_run(run)


@app.route("/api/shorts/export", methods=["POST"])
def shorts_export():
    """Phase 4.0-H "Exportar para TikTok / Reels": the 9:16 file and a caption per platform; nothing is posted."""
    from uploader import social_export
    try:
        found = social_export.export(config.OUTPUT_DIR, str((request.get_json(force=True) or {}).get("id", "")))
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **found})


@app.route("/api/shorts/audio")
def shorts_audio():
    """The episode audio for play-from/to in the Shorts review."""
    output = Path(config.OUTPUT_DIR)
    path = next((output / name for name in ("narration_mixed.mp3", "narration.mp3") if (output / name).is_file()), None)
    if path is None:
        abort(404)
    return send_file(str(path), mimetype="audio/mpeg", conditional=True)


def _regeneration_guard(steps: list, body: dict):
    """Re-running the Story Brief (the "research" step) discards the screenplay and everything after
    it: the GUI must confirm, seeing the list, before it runs (409 + impact otherwise)."""
    if "research" not in steps or body.get("confirm_regenerate"):
        return None
    impact = projects.regeneration_impact(config.OUTPUT_DIR)
    if not impact["discard"]:
        return None
    return jsonify({"ok": False, "needs_regenerate_confirmation": True, "impact": impact,
                    "error": "Regenerating the Story Brief discards: " + ", ".join(impact["discard"])}), 409


@app.route("/api/projects/discard", methods=["POST"])
def discard_project_endpoint():
    """"Descartar projeto": delete an empty project (only project.json, nothing generated)."""
    body = request.get_json(force=True) or {}
    try:
        path = projects.path_for(str(body.get("slug", "")))
    except projects.ProjectNotFound as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    if _state["running"] or _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running step to finish"}), 409
    current = Path(config.OUTPUT_DIR).resolve() == path.resolve()
    try:
        projects.discard(path)
    except (ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if current:                                   # nothing loaded: a blank draft
        _enter_draft()
        _remember_active(None)
    return jsonify({"ok": True})


@app.route("/api/projects/stage/<stage>", methods=["POST"])
def project_stage(stage):
    """Run / re-run / skip one stage of the current project, respecting existing artifacts."""
    if stage not in projects.STAGE_KEYS:
        return jsonify({"ok": False, "error": f"Unknown stage {stage}"}), 404
    if _state.get("draft"):
        return jsonify({"ok": False, "error": "Crie o projeto para começar"}), 409
    body = request.get_json(force=True) or {}
    action = body.get("action", "run")
    out = Path(config.OUTPUT_DIR)
    if action == "skip":
        if not projects.is_project(out):
            return jsonify({"ok": False, "error": "Skipping is recorded per project; open a project first"}), 400
        data = projects.load(out)
        data["skipped"] = sorted(set(data.get("skipped") or []) | {stage})
        projects.save(out, data)
        projects.touch(out)
        return jsonify({"ok": True, **_current_summary()})
    if action not in ("run", "rerun", "from_here"):
        return jsonify({"ok": False, "error": "action must be run, rerun, from_here or skip"}), 400
    if _state["running"] or _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Another step is already running"}), 409
    statuses = projects.stage_status(out)
    upstream = projects.blocking_upstream(statuses, stage)
    if upstream and not body.get("confirm") and not body.get("rerun_upstream"):
        return jsonify({"ok": False, "needs_confirmation": True, "upstream": upstream,
                        "error": "Upstream stages need work first: "
                                 + ", ".join(f"{item['label']} ({item['status']})" for item in upstream)}), 409
    if projects.is_project(out):
        data = projects.load(out)
        if stage in (data.get("skipped") or []):
            data["skipped"] = [item for item in data["skipped"] if item != stage]
            projects.save(out, data)
    if stage == "references":
        return imagegen_candidates()
    if stage == "publish":
        return jsonify({"ok": False, "error": "Publish from the Review & Edit tab (approval gate)"}), 400
    steps = []
    if body.get("rerun_upstream"):
        for item in upstream:
            steps += [step for step in projects.steps_for(item["stage"]) if step not in steps]
    steps += [step for step in projects.steps_for(stage) if step not in steps]
    if action == "from_here":                        # "Rodar a partir daqui": this stage and everything after it
        from studio import stages as stage_graph
        for later in stage_graph.downstream(stage):
            steps += [step for step in projects.steps_for(later) if step not in steps]
    guard = _regeneration_guard(steps, body)
    if guard:
        return guard
    info = projects.load(out)
    video_type = info.get("video_type") or _state.get("video_type") or "normal"
    target = float(info.get("target_duration_minutes") or 15)
    for name in steps:
        if name in _state["stages"]:
            _state["stages"][name] = "pending"
    _state["video_type"] = video_type
    _stop_event.clear()
    # The "Novo projeto" choices (premise, theme, type) feed the Story Brief stage.
    threading.Thread(target=_run_pipeline,
                     args=(steps, info.get("premise", ""), video_type, info.get("focus_angle", ""),
                           info.get("narrative_type", "auto"), target, info.get("narrative_review", True)),
                     daemon=True).start()
    return jsonify({"ok": True, "steps": steps})


@app.route("/api/video/preflight")
def video_preflight():
    """What the Video stage checks before it renders."""
    from video import render_check
    return jsonify({"ok": True, **render_check.preflight(config.OUTPUT_DIR)})


@app.route("/api/stop", methods=["POST"])
def stop_pipeline():
    """Stop: the running video encode ends at once and the other stages at their next checkpoint."""
    _stop_event.set()
    _state["stop_requested"] = True
    _log("⏹ Parada pedida (botão Parar): um vídeo em render é interrompido; outras etapas param no próximo ponto seguro", "warn")
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    return jsonify({
        "running":        _state["running"],
        "stop_requested": _state.get("stop_requested", False),
        "stages":         _state["stages"],
        "has_video":      bool(_state.get("video_path") and os.path.exists(_state["video_path"])),
        "has_thumb":      os.path.exists(os.path.join(config.OUTPUT_DIR, "thumbnail.jpg")),
        "has_script":     bool(_state.get("script")),
        "yt_url":         _state.get("yt_url"),
        "error":          _state.get("error"),
        "upload_pct":     _state.get("upload_pct", 0),
        "uploading":      _state.get("uploading", False),
        "video_type":     _state.get("video_type", "normal"),
        "lanes":          _state.get("lanes"),          # Phase 3.8: planning / images progress
        "lane_activity":  _state.get("lane_activity") or {},   # stages running per lane
    })


# ── Media serving ─────────────────────────────────────────────────────────────

@app.route("/video")
def serve_video():
    p = _state.get("video_path")
    if p and os.path.exists(p): return send_file(p, mimetype="video/mp4")
    abort(404)

@app.route("/thumbnail")
def serve_thumb():
    """The open project's thumbnail.jpg (the selected variant), never another project's."""
    p = os.path.join(config.OUTPUT_DIR, "thumbnail.jpg")
    if os.path.exists(p): return send_file(p, mimetype="image/jpeg", max_age=0)
    abort(404)

@app.route("/thumbnail/<int:ts>")  # cache-busting route
def serve_thumb_ts(ts):
    return serve_thumb()


# ── Script / description data ─────────────────────────────────────────────────

@app.route("/api/output")
def get_output():
    s = _state.get("script") or {}
    r = _state.get("research") or {}
    return jsonify({
        "title":       s.get("title", ""),
        "description": _state.get("description", _build_description(s, r, _state.get("video_type","normal")) if s and r else ""),
        "tags":        s.get("tags", []),
        "sections":    s.get("sections", []),
        "topic":       r.get("topic", ""),
        "why_now":     r.get("why_now", ""),
        "hook":        r.get("hook_question", ""),
        "story_brief": r,
        "character_bible": _state.get("character_bible") or {},
        "story_outline": _state.get("story_outline") or {},
        "narrative_review": _state.get("narrative_review") or {},
    })

def _title_findings(title: str) -> list:
    brief = _state.get("research") or load_json(Path(config.OUTPUT_DIR, "story_brief.json")) or {}
    if not brief.get("psychological_theme") and _state.get("script"):
        brief = {"psychological_theme": _state["script"].get("psychological_theme", "")}
    return validate_title(title, brief)


@app.route("/api/validate-title", methods=["POST"])
def validate_title_route():
    body = request.get_json(force=True) or {}
    findings = _title_findings(str(body.get("title", "")))
    return jsonify({"ok": True, "passes": title_passes(findings), "findings": findings})


@app.route("/api/titles")
def titles_status():
    """Phase 3.6-B: the scored title candidates (best first), the chosen one and earlier choices."""
    from agents import packaging
    output = Path(config.OUTPUT_DIR)
    record = load_json(output / packaging.TITLES_FILE) or {}
    brief = _state.get("research") or load_json(output / "story_brief.json") or {}
    candidates = record.get("candidates") or brief.get("title_scores") or []
    script = _state.get("script") or {}
    return jsonify({"ok": True, "candidates": candidates, "selected": record.get("selected") or script.get("title", ""),
                    "history": record.get("history", []),
                    "thumbnail_text": packaging.thumbnail_text_for(output, script, brief)})


@app.route("/api/titles/score", methods=["POST"])
def titles_score():
    """Score the brief's candidates (plus the current title) with Gemini — for projects made before 3.6."""
    from agents import packaging
    from agents.gemini_client import generate
    output = Path(config.OUTPUT_DIR)
    brief = _state.get("research") or load_json(output / "story_brief.json") or {}
    review = brief.get("title_review") or {}
    candidates = list(review.get("title_candidates") or brief.get("title_candidates") or [])
    current = (_state.get("script") or {}).get("title", "")
    if current:
        candidates.append({"title": current, "rationale": "current title"})
    if not candidates:
        return jsonify({"ok": False, "error": "No title candidates in the story brief"}), 400

    def run():
        scored = packaging.score_titles(brief, candidates, generator=generate)
        packaging.save_titles(output, scored, current or (scored[0]["title"] if scored else ""))
        _imagegen_progress(f"Títulos pontuados: {len(scored)}")
    return _imagegen_run(run)


@app.route("/api/titles/select", methods=["POST"])
def titles_select():
    """Use one of the scored titles (or an edited one); the previous choice stays in the history."""
    from agents import packaging
    title = str((request.get_json(force=True) or {}).get("title", "")).strip()
    if not title or not _state.get("script"):
        return jsonify({"ok": False, "error": "No title or no script loaded"}), 400
    _state["script"]["title"] = title
    atomic_write_json(f"{config.OUTPUT_DIR}/script.json", _state["script"])
    packaging.save_titles(config.OUTPUT_DIR, None, title, reason="user")
    findings = _title_findings(title)
    return jsonify({"ok": True, "title": title, "title_passes": title_passes(findings), "title_findings": findings})


@app.route("/api/description/check")
def description_check():
    """Findings of the current description and the pinned-comment suggestion (pin it by hand)."""
    from agents import packaging
    from video import description
    text = _state.get("description") or ""
    parts = load_json(Path(config.OUTPUT_DIR, packaging.DESCRIPTION_PARTS_FILE)) or {}
    findings = description.validate(text)
    return jsonify({"ok": True, "findings": findings, "passes": description.passes(findings),
                    "chars": len(text), "pinned_comment": parts.get("pinned_comment", "")})


@app.route("/api/description/parts", methods=["POST"])
def description_parts():
    """Gemini writes the hook, chapter titles, comment question and pinned comment; then the
    description is rebuilt from the template."""
    from agents import packaging
    from agents.gemini_client import generate
    output = Path(config.OUTPUT_DIR)
    script = _state.get("script")
    if not script:
        return jsonify({"ok": False, "error": "No script loaded"}), 400

    def run():
        brief = _state.get("research") or load_json(output / "story_brief.json") or {}
        packaging.generate_description_parts(output, brief=brief, script=script,
                                             timeline=load_timeline(output / "scene_timeline.json"),
                                             generator=generate, force=True)
        _state["description"] = _build_description(script, brief, _state.get("video_type", "normal"))
        _imagegen_progress("Descrição refeita pelo modelo (gancho, capítulos, CTA)")
    return _imagegen_run(run)


@app.route("/api/description-template", methods=["GET", "POST"])
def description_template():
    """Settings: the description template (placeholders {hook} {chapters} {cta} {channel_line}
    {fiction_note} {credits} {hashtags} {title}; {chapters} and {cta} are required)."""
    from video import description
    if request.method == "GET":
        return jsonify({"ok": True, "template": description.template()})
    try:
        description.save_template(str((request.get_json(force=True) or {}).get("template", "")))
    except description.DescriptionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/api/analytics")
def analytics_overview():
    """Desempenho: the channel overview and every published video, from the cache (no API call)."""
    from uploader import analytics
    data = analytics.overview()
    slug = Path(config.OUTPUT_DIR).name
    data["project_videos"] = [item["video_id"] for item in data["videos"] if item.get("project") == slug]
    data["status"] = analytics.status()                  # Phase 4.0-C: last refresh and its error
    return jsonify({"ok": True, **data, "busy": _state.get("analytics_busy", False),
                    "message": _state.get("analytics_message", "")})


@app.route("/api/analytics/<video_id>")
def analytics_video(video_id):
    from uploader import analytics
    record = analytics.cached(video_id)
    if record is None:
        return jsonify({"ok": False, "error": "No data yet; click Atualizar"}), 404
    return jsonify({"ok": True, **record})


@app.route("/api/analytics/authorize", methods=["POST"])
def analytics_authorize():
    """Phase 4.0-C: ask for the YouTube consent again (opens the browser on this computer; the person signs in),
    then refresh. The token is never shown."""
    from uploader import analytics
    if _state.get("analytics_busy"):
        return jsonify({"ok": False, "error": "Já atualizando"}), 409

    def work():
        _state.update(analytics_busy=True, analytics_message="Aguardando a autorização no navegador…")
        try:
            done = analytics.refresh_with_status(force=True, interactive=True)["videos"]
            _state["analytics_message"] = f"Autorizado e atualizado: {len(done)} vídeo(s)"
        except Exception as exc:                         # noqa: BLE001
            _state["analytics_message"] = f"Erro: {str(exc)[:200]}"
        finally:
            _state["analytics_busy"] = False
    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/swap/proposals")
def swap_proposals():
    """Phase 4.0-C: packaging swaps the rule proposes (never applied without a click)."""
    from uploader import packaging_swap
    return jsonify({"ok": True, **packaging_swap.proposals()})


@app.route("/api/swap/apply", methods=["POST"])
def swap_apply():
    from uploader import packaging_swap
    body = request.get_json(force=True) or {}
    try:
        entry = packaging_swap.apply(str(body.get("video_id", "")), str(body.get("kind", "")))
    except (LookupError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:                             # noqa: BLE001 — auth, network
        return jsonify({"ok": False, "error": f"{exc.__class__.__name__}: {str(exc)[:240]}"}), 500
    return jsonify({"ok": True, "swap": entry})


@app.route("/api/swap/thumbnail")
def swap_thumbnail():
    from uploader import packaging_swap
    path = packaging_swap.variant_path(request.args.get("project", ""), request.args.get("path", ""))
    if not path:
        return "", 404
    return send_file(str(path), mimetype="image/jpeg")


@app.route("/api/swap/test-compare", methods=["POST"])
def swap_test_compare():
    """Export the 3 thumbnails and titles for YouTube Studio's Test & Compare (no API for it)."""
    from uploader import packaging_swap
    try:
        folder = packaging_swap.export_test_compare(str((request.get_json(force=True) or {}).get("video_id", "")))
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "folder": str(folder)})


@app.route("/api/after-upload", methods=["GET", "POST"])
def after_upload_checklist():
    """Phase 4.0-E "Depois do envio": GET ?video_id= the checklist with ready-to-copy values (and the Community
    drafts, 4.0-G); POST {video_id, item, ticked} ticks an item in the ledger."""
    from uploader import after_upload
    try:
        if request.method == "POST":
            body = request.get_json(force=True) or {}
            after_upload.tick(str(body.get("video_id", "")), str(body.get("item", "")), bool(body.get("ticked", True)))
            video_id = str(body.get("video_id", ""))
        else:
            video_id = request.args.get("video_id", "")
        generator = None
        if request.args.get("drafts") == "gemini":
            from agents.gemini_client import generate as generator
        return jsonify({"ok": True, **after_upload.checklist(video_id, generator=generator)})
    except (LookupError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/analytics/refresh", methods=["POST"])
def analytics_refresh():
    """Fetch now (Analytics + Reporting APIs). Needs the yt-analytics.readonly consent: when it is missing
    the message says to run python -m scripts.sync_publications once."""
    from uploader import analytics
    if _state.get("analytics_busy"):
        return jsonify({"ok": False, "error": "Already refreshing"}), 409

    def work():
        _state.update(analytics_busy=True, analytics_message="Atualizando…")
        try:
            done = analytics.refresh_with_status(force=True, interactive=False)["videos"]
            _state["analytics_message"] = f"Atualizado: {len(done)} vídeo(s)"
        except Exception as exc:                      # noqa: BLE001 — shown in the tab
            _state["analytics_message"] = f"Erro: {exc}"
        finally:
            _state["analytics_busy"] = False
    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/save-output", methods=["POST"])
def save_output():
    body = request.get_json(force=True) or {}
    if _state["script"]:
        if "title"       in body: _state["script"]["title"]       = body["title"]
        if "description" in body: _state["script"]["description"] = body["description"]
        if "tags"        in body: _state["script"]["tags"]        = body["tags"]
    if "description" in body:
        _state["description"] = body["description"]
    # Persist script to disk
    if _state["script"]:
        atomic_write_json(f"{config.OUTPUT_DIR}/script.json", _state["script"])
        # Packaging edits do not invalidate narration. The thumbnail is the
        # only derived visual that contains the editable title.
        if "title" in body and not Path(config.OUTPUT_DIR, "thumbnails", "thumbnails.json").is_file():
            Path(config.OUTPUT_DIR, "thumbnail.jpg").unlink(missing_ok=True)   # variants carry no title
        if "title" in body:
            from agents import packaging
            packaging.save_titles(config.OUTPUT_DIR, None, body["title"], reason="edited")
    findings = _title_findings(body["title"]) if "title" in body else []
    return jsonify({"ok": True, "title_passes": title_passes(findings), "title_findings": findings})


@app.route("/api/regenerate-scene", methods=["POST"])
def regenerate_one_scene():
    if _state.get("running"):
        return jsonify({"ok": False, "error": "Pipeline already running"}), 409
    body = request.get_json(force=True) or {}
    try:
        scene_id = int(body.get("scene_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "scene_id must be an integer"}), 400
    script = _state.get("script") or load_json(Path(config.OUTPUT_DIR, "script.json"))
    brief = _state.get("research") or load_json(Path(config.OUTPUT_DIR, "story_brief.json"))
    bible = _state.get("character_bible") or load_json(Path(config.OUTPUT_DIR, "character_bible.json"))
    outline = _state.get("story_outline") or load_json(Path(config.OUTPUT_DIR, "story_outline.json"))
    if not all((script, brief, bible, outline)):
        return jsonify({"ok": False, "error": "Story artifacts are incomplete"}), 409
    try:
        from agents.scriptwriter import regenerate_scene
        revised = regenerate_scene(script, brief, bible, outline, scene_id, config.OUTPUT_DIR)
        invalidate_scene_outputs(scene_id, config.OUTPUT_DIR)
        _state["script"] = revised
        _state["narrative_review"] = load_json(Path(config.OUTPUT_DIR, "narrative_review.json"))
        _state["audio_path"] = None
        _state["video_path"] = None
        _state["thumb_path"] = None
        return jsonify({"ok": True, "scene": next(
            scene for scene in revised["sections"] if int(scene["id"]) == scene_id
        )})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── AI description regeneration ───────────────────────────────────────────────

@app.route("/api/regenerate-description", methods=["POST"])
def regen_description():
    if not _state.get("script") or not _state.get("research"):
        return jsonify({"ok": False, "error": "No script loaded"})
    try:
        from agents.gemini_client import generate
        script   = _state["script"]
        research = _state["research"]
        prompt = f"""Write a restrained YouTube description for this original cinematic fiction episode.

Title: {script.get('title','')}
Topic: {research.get('topic','')}
Hook: {research.get('hook_question','')}
Logline: {research.get('logline','')}
Emotional experience: {research.get('intended_emotional_experience','')}
Tags: {json.dumps(script.get('tags',[]))}

The description should:
- Open with a compelling one-sentence dramatic premise without spoiling the reversal
- Clearly state that the episode is an original work of fiction
- Invite reflection with one story-specific question
- Use no learning bullets, factual claims, generic hype, or manipulative promises
- End with a small set of relevant hashtags

Return ONLY the description text, no explanation."""
        desc = generate(prompt)
        _state["description"] = desc
        return jsonify({"ok": True, "description": desc})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Thumbnail regeneration ────────────────────────────────────────────────────

@app.route("/api/thumbnails")
def thumbnails_status():
    """The thumbnail gallery (Phase 3.8-E): every variant (newest first in the GUI), the selected one, the
    running generation's progress and the last error of the background job."""
    from agents import packaging
    from video import thumbnails
    output = Path(config.OUTPUT_DIR)
    index = load_json(output / thumbnails.FOLDER / thumbnails.INDEX) or {"variants": [], "selected": None}
    concepts = load_json(output / packaging.CONCEPTS_FILE) or {}
    return jsonify({"ok": True, **index, "concepts": concepts.get("concepts", []),
                    "busy": _state.get("imagegen_busy", False), "progress": _state.get("thumb_progress", ""),
                    "last_error": _state.get("thumb_error"), "project": output.name})


def _first_thumbnails(output, generator) -> None:
    """After the shots: the first thumbnail variants when the project has none (non-fatal)."""
    from video import thumbnails
    if (load_json(Path(output) / thumbnails.FOLDER / thumbnails.INDEX) or {}).get("variants"):
        return
    try:
        from video.qa.layered import LayeredQA
        thumbnails.generate_variants(output, generator, progress=_imagegen_progress,
                                     qa=LayeredQA(comfy=generator.client, mode="advisory"))
        _state["thumb_path"] = str(Path(output) / "thumbnail.jpg")
    except Exception as exc:                         # noqa: BLE001 — "Mais opções" in Review makes them later
        _log(f"Thumbnail variants skipped: {exc}", "warn")


def _thumbnail_job(work):
    """Run a thumbnail generation in the background; its progress and any error reach /api/thumbnails."""
    from video import shot_generation as shotgen

    def progress(message: str):
        _state["thumb_progress"] = message.strip()
        _imagegen_progress(message)

    def run():
        _state["thumb_error"] = None
        _state["thumb_progress"] = "Começando…"
        try:
            work(progress)
        except Exception as exc:
            _state["thumb_error"] = {"message": str(exc), "approval": isinstance(exc, shotgen.ApprovalRequired)}
            raise
        finally:
            _state["thumb_progress"] = ""
    return _imagegen_run(run)


def _thumbnail_more(output: Path, new_concepts: bool = False, count: int | None = None):
    """"Mais opções" / "Novos conceitos" for ``output`` (the project open when the button was pressed)."""
    from video import thumbnails
    from video.imagegen_comfyui import ImageGenerator
    from video.qa.layered import LayeredQA

    def work(progress):
        generator = ImageGenerator(output)
        thumbnails.generate_variants(output, generator, progress=progress, new_concepts=new_concepts, count=count,
                                     qa=LayeredQA(comfy=generator.client, mode="advisory", progress=progress))
    return _thumbnail_job(work)


@app.route("/api/thumbnails/generate", methods=["POST"])
def thumbnails_generate():
    body = request.get_json(force=True) or {}
    count = body.get("count")
    return _thumbnail_more(Path(config.OUTPUT_DIR), bool(body.get("new_concepts")),
                           int(count) if count else None)


@app.route("/api/thumbnails/regenerate", methods=["POST"])
def thumbnails_regenerate():
    """"Gerar de novo" on one card: same concept, new seed, appended and linked to its parent."""
    from video import thumbnails
    from video.imagegen_comfyui import ImageGenerator
    from video.qa.layered import LayeredQA
    output = Path(config.OUTPUT_DIR)
    number = int((request.get_json(force=True) or {}).get("number", 0))
    index = load_json(output / thumbnails.FOLDER / thumbnails.INDEX) or {}
    if not any(item["number"] == number for item in index.get("variants", [])):
        return jsonify({"ok": False, "error": f"No thumbnail variant {number}"}), 404

    def work(progress):
        generator = ImageGenerator(output)
        thumbnails.regenerate_variant(output, generator, number, progress=progress,
                                      qa=LayeredQA(comfy=generator.client, mode="advisory", progress=progress))
    return _thumbnail_job(work)


@app.route("/api/thumbnails/archive", methods=["POST"])
def thumbnails_archive():
    from video import thumbnails
    try:
        thumbnails.archive_variant(config.OUTPUT_DIR, int((request.get_json(force=True) or {}).get("number", 0)))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/api/thumbnails/select", methods=["POST"])
def thumbnails_select():
    from video import thumbnails
    try:
        path = thumbnails.select(config.OUTPUT_DIR, int((request.get_json(force=True) or {}).get("number", 0)))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    _state["thumb_path"] = path
    return jsonify({"ok": True, "ts": int(time.time())})


@app.route("/api/regenerate-thumbnail", methods=["POST"])
def regen_thumbnail():
    """Legacy upload-panel button: now "Mais opções" in the variant system. A title change never
    re-letters the thumbnail: its text is independent and must not repeat the title."""
    body = request.get_json(force=True) or {}
    if body.get("title") and _state.get("script"):
        _state["script"]["title"] = body["title"]
    return _thumbnail_more(Path(config.OUTPUT_DIR))


# ── Image generation (ComfyUI) ───────────────────────────────────────────────

def _imagegen_progress(message: str):
    _state["imagegen_message"] = message.strip()
    _log(message.strip())


def _imagegen_run(target, *args):
    """Run one image-generation action in the background (one at a time)."""
    if _state.get("imagegen_busy") or _state.get("running"):
        return jsonify({"ok": False, "error": "Another generation is already running"}), 409

    def work():
        _state["imagegen_busy"] = True
        try:
            target(*args)
            _state["imagegen_message"] = "Done"
        except Exception as exc:
            _state["imagegen_message"] = f"Error: {exc}"
            _log(f"Image generation: {exc}", "error")
        finally:
            _state["imagegen_busy"] = False

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/imagegen/status")
def imagegen_status():
    from agents import identity, style_refs
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ComfyClient
    from video.qa import fixes, policy
    output = Path(config.OUTPUT_DIR)
    vb = load_json(output / vbible.FILE_NAME) or {}
    shot_list = load_json(output / "shot_list.json") or {}
    candidates = load_json(output / shotgen.CANDIDATES_FILE) or {}
    references = []
    recurring = {item["location_id"] for item in shotgen.recurring_locations(vb, shot_list)}
    # Every location is listed: recurring ones need a reference, the others may get one ("Gerar referência").
    for kind, items in (("character", vbible.on_screen(vb)),          # no portrait for voice-only / bodiless
                        ("body", vbible.on_screen(vb)),               # Phase 3.9.8-A: identity at a distance
                        ("location", vb.get("locations", []))):
        for item in items:
            ident = item["location_id" if kind == "location" else "character_id"]
            references.append({
                "kind": kind, "id": ident, "optional": (kind == "location" and ident not in recurring) or kind == "body",
                "approved": bool(vbible.approved_reference(vb, kind, ident, output)),
                "approved_path": (vb.get("references") or {}).get(vbible.reference_key(kind, ident), {}).get("path"),
                "candidates": [item for item in candidates.get(vbible.reference_key(kind, ident), [])
                               if shotgen.candidate_is_current(vbible.reference_key(kind, ident), item)],
                **(_reconcile_state(vb, ident) if kind == "character" else {}),
            })
    shots = shot_list.get("shots", [])
    rules = policy.Rules()
    for shot in shots:                    # stored results shown under the current QA mode (not saved)
        fixes.reapply_policy(shot, rules)
    done = [shot for shot in shots if not shot.get("reuse_of") and shot.get("image_path")
            and (output / shot["image_path"]).is_file()]
    estimate = shotgen.estimate_gpu_seconds(shot_list, output) if shot_list else None
    characters = [{"id": item["character_id"], "markers": vbible.signature_markers(vb, item["character_id"]),
                   "approved": bool(vbible.approved_reference(vb, "character", item["character_id"], output))}
                  for item in vbible.on_screen(vb)]
    return jsonify({
        "comfyui": ComfyClient(timeout=3).available(),
        "visual_bible": bool(vb), "shot_list": bool(shot_list),
        "busy": _state.get("imagegen_busy", False), "message": _state.get("imagegen_message", ""),
        "references": references,
        "missing_approvals": shotgen.missing_approvals(vb, shot_list, output) if vb and shot_list else [],
        "unique_shots": shot_list.get("unique_shots", 0), "done": len(done),
        "characters": characters, "qa_mode": policy.mode(),
        "current_shot": _state.get("imagegen_current") if _state.get("imagegen_busy") else None,
        "queued": sum(len(shot.get("edit_queue") or []) for shot in shots),
        "adherence": (shot_list.get("adherence") or {}).get("line", ""),
        "hinted": sum(1 for shot in shots if fixes.hinted(shot)),
        "eta_seconds": estimate,
        "style": {"name": identity.visual_style(), "variant": identity.style_variant(),
                  "hash": identity.style_hash(),
                  "boards": [path.name for path in sorted(style_refs.candidates_dir().glob("board_*.jpg"))],
                  "approved": [entry["number"] for entry in style_refs.approved()]},
        "shots": [{key: shot.get(key) for key in ("shot_id", "scene_id", "start", "end", "shot_type",
                                                   "characters", "face_visibility", "reuse_of",
                                                   "action", "prompt", "image_path", "provider",
                                                   "flags", "warnings", "qa", "edit_queue",
                                                   "image_flags", "copy_check", "removal", "source",
                                                   "adherence", "location_review", "location_relocated_from", "staging",
                                                   "image_adherence", "prompt_patch", "external_provider",
                                                   "narration_excerpt", "location", "generated_at")}
                  for shot in shots],
    })


@app.route("/imagegen/file")
def imagegen_file():
    output = Path(config.OUTPUT_DIR).resolve()
    target = (output / request.args.get("path", "")).resolve()
    if output not in target.parents or not target.is_file() or target.suffix.lower() not in {".jpg", ".png", ".mp4"}:
        abort(404)
    return send_file(str(target), conditional=True)


@app.route("/imagegen/style_file")
def imagegen_style_file():
    from agents import style_refs
    root = style_refs.candidates_dir().resolve()
    target = (root / request.args.get("name", "")).resolve()
    if target.parent != root or not target.is_file() or target.suffix.lower() != ".jpg":
        abort(404)
    return send_file(str(target))


@app.route("/api/imagegen/style/candidates", methods=["POST"])
def imagegen_style_candidates():
    from scripts import generate_style_refs
    return _imagegen_run(lambda: generate_style_refs.main(["--output-dir", config.OUTPUT_DIR]))


@app.route("/api/imagegen/style/approve", methods=["POST"])
def imagegen_style_approve():
    from agents import style_refs
    try:
        style_refs.approve(int((request.get_json(force=True) or {}).get("number", 0)))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/api/imagegen/candidates", methods=["POST"])
def imagegen_candidates():
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator

    def run():
        vb = shotgen.ensure_visual_bible(config.OUTPUT_DIR)
        shotgen.generate_reference_candidates(vb, config.OUTPUT_DIR, ImageGenerator(config.OUTPUT_DIR),
                                              progress=_imagegen_progress)
    return _imagegen_run(run)


@app.route("/api/imagegen/location/candidates", methods=["POST"])
def imagegen_location_candidates():
    """"Gerar referência": two candidates for one location, recurring or not (optional)."""
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator
    lid = str((request.get_json(force=True) or {}).get("id", ""))
    vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
    if not vbible.location(vb, lid):
        return jsonify({"ok": False, "error": f"Unknown location {lid!r}"}), 400

    def run():
        shotgen.generate_location_candidates(vb, config.OUTPUT_DIR, ImageGenerator(config.OUTPUT_DIR), [lid],
                                             progress=_imagegen_progress)
    return _imagegen_run(run)


@app.route("/api/imagegen/references/new_batch", methods=["POST"])
def imagegen_new_batch():
    """"Gerar novos candidatos": a new batch (new seeds) for one character or location; older batches stay."""
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator
    body = request.get_json(force=True) or {}
    kind, ident = str(body.get("kind", "")), str(body.get("id", ""))
    vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
    known = ([item["character_id"] for item in vbible.on_screen(vb)] if kind in ("character", "body")
             else [item["location_id"] for item in vb.get("locations", [])] if kind == "location" else [])
    if ident not in known:
        return jsonify({"ok": False, "error": f"No reference for {kind} {ident!r}"}), 400
    return _imagegen_run(lambda: shotgen.generate_new_batch(
        vb, config.OUTPUT_DIR, ImageGenerator(config.OUTPUT_DIR), kind, ident, progress=_imagegen_progress))


@app.route("/api/imagegen/shots/regenerate", methods=["POST"])
def imagegen_regenerate_shots():
    """"Gerar de novo (lote)": new seeds for several selected shots, generated in one run."""
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator
    ids = [str(item) for item in (request.get_json(force=True) or {}).get("shot_ids") or []]
    if not ids:
        return jsonify({"ok": False, "error": "Select at least one shot"}), 400
    unknown = [sid for sid in ids
               if not _update_shot(sid, lambda shot: shot.update(seed_offset=int(shot.get("seed_offset", 0)) + 1))]
    if unknown:
        return jsonify({"ok": False, "error": f"Unknown shots: {unknown}"}), 404
    return _imagegen_run(lambda: shotgen.generate_shots(
        config.OUTPUT_DIR, ImageGenerator(config.OUTPUT_DIR), only=ids, progress=_imagegen_progress))


def _reconcile_state(vb: dict, ident: str) -> dict:
    """The stored sheet reading and what still differs from the descriptor (no call)."""
    from agents import sheet_reading
    from agents import visual_bible as vbible
    entry = vbible.character(vb, ident) or {}
    reading = ((vb.get("references") or {}).get(vbible.reference_key("character", ident)) or {}).get("sheet_reading")
    if not reading:
        return {}
    pending = [item for item in sheet_reading.differences(entry.get("descriptor", ""), vbible.signature_markers(vb, ident), reading)
               if not (item["field"] == "marker" and item["descriptor"] in (entry.get("marker_conflicts") or []))]
    return {"sheet_reading": reading, "differences": pending, "descriptor": entry.get("descriptor", "")}


@app.route("/api/imagegen/reconcile", methods=["POST"])
def imagegen_reconcile():
    """"Conferir descritor" (Phase 3.9.8-B): one vision call reads the approved face sheet; the differences come back."""
    from agents import sheet_reading
    from agents import visual_bible as vbible
    ident = str((request.get_json(force=True) or {}).get("id", ""))
    path = Path(config.OUTPUT_DIR, vbible.FILE_NAME)
    vb = load_json(path) or {}
    sheet = vbible.approved_reference(vb, "character", ident, config.OUTPUT_DIR)
    if not sheet:
        return jsonify({"ok": False, "error": "Approve a face sheet first"}), 400
    try:
        reading = sheet_reading.read_sheet(sheet)
    except Exception as exc:                             # noqa: BLE001 — quota, network, parse
        return jsonify({"ok": False, "error": f"Leitura da folha falhou: {str(exc)[:200]}"}), 502
    vb["references"][vbible.reference_key("character", ident)]["sheet_reading"] = reading
    atomic_write_json(path, vb)
    return jsonify({"ok": True, **_reconcile_state(vb, ident)})


@app.route("/api/imagegen/reconcile/apply", methods=["POST"])
def imagegen_reconcile_apply():
    """Accept differences (or a hand-edited descriptor); the old descriptor stays in descriptor_history."""
    from agents import sheet_reading
    from agents import visual_bible as vbible
    body = request.get_json(force=True) or {}
    ident = str(body.get("id", ""))
    path = Path(config.OUTPUT_DIR, vbible.FILE_NAME)
    vb = load_json(path) or {}
    reading = ((vb.get("references") or {}).get(vbible.reference_key("character", ident)) or {}).get("sheet_reading")
    if not reading or not vbible.character(vb, ident):
        return jsonify({"ok": False, "error": "Run Conferir descritor first"}), 400
    accepted = [str(item) for item in body.get("accepted") or []]
    descriptor = str(body["descriptor"]).strip() if str(body.get("descriptor") or "").strip() else None
    entry = sheet_reading.apply(vb, ident, reading, accepted, descriptor=descriptor)
    atomic_write_json(path, vb)
    return jsonify({"ok": True, "descriptor": entry["descriptor"], **_reconcile_state(vb, ident)})


@app.route("/api/imagegen/approve", methods=["POST"])
def imagegen_approve():
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    body = request.get_json(force=True) or {}
    vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME))
    try:
        shotgen.approve_reference(vb, str(body.get("kind")), str(body.get("id")),
                                  int(body.get("number", 0)), config.OUTPUT_DIR)
    except (FileNotFoundError, TypeError, ValueError, KeyError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


def _update_shot(shot_id: str, change) -> dict | None:
    """Apply ``change`` to one shot and invalidate only its image (and the rendered video)."""
    path = Path(config.OUTPUT_DIR, "shot_list.json")
    shot_list = load_json(path) or {}
    shot = next((item for item in shot_list.get("shots", []) if item["shot_id"] == shot_id), None)
    if shot is None:
        return None
    change(shot)
    shot.pop("image_path", None)          # invalidate only this shot
    atomic_write_json(path, shot_list)
    Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
    return shot


@app.route("/api/imagegen/shot/<shot_id>/regenerate", methods=["POST"])
def imagegen_regenerate_shot(shot_id):
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator
    if not _update_shot(shot_id, lambda shot: shot.update(seed_offset=int(shot.get("seed_offset", 0)) + 1)):
        return jsonify({"ok": False, "error": "Unknown shot"}), 404
    return _imagegen_run(lambda: shotgen.generate_shots(
        config.OUTPUT_DIR, ImageGenerator(config.OUTPUT_DIR), only=shot_id, progress=_imagegen_progress))


@app.route("/api/imagegen/shot/<shot_id>/fix", methods=["POST"])
def imagegen_fix_shot(shot_id):
    """Shot-card buttons (3.5-F): "Editar (QA)" (mode qa), "Editar com instrução própria" (mode custom:
    instruction + optional box drawn on the image), "Gerar de novo" (mode regenerate). Every version is
    kept in images/shots/history/; the shot is checked again by the layered QA right after."""
    from agents import shot_list as shots_module
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    from video import visual_qa
    from video.imagegen_comfyui import ImageGenerator
    from video.qa import fixes
    from video.qa.layered import LayeredQA
    body = request.get_json(force=True) or {}
    mode = str(body.get("mode", "qa"))
    instruction = str(body.get("instruction", "")).strip()
    box = body.get("box")
    if mode not in ("qa", "custom", "regenerate"):
        return jsonify({"ok": False, "error": "mode must be qa, custom or regenerate"}), 400
    if mode == "custom" and not instruction:
        return jsonify({"ok": False, "error": "Type the edit instruction"}), 400
    if box is not None:
        try:
            box = [min(max(float(value), 0.0), 1.0) for value in box][:4]
            if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "box must be [x0, y0, x1, y1] between 0 and 1"}), 400
    output = Path(config.OUTPUT_DIR)
    path = output / shots_module.FILE_NAME
    shot_list = load_json(path) or {}
    shot = next((item for item in shot_list.get("shots", []) if item["shot_id"] == shot_id), None)
    if shot is None or not shot.get("image_path") or not (output / shot["image_path"]).is_file():
        return jsonify({"ok": False, "error": "Unknown shot or no image yet"}), 404
    if mode == "qa" and not fixes.needs_fix(shot):
        return jsonify({"ok": False, "error": "QA found nothing to fix on this shot (run the QA first)"}), 400

    def work():
        current = load_json(path) or {}
        target = next(item for item in current["shots"] if item["shot_id"] == shot_id)
        vb = load_json(output / vbible.FILE_NAME) or {}
        generator = ImageGenerator(output)
        narration = shots_module.scene_narration(current["shots"])
        entry = {"item": target, "image": output / target["image_path"],
                 "expected": visual_qa.expected_for(target, vb, narration.get(target.get("scene_id", ""), "")),
                 "factory": lambda offset: shotgen.shot_job(target, vb, output, offset)[0]}
        action = fixes.apply(generator, output, target, vb, image_path=entry["image"], job_factory=entry["factory"],
                             custom=instruction if mode == "custom" else "", custom_box=box if mode == "custom" else None,
                             force="regenerate" if mode == "regenerate" else ("edit" if mode == "custom" else ""),
                             progress=_imagegen_progress)
        atomic_write_json(path, current)
        Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
        if action["action"] in ("edit", "regenerate"):
            shotgen.qa_rounds(generator, output, vb, [entry], LayeredQA(comfy=generator.client,
                                                                        progress=_imagegen_progress),
                              lambda: atomic_write_json(path, current), progress=_imagegen_progress, rounds=0)
    return _imagegen_run(work)


# ── Human review of the shots (3.5.1-C): one-click edits, queued, run in one Klein batch ──

def _review_shot(shot_id: str):
    """(shot_list path, shot list, shot) for a shot with an image, or a (response, status) error."""
    path = Path(config.OUTPUT_DIR, "shot_list.json")
    shot_list = load_json(path) or {}
    shot = next((item for item in shot_list.get("shots", []) if item["shot_id"] == shot_id), None)
    if shot is None or not shot.get("image_path") or not Path(config.OUTPUT_DIR, shot["image_path"]).is_file():
        return None, (jsonify({"ok": False, "error": "Unknown shot or no image yet"}), 404)
    return (path, shot_list, shot), None


def _save_brush_mask(shot: dict, data_url: str) -> str:
    """A brush mask from the viewer (PNG data URL): saved with the shot, its relative path returned."""
    import base64
    from PIL import Image
    from video.qa import removal
    try:
        raw = base64.b64decode(str(data_url).split(",", 1)[-1])
        with Image.open(io.BytesIO(raw)) as image:
            mask = image.convert("RGBA").getchannel("A") if "A" in image.getbands() else image.convert("L")
    except (ValueError, OSError) as exc:
        raise ValueError("the mask is not a readable image") from exc
    if not mask.getbbox():
        raise ValueError("the mask is empty: paint over the figure")
    relative = removal.FOLDER / f"{shot['shot_id']}_brush_{int(time.time() * 1000)}.png"
    target = Path(config.OUTPUT_DIR, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    mask.save(target, "PNG")
    return relative.as_posix()


@app.route("/api/imagegen/shot/<shot_id>/mask_preview", methods=["POST"])
def imagegen_mask_preview(shot_id):
    """The mask a box becomes (refined to the person, dilated, with the shadow), to show and edit before running."""
    import base64
    from PIL import Image
    from video.qa import fixes, region, removal, review
    found, error = _review_shot(shot_id)
    if error:
        return error
    _, _, shot = found
    try:
        box = review.clean_box((request.get_json(force=True) or {}).get("box"))
        if not box:
            raise ValueError("Draw a box")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    image_path = Path(config.OUTPUT_DIR, shot["image_path"])
    detector = fixes._detector()
    persons = detector.detect(image_path).get("persons", []) if detector else []
    with Image.open(image_path) as image:
        size = image.size
    hard = removal.build_mask(size, box, persons=persons, grow=removal.FIGURE_GROW)
    buffer = io.BytesIO()
    hard.save(buffer, "PNG")
    overlaps = bool(region.overlap_warnings({"persons": persons}, shot, box)) if persons else False
    return jsonify({"ok": True, "mask": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "box": removal.mask_box(hard), "refined": removal.refine_box(box, persons)[1],
                    "regenerate_suggested": removal.regenerate_suggested(hard, overlaps)})


@app.route("/api/imagegen/shot/<shot_id>/removal/choose", methods=["POST"])
def imagegen_removal_choose(shot_id):
    """"Usar este": a removal result becomes the shot; the image before it stays in the history."""
    from video.qa import removal
    if _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running generation"}), 409
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    try:
        action = removal.choose(config.OUTPUT_DIR, shot, int((request.get_json(force=True) or {}).get("index", -1)))
    except (IndexError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    atomic_write_json(path, shot_list)
    Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
    return jsonify({"ok": True, "action": action})


@app.route("/api/imagegen/shot/<shot_id>/removal/discard", methods=["POST"])
def imagegen_removal_discard(shot_id):
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    shot.pop("removal", None)
    atomic_write_json(path, shot_list)
    return jsonify({"ok": True})


def _open_folder(path) -> None:
    """Show a folder in the file manager (best effort: exporting worked either way)."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
    except OSError:
        pass


@app.route("/api/imagegen/shot/<shot_id>/export", methods=["POST"])
def imagegen_export(shot_id):
    """"Exportar para edição": the shot at full size, or the crop around a box, in exports/."""
    from video import external_images
    from video.qa import review
    found, error = _review_shot(shot_id)
    if error:
        return error
    try:
        box = review.clean_box((request.get_json(force=True) or {}).get("box"))
        target = external_images.export_image(config.OUTPUT_DIR, found[2], box)
    except (ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    _open_folder(target.parent)
    return jsonify({"ok": True, "path": Path(target).relative_to(config.OUTPUT_DIR).as_posix()})


@app.route("/api/imagegen/shot/<shot_id>/import", methods=["POST"])
def imagegen_import(shot_id):
    """"Importar imagem editada": any size; an aspect far from 16:9 answers 409 until the user picks crop or blur."""
    import tempfile
    from video import external_images
    from video.qa import fixes
    if _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running generation"}), 409
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    try:
        position = float(request.form.get("position", 0.5))
    except ValueError:
        position = 0.5
    with tempfile.TemporaryDirectory() as temp:
        held = Path(temp) / "upload"
        upload.save(held)
        try:
            action = external_images.import_image(config.OUTPUT_DIR, shot, held, upload.filename, mode=request.form.get("mode", ""),
                                                  position=position, detector=fixes._detector())
        except external_images.NeedsChoice as exc:
            return jsonify({"ok": False, "needs_choice": True, "width": exc.size[0], "height": exc.size[1],
                            "difference": round(exc.difference, 4)}), 409
        except (ValueError, OSError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    atomic_write_json(path, shot_list)
    Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
    return jsonify({"ok": True, "action": action, "warnings": shot.get("image_flags", [])})


def _project_flag(name: str, value=None):
    """A per-project setting in project.json (Phase 3.9.9: "image_adherence")."""
    path = Path(config.OUTPUT_DIR, "project.json")
    data = load_json(path) or {}
    if value is not None:
        data[name] = value
        atomic_write_json(path, data)
    return data.get(name)


@app.route("/api/imagegen/adherence", methods=["GET", "POST"])
def imagegen_adherence():
    """"Checar aderência das imagens" (off by default): GET the toggle and the cost estimate, POST {enabled} to switch it."""
    from video.qa import image_adherence
    if request.method == "POST":
        _project_flag("image_adherence", bool((request.get_json(force=True) or {}).get("enabled")))
    shot_list = load_json(Path(config.OUTPUT_DIR, "shot_list.json")) or {}
    count = sum(1 for shot in shot_list.get("shots", []) if shot.get("characters") and not shot.get("reuse_of") and shot.get("image_path"))
    return jsonify({"ok": True, "enabled": bool(_project_flag("image_adherence")), "estimate": image_adherence.estimate(count)})


@app.route("/api/imagegen/adherence/run", methods=["POST"])
def imagegen_adherence_run():
    from agents import visual_bible as vbible
    from video.qa import image_adherence
    if not _project_flag("image_adherence"):
        return jsonify({"ok": False, "error": "Ative \"Checar aderência das imagens\" neste projeto primeiro"}), 400
    path = Path(config.OUTPUT_DIR, "shot_list.json")

    def work():
        shot_list = load_json(path) or {}
        vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
        outcome = image_adherence.run(shot_list, vb, config.OUTPUT_DIR, progress=_imagegen_progress)
        atomic_write_json(path, shot_list)
        _imagegen_progress(f"Aderência das imagens: {outcome['checked']} checados, {len(outcome['failed'])} fora da narração")
    return _imagegen_run(work)


@app.route("/api/imagegen/adherence/regenerate", methods=["POST"])
def imagegen_adherence_regenerate():
    """"Gerar de novo com o prompt corrigido": one shot ({shot_id}) or every shot that failed (batch)."""
    from agents import visual_bible as vbible
    from video.qa import image_adherence, review
    body = request.get_json(force=True) or {}
    path = Path(config.OUTPUT_DIR, "shot_list.json")
    shot_list = load_json(path) or {}
    vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
    wanted = [str(body["shot_id"])] if body.get("shot_id") else \
        [shot["shot_id"] for shot in shot_list.get("shots", []) if (shot.get("image_adherence") or {}).get("failures") or
         (shot.get("adherence") or {}).get("status") in ("fixed", "review")]
    queued = []
    for shot in shot_list.get("shots", []):
        if shot["shot_id"] not in wanted:
            continue
        patch_text = image_adherence.regenerate_patch(shot)
        if patch_text:
            shot["prompt_patch"] = patch_text
        if not any(item.get("kind") == "regenerate" for item in shot.get("edit_queue", [])):
            review.enqueue(shot, review.make_request("regenerate", vb))
            queued.append(shot["shot_id"])
    atomic_write_json(path, shot_list)
    return jsonify({"ok": True, "queued": queued, "message": f"{len(queued)} plano(s) na fila com o prompt corrigido: clique em Executar fila"})


@app.route("/api/imagegen/external", methods=["GET", "POST"])
def imagegen_external():
    """Phase 3.9.9-F: the provider's state, the budget and the estimate for ``count`` shots; POST {cap} sets the cap."""
    from video import external_provider
    if request.method == "POST" and "cap" in (request.get_json(force=True) or {}):
        external_provider.set_cap(config.OUTPUT_DIR, float((request.get_json(force=True) or {})["cap"]))
    try:
        external_provider.provider()
        enabled, reason = True, ""
    except external_provider.ProviderDisabled as exc:
        enabled, reason = False, str(exc)
    count = int(request.args.get("count", 1) or 1)
    return jsonify({"ok": True, "enabled": enabled, "reason": reason, **external_provider.estimate(config.OUTPUT_DIR, count)})


@app.route("/api/imagegen/external/redo", methods=["POST"])
def imagegen_external_redo():
    """"Refazer com provedor externo": never automatic; the GUI confirms the cost first ({shot_ids, confirmed})."""
    from agents import visual_bible as vbible
    from video import external_provider
    body = request.get_json(force=True) or {}
    ids = [str(item) for item in body.get("shot_ids") or []]
    if not ids:
        return jsonify({"ok": False, "error": "Escolha ao menos um plano"}), 400
    try:
        external_provider.provider()
        external_provider.check(config.OUTPUT_DIR, len(ids))
    except (external_provider.ProviderDisabled, external_provider.BudgetExceeded) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not body.get("confirmed"):
        return jsonify({"ok": False, "error": "Confirme o custo antes de enviar"}), 400
    path = Path(config.OUTPUT_DIR, "shot_list.json")

    def work():
        shot_list = load_json(path) or {}
        vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
        for shot in shot_list.get("shots", []):
            if shot["shot_id"] not in ids:
                continue
            try:
                external_provider.redo_shot(config.OUTPUT_DIR, shot, vb, confirmed=True, edit=bool(body.get("edit")))
                _imagegen_progress(f"{shot['shot_id']}: refeito pelo provedor externo")
            except Exception as exc:                     # noqa: BLE001 — one failure never stops the batch
                _imagegen_progress(f"{shot['shot_id']}: provedor externo falhou ({str(exc)[:160]})")
            atomic_write_json(path, shot_list)
        Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
    return _imagegen_run(work)


@app.route("/api/imagegen/regenerate_generic", methods=["POST"])
def imagegen_regenerate_generic():
    """"Gerar de novo os genéricos" (Phase 3.9.8-C): a new seed queued for every shot flagged as a generic character."""
    from agents import shot_list as shots_module
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    from video.qa import review
    path = Path(config.OUTPUT_DIR, shots_module.FILE_NAME)
    shot_list = load_json(path) or {}
    vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
    shots = shot_list.get("shots", [])
    # 1. Shots drawn before their character had a usable reference (e.g. wide/silhouette shots made while
    #    FACELESS_PORTRAIT_REFERENCE was "none", before the full-body sheets): text-only identity by construction.
    missing_ref = []
    for shot in shots:
        kinds_by_id = {(slot.get("kind"), slot.get("id")) for slot in shot.get("reference_slots") or []}
        for cid in shot.get("characters") or []:
            approved = any(vbible.approved_reference(vb, kind, cid, config.OUTPUT_DIR) for kind in ("character", "body"))
            if approved and not ({("character", cid), ("body", cid)} & kinds_by_id) and shot.get("shot_type") not in ("hands", "insert"):
                missing_ref.append(shot["shot_id"])
                break
    # 2. Shots never scored by the identity check (made before 3.9.8): score them now (CPU, SFace).
    scored = 0
    try:
        from video.qa import fixes, identity_check
        detector = fixes._detector()
        checker = identity_check.IdentityCheck(detector, vb, config.OUTPUT_DIR, shots) if detector else None
    except Exception as exc:                              # noqa: BLE001 — scoring is best effort
        checker = None
        _log(f"Identity check unavailable: {exc}", "warn")
    if checker is not None:
        for shot in shots:
            qa = shot.setdefault("qa", {})
            image = Path(config.OUTPUT_DIR, str(shot.get("image_path", "")).replace("\\", "/"))
            if qa.get("identity") is not None or not shot.get("characters") or not image.is_file():
                continue
            try:
                result = checker.score(image, shot["characters"], shot_id=shot["shot_id"], scene_id=shot.get("scene_id", ""),
                                       shot_type=shot.get("shot_type", ""))
            except Exception as exc:                      # noqa: BLE001
                _log(f"Identity check skipped for {shot['shot_id']}: {str(exc)[:120]}", "warn")
                continue
            qa["identity"] = result
            qa["issues"] = [item for item in qa.get("issues") or [] if item.get("type") != "identity"] + \
                identity_check.issues(result, vb, regenerated=bool(qa.get("regenerations")))
            scored += 1
    generic = shotgen.generic_shots(shot_list)
    wanted = list(dict.fromkeys(generic + missing_ref))
    queued = []
    for shot in shots:
        if shot["shot_id"] in wanted and not any(item.get("kind") == "regenerate" for item in shot.get("edit_queue", [])):
            review.enqueue(shot, review.make_request("regenerate", vb))
            queued.append(shot["shot_id"])
    atomic_write_json(path, shot_list)
    message = (f"{len(queued)} plano(s) na fila ({len(generic)} com rosto genérico, {len(missing_ref)} gerados sem a "
               f"referência da personagem; {scored} avaliados agora). Clique em Executar fila." if queued
               else f"Nenhum plano genérico encontrado ({scored} avaliados agora).")
    _log(message)
    return jsonify({"ok": True, "queued": queued, "generic": generic, "missing_reference": missing_ref, "scored": scored,
                    "message": message})


@app.route("/api/imagegen/shot/<shot_id>/queue", methods=["POST"])
def imagegen_queue_edit(shot_id):
    """"Remover figura", "Adicionar marcador", "Remover marcas", "Instrução livre", "Gerar de novo":
    added to the shot's queue; nothing runs until "Executar fila"."""
    from agents import visual_bible as vbible
    from video.qa import review
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    body = request.get_json(force=True) or {}
    vb = load_json(Path(config.OUTPUT_DIR, vbible.FILE_NAME)) or {}
    try:
        mask = _save_brush_mask(shot, body.get("mask")) if body.get("mask") else ""
        entry = review.make_request(str(body.get("kind", "")), vb, box=body.get("box"),
                                    character=str(body.get("character", "")), marker=str(body.get("marker", "")),
                                    text=str(body.get("text", "")), mask=mask, replace=str(body.get("replace", "")),
                                    erase_only=bool(body.get("erase_only")), large=bool(body.get("large")))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    queue = review.enqueue(shot, entry)
    atomic_write_json(path, shot_list)
    return jsonify({"ok": True, "queue": queue, "queued": sum(len(item.get("edit_queue", []))
                                                              for item in shot_list["shots"])})


@app.route("/api/imagegen/shot/<shot_id>/check_box", methods=["POST"])
def imagegen_check_box(shot_id):
    """Before queuing a removal: does the box cover > 20 % of a main character? (layer-1 detector)"""
    from video.qa import fixes, region, review
    found, error = _review_shot(shot_id)
    if error:
        return error
    _, _, shot = found
    try:
        box = review.clean_box((request.get_json(force=True) or {}).get("box"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    detector = fixes._detector()
    if not box or detector is None:
        return jsonify({"ok": True, "warnings": [], "detector": detector is not None})
    warnings = region.overlap_warnings(detector.detect(Path(config.OUTPUT_DIR, shot["image_path"])), shot, box)
    for item in warnings:
        names = item["candidates"]
        who = names[0] if len(names) == 1 else "um personagem principal (" + " ou ".join(names) + ")"
        item["message"] = f"a caixa cobre {item['covered']:.0%} de {who}"
    return jsonify({"ok": True, "warnings": warnings, "detector": True})


@app.route("/api/imagegen/shot/<shot_id>/queue/remove", methods=["POST"])
def imagegen_unqueue_edit(shot_id):
    from video.qa import review
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    try:
        queue = review.dequeue(shot, int((request.get_json(force=True) or {}).get("index", -1)))
    except (IndexError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    atomic_write_json(path, shot_list)
    return jsonify({"ok": True, "queue": queue})


@app.route("/api/imagegen/queue/run", methods=["POST"])
def imagegen_run_queue():
    """Every queued edit of every shot in one background batch, Klein loaded throughout."""
    from agents import visual_bible as vbible
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator
    from video.qa import review
    path = Path(config.OUTPUT_DIR, "shot_list.json")
    if not review.queued(load_json(path) or {}):
        return jsonify({"ok": False, "error": "No edit queued"}), 400

    def work():
        output = Path(config.OUTPUT_DIR)
        current = load_json(path) or {}
        vb = load_json(output / vbible.FILE_NAME) or {}
        summary = review.run_queue(
            ImageGenerator(output), output, vb, current,
            job_factory=lambda shot: (lambda offset: shotgen.shot_job(shot, vb, output, offset)[0]),
            save=lambda: atomic_write_json(path, current), progress=_imagegen_progress,
            on_shot=lambda shot_id: _state.update(imagegen_current=shot_id))
        if summary["edits"] or summary["regenerations"]:      # a removal changes nothing until a result is chosen
            Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
        _imagegen_progress(f"Fila: {summary.get('removals', 0)} remoção(ões) a escolher, "
                           f"{summary['edits']} edição(ões), {summary['regenerations']} nova(s) seed(s)"
                           + (f"; falharam (original mantido): {', '.join(summary['failed'])}" if summary["failed"]
                              else ""))
    return _imagegen_run(work)


@app.route("/api/imagegen/shot/<shot_id>/restore", methods=["POST"])
def imagegen_restore_version(shot_id):
    """Undo: bring back any earlier version (the current one is kept in the history)."""
    from video.qa import fixes
    if _state.get("imagegen_busy"):
        return jsonify({"ok": False, "error": "Wait for the running generation"}), 409
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    try:
        action = fixes.restore_version(config.OUTPUT_DIR, shot, Path(config.OUTPUT_DIR, shot["image_path"]),
                                       int((request.get_json(force=True) or {}).get("version", 0)))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    atomic_write_json(path, shot_list)
    Path(config.OUTPUT_DIR, "final_video.mp4").unlink(missing_ok=True)
    return jsonify({"ok": True, "action": action})


@app.route("/api/imagegen/second_opinion", methods=["POST"])
def imagegen_second_opinion():
    """"Segunda opinião (Gemini)": one shot (shot_id) or every shot with hints (hinted: true);
    Gemini answers the focused questions, stored beside the local result, advisory only."""
    from scripts import qa_shots
    from video.qa import fixes
    body = request.get_json(force=True) or {}
    shot_list = load_json(Path(config.OUTPUT_DIR, "shot_list.json")) or {}
    shots = shot_list.get("shots", [])
    if body.get("shot_id"):
        selected = [str(body["shot_id"])]
    elif body.get("hinted"):
        selected = [shot["shot_id"] for shot in shots if fixes.hinted(shot)]
    else:
        return jsonify({"ok": False, "error": "Pass shot_id or hinted"}), 400
    if not selected:
        return jsonify({"ok": False, "error": "No shot to check"}), 400

    def work():
        summary = qa_shots.run(Path(config.OUTPUT_DIR), backend="gemini", shot_ids=selected,
                               progress=_imagegen_progress)
        _imagegen_progress(f"Segunda opinião (Gemini): {summary['second_opinions']} plano(s)")
    return _imagegen_run(work)


@app.route("/api/imagegen/shot/<shot_id>/review", methods=["POST"])
def imagegen_review_shot(shot_id):
    """"Marcar OK" (action ok) or "Ignorar aviso" (action ignore, issue = its index)."""
    from video.qa import review
    found, error = _review_shot(shot_id)
    if error:
        return error
    path, shot_list, shot = found
    body = request.get_json(force=True) or {}
    issue = body.get("issue")
    try:
        qa = review.mark(shot, str(body.get("action", "")), None if issue is None else int(issue))
    except (IndexError, TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    atomic_write_json(path, shot_list)
    return jsonify({"ok": True, "qa": qa})


@app.route("/api/imagegen/shot/<shot_id>/prompt", methods=["POST"])
def imagegen_edit_shot(shot_id):
    from agents import identity
    action = str((request.get_json(force=True) or {}).get("action", "")).strip()
    if not action:
        return jsonify({"ok": False, "error": "The shot action must not be empty"}), 400
    from agents import shot_list as shots_module
    banned = identity.forbidden_terms(action) + identity.layout_terms(action)
    if banned:       # blocking: the shot stays as it was (and flagged, if it was)
        return jsonify({"ok": False, "error": "The visual style excludes: " + ", ".join(banned)}), 400
    path = Path(config.OUTPUT_DIR, "shot_list.json")
    narration = shots_module.scene_narration((load_json(path) or {}).get("shots", []))

    def revalidate(shot):
        # Re-validated: the edit cleared the blocking problem; advisory notes are recomputed.
        shot["action"] = action
        shot["action_edited_by_human"] = True        # editorial record (Phase 3.9-D)
        shot.pop("flags", None)
        notes = shots_module.advice_for(shot, narration.get(shot.get("scene_id", ""), ""))
        if notes:
            shot["warnings"] = notes
        else:
            shot.pop("warnings", None)

    shot = _update_shot(shot_id, revalidate)
    if not shot:
        return jsonify({"ok": False, "error": "Unknown shot"}), 404
    shot_list = load_json(path) or {}
    shot_list["flagged"] = [item["shot_id"] for item in shot_list.get("shots", []) if item.get("flags")]
    atomic_write_json(path, shot_list)
    shots_module.write_report(config.OUTPUT_DIR, shot_list)
    return jsonify({"ok": True, "warnings": shot.get("warnings", [])})


@app.route("/api/imagegen/qa", methods=["POST"])
def imagegen_qa():
    """"Revisar planos com QA": visual QA of the existing shots, without regenerating them."""
    from scripts import qa_shots
    only = str((request.get_json(force=True) or {}).get("only", "")).strip() or None

    def work():
        summary = qa_shots.run(Path(config.OUTPUT_DIR), only=only, progress=_imagegen_progress)
        _imagegen_progress(f"QA: {summary['checked']} checked, {summary['passed']} passed, "
                           f"{len(summary['failed'])} failed ({len(summary['edit'])} to edit, "
                           f"{len(summary['regenerate'])} to regenerate), {summary['skipped']} skipped")
    return _imagegen_run(work)


@app.route("/api/imagegen/generate", methods=["POST"])
def imagegen_generate():
    from video import shot_generation as shotgen
    from video.imagegen_comfyui import ImageGenerator
    body = request.get_json(force=True) or {}

    def run():
        shotgen.ensure_shot_list(config.OUTPUT_DIR)
        shotgen.generate_shots(config.OUTPUT_DIR, ImageGenerator(config.OUTPUT_DIR),
                               limit=body.get("limit"), progress=_imagegen_progress)
    return _imagegen_run(run)


# ── YouTube upload ────────────────────────────────────────────────────────────

@app.route("/api/yt-status")
def yt_status():
    token_ok = os.path.exists("youtube_token.pickle")
    secret_ok = os.path.exists(config.YOUTUBE_CLIENT_SECRET)
    return jsonify({"token": token_ok, "secret": secret_ok})

def _episode_tags(script: dict) -> list:
    """Phase 4.0-A: at most 8 relevant tags (topic + type), never another channel's name."""
    if not script:
        return []
    try:
        from agents import topic
        return topic.episode_tags(config.OUTPUT_DIR, script)
    except Exception:                                    # noqa: BLE001 — the script's own tags still go
        return list(script.get("tags", []))[:8]


def _upload_metadata() -> dict:
    """Exactly what an upload sends: shared by the upload and its approval."""
    script = _state.get("script") or {}
    thumb = _state.get("thumb_path")
    return {
        "video_path": _state.get("video_path"),
        "title": script.get("title", ""),
        "description": _state.get("description", script.get("description", "")),
        "tags": _episode_tags(script),
        "thumbnail_path": thumb if thumb and os.path.exists(thumb) else None,
    }


@app.route("/api/review-gate")
def get_review_gate():
    """Checklist, narrative status, title and license findings, approval validity."""
    from uploader.approval import review_summary
    meta = _upload_metadata()
    if not meta["video_path"] or not os.path.exists(meta["video_path"]):
        return jsonify({"ok": False, "error": "No video to review"})
    from agents.gemini_client import generate
    return jsonify({"ok": True, **review_summary(config.OUTPUT_DIR, generator=generate, **meta)})


@app.route("/api/approve-upload", methods=["POST"])
def approve_upload():
    from uploader.approval import UploadNotApproved, approve
    body = request.get_json(force=True) or {}
    meta = _upload_metadata()
    try:
        record = approve(
            config.OUTPUT_DIR, checklist=body.get("checklist") or {},
            notes=body.get("notes", ""),
            title_override_reason=body.get("title_override_reason", ""),
            status_override_reason=body.get("status_override_reason", ""),
            synthetic_disclosure=body.get("synthetic_disclosure"),
            disclosure_override_reason=body.get("disclosure_override_reason", ""),
            **meta,
        )
    except UploadNotApproved as exc:
        return jsonify({"ok": False, "error": str(exc), "reasons": exc.reasons}), 400
    return jsonify({"ok": True, "approved_at": record["approved_at"]})


@app.route("/api/localizations", methods=["GET", "POST"])
def localizations_panel():
    """Phase 4.0-B "Traduções": GET the saved translations of the current title and description (POST
    {generate: true} makes them with Gemini; POST {translations} saves the edits, validated per language)."""
    from uploader import localize
    meta = _upload_metadata()
    body = request.get_json(force=True, silent=True) or {} if request.method == "POST" else {}
    try:
        if body.get("translations"):
            localize.save(config.OUTPUT_DIR, meta["title"], meta["description"], body["translations"])
        elif body.get("generate"):
            from agents.gemini_client import generate
            localize.ensure(config.OUTPUT_DIR, meta["title"], meta["description"], generator=generate)
    except Exception as exc:                             # noqa: BLE001 — shown in the panel
        return jsonify({"ok": False, "error": str(exc)[:400]}), 400
    found = localize.current(config.OUTPUT_DIR, meta["title"], meta["description"])
    codes = localize.ordered(localize.languages())
    return jsonify({"ok": True, "languages": codes, "names": localize.NAMES, "translations": found or {},
                    "stale": found is None and bool(localize.load(config.OUTPUT_DIR))})


@app.route("/api/discovery-settings", methods=["GET", "POST"])
def discovery_settings_api():
    """Phase 4.0 Settings: languages, publishing slots, playlists, the swap rule."""
    from uploader import discovery_settings
    if request.method == "POST":
        try:
            return jsonify({"ok": True, **discovery_settings.save(request.get_json(force=True) or {})})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **discovery_settings.load()})


@app.route("/api/upload/next-slot")
def upload_next_slot():
    """Phase 4.0-F: "Agendar para o próximo horário do tipo" — the next weekly slot of the episode's type."""
    from uploader import slots
    kind = (_state.get("script") or {}).get("narrative_type", "")
    when = slots.next_slot(kind) if kind else None
    if when is None:
        return jsonify({"ok": False, "kind": kind, "error": "Sem horário fixo para este tipo (veja Configurações)"})
    local = when.astimezone()
    return jsonify({"ok": True, "kind": kind, "slot": slots.settings()["slots"].get(kind), "publish_at": slots.rfc3339(when),
                    "local_input": local.strftime("%Y-%m-%dT%H:%M"), "label": slots.label(when)})


@app.route("/api/upload/plan")
def upload_plan():
    """What the confirmation dialog shows: the episode now/scheduled, each Short's planned time and the
    cadence advisor (Phase 3.9-C: episodes per week, Shorts per day)."""
    from datetime import datetime, timezone
    from studio import variation
    from uploader import shorts_upload
    from uploader.publications import load_ledger
    privacy = request.args.get("privacy", config.VIDEO_PRIVACY)
    publish_at = request.args.get("publish_at", "")
    plan = shorts_upload.plan_lines(config.OUTPUT_DIR, privacy=privacy, publish_at=publish_at)
    now = datetime.now(timezone.utc)

    def when(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else now
        except ValueError:
            return now
    planned = [{"kind": "episode", "at": when(publish_at)}] + \
        [{"kind": "short", "at": when(item.get("publish_at"))} for item in plan["shorts"]]
    pace = variation.cadence(load_ledger().get("publications", []), planned, now=now)
    plan["cadence"] = pace
    plan["cadence_text"] = (f"Ritmo: {pace['episodes_per_week']} episódio(s) em 7 dias, até "
                            f"{pace['shorts_per_day']} Short(s) por dia." +
                            "".join(f"\n⚠ {warning}" for warning in pace["warnings"]))
    return jsonify({"ok": True, **plan})


@app.route("/api/variation")
def variation_status():
    """Phase 3.9-C: the open episode's variation warnings and the published episodes' profiles."""
    from studio import variation
    from uploader.publications import load_ledger
    return jsonify({"ok": True, **variation.report(config.OUTPUT_DIR, load_ledger().get("publications", []))})


@app.route("/api/variation/profile", methods=["POST"])
def variation_profile():
    """Edit a published episode's profile (theme tags, narrative type, opening device)."""
    from studio import variation
    body = request.get_json(force=True) or {}
    video_id = str(body.get("video_id", ""))
    if not video_id:
        return jsonify({"ok": False, "error": "video_id required"}), 400
    current = variation.load_profiles().get(video_id, {})
    tags = variation.normalise_tags(body.get("theme_tags", current.get("theme_tags", [])))
    device = body.get("opening_device", current.get("opening_device", ""))
    if device and device not in variation.DEVICES:
        return jsonify({"ok": False, "error": "Unknown opening device"}), 400
    value = {**current, "theme_tags": tags, "primary": tags[0] if tags else "", "opening_device": device,
             "narrative_type": body.get("narrative_type", current.get("narrative_type", "")), "source": "edited"}
    return jsonify({"ok": True, "profile": variation.save_profile(video_id, value)})


@app.route("/api/variation/backfill", methods=["POST"])
def variation_backfill():
    """Profiles for published episodes that have none (one Gemini call each, cached)."""
    from agents.gemini_client import generate
    from studio import variation
    from uploader.publications import load_ledger
    profiles = variation.backfill(load_ledger().get("publications", []), generator=generate)
    return jsonify({"ok": True, "profiles": profiles})


@app.route("/api/upload", methods=["POST"])
def upload():
    if _state.get("uploading"):
        return jsonify({"ok": False, "error": "Upload already in progress"})
    if not _state.get("video_path") or not os.path.exists(_state["video_path"]):
        return jsonify({"ok": False, "error": "No video to upload"})

    body    = request.get_json(force=True) or {}
    privacy = body.get("privacy", config.VIDEO_PRIVACY)
    publish_at = body.get("publish_at", "").strip()   # RFC 3339 datetime or ""
    from uploader.approval import check_approval
    problems = check_approval(config.OUTPUT_DIR, **_upload_metadata())
    if problems:
        return jsonify({"ok": False, "error": "Upload refused: " + "; ".join(problems),
                        "reasons": problems}), 409

    def _do_upload():
        _state["uploading"]  = True
        _state["upload_pct"] = 0
        _state["yt_url"]     = None
        try:
            from uploader.youtube import upload_run
            meta = _upload_metadata()
            record = upload_run(
                config.OUTPUT_DIR, video_path=meta["video_path"], title=meta["title"],
                description=meta["description"], tags=meta["tags"], privacy=privacy,
                publish_at=publish_at, thumbnail_path=meta["thumbnail_path"],
                progress=lambda pct: _state.__setitem__("upload_pct", pct),
            )
            _state["yt_url"]     = record["url"]
            _state["upload_pct"] = 100
            _log(f"Uploaded! {_state['yt_url']} (recorded as pending publication)")
            notes = []
            if record.get("shorts"):
                from uploader import shorts_upload
                notes.append(shorts_upload.reminder(record["url"], len(record["shorts"])))
            from uploader import localize
            notes.append(localize.DUBBING_REMINDER)          # Phase 4.0-B: no API for auto-dubbing
            _state["upload_note"] = "\n".join(notes)
            for note in notes:
                _log(note)

        except Exception as e:
            _log(f"Upload error: {e}", "error")
            _state["error"] = str(e)
        finally:
            _state["uploading"] = False

    threading.Thread(target=_do_upload, daemon=True).start()
    return jsonify({"ok": True})


# ── Settings read / write ─────────────────────────────────────────────────────

_CONFIG_PATCH_KEYS = {
    "MUSIC_ENABLED":     "music_enabled",
    "MUSIC_VOLUME":      "music_volume",
    "CHANNEL_NAME":      "channel_name",
    "VOICE_ID":          "voice_id",
    "VOICE_RATE":        "voice_rate",
    "VOICE_PITCH":       "voice_pitch",
    "OVERLAY_OPACITY":   "overlay",
    "KB_ZOOM_START":     "kb_start",
    "KB_ZOOM_END":       "kb_end",
    "VIDEO_PRIVACY":     "privacy",
    "BROLL_INTERVAL":    "broll_interval",
    "BROLL_XFADE_DUR":   "broll_xfade",
    "RENDER_PRESET":     "render_preset",
}


def patch_config_source(src: str, body: dict) -> str:
    """Rewrite the first assignment of each edited setting; keep line endings intact."""
    newline = "\r\n" if "\r\n" in src else "\n"
    for attr, key in _CONFIG_PATCH_KEYS.items():
        val = body.get(key)
        if val is None:
            continue
        pat = rf'({re.escape(attr)}\s*=\s*)[^\r\n]+'
        value = f'"{val}"' if isinstance(val, str) else str(val)
        src = re.sub(pat, lambda match: match.group(1) + value, src, count=1)
    # Channel description (multiline)
    if "channel_desc" in body:
        cd = body["channel_desc"].replace('"""', "'''").replace("\r\n", "\n").replace("\n", newline)
        pat = r'(CHANNEL_DESCRIPTION\s*=\s*)"""[\s\S]*?"""'
        replacement = f'CHANNEL_DESCRIPTION = """{newline}{cd}{newline}"""'
        src = re.sub(pat, lambda match: replacement, src, count=1)
    return src


def patch_config_file(cfg_path: str, body: dict) -> None:
    """config.py contains non-ASCII (box-drawing) characters: always UTF-8, never translate EOLs."""
    with open(cfg_path, encoding="utf-8", newline="") as f:
        src = f.read()
    with open(cfg_path, "w", encoding="utf-8", newline="") as f:
        f.write(patch_config_source(src, body))


def _key_configured(value):
    value = str(value or "").strip()
    return bool(value and not value.startswith("YOUR_") and "your_" not in value.lower())


def _write_env_settings(updates):
    """Update only named .env values, preserving unrelated credentials."""
    env_path = Path(ROOT) / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    output = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if remaining and output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    temp_path = env_path.with_suffix(".env.tmp")
    temp_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temp_path.replace(env_path)

@app.route("/api/settings")
def get_settings():
    return jsonify({
        "gemini_configured":  _key_configured(config.GEMINI_API_KEY),
        "gemini_key_source":  getattr(config, "GEMINI_API_KEY_SOURCE", "unknown"),
        "pixabay_configured": _key_configured(config.PIXABAY_API_KEY),
        "stock_enabled":      bool(getattr(config, "STOCK_ENABLED", False)),
        "stock_provider":     getattr(config, "STOCK_PROVIDER", "auto"),
        "stock_fallback":     getattr(config, "STOCK_FALLBACK_PROVIDER", "openverse"),
        "stock_cache":        getattr(config, "STOCK_CACHE_ENABLED", True),
        "stock_license_log":  getattr(config, "STOCK_LICENSE_LOG_ENABLED", True),
        "provider_status": {
            "pixabay": "configured" if _key_configured(config.PIXABAY_API_KEY) else "API key required",
            "openverse": "available without credentials",
        },
        "music_enabled":   getattr(config, "MUSIC_ENABLED", True),
        "music_volume":    getattr(config, "MUSIC_VOLUME", 0.12),
        "music_library":   getattr(config, "MUSIC_LIBRARY_DIR", ""),
        "channel_name":    config.CHANNEL_NAME,
        "channel_desc":    config.CHANNEL_DESCRIPTION,
        "voice_rate":      config.VOICE_RATE,
        "voice_pitch":     config.VOICE_PITCH,
        "configured_wpm":  voice_calibration.configured_wpm(),
        "calibrated_wpm":  voice_calibration.calibrated_wpm(),
        "planning_wpm":    voice_calibration.planning_wpm(),
        "gemini_model":    config.GEMINI_MODEL,
        "overlay":         config.OVERLAY_OPACITY,
        "kb_start":        config.KB_ZOOM_START,
        "kb_end":          config.KB_ZOOM_END,
        "privacy":         config.VIDEO_PRIVACY,
        "broll_interval":  getattr(config, "BROLL_INTERVAL",  10.0),
        "broll_xfade":     getattr(config, "BROLL_XFADE_DUR",  1.2),
        "render_preset":   getattr(config, "RENDER_PRESET", "ultrafast"),
        "film_effect":     __import__("video.tape", fromlist=["look"]).look(),
        "motion_style":    __import__("video.motion", fromlist=["style"]).style(),
        "credit_line":     getattr(config, "DESCRIPTION_CREDIT_LINE", ""),
        "end_credit_card": bool(getattr(config, "END_CREDIT_CARD", True)),
        "about_text":      __import__("video.description", fromlist=["about_text"]).about_text(),
        "ambient_enabled": bool(getattr(config, "AMBIENT_ENABLED", True)),
        "cold_open":       __import__("agents.cold_open", fromlist=["configured"]).configured(),
        "banned_topics":   load_banned_topics(),
    })


# ── Channel voice (Phase 3.9-A) ─────────────────────────────────────────────

def _audition_dir() -> Path:
    from scripts.voice_audition import OUT
    return OUT


_voice_job = {"running": False, "done": 0, "total": 0, "current": "", "log": [], "errors": {}}


def voice_rows() -> list[dict]:
    """The catalogue voices installed here plus any other auditioned voice, each with its latest sample
    (play URL), stats and error, and whether it is the channel voice."""
    from audio import tts
    from audio.tts import catalogue
    channel = tts.identity()
    results = {catalogue.key(row["backend"], row["voice"]): row
               for row in (load_json(_audition_dir() / "results.json") or {}).get("rows", [])}
    entries = catalogue.catalogue()
    known = {catalogue.key(entry["backend"], entry["voice"]) for entry in entries}
    if catalogue.key(channel["backend"], channel["voice"]) not in known:   # the channel voice is always listed
        entries.insert(0, {"backend": channel["backend"], "voice": channel["voice"], "label": channel["voice"],
                           "licence": catalogue.LICENCES.get(channel["backend"], "")})
        known.add(catalogue.key(channel["backend"], channel["voice"]))
    entries += [{"backend": row["backend"], "voice": row["voice"], "label": row["voice"], "licence": row.get("licence", "")}
                for key, row in results.items() if key not in known]
    rows = []
    for entry in entries:
        key = catalogue.key(entry["backend"], entry["voice"])
        result = results.get(key, {})
        has_file = bool(result.get("file")) and (_audition_dir() / result["file"]).is_file()
        rows.append({**entry, **{name: result.get(name) for name in ("rtf", "coverage", "drift_ms", "lufs", "vram_mib")},
                     "error": result.get("error") or _voice_job["errors"].get(key),
                     "url": f"/api/voices/sample/{result['file']}" if has_file else "",
                     "current": entry["backend"] == channel["backend"] and entry["voice"] == channel["voice"]})
    return rows


@app.route("/api/voices")
def voices_status():
    """"Vozes": the channel voice, every catalogue voice with its preview, the backends, the reference
    clips and the "Gerar prévias" job."""
    from audio import tts
    from audio.tts import voices
    from scripts.voice_audition import DEFAULT_TEXT
    backends = []
    for name in tts.BACKENDS:
        ok, detail = tts.get(name).available()
        backends.append({"name": name, "available": ok, "detail": detail})
    stale = projects.is_project(config.OUTPUT_DIR) and projects.narration_voice_changed(config.OUTPUT_DIR)
    text = DEFAULT_TEXT.read_text(encoding="utf-8").strip() if DEFAULT_TEXT.is_file() else ""
    return jsonify({"ok": True, "channel": tts.identity(), "voices": voice_rows(), "backends": backends,
                    "speed_choices": [0.85, 0.88, 0.92],
                    "references": voices.references(), "narration_stale": bool(stale),
                    "preview_text": text, "job": {key: value for key, value in _voice_job.items() if key != "errors"}})


@app.route("/api/voices/previews", methods=["POST"])
def voices_previews():
    """"Gerar prévias": render the chosen voices in the background (cached samples are reused; a failing
    voice is recorded and the others go on)."""
    from scripts import voice_audition
    body = request.get_json(force=True) or {}
    pairs = [tuple(str(item).split(":", 1)) for item in body.get("voices") or [] if ":" in str(item)]
    if not pairs:
        return jsonify({"ok": False, "error": "Escolha pelo menos uma voz"}), 400
    if _voice_job["running"]:
        return jsonify({"ok": False, "error": "As prévias já estão sendo geradas"}), 409
    text = str(body.get("text") or "").strip() or voice_audition.DEFAULT_TEXT.read_text(encoding="utf-8").strip()
    _voice_job.update(running=True, done=0, total=len(pairs), current="", log=[], errors={})

    def progress(line):
        _voice_job["log"] = (_voice_job["log"] + [line])[-40:]
        if line.startswith("["):
            _voice_job["current"] = line.split("] ", 1)[-1]
        elif line.startswith("   "):
            _voice_job["done"] += 1

    def work():
        try:
            for row in voice_audition.run(pairs, text, _audition_dir(), progress=progress):
                if row.get("error"):
                    _voice_job["errors"][f"{row['backend']}:{row['voice']}"] = row["error"]
        except Exception as exc:                         # noqa: BLE001 — shown in the panel
            progress(f"Erro: {exc}")
            _log(f"Voice previews: {exc}", "error")
        finally:
            _voice_job.update(running=False, current="")

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "total": len(pairs)})


@app.route("/api/narration/flagged/<int:index>")
def narration_flagged(index):
    """Play a sentence the stutter check still flags (Phase 3.9.5-B)."""
    output = Path(config.OUTPUT_DIR).resolve()
    flagged = (load_json(output / "narration_stutter.json") or {}).get("flagged") or []
    if index >= len(flagged) or not flagged[index].get("clip"):
        abort(404)
    path = (output / flagged[index]["clip"]).resolve()
    if output not in path.parents or path.suffix.lower() != ".wav" or not path.is_file():
        abort(404)
    return send_file(str(path), conditional=True)


@app.route("/api/voices/sample/<name>")
def voices_sample(name):
    folder = _audition_dir().resolve()
    path = (folder / name).resolve()
    if path.parent != folder or path.suffix.lower() not in (".mp3", ".wav") or not path.is_file():
        abort(404)
    return send_file(str(path), conditional=True)


@app.route("/api/voices/channel", methods=["POST"])
def voices_channel():
    """Make a backend + voice the channel voice (used by every later narration and Short)."""
    from audio import tts
    body = request.get_json(force=True) or {}
    backend, voice = str(body.get("backend", "")), str(body.get("voice", "")).strip()
    if backend not in tts.BACKENDS or not voice:
        return jsonify({"ok": False, "error": "Choose a backend and a voice"}), 400
    voice, _, chosen_speed = voice.partition("@")
    settings = {"TTS_BACKEND": backend, "TTS_VOICE": voice}
    speed = body.get("speed") or chosen_speed
    if speed:
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Speed must be a number"}), 400
        if not 0.7 <= speed <= 1.2:
            return jsonify({"ok": False, "error": "Speed must be between 0.7 and 1.2"}), 400
        config.TTS_LOCAL_SPEED = speed
        settings["TTS_LOCAL_SPEED"] = str(speed)
    config.TTS_BACKEND, config.TTS_VOICE = backend, voice
    try:
        _write_env_settings(settings)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not save the voice: {exc}"}), 500
    stale = projects.is_project(config.OUTPUT_DIR) and projects.narration_voice_changed(config.OUTPUT_DIR)
    return jsonify({"ok": True, "channel": tts.identity(), "narration_stale": bool(stale)})


@app.route("/api/voices/reference", methods=["POST"])
def voices_reference():
    """Upload a reference clip for cloning: refused without speaker, licence and both confirmations."""
    from audio.tts import voices
    upload = request.files.get("clip")
    if upload is None:
        return jsonify({"ok": False, "error": "No audio file"}), 400
    try:
        path = voices.save_reference(upload.read(), upload.filename or "reference.wav",
                                     speaker=request.form.get("speaker", ""), licence=request.form.get("licence", ""),
                                     own_voice=request.form.get("own_voice") == "true",
                                     not_public_person=request.form.get("not_public_person") == "true")
    except voices.VoiceProvenanceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "name": path.name})


@app.route("/api/gemini/models")
def get_gemini_models():
    """Explicit capability check; never returns credentials or raw SDK errors."""
    try:
        from agents.gemini_client import build_chain, discover_models
        available = discover_models(force=request.args.get("refresh") == "1")
        chain = build_chain(getattr(config, "GEMINI_MODEL", None), available)
        return jsonify({
            "ok": True,
            "available_models": available,
            "selected_model": chain[0] if chain else None,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

@app.route("/api/save-settings", methods=["POST"])
def save_settings():
    body = request.get_json(force=True) or {}
    requested_model = str(body.get("gemini_model") or "").strip()
    if requested_model:
        from agents.gemini_client import RETIRED_MODELS
        if (not re.fullmatch(r"gemini-[a-z0-9.-]+", requested_model)
                or requested_model in RETIRED_MODELS):
            return jsonify({"ok": False, "error": "Unsupported or retired Gemini model"}), 400
    env_updates = {}
    secret_fields = {
        "gemini_key": ("GEMINI_API_KEY", "GEMINI_API_KEY"),
        "pixabay_key": ("PIXABAY_API_KEY", "PIXABAY_API_KEY"),
    }
    for field, (env_name, attr) in secret_fields.items():
        value = str(body.get(field) or "").strip()
        if value:
            env_updates[env_name] = value
            os.environ[env_name] = value
            setattr(config, attr, value)
    if env_updates:
        try:
            _write_env_settings(env_updates)
        except OSError as exc:
            return jsonify({"ok": False, "error": f"Could not save local credentials: {exc}"}), 500
    if str(body.get("gemini_key") or "").strip():
        config.GEMINI_API_KEY_SOURCE = "local .env (GUI override for this process)"
        from agents.gemini_client import reset_client
        reset_client()

    # Update live config object
    m = {
        "stock_enabled": "STOCK_ENABLED",
        "stock_provider": "STOCK_PROVIDER",
        "stock_fallback": "STOCK_FALLBACK_PROVIDER",
        "stock_cache": "STOCK_CACHE_ENABLED",
        "stock_license_log": "STOCK_LICENSE_LOG_ENABLED",
        "music_enabled": "MUSIC_ENABLED",
        "music_volume":  "MUSIC_VOLUME",
        "channel_name":  "CHANNEL_NAME",
        "channel_desc":  "CHANNEL_DESCRIPTION",
        "voice_rate":    "VOICE_RATE",
        "voice_pitch":   "VOICE_PITCH",
        "gemini_model":  "GEMINI_MODEL",
        "overlay":       "OVERLAY_OPACITY",
        "kb_start":      "KB_ZOOM_START",
        "kb_end":        "KB_ZOOM_END",
        "privacy":       "VIDEO_PRIVACY",
        "broll_interval":"BROLL_INTERVAL",
        "broll_xfade":   "BROLL_XFADE_DUR",
        "render_preset": "RENDER_PRESET",
    }
    if "film_effect" in body:                       # Phase 3.8-D: off | grain | super8 | super8_strong
        from video import tape
        if body["film_effect"] not in ("off", *tape.LOOKS):
            return jsonify({"ok": False, "error": "Invalid film effect"}), 400
        config.VIDEO_FILM_EFFECT = config.VIDEO_TAPE_EFFECT = body["film_effect"]
    for k, attr in m.items():
        if k in body:
            val = body[k]
            if attr in ("OVERLAY_OPACITY","KB_ZOOM_START","KB_ZOOM_END","MUSIC_VOLUME",
                          "BROLL_INTERVAL","BROLL_XFADE_DUR"):
                val = float(val)
            if attr == "MUSIC_ENABLED":
                val = bool(val)
            if attr in ("STOCK_ENABLED", "STOCK_CACHE_ENABLED", "STOCK_LICENSE_LOG_ENABLED"):
                val = bool(val)
            if attr == "STOCK_PROVIDER" and val not in ("auto", "pixabay", "openverse"):
                return jsonify({"ok": False, "error": "Invalid stock provider"}), 400
            if attr == "STOCK_FALLBACK_PROVIDER" and val not in ("openverse", "none"):
                return jsonify({"ok": False, "error": "Invalid fallback provider"}), 400
            setattr(config, attr, val)

    stock_env = {}
    if "stock_enabled" in body:
        stock_env["STOCK_ENABLED"] = str(bool(config.STOCK_ENABLED)).lower()
    if "stock_provider" in body:
        stock_env["STOCK_PROVIDER"] = str(config.STOCK_PROVIDER)
    if "stock_fallback" in body:
        stock_env["STOCK_FALLBACK_PROVIDER"] = str(config.STOCK_FALLBACK_PROVIDER)
    if "stock_cache" in body:
        stock_env["STOCK_CACHE_ENABLED"] = str(bool(config.STOCK_CACHE_ENABLED)).lower()
    if "stock_license_log" in body:
        stock_env["STOCK_LICENSE_LOG_ENABLED"] = str(bool(config.STOCK_LICENSE_LOG_ENABLED)).lower()
    if "gemini_model" in body:
        stock_env["GEMINI_MODEL"] = str(config.GEMINI_MODEL)
    if "film_effect" in body:
        stock_env["VIDEO_FILM_EFFECT"] = str(config.VIDEO_FILM_EFFECT)
    if body.get("motion_style") in ("parallax", "kenburns"):         # Phase 3.9-B switches
        config.MOTION_STYLE = stock_env["MOTION_STYLE"] = body["motion_style"]
    if "ambient_enabled" in body:
        config.AMBIENT_ENABLED = bool(body["ambient_enabled"])
        stock_env["AMBIENT_ENABLED"] = str(config.AMBIENT_ENABLED).lower()
    if isinstance(body.get("cold_open"), dict):            # Phase 3.9.7: mode per narrative type
        from agents import cold_open
        current = dict(getattr(config, "COLD_OPEN", {}) or {})
        for letter, mode in body["cold_open"].items():
            if letter in cold_open.DEFAULTS and mode in cold_open.MODES:
                current[letter] = mode
                stock_env[f"COLD_OPEN_{letter}"] = mode
        config.COLD_OPEN = current
    if "credit_line" in body:                          # Phase 3.9-D: one line, kept in .env
        line = " ".join(str(body["credit_line"]).split())
        if not line:
            return jsonify({"ok": False, "error": "The credit line cannot be empty"}), 400
        config.DESCRIPTION_CREDIT_LINE = stock_env["DESCRIPTION_CREDIT_LINE"] = line
    if "end_credit_card" in body:
        config.END_CREDIT_CARD = bool(body["end_credit_card"])
        stock_env["END_CREDIT_CARD"] = str(config.END_CREDIT_CARD).lower()
    if stock_env:
        try:
            _write_env_settings(stock_env)
        except OSError as exc:
            return jsonify({"ok": False, "error": f"Could not save stock settings: {exc}"}), 500

    # Patch config.py file on disk
    cfg_path = os.path.join(ROOT, "config.py")
    try:
        patch_config_file(cfg_path, body)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Saved to memory but disk write failed: {e}"})

    return jsonify({"ok": True})


@app.route("/api/banned-topics")
def get_banned_topics():
    entries = load_banned_topics()
    return jsonify({"topics": [entry["raw"] for entry in entries], "entries": entries})


@app.route("/api/save-banned-topics", methods=["POST"])
def post_banned_topics():
    body   = request.get_json(force=True) or {}
    topics = body.get("topics", [])
    try:
        save_banned_topics(topics)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import webbrowser
    port = 7070
    print(f"\n🎬  YouTube AI Agent Studio")
    print(f"   Opening at  http://localhost:{port}\n")
    # Continue where the user left off, unless that project is archived or gone (then a blank draft).
    startup()
    print(f"   Project folder: {config.OUTPUT_DIR}\n")
    threading.Timer(1.4, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    # Sync publication states now and every PUBLICATION_SYNC_INTERVAL_MINUTES.
    from uploader.publications import start_background_sync
    start_background_sync()
    app.run(port=port, debug=False, threaded=True, use_reloader=False)
