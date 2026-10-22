#!/usr/bin/env python3
"""
YouTube AI Agent — Main Pipeline (Free Edition)
Uses: Gemini · Edge TTS · Pixabay/Openverse · MoviePy · Flask · YouTube Data API

Run:  python pipeline.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import config
from agents import gemini_client
from agents.researcher   import research_topic
from agents.scriptwriter import write_script
from video.narrator      import generate_narration
from video.music         import prepare_final_audio
from video.stock         import download_images
from video.creator       import create_video
from review.app          import start_review_server
from uploader.youtube    import build_upload_description, upload_to_youtube
from agents.narrative import atomic_write_json
from uploader.publications import sync_publications_safely


def banner(text: str):
    print("\n" + "─" * 62)
    print(f"  {text}")
    print("─" * 62)


def run(resume: bool = False):
    """Full pipeline. With ``resume`` the saved story brief is reused and the
    script stage continues from its checkpoints (bible, outline and any scene
    batches, expansions or reviews finished before a failure)."""
    gemini_client.clear_call_records()
    try:
        _run(resume)
    finally:
        print("\n  " + gemini_client.usage_line())


def _run(resume: bool):
    Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
    Path(config.IMAGES_DIR).mkdir(exist_ok=True)

    print("\n🎬  Private Gravity  ·  Cinematic Fiction Pipeline\n")

    # Ban topics of videos that became public since the last run (never blocks).
    sync_publications_safely(interactive=False)

    # ── 1. Research ───────────────────────────────────────────
    banner("1 / 6  ·  Developing and selecting a story premise  [Gemini]")
    research = research_topic(
        config.CHANNEL_DESCRIPTION,
        narrative_type=getattr(config, "NARRATIVE_TYPE", "auto"),
        focus_angle=getattr(config, "PSYCHOLOGICAL_THEME", ""),
        target_duration_minutes=getattr(config, "TARGET_DURATION_MINUTES", 15),
        reuse_cached=resume,
    )
    print(f"\n  ✅  Premise: {research['topic']}")
    print(f"      Title : {research['video_title']}")
    print(f"      Hook  : {research.get('hook_question', '')[:90]}")
    atomic_write_json(f"{config.OUTPUT_DIR}/research.json", research)

    # ── 2. Script ─────────────────────────────────────────────
    banner("2 / 6  ·  Character bible → outline → screenplay  [Gemini]")
    script = write_script(
        research,
        target_duration_minutes=getattr(config, "TARGET_DURATION_MINUTES", 15),
        narrative_review=getattr(config, "NARRATIVE_REVIEW_ENABLED", True),
    )
    print(f"\n  ✅  {len(script['sections'])} scenes written")
    for s in script["sections"]:
        words = len(s.get("narration", "").split())
        # Safely grab the title, or default to "Untitled" if the AI forgot it
        title = s.get("title", "Untitled") 
        print(f"      [{s.get('id', 9):02d}] {title:<42} {words:3d} words")

    # ── 3. Stock Images ───────────────────────────────────────
    banner("3 / 6  ·  Acquiring stock images  [Pixabay · Openverse]")
    image_map = download_images(script, config.OUTPUT_DIR)
    found = sum(1 for v in image_map.values() if v)
    print(f"\n  ✅  {found}/{len(image_map)} images downloaded")

    # ── 4. Narration ──────────────────────────────────────────
    banner("4 / 6  ·  Generating voiceover  [Edge TTS — free]")
    audio_path = generate_narration(script, config.OUTPUT_DIR)
    audio_path = prepare_final_audio(
        audio_path, os.path.join(config.OUTPUT_DIR, "narration_mixed.mp3"),
        episode={**research, **script}, output_dir=config.OUTPUT_DIR,
    )
    print(f"\n  ✅  Audio: {audio_path}")

    # ── 5. Video ──────────────────────────────────────────────
    banner("5 / 6  ·  Building cinematic video  [MoviePy]")
    video_path = create_video(script, audio_path, config.OUTPUT_DIR, image_map)

    # ── 6. Review ─────────────────────────────────────────────
    banner("6 / 6  ·  Human review  [Flask dashboard]")
    print("\n  → Opening review dashboard in your browser…")
    review_data = {
        "research":   research,
        "script":     script,
        "video_path": video_path,
    }
    atomic_write_json(
        f"{config.OUTPUT_DIR}/review_data.json",
        {k: v for k, v in review_data.items() if k != "video_path"},
    )

    # The approval is bound to exactly what upload_to_youtube sends.
    upload = {
        "video_path": video_path, "title": script["title"],
        "description": build_upload_description(script), "tags": script.get("tags", []),
        "thumbnail_path": None,
    }
    approved = start_review_server(review_data, run_dir=config.OUTPUT_DIR, upload=upload)

    # ── Upload or stop ────────────────────────────────────────
    if approved:
        banner("🚀  Uploading to YouTube")
        url = upload_to_youtube(script, video_path)
        print(f"\n  🎉  Uploaded: {url} (recorded as pending publication)\n")
    else:
        print("\n  ❌  Rejected — pipeline stopped.")
        print("      Tip: edit output/script.json and re-run just the video step.\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Private Gravity pipeline")
    parser.add_argument("--resume", action="store_true",
                        help="reuse the saved story brief and continue the script stage from its checkpoints")
    run(resume=parser.parse_args().resume)
