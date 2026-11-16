"""Compare the viewer simulation with the real retention (Phase 4.1-G). Offline: it reads the recorded simulations
(docs/reports/4.1/recorded/<name>/viewer_sim.json) and the analytics cache (data/analytics/<video id>.json).

Run:  python -m scripts.attention_calibration
For every published long-form episode with both a recorded simulation and a real retention curve, prints the
correlation between the simulated share still watching and the real audience watch ratio, and the simulated leave
points next to the biggest real drops. The simulation is a filter, not a forecast: this tells how far to trust it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RECORDED = Path("docs/reports/4.1/recorded")


def rows(recorded: Path = RECORDED) -> list[dict]:
    from agents import viewer_sim
    from agents.narrative import load_json
    from uploader import analytics, publications, retention
    ledger = publications.load_ledger().get("publications", [])
    calibration = load_json(recorded.parent / "calibration.json") or {}
    found = []
    for name, info in calibration.items():
        record = next((item for item in ledger if item.get("project") == info.get("slug") and not item.get("parent_video_id")
                       and item.get("kind", "episode") != "short"), None)
        sim = load_json(recorded / name / "viewer_sim.json")
        stats = analytics.cached(record["video_id"]) if record else None
        curve = (stats or {}).get("retention") or []
        row = {"name": name, "video_id": (record or {}).get("video_id"), "has_sim": bool(sim), "has_retention": bool(curve)}
        if sim and curve:
            duration = float(sim.get("duration") or 0)
            row["correlation"] = viewer_sim.correlation(sim.get("predicted_retention") or [], curve, duration)
            row["simulated_leaves"] = [persona["first_leave_second"] for persona in sim.get("personas", [])]
            row["real_drops"] = [item["seconds"] for item in retention.drops(curve, duration)]
        found.append(row)
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    found = rows()
    if args.json:
        print(json.dumps(found, indent=1, ensure_ascii=False))
        return 0
    for row in found:
        if "correlation" in row:
            print(f"{row['name']}: correlação {row['correlation']} · saídas simuladas {row['simulated_leaves']} · quedas reais {row['real_drops']}")
        else:
            print(f"{row['name']}: " + ("sem retenção real ainda" if row["has_sim"] else "sem simulação gravada"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
