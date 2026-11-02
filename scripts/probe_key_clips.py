"""Probe what image-to-video can run here for key-moment clips (Phase 3.9-B.3).

Run:  python -m scripts.probe_key_clips
      python -m scripts.probe_key_clips --measure --image path/to/shot.jpg   # one real 3.5-s clip, timed

Reports the missing nodes/models (Wan 2.2 TI2V-5B, umt5-xxl text encoder, Wan 2.2 VAE) or, with
--measure, the minutes per clip. Keep KEY_MOMENT_CLIPS = 0 unless a clip renders in < 10 min.
"""
from __future__ import annotations

import argparse
import json
import sys

from video import key_clips


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Image-to-video probe for key-moment clips.")
    parser.add_argument("--measure", action="store_true")
    parser.add_argument("--image")
    args = parser.parse_args(argv)
    report = key_clips.probe()
    if report["runs"] and args.measure and args.image:
        from video.imagegen_comfyui import ComfyClient
        client = ComfyClient(timeout=30)
        name = client.upload_image(args.image)
        report["minutes_per_clip"] = key_clips.timed_probe(
            client, name, "subtle slow motion, the same engraved illustration style", report["models"])
        report["fits"] = report["minutes_per_clip"] < key_clips.MAX_MINUTES
    print(json.dumps(report, indent=2))
    return 0 if report["runs"] else 2


if __name__ == "__main__":
    sys.exit(main())
