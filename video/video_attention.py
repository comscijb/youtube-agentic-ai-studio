"""The viewer-experience QA for the cut (Phase 4.2-F): a video attention map in 5-second bins.

Each bin counts narration events (4.1's attention map), visual changes (view cuts, text on screen) and audio events
(SFX, music changes). The checks:

1. no hold longer than 7 s without a visual change (except the final image)          — major
2. no 20 s without any narration, visual or audio event                               — minor
3. the first 5 seconds have image motion, a sound event and the first spoken words;
   no black screen, no slow fade-in, no title card                                    — major
4. key lines readable: at least 1.2 s on screen, contrast at least 4.5               — minor
5. speech intelligibility: in every 1 s window the voice is ≥ 10 dB above the
   music + SFX bed in the 1-4 kHz band                                                — major
6. no SFX within 1 s of another accent                                                — minor

Rendering is allowed with warnings; a major finding needs a typed override (``override``). One-click fixes write
``cut_overrides.json``: an extra cut (a derived view / a shorter hold), a removed SFX, a lower music level.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np

from agents.narrative import atomic_write_json, load_json

BIN = 5.0
MAX_HOLD = 7.0
QUIET = 20.0
MIN_LINE = 1.2
MIN_CONTRAST = 4.5
SPEECH_MARGIN_DB = 10.0
ACCENT_GAP = 1.0
OVERRIDES = "cut_overrides.json"
REPORT = "video_attention.json"
MAJOR_CHECKS = ("hold", "first_5_seconds", "intelligibility")


def _finding(check: str, severity: str, start: float, end: float, claim: str, fix: dict | None = None) -> dict:
    return {"check": check, "severity": severity, "start": round(start, 2), "end": round(end, 2),
            "at": f"{int(start) // 60}:{int(start) % 60:02d}", "claim": claim, "fix": fix}


def visual_changes(plan: dict, durations: list[float], sections: list[dict]) -> list[dict]:
    """[{"time", "kind"}] of every view cut on the global clock, plus the view list for the hold check."""
    changes, cursor = [], 0.0
    for section, duration in zip(sections, durations):
        for item in plan.get("schedule", {}).get(section["id"], []) or plan.get("sections", {}).get(str(section["id"]), []):
            changes.append({"time": round(cursor + item["start"], 3), "end": round(cursor + item["end"], 3),
                            "kind": "cut", "final": bool(item.get("final")), "section": section["id"]})
        cursor += duration
    return sorted(changes, key=lambda item: item["time"])


def build(*, plan: dict, sections: list[dict], durations: list[float], narration_events: list[float],
          key_lines: list[dict], sfx_cues: list[dict], music: dict | None, first_word: float | None,
          title_card: bool = False, dip_at_start: bool = False, intelligibility: list[dict] | None = None) -> dict:
    total = sum(durations)
    views = visual_changes(plan, durations, sections)
    visual = [item["time"] for item in views] + [line["start"] for line in key_lines]
    audio = [cue["start"] for cue in sfx_cues] + [segment["start"] for segment in (music or {}).get("segments", [])] + \
        [drop[0] for drop in (music or {}).get("drops", [])]
    bins = []
    start = 0.0
    while start < total:
        end = start + BIN
        count = lambda times: sum(1 for time in times if start <= time < end)
        bins.append({"start": start, "end": min(end, total), "narration": count(narration_events), "visual": count(visual),
                     "audio": count(audio), "text": any(line["start"] < end and line["end"] > start for line in key_lines)})
        start = end
    findings = []
    for item in views:                                                       # 1. holds
        if item["end"] - item["time"] > MAX_HOLD + 0.01 and not item["final"]:
            findings.append(_finding("hold", "major", item["time"], item["end"],
                                     f"{item['end'] - item['time']:.1f} s sem mudança visual (máximo {MAX_HOLD:.0f} s).",
                                     {"action": "add_cut", "at": round((item["time"] + item["end"]) / 2, 2)}))
    events = sorted(set(narration_events + visual + audio + [0.0, total]))   # 2. quiet stretches
    for earlier, later in zip(events, events[1:]):
        if later - earlier > QUIET:
            findings.append(_finding("quiet", "minor", earlier, later, f"{later - earlier:.0f} s sem nenhum evento de narração, imagem ou som.",
                                     {"action": "add_cut", "at": round((earlier + later) / 2, 2)}))
    problems = []                                                            # 3. the first 5 seconds
    if not views or views[0]["time"] > 0.5:
        problems.append("sem imagem em movimento")
    if not any(time < BIN for time in audio):
        problems.append("sem evento de som")
    if first_word is None or first_word >= BIN:
        problems.append("a narração não começa")
    if title_card:
        problems.append("cartão de título antes do cold open")
    if dip_at_start:
        problems.append("começa no preto (fade-in lento)")
    if problems:
        findings.append(_finding("first_5_seconds", "major", 0.0, BIN, "Os primeiros 5 s: " + ", ".join(problems) + ".",
                                 {"action": "add_sfx_accent", "at": 0.0} if "sem evento de som" in problems else None))
    from video import key_lines as lines_module                              # 4. key lines
    contrast = lines_module.contrast_ratio()
    for line in key_lines:
        visible = line["end"] - line["start"]
        if visible < MIN_LINE or contrast < MIN_CONTRAST:
            findings.append(_finding("key_line", "minor", line["start"], line["end"],
                                     f"Frase-chave \"{line['text'][:40]}\": {visible:.1f} s na tela, contraste {contrast}.", None))
    for window in intelligibility or []:                                     # 5. intelligibility
        findings.append(_finding("intelligibility", "major", window["start"], window["start"] + 1.0,
                                 f"A voz fica só {window['margin_db']:.1f} dB acima da trilha e dos efeitos (1–4 kHz; mínimo {SPEECH_MARGIN_DB:.0f}).",
                                 {"action": "lower_music", "scale": 0.8}))
    for cue in sfx_cues:                                                     # 6. SFX close to an accent
        for accent in [item for item in sfx_cues if item.get("kind") == "accent" and item is not cue]:
            if abs(cue["start"] - accent["start"]) < ACCENT_GAP and (cue.get("kind") != "accent" or cue["start"] < accent["start"]):
                findings.append(_finding("sfx_overlap", "minor", cue["start"], cue["start"] + ACCENT_GAP,
                                         f"Efeito \"{cue['tag']}\" a menos de 1 s do acento \"{accent['tag']}\".",
                                         {"action": "remove_sfx", "tag": cue["tag"], "start": cue["start"]}))
                break
    findings.sort(key=lambda item: item["start"])
    return {"bins": bins, "findings": findings, "duration": round(total, 2),
            "major": sum(1 for item in findings if item["severity"] == "major")}


# ── Intelligibility (5) ─────────────────────────────────────────────────────

def band_levels(samples: np.ndarray, rate: int, low: float = 1000.0, high: float = 4000.0) -> list[float]:
    """dB level of the 1-4 kHz band in every 1-second window."""
    window = rate
    levels = []
    for start in range(0, max(0, samples.size - window + 1), window):
        chunk = samples[start:start + window] * np.hanning(window)
        spectrum = np.abs(np.fft.rfft(chunk)) ** 2
        freqs = np.fft.rfftfreq(window, 1 / rate)
        energy = float(spectrum[(freqs >= low) & (freqs <= high)].sum()) / window
        levels.append(10 * math.log10(max(energy, 1e-12)))
    return levels


def intelligibility(voice: np.ndarray, bed: np.ndarray, rate: int, margin: float = SPEECH_MARGIN_DB) -> list[dict]:
    """Windows with speech where the voice is less than ``margin`` dB above the bed (1-4 kHz)."""
    voice_levels, bed_levels = band_levels(voice, rate), band_levels(bed, rate)
    loudest = max(voice_levels, default=-120.0)
    found = []
    for index, (speech, music) in enumerate(zip(voice_levels, bed_levels)):
        if speech < loudest - 30:                                            # a pause: nothing to understand
            continue
        if speech - music < margin:
            found.append({"start": float(index), "margin_db": round(speech - music, 1)})
    return found


def decode(path: str, rate: int = 16000) -> np.ndarray:
    from video.music import ffmpeg_exe
    result = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                            capture_output=True, check=False)
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


# ── Overrides and one-click fixes ────────────────────────────────────────────

def overrides(output_dir) -> dict:
    return load_json(Path(output_dir) / OVERRIDES) or {}


def apply_fix(output_dir, fix: dict) -> dict:
    """Record a one-click fix in cut_overrides.json; the next cut (or audio mix) honours it."""
    data = overrides(output_dir)
    action = fix.get("action")
    if action == "add_cut":
        data.setdefault("extra_cuts", []).append(float(fix["at"]))
    elif action == "remove_sfx":
        data.setdefault("removed_sfx", []).append({"tag": fix["tag"], "start": float(fix["start"])})
    elif action == "lower_music":
        data["music_scale"] = round(float(data.get("music_scale", 1.0)) * float(fix.get("scale", 0.8)), 3)
    elif action == "add_sfx_accent":
        data.setdefault("extra_accents", []).append(float(fix.get("at", 0.0)))
    else:
        raise ValueError(f"unknown fix: {action}")
    atomic_write_json(Path(output_dir) / OVERRIDES, data)
    return data


def override(output_dir, reason: str) -> dict:
    """The typed reason to render despite major findings (kept in cut_overrides.json)."""
    if len(str(reason or "").strip()) < 10:
        raise ValueError("escreva o motivo (pelo menos 10 caracteres)")
    data = overrides(output_dir)
    data["render_override"] = str(reason).strip()
    atomic_write_json(Path(output_dir) / OVERRIDES, data)
    return data


def blocked(report: dict, output_dir) -> list[str]:
    """The major findings that still block rendering (none once an override reason is recorded)."""
    if overrides(output_dir).get("render_override"):
        return []
    return [item["claim"] for item in report.get("findings", []) if item["check"] in MAJOR_CHECKS]


def evaluate(output_dir, sections: list[dict], durations: list[float], plan: dict, key_lines: list[dict]) -> dict:
    """The video attention map for a cut plan, from the project's saved files (no API call)."""
    from video import cut_plan, sfx
    output = Path(output_dir)
    word_list = cut_plan.words(output)
    narration = sorted(cut_plan.attention_events(output, word_list))
    cues = (load_json(output / sfx.FILE_NAME) or {}).get("cues", [])
    removed = {(item["tag"], round(item["start"], 2)) for item in overrides(output).get("removed_sfx", [])}
    placed, _ = sfx.assign_files([cue for cue in cues if (cue["tag"], round(cue["start"], 2)) not in removed])
    manifest = (load_json(output / "asset_manifest.json") or {}).get("music") or {}
    music = {"segments": [{"start": item.get("start") or 0.0} for item in manifest.get("music_plan") or []] or
             ([{"start": 0.0}] if manifest.get("track") else []), "drops": manifest.get("drops") or []}
    windows = None
    bed = output / "bed_stem.wav"
    voice = next((output / name for name in ("narration.mp3", "narration.wav") if (output / name).is_file()), None)
    if bed.is_file() and voice:
        windows = intelligibility(decode(str(voice)), decode(str(bed)), 16000)
    first_section = sections[0] if sections else {}
    edges = plan.get("boundaries") or {}
    return build(plan=plan, sections=sections, durations=durations, narration_events=narration, key_lines=key_lines,
                 sfx_cues=placed, music=music, first_word=float(word_list[0]["start"]) if word_list else None,
                 title_card=bool(str(first_section.get("on_screen_text", "")).strip()),
                 dip_at_start=bool((edges.get(first_section.get("id")) or {}).get("dip_in")), intelligibility=windows)
