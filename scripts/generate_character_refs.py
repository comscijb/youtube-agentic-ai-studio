"""Generate reference candidates (4 portraits per character, 2 per recurring location).

Run:  python -m scripts.generate_character_refs                 # candidates (ComfyUI + Gemini)
      python -m scripts.generate_character_refs --approve character:julian_vance:2
      python -m scripts.generate_character_refs --status

Approve one candidate per character (and optionally per location) in the GUI
or with --approve; shot generation refuses to start before that.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from studio import projects
from agents import visual_bible as vbible
from agents.narrative import load_json
from video import shot_generation as gen
from video.imagegen_comfyui import ComfyUnavailable, ImageGenerator


def status(output: Path) -> int:
    vb = load_json(output / vbible.FILE_NAME)
    if not vb:
        print("No visual_bible.json yet.")
        return 1
    candidates = load_json(output / gen.CANDIDATES_FILE) or {}
    for kind, items in (("character", vb.get("characters", [])), ("location", vbible.recurring_locations(vb))):
        for item in items:
            ident = item[f"{kind}_id"]
            approved = vbible.approved_reference(vb, kind, ident, output)
            count = len(candidates.get(vbible.reference_key(kind, ident), []))
            print(f"  {kind:<9} {ident:<24} candidates={count}  approved={'yes' if approved else 'no'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Character/location reference candidates.")
    parser.add_argument("--output-dir", default=None, help="explicit folder (overrides the project)")
    projects.add_argument(parser)
    parser.add_argument("--approve", metavar="KIND:ID:N",
                        help="approve candidate N, e.g. character:julian_vance:2 or location:loft:1")
    parser.add_argument("--only", help="character_id to (re)generate")
    parser.add_argument("--no-locations", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    output = projects.cli_output(args)
    if args.status:
        return status(output)
    if args.approve:
        try:
            kind, ident, number = args.approve.split(":")
            vb = load_json(output / vbible.FILE_NAME)
            gen.approve_reference(vb, kind, ident, int(number), output)
        except (ValueError, FileNotFoundError, TypeError) as exc:
            parser.error(str(exc))
        print(f"Approved {kind} {ident} #{number}.")
        return status(output)
    vb = gen.ensure_visual_bible(output)
    try:
        gen.generate_reference_candidates(vb, output, ImageGenerator(output),
                                          characters=[args.only] if args.only else None,
                                          locations=not args.no_locations)
    except ComfyUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    print("Candidates saved under output/character_refs/candidates and output/location_refs/candidates.")
    print("Approve one per character in the GUI, or: python -m scripts.generate_character_refs "
          "--approve character:<id>:<n>")
    return status(output)


if __name__ == "__main__":
    sys.exit(main())
