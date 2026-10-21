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
from video.timeline import load_timeline

from flask import Flask, Response, jsonify, request, send_file, abort

import config

# ── Lazy-import pipeline modules so GUI loads even if deps missing ──────────
def _import(mod):
    try:
        return __import__(mod, fromlist=["_"])
    except Exception as e:
        return None

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
        "images":    "pending",
        "narration": "pending",
        "video":     "pending",
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

def _stage(name, status):
    _state["stages"][name] = status
    _log_queue.put(("stage", f"{name}:{status}"))


# ══════════════════════════════════════════════════════════════════════════════
#  THUMBNAIL GENERATOR  (built-in — no separate file needed)
# ══════════════════════════════════════════════════════════════════════════════

def _make_thumbnail(script, image_map, output_dir):
    from PIL import Image, ImageDraw, ImageFont

    TW, TH = 1280, 720
    title = script.get("title", "")
    C = config.COLORS

    def _font(variant, size):
        for p in config.FONT_PATHS.get(variant, config.FONT_PATHS["regular"]):
            try: return ImageFont.truetype(p, size)
            except: pass
        return ImageFont.load_default()

    def _wrap(draw, text, font, max_w):
        words, lines, cur = text.split(), [], []
        for w in words:
            test = " ".join(cur + [w])
            if draw.textbbox((0,0), test, font=font)[2] > max_w and cur:
                lines.append(" ".join(cur)); cur = [w]
            else: cur.append(w)
        if cur: lines.append(" ".join(cur))
        return lines or [text]

    def _shadow(draw, xy, text, font, fill, r=5):
        x, y = xy
        for dx in range(-r, r+1, 2):
            for dy in range(-r, r+1, 2):
                if abs(dx)+abs(dy) >= r:
                    draw.text((x+dx, y+dy), text, font=font, fill=(0,0,0))
        draw.text((x,y), text, font=font, fill=fill)

    # Background
    # FIX: For Shorts, image_map values are lists of paths. Unwrap to a single path.
    def _first_path(v):
        if isinstance(v, list):
            return v[0] if v else None
        return v

    bg_path = _first_path(image_map.get(1)) or next(
        (_first_path(v) for v in image_map.values() if v), None
    )
    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGB")
        bw, bh = bg.size
        scale = max(TW/bw, TH/bh)
        bg = bg.resize((int(bw*scale), int(bh*scale)), Image.LANCZOS)
        nw, nh = bg.size
        bg = bg.crop(((nw-TW)//2, (nh-TH)//2, (nw-TW)//2+TW, (nh-TH)//2+TH))
    else:
        bg = Image.new("RGB", (TW, TH), C["background"])

    # Dark overlay
    ov = Image.new("RGBA", (TW, TH), (0,0,0,0))
    od = ImageDraw.Draw(ov)
    for x in range(TW):
        od.line([(x,0),(x,TH)], fill=(0,0,10, int(190*(1-x/TW*0.45))))
    od.rectangle([0, TH-80, TW, TH], fill=(0,0,0,185))
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    # Accent bar
    draw.rectangle([60, 100, 700, 108], fill=C["primary"])

    # Title
    font_big = _font("bold", 92)
    font_sm  = _font("bold", 70)
    lines = _wrap(draw, title.upper(), font_big, TW-140)
    if len(lines) > 2: font_big = font_sm; lines = _wrap(draw, title.upper(), font_big, TW-140)
    lines = lines[:2]

    for i, line in enumerate(lines):
        words = line.split(" ", 1)
        fw = words[0]; rest = words[1] if len(words) > 1 else ""
        fw_w = draw.textbbox((0,0), fw+" ", font=font_big)[2]
        _shadow(draw, (60, 130+i*108), fw, font_big, fill=C["highlight"], r=6)
        if rest: _shadow(draw, (60+fw_w, 130+i*108), rest, font_big, fill=C["white"], r=6)

    # Channel name
    _shadow(draw, (60, TH-60), config.CHANNEL_NAME.upper(), _font("bold", 36), fill=C["light"], r=3)

    out = os.path.join(output_dir, "thumbnail.jpg")
    bg.save(out, "JPEG", quality=95)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  DESCRIPTION / CHAPTERS BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _ts(secs):
    h, m, s = secs//3600, (secs%3600)//60, secs%60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _build_description(script, research, video_type: str = "normal", output_dir=None):
    sections = script.get("sections", [])
    is_shorts = video_type == "shorts" or script.get("video_type") == "shorts"

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

    tags = script.get("tags", [])
    if tags: lines.append(" ".join(f"#{t.replace(' ','')}" for t in tags[:8]))
    if is_shorts: lines.append("#Shorts #YouTubeShorts")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE RUNNER  (runs in background thread)
# ══════════════════════════════════════════════════════════════════════════════

def _run_pipeline(steps: list, topic_override: str = "", video_type: str = "normal",
                  focus_angle: str = "", narrative_type: str = "auto",
                  target_duration: float = 15, narrative_review: bool = True):
    orig_stdout = sys.stdout
    sys.stdout = _Capture(orig_stdout)
    _state["running"] = True
    _state["error"]   = None
    _state["stop_requested"] = False
    _stop_event.clear()

    def _check_stop(after_stage: str):
        """Raise if user clicked Stop."""
        if _stop_event.is_set():
            raise InterruptedError(f"Stopped by user after {after_stage}")

    def _complete_partial(label: str):
        _log(f"Partial pipeline complete after {label}. Review the saved artifacts before continuing.")
        _log_queue.put(("done", ""))

    try:
        Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
        Path(config.IMAGES_DIR).mkdir(exist_ok=True)

        from agents.researcher   import research_topic
        from agents.scriptwriter import write_script
        from video.narrator      import generate_narration
        from video.stock         import download_images, load_existing_images
        from video.creator       import create_video
        from video.music         import mix_music_with_narration

        # ── Research ──────────────────────────────────────────
        if "research" in steps:
            _stage("research", "running")
            research = research_topic(
                channel_description = config.CHANNEL_DESCRIPTION,
                topic_override      = topic_override,
                focus_angle         = focus_angle,
                narrative_type      = narrative_type,
                target_duration_minutes = target_duration,
            )
            invalidate_downstream("story_brief", config.OUTPUT_DIR)
            _state["research"] = research
            _state.update({
                "script": None, "character_bible": None, "story_outline": None,
                "narrative_review": None, "image_map": {}, "audio_path": None,
                "video_path": None, "thumb_path": None,
            })
            atomic_write_json(f"{config.OUTPUT_DIR}/research.json", research)
            _stage("research", "done")
            _log(f"Topic: {research['video_title']}")
        else:
            try:
                with open(f"{config.OUTPUT_DIR}/research.json") as f:
                    _state["research"] = json.load(f)
            except: pass

        _check_stop("research")
        research = _state["research"]
        if not research: raise ValueError("No research data — run Research step first.")
        if not any(step in steps for step in ("script", "images", "narration", "music", "video")):
            _complete_partial("Story Brief")
            return

        # ── Script ────────────────────────────────────────────
        if "script" in steps:
            _stage("script", "running")
            _log(f"Generating {'Shorts' if video_type == 'shorts' else 'normal'} script…")
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
                with open(f"{config.OUTPUT_DIR}/script.json") as f:
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
        downstream_requested = any(
            step in steps for step in ("images", "narration", "music", "video")
        )
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
        if not any(step in steps for step in ("images", "narration", "music", "video")):
            _state["description"] = _build_description(script, research, video_type=video_type)
            _complete_partial("Narrative Development")
            return

        # ── Images ────────────────────────────────────────────
        if "images" in steps:
            _stage("images", "running")
            image_map = download_images(script, config.OUTPUT_DIR)
            _state["image_map"] = image_map
            _stage("images", "done")
        else:
            _state["image_map"] = load_existing_images(script, config.OUTPUT_DIR)

        _check_stop("images")
        if not any(step in steps for step in ("narration", "music", "video")):
            _complete_partial("Stock Assets")
            return

        # ── Narration ─────────────────────────────────────────
        if "narration" in steps:
            _stage("narration", "running")
            audio_path = generate_narration(script, config.OUTPUT_DIR)
            _state["audio_path"] = audio_path
            _stage("narration", "done")
        else:
            ap = os.path.join(config.OUTPUT_DIR, "narration.mp3")
            _state["audio_path"] = ap if os.path.exists(ap) else None

        if not _state["audio_path"] or not os.path.exists(_state["audio_path"]):
            raise FileNotFoundError("narration.mp3 missing — run Narration step.")

        _check_stop("narration")

        # ── Music  (optional, non-blocking) ───────────────────
        final_audio_path = _state["audio_path"]
        if config.MUSIC_ENABLED and "music" in steps:
            _log("Mixing background music from library…")
            mixed_path = os.path.join(config.OUTPUT_DIR, "narration_mixed.mp3")
            final_audio_path = mix_music_with_narration(
                narration_path = _state["audio_path"],
                output_path    = mixed_path,
                music_volume   = getattr(config, "MUSIC_VOLUME", 0.12),
                library_dir    = getattr(config, "MUSIC_LIBRARY_DIR", ""),
            )
            _log("Audio ready (narration + music)")

        _check_stop("music")

        # ── Thumbnail ─────────────────────────────────────────
        _log("Generating thumbnail…")
        try:
            image_map_int = {int(k): v for k, v in _state["image_map"].items()}
            thumb = _make_thumbnail(script, image_map_int, config.OUTPUT_DIR)
            _state["thumb_path"] = thumb
            _log(f"Thumbnail saved: {thumb}")
        except Exception as e:
            _log(f"Thumbnail failed (non-fatal): {e}", "warn")

        _check_stop("thumbnail")

        # ── Video ─────────────────────────────────────────────
        if "video" in steps:
            _stage("video", "running")
            image_map_int = {int(k): v for k, v in _state["image_map"].items()}
            video_path = create_video(
                script, final_audio_path, config.OUTPUT_DIR, image_map_int,
                video_type=video_type
            )
            _state["video_path"] = video_path
            _stage("video", "done")
        else:
            vp_short = os.path.join(config.OUTPUT_DIR, "final_short.mp4")
            vp_norm  = os.path.join(config.OUTPUT_DIR, "final_video.mp4")
            vp = vp_short if os.path.exists(vp_short) else vp_norm
            _state["video_path"] = vp if os.path.exists(vp) else None

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
    body       = request.get_json(force=True) or {}
    steps       = body.get("steps", ["research","script","images","narration","music","video"])
    if not isinstance(steps, list) or not steps:
        return jsonify({"ok": False, "error": "Select at least one pipeline step"}), 400
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
    if video_type == "normal" and not 12 <= target_duration <= 20:
        return jsonify({"ok": False, "error": "Regular episodes must target 12-20 minutes"}), 400
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


@app.route("/api/stop", methods=["POST"])
def stop_pipeline():
    """Request the pipeline to stop after the current stage finishes."""
    _stop_event.set()
    _state["stop_requested"] = True
    _log("⏹ Stop requested — will halt after current step completes…", "warn")
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    return jsonify({
        "running":        _state["running"],
        "stop_requested": _state.get("stop_requested", False),
        "stages":         _state["stages"],
        "has_video":      bool(_state.get("video_path") and os.path.exists(_state["video_path"])),
        "has_thumb":      bool(_state.get("thumb_path") and os.path.exists(_state["thumb_path"])),
        "has_script":     bool(_state.get("script")),
        "yt_url":         _state.get("yt_url"),
        "error":          _state.get("error"),
        "upload_pct":     _state.get("upload_pct", 0),
        "uploading":      _state.get("uploading", False),
        "video_type":     _state.get("video_type", "normal"),
    })


# ── Media serving ─────────────────────────────────────────────────────────────

@app.route("/video")
def serve_video():
    p = _state.get("video_path")
    if p and os.path.exists(p): return send_file(p, mimetype="video/mp4")
    abort(404)

@app.route("/thumbnail")
def serve_thumb():
    p = _state.get("thumb_path")
    if p and os.path.exists(p): return send_file(p, mimetype="image/jpeg")
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
        if "title" in body:
            Path(config.OUTPUT_DIR, "thumbnail.jpg").unlink(missing_ok=True)
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

@app.route("/api/regenerate-thumbnail", methods=["POST"])
def regen_thumbnail():
    if not _state.get("script"):
        return jsonify({"ok": False, "error": "No script loaded"})
    body = request.get_json(force=True) or {}

    # Allow title override for thumbnail
    if "title" in body and _state["script"]:
        _state["script"]["title"] = body["title"]

    try:
        image_map_int = {int(k): v for k, v in _state.get("image_map", {}).items()}
        thumb = _make_thumbnail(_state["script"], image_map_int, config.OUTPUT_DIR)
        _state["thumb_path"] = thumb
        return jsonify({"ok": True, "ts": int(time.time())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── YouTube upload ────────────────────────────────────────────────────────────

@app.route("/api/yt-status")
def yt_status():
    token_ok = os.path.exists("youtube_token.pickle")
    secret_ok = os.path.exists(config.YOUTUBE_CLIENT_SECRET)
    return jsonify({"token": token_ok, "secret": secret_ok})

def _upload_metadata() -> dict:
    """Exactly what an upload sends: shared by the upload and its approval."""
    script = _state.get("script") or {}
    thumb = _state.get("thumb_path")
    return {
        "video_path": _state.get("video_path"),
        "title": script.get("title", ""),
        "description": _state.get("description", script.get("description", "")),
        "tags": script.get("tags", []),
        "thumbnail_path": thumb if thumb and os.path.exists(thumb) else None,
    }


@app.route("/api/review-gate")
def get_review_gate():
    """Checklist, narrative status, title and license findings, approval validity."""
    from uploader.approval import review_summary
    meta = _upload_metadata()
    if not meta["video_path"] or not os.path.exists(meta["video_path"]):
        return jsonify({"ok": False, "error": "No video to review"})
    return jsonify({"ok": True, **review_summary(config.OUTPUT_DIR, **meta)})


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
            **meta,
        )
    except UploadNotApproved as exc:
        return jsonify({"ok": False, "error": str(exc), "reasons": exc.reasons}), 400
    return jsonify({"ok": True, "approved_at": record["approved_at"]})


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

        except Exception as e:
            _log(f"Upload error: {e}", "error")
            _state["error"] = str(e)
        finally:
            _state["uploading"] = False

    threading.Thread(target=_do_upload, daemon=True).start()
    return jsonify({"ok": True})


# ── Settings read / write ─────────────────────────────────────────────────────

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
        "voice_id":        config.VOICE_ID,
        "voice_rate":      config.VOICE_RATE,
        "voice_pitch":     config.VOICE_PITCH,
        "gemini_model":    config.GEMINI_MODEL,
        "overlay":         config.OVERLAY_OPACITY,
        "kb_start":        config.KB_ZOOM_START,
        "kb_end":          config.KB_ZOOM_END,
        "privacy":         config.VIDEO_PRIVACY,
        "broll_interval":  getattr(config, "BROLL_INTERVAL",  10.0),
        "broll_xfade":     getattr(config, "BROLL_XFADE_DUR",  1.2),
        "render_preset":   getattr(config, "RENDER_PRESET", "ultrafast"),
        "banned_topics":   load_banned_topics(),
    })


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
        "stock_provider": "STOCK_PROVIDER",
        "stock_fallback": "STOCK_FALLBACK_PROVIDER",
        "stock_cache": "STOCK_CACHE_ENABLED",
        "stock_license_log": "STOCK_LICENSE_LOG_ENABLED",
        "music_enabled": "MUSIC_ENABLED",
        "music_volume":  "MUSIC_VOLUME",
        "channel_name":  "CHANNEL_NAME",
        "channel_desc":  "CHANNEL_DESCRIPTION",
        "voice_id":      "VOICE_ID",
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
    for k, attr in m.items():
        if k in body:
            val = body[k]
            if attr in ("OVERLAY_OPACITY","KB_ZOOM_START","KB_ZOOM_END","MUSIC_VOLUME",
                          "BROLL_INTERVAL","BROLL_XFADE_DUR"):
                val = float(val)
            if attr == "MUSIC_ENABLED":
                val = bool(val)
            if attr in ("STOCK_CACHE_ENABLED", "STOCK_LICENSE_LOG_ENABLED"):
                val = bool(val)
            if attr == "STOCK_PROVIDER" and val not in ("auto", "pixabay", "openverse"):
                return jsonify({"ok": False, "error": "Invalid stock provider"}), 400
            if attr == "STOCK_FALLBACK_PROVIDER" and val not in ("openverse", "none"):
                return jsonify({"ok": False, "error": "Invalid fallback provider"}), 400
            setattr(config, attr, val)

    stock_env = {}
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
    if stock_env:
        try:
            _write_env_settings(stock_env)
        except OSError as exc:
            return jsonify({"ok": False, "error": f"Could not save stock settings: {exc}"}), 500

    # Patch config.py file on disk
    cfg_path = os.path.join(ROOT, "config.py")
    try:
        with open(cfg_path) as f: src = f.read()

        patches = {
            "MUSIC_ENABLED":     body.get("music_enabled"),
            "MUSIC_VOLUME":      body.get("music_volume"),
            "CHANNEL_NAME":      body.get("channel_name"),
            "VOICE_ID":          body.get("voice_id"),
            "VOICE_RATE":        body.get("voice_rate"),
            "VOICE_PITCH":       body.get("voice_pitch"),
            "OVERLAY_OPACITY":   body.get("overlay"),
            "KB_ZOOM_START":     body.get("kb_start"),
            "KB_ZOOM_END":       body.get("kb_end"),
            "VIDEO_PRIVACY":     body.get("privacy"),
            "BROLL_INTERVAL":    body.get("broll_interval"),
            "BROLL_XFADE_DUR":   body.get("broll_xfade"),
            "RENDER_PRESET":     body.get("render_preset"),
        }
        for attr, val in patches.items():
            if val is None: continue
            # Replace the first assignment of this variable
            if isinstance(val, str):
                pat = rf'({re.escape(attr)}\s*=\s*)[^\n]+'
                src = re.sub(pat, rf'\g<1>"{val}"', src, count=1)
            else:
                pat = rf'({re.escape(attr)}\s*=\s*)[^\n]+'
                src = re.sub(pat, rf'\g<1>{val}', src, count=1)

        # Channel description (multiline)
        if "channel_desc" in body:
            cd = body["channel_desc"].replace('"""', "'''")
            pat = r'(CHANNEL_DESCRIPTION\s*=\s*)"""[\s\S]*?"""'
            src = re.sub(pat, f'CHANNEL_DESCRIPTION = """\n{cd}\n"""', src, count=1)

        with open(cfg_path, "w") as f: f.write(src)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Saved to memory but disk write failed: {e}"})

    return jsonify({"ok": True})


@app.route("/api/banned-topics")
def get_banned_topics():
    entries = load_banned_topics()
    return jsonify({"topics": [entry["raw"] for entry in entries], "entries": entries})


@app.route("/api/publications")
def get_publications():
    from uploader.publications import load_ledger
    return jsonify(load_ledger())


@app.route("/api/sync-publications", methods=["POST"])
def post_sync_publications():
    from uploader.publications import sync_publications_safely
    summary = sync_publications_safely(interactive=False)
    if summary is None:
        return jsonify({"ok": False, "error": "Sync failed; see the log. Run python -m scripts.sync_publications to re-authorize."})
    return jsonify({"ok": True, "summary": summary})


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
    threading.Timer(1.4, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    # Sync publication states now and every PUBLICATION_SYNC_INTERVAL_MINUTES.
    from uploader.publications import start_background_sync
    start_background_sync()
    app.run(port=port, debug=False, threaded=True, use_reloader=False)
