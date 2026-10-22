"""Phase 3.0-D: music selection, ducking, loop crossfade and loudness."""
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from video import music

LOUDNORM_1 = ('[Parsed_loudnorm_0]\n{\n "input_i" : "-20.60", "input_tp" : "-5.70", '
              '"input_lra" : "6.10", "input_thresh" : "-31.00", "target_offset" : "0.30"\n}')
LOUDNORM_2 = ('{\n "input_i" : "-20.60", "input_tp" : "-5.70", "input_lra" : "6.10", '
              '"input_thresh" : "-31.00", "output_i" : "-14.02", "output_tp" : "-1.10", '
              '"target_offset" : "0.00"\n}')


class FakeFfmpeg:
    def __init__(self, mix_returncode=0):
        self.calls = []
        self.mix_returncode = mix_returncode

    def __call__(self, args):
        self.calls.append(args)
        if "-filter_complex" in args:
            return SimpleNamespace(returncode=self.mix_returncode, stderr="mix error")
        if "loudnorm" in " ".join(args) and "measured_I" not in " ".join(args):
            return SimpleNamespace(returncode=0, stderr=LOUDNORM_1)
        Path(args[-1]).write_bytes(b"final")
        return SimpleNamespace(returncode=0, stderr=LOUDNORM_2)


def make_library(root: Path, metadata=True) -> Path:
    library = root / "music library"
    library.mkdir()
    for name in ("Orison - Dan Bodan - Classical - Sombrio.mp3",
                 "Bright Day - Someone - Pop - Happy.mp3"):
        (library / name).write_bytes(b"mp3")
    if metadata:
        (library / "tracks.json").write_text(json.dumps([
            {"file": "Orison - Dan Bodan - Classical - Sombrio.mp3", "moods": ["dark", "tension"],
             "license": "YouTube Audio Library", "source_url": "https://example.test/orison",
             "attribution": "Orison by Dan Bodan"},
        ]), encoding="utf-8")
    return library


def quiet(function, *args, **kwargs):
    output = io.StringIO()
    with redirect_stdout(output):
        result = function(*args, **kwargs)
    return result, output.getvalue()


class SelectionTests(unittest.TestCase):
    def test_choose_by_mood_with_metadata_and_file_names(self):
        with tempfile.TemporaryDirectory() as temp:
            tracks = music.library_tracks(str(make_library(Path(temp), metadata=False)))
            self.assertEqual(sorted(track["moods"][0] for track in tracks), ["happy", "sombrio"])
            episode = {"psychological_theme": "Envy", "intended_emotional_experience": "Dread"}
            chosen = music.choose_track(tracks, episode)
        self.assertTrue(chosen["file"].startswith("Orison"))
        self.assertEqual(music.choose_track(tracks, episode), chosen)          # deterministic

    def test_metadata_overrides_file_name_moods(self):
        with tempfile.TemporaryDirectory() as temp:
            tracks = music.library_tracks(str(make_library(Path(temp))))
        orison = next(track for track in tracks if track["file"].startswith("Orison"))
        self.assertEqual(orison["moods"], ["dark", "tension"])
        self.assertEqual(orison["license"], "YouTube Audio Library")

    def test_tracks_wrapper_with_filename_mood_and_url_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            library = make_library(Path(temp), metadata=False)
            (library / "tracks.json").write_text(json.dumps({"tracks": [
                {"filename": "Bright Day - Someone - Pop - Happy.mp3", "mood": "Somber",
                 "license": "No Copyright, no attribution required",
                 "url": "youtube studio audio library"}]}), encoding="utf-8")
            tracks = {track["file"]: track for track in music.library_tracks(str(library))}
        bright = tracks["Bright Day - Someone - Pop - Happy.mp3"]
        self.assertEqual(bright["moods"], ["somber"])
        self.assertEqual(bright["license"], "No Copyright, no attribution required")
        self.assertEqual(bright["source_url"], "youtube studio audio library")
        self.assertIn("dark", music.mood_terms(*bright["moods"]))

    def test_missing_library_returns_no_tracks(self):
        self.assertEqual(music.library_tracks("does/not/exist"), [])
        self.assertIsNone(music.choose_track([], {}))


