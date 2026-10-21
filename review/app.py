"""
Review Server
Spins up a local Flask web app so you can watch the video, read the script
and the narrative review, complete the pre-publication checklist, and approve
or reject before uploading. Approval writes the same review_approval.json the
GUI writes, bound to the exact files pipeline.py will upload.
"""
import os
import threading
import webbrowser
import time
from flask import Flask, render_template, jsonify, request, send_file, abort
from werkzeug.serving import make_server
import config
from uploader.approval import UploadNotApproved, approve as record_approval, review_summary

app = Flask(__name__, template_folder="templates")

# Shared state (set before server starts)
_state = {
    "review_data": {},
    "run_dir": None,
    "upload": {},         # video_path, title, description, tags, thumbnail_path
    "decision": None,     # "approved" | "rejected"
}
_decided = threading.Event()


@app.route("/")
def index():
    return render_template("review.html")


@app.route("/api/data")
def get_data():
    data = _state["review_data"]
    # Strip the video path from JSON (serve it via /video endpoint instead)
    safe = {k: v for k, v in data.items() if k != "video_path"}
    if _state["run_dir"]:
        safe["gate"] = review_summary(_state["run_dir"], **_state["upload"])
        safe["upload_description"] = _state["upload"].get("description", "")
        safe["privacy"] = config.VIDEO_PRIVACY
    return jsonify(safe)


@app.route("/video")
def serve_video():
    path = _state["review_data"].get("video_path", "")
    if path and os.path.exists(path):
        return send_file(path, mimetype="video/mp4")
    abort(404)


@app.route("/api/approve", methods=["POST"])
def approve():
    body = request.get_json(silent=True) or {}
    try:
        record_approval(
            _state["run_dir"], checklist=body.get("checklist") or {},
            notes=body.get("notes", ""),
            title_override_reason=body.get("title_override_reason", ""),
            status_override_reason=body.get("status_override_reason", ""),
            **_state["upload"],
        )
    except UploadNotApproved as exc:
        return jsonify({"status": "refused", "reasons": exc.reasons}), 400
    _state["decision"] = "approved"
    _decided.set()
    return jsonify({"status": "approved"})


@app.route("/api/reject", methods=["POST"])
def reject():
    _state["decision"] = "rejected"
    _decided.set()
    return jsonify({"status": "rejected"})


def start_review_server(review_data: dict, *, run_dir: str | None = None,
                        upload: dict | None = None) -> bool:
    """
    Starts Flask, opens the browser, and blocks until the user approves
    (with a complete checklist) or rejects.
    Returns True if approved, False if rejected.
    """
    _state.update({
        "review_data": review_data, "run_dir": run_dir or config.OUTPUT_DIR,
        "upload": dict(upload or {}), "decision": None,
    })
    _decided.clear()

    def open_browser():
        time.sleep(1.8)
        webbrowser.open(f"http://localhost:{config.REVIEW_PORT}")

    threading.Thread(target=open_browser, daemon=True).start()
    print(f"   → Review dashboard at http://localhost:{config.REVIEW_PORT}")

    server = make_server("127.0.0.1", config.REVIEW_PORT, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _decided.wait()
        time.sleep(0.5)   # let the browser receive the response
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return _state.get("decision") == "approved"
