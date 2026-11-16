"""The episode's sound bed (Phase 4.2-D/E): music segments and SFX cues mixed under the voice with ffmpeg.

``graph`` builds one filter_complex: every music segment (the music plan, E) and every SFX cue (D) is trimmed,
faded, delayed to its time and given its level; the bed is shaped by the plan's automation (the sound drop before
the reversal line, the swell under the final line); then the whole bed is ducked by the narration with the same
sidechain as the music (3.9.4-G) and mixed with the voice. ``levels`` turns "N dB under the voice" into gains from the
measured RMS of each file.
"""
from __future__ import annotations

import math
import subprocess

import numpy as np

import config

PAUSE_KEY_LEVEL = 0.085
BED_THRESHOLD = 0.02           # about -34 dBFS: the quiet bed passes, a swell is pulled back
BED_RATIO = 6


def rms_db(path: str, seconds: float = 30.0) -> float:
    """RMS level (dBFS) of the first ``seconds`` of an audio file (ffmpeg decode, mono 16 kHz)."""
    from video.music import ffmpeg_exe
    result = subprocess.run([ffmpeg_exe(), "-v", "error", "-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", "16000",
                             "-f", "s16le", "-"], capture_output=True, check=False)
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return -90.0
    voiced = samples[np.abs(samples) > 1e-4]
    level = float(np.sqrt(np.mean((voiced if voiced.size else samples) ** 2)))
    return 20 * math.log10(max(level, 1e-6))


def gain_for(voice_db: float, file_db: float, under_db: float) -> float:
    """Linear gain that puts a file ``under_db`` below the voice."""
    return round(10 ** ((voice_db - under_db - file_db) / 20.0), 5)


def automation(drops=(), swells=()) -> str:
    """A volume expression: near-silence over each drop (start, end, fades), a +6 dB bump at each swell."""
    terms = []
    for start, end in drops:
        fade = 0.6
        terms.append(f"(1-0.92*min(1,max(0,min((t-{start - fade:.3f})/{fade},({end + fade:.3f}-t)/{fade}))))")
    expression = "*".join(terms) or "1"
    if swells:
        expression = f"({expression})*(1+" + "+".join(f"max(0,1-abs(t-{centre:.3f})/1.5)" for centre in swells) + ")"
    return expression


def graph(duration: float, music: list[dict], cues: list[dict], *, drops=(), swells=(), ducking: bool = True) -> tuple[str, list[dict]]:
    """(filter_complex, inputs). Input 0 is the narration; the result is labelled [mix].

    ``music`` items: {"path", "start", "end", "offset", "fade_in", "fade_out", "volume"};
    ``cues`` items: {"path", "start", "end", "gain", "kind"} (beds loop)."""
    inputs = [{"path": None, "loop": False}]
    parts, labels = [], []
    for index, segment in enumerate(music):
        inputs.append({"path": segment["path"], "loop": True})
        source = len(inputs) - 1
        length = max(0.1, segment["end"] - segment["start"])
        offset = float(segment.get("offset", 0.0))
        parts.append(f"[{source}:a]atrim={offset:.3f}:{offset + length:.3f},asetpts=PTS-STARTPTS,volume={segment['volume']:.4f},"
                     f"afade=t=in:st=0:d={segment.get('fade_in', 2.0):.2f},"
                     f"afade=t=out:st={max(0.0, length - segment.get('fade_out', 2.0)):.2f}:d={segment.get('fade_out', 2.0):.2f},"
                     f"adelay={int(segment['start'] * 1000)}:all=1[m{index}]")
        labels.append(f"[m{index}]")
    for index, cue in enumerate(cues):
        inputs.append({"path": cue["path"], "loop": cue.get("kind") == "bed"})
        source = len(inputs) - 1
        length = max(0.1, cue["end"] - cue["start"])
        fade = 1.5 if cue.get("kind") == "bed" else 0.05
        parts.append(f"[{source}:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,volume={cue['gain']:.5f},"
                     f"afade=t=in:st=0:d={min(fade, length / 3):.2f},afade=t=out:st={max(0.0, length - min(1.5, length / 3)):.2f}:"
                     f"d={min(1.5, length / 3):.2f},adelay={int(cue['start'] * 1000)}:all=1[s{index}]")
        labels.append(f"[s{index}]")
    if not labels:
        parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{duration:.3f}[silent]")
        labels.append("[silent]")
    # The bed's own swells (a loud passage in a track) are tamed before the ducking: a voice-band check (4.2-F) found
    # music 18 dB above its median under speech in "The Man Who Bought the Sunrise".
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=0:{duration:.3f},"
                 f"acompressor=threshold={BED_THRESHOLD}:ratio={BED_RATIO}:attack=30:release=400[rawbed]")
    expression = automation(drops, swells)
    parts.append(f"[rawbed]volume=eval=frame:volume='{expression}'[bed]" if expression != "1" else "[rawbed]anull[bed]")
    if ducking:
        ratio = float(getattr(config, "MUSIC_DUCKING_RATIO", 8))
        threshold = float(getattr(config, "MUSIC_DUCKING_THRESHOLD", 0.03))
        parts.append("[0:a]asplit=2[narr][speech]")
        parts.append(f"sine=frequency=110:sample_rate=48000:duration={duration:.3f},volume={PAUSE_KEY_LEVEL}[tone]")
        parts.append("[speech][tone]amix=inputs=2:duration=first:normalize=0[key]")
        parts.append(f"[bed][key]sidechaincompress=threshold={threshold}:ratio={ratio}:attack=20:release=900[ducked]")
        parts.append("[narr][ducked]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        parts.append("[0:a][bed]amix=inputs=2:duration=first:normalize=0[mix]")
    return ";".join(parts), inputs


def command(narration: str, inputs: list[dict], filter_graph: str, output: str, *, label: str = "[mix]") -> list[str]:
    args = ["-y", "-i", narration]
    for item in inputs[1:]:
        args += (["-stream_loop", "-1"] if item["loop"] else []) + ["-i", item["path"]]
    return args + ["-filter_complex", filter_graph, "-map", label, output]


def render_bed(narration: str, music: list[dict], cues: list[dict], output: str, *, drops=(), swells=()) -> str:
    """The ducked bed alone (no voice) as a wav, for the intelligibility check (F)."""
    from video.music import _run, media_duration
    duration = media_duration(narration)
    filter_graph, inputs = graph(duration, music, cues, drops=drops, swells=swells)
    filter_graph = filter_graph.replace("[narr][ducked]amix=inputs=2:duration=first:normalize=0[mix]", "[narr]anullsink;[ducked]anull[mix]")
    result = _run(command(narration, inputs, filter_graph, output))
    if result.returncode != 0:
        raise RuntimeError(f"bed render failed: {result.stderr[-300:]}")
    return output