class FilterTests(unittest.TestCase):
    def test_short_track_loops_with_crossfade_and_ducks(self):
        graph, loops = music.music_filter(600.0, 180.0, 0.12)
        self.assertEqual(loops, 4)                      # 180 + 3 x (180 - 3) = 711 s >= 600 s
        self.assertIn("asplit=4", graph)
        self.assertEqual(graph.count("acrossfade"), 3)
        self.assertIn("atrim=0:600.000", graph)
        self.assertIn("volume=0.12", graph)
        self.assertIn("afade=t=in", graph)
        self.assertIn("afade=t=out", graph)
        self.assertIn("sidechaincompress", graph)
        self.assertIn(f"ratio={float(config.MUSIC_DUCKING_RATIO)}", graph)
        self.assertIn("amix=inputs=2:duration=first:normalize=0[mix]", graph)

    def test_long_track_is_not_looped(self):
        graph, loops = music.music_filter(300.0, 400.0, 0.1)
        self.assertEqual(loops, 1)
        self.assertNotIn("acrossfade", graph)
        self.assertTrue(graph.startswith("[1:a]atrim"))


class PrepareFinalAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.narration = self.root / "narration.mp3"
        self.narration.write_bytes(b"narration")
        self.output = self.root / "narration_mixed.mp3"

    def tearDown(self):
        self.temp.cleanup()

    def run_prepare(self, fake, library, enabled=True):
        with patch.object(music, "_run", fake), \
                patch.object(music, "media_duration", side_effect=lambda path: 600.0
                             if "narration" in str(path) else 180.0):
            return quiet(music.prepare_final_audio, str(self.narration), str(self.output),
                         episode={"psychological_theme": "envy"}, music_enabled=enabled,
                         library_dir=str(library), output_dir=str(self.root))

    def manifest(self):
        return json.loads((self.root / "asset_manifest.json").read_text(encoding="utf-8"))

    def test_mixes_records_track_and_normalises(self):
        fake = FakeFfmpeg()
        _, output = self.run_prepare(fake, make_library(self.root))
        self.assertTrue(any("-filter_complex" in call for call in fake.calls))
        loudnorm = [call for call in fake.calls if "loudnorm" in " ".join(call)]
        self.assertEqual(len(loudnorm), 2)
        self.assertIn("I=-14.0", " ".join(loudnorm[0]))
        self.assertIn("measured_I=-20.60", " ".join(loudnorm[1]))
        self.assertIn("TP=-1.0", " ".join(loudnorm[1]))
        record = self.manifest()["music"]
        self.assertTrue(record["track"].startswith("Orison"))
        self.assertEqual(record["attribution"], "Orison by Dan Bodan")
        self.assertEqual(record["loudness"]["output_integrated_lufs"], -14.02)
        self.assertIn("-20.6 → -14.0 LUFS", output)
        self.assertEqual(self.output.read_bytes(), b"final")

    def test_empty_library_says_so_and_still_normalises(self):
        empty = self.root / "empty"
        empty.mkdir()
        fake = FakeFfmpeg()
        _, output = self.run_prepare(fake, empty)
        self.assertIn(music.NO_MUSIC_MESSAGE, output)
        self.assertFalse(any("-filter_complex" in call for call in fake.calls))
        self.assertEqual(len([c for c in fake.calls if "loudnorm" in " ".join(c)]), 2)
        self.assertIsNone(self.manifest()["music"]["track"])

    def test_disabled_music_skips_mix_without_warning(self):
        _, output = self.run_prepare(FakeFfmpeg(), make_library(self.root), enabled=False)
        self.assertNotIn(music.NO_MUSIC_MESSAGE, output)
        self.assertIsNone(self.manifest()["music"]["track"])

    def test_failure_falls_back_to_narration_copy(self):
        _, output = self.run_prepare(FakeFfmpeg(mix_returncode=1), make_library(self.root))
        self.assertIn("using the narration unchanged", output)
        self.assertEqual(self.output.read_bytes(), b"narration")
        self.assertIn("music mix failed", self.manifest()["music"]["error"])

    def test_attribution_reaches_both_descriptions(self):
        self.run_prepare(FakeFfmpeg(), make_library(self.root))
        from uploader.youtube import build_upload_description
        description = build_upload_description({"description": "Story.", "tags": ["a"]},
                                               output_dir=str(self.root))
        self.assertIn("Music: Orison by Dan Bodan", description)
        import gui
        gui_description = gui._build_description({"description": "Story.", "sections": []},
                                                 {}, output_dir=str(self.root))
        self.assertIn("Music: Orison by Dan Bodan", gui_description)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RealFfmpegTest(unittest.TestCase):
    def test_loudness_normalisation_reaches_target(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp, "quiet.mp3")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "anoisesrc=d=12:c=pink:a=0.05", "-ar", "44100", str(source)], check=True)
            result = music.normalize_loudness(str(source), str(Path(temp, "out.mp3")))
            measured = music.measure_loudness(str(Path(temp, "out.mp3")))
        self.assertLess(result["input_integrated_lufs"], -20)
        self.assertAlmostEqual(measured["integrated_lufs"], -14.0, delta=1.0)
        self.assertLessEqual(measured["true_peak_dbtp"], -0.5)


if __name__ == "__main__":
    unittest.main()
