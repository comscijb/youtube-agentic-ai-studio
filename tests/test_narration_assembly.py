"""Phase 3.9.4-G: narration in PCM — clean joins, exact pauses, speed, re-timing without regenerating."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from agents import voice_calibration
from agents.narrative import atomic_write_json, load_json
from audio import assembly, tts
from audio.qa import joins
from audio.tts import local
from scripts import voice_audition
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import retime, shorts

RATE = assembly.RATE
F = Path(__file__).parent / "fixtures" / "joins"
PAUSES = {"sentence": 0.35, "paragraph": 0.7, "scene": 1.5, "major_scene": 2.0, "final": 2.5}


def voiced(seconds: float, amplitude: float = 0.2) -> np.ndarray:
    return (amplitude * np.sin(np.arange(int(seconds * RATE)) * 0.1)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def word(text, start, end, sentence_end=False):
    return {"text": text, "start": start, "end": end, "sentence_end": sentence_end}


def two_sentence_scene(scene_id=1, section_type="development", text="One two. Three four."):
    """0.1 silence, 'One two.' 0.1-1.1, 0.2 silence, 'Three four.' 1.3-2.3, 0.3 silence."""
    samples = np.concatenate([silence(.1), voiced(1.0), silence(.2), voiced(1.0), silence(.3)])
    words = [word("One", .1, .6), word("two.", .6, 1.1, True), word("Three", 1.3, 1.8), word("four.", 1.8, 2.3, True)]
    return {"id": scene_id, "samples": samples, "words": words, "text": text, "section_type": section_type}


def rms(samples):
    return float(np.sqrt(np.mean(samples ** 2)))


class TrimAndFadeTests(unittest.TestCase):
    def test_trim_keeps_40_ms_around_the_threshold(self):
        samples = np.concatenate([silence(.5), voiced(1.0), silence(.5)])
        first, last = assembly.trim_bounds(samples)
        self.assertAlmostEqual(first / RATE, .5 - .04, delta=.011)
        self.assertAlmostEqual(last / RATE, 1.5 + .04, delta=.011)

    def test_threshold_ignores_a_tail_below_minus_50_db(self):
        quiet_tail = voiced(.5, amplitude=10 ** (-60 / 20))
        samples = np.concatenate([voiced(1.0), quiet_tail])
        _, last = assembly.trim_bounds(samples)
        self.assertLess(last / RATE, 1.1)
        audible_tail = voiced(.5, amplitude=10 ** (-40 / 20))
        _, last = assembly.trim_bounds(np.concatenate([voiced(1.0), audible_tail]))
        self.assertGreater(last / RATE, 1.45)

    def test_all_silence_is_empty(self):
        self.assertEqual(assembly.trim_bounds(silence(1)), (0, 0))

    def test_equal_power_fades_at_both_ends(self):
        faded = assembly.with_fades(np.ones(RATE, dtype=np.float32))
        count = int(assembly.FADE_SECONDS * RATE)
        self.assertLess(faded[0], .01)
        self.assertLess(faded[-1], .01)
        self.assertAlmostEqual(float(faded[count // 2]), np.sin(np.pi / 4), places=1)
        self.assertEqual(float(faded[RATE // 2]), 1.0)

    def test_join_has_no_click_at_the_seam(self):
        joined = assembly.join([np.ones(RATE // 2, dtype=np.float32), np.ones(RATE // 2, dtype=np.float32)])
        self.assertEqual(len(joined), RATE)
        self.assertLess(float(joined[RATE // 2]), .05)


class AssembleTests(unittest.TestCase):
    def built(self, scenes, **kwargs):
        return assembly.assemble(scenes, settings=PAUSES, **kwargs)

    def test_pause_lengths_are_exact(self):
        built = self.built([two_sentence_scene(1), two_sentence_scene(2)])
        kinds = [(item["kind"], item["seconds"]) for item in built["pauses"]]
        self.assertEqual(kinds, [("sentence", .35), ("scene", 1.5), ("sentence", .35)])
        samples = built["samples"]
        for pause in built["pauses"]:
            start = int(pause["at"] * RATE)
            self.assertTrue(np.all(samples[start:start + int(pause["seconds"] * RATE)] == 0))

    def test_major_scenes_breathe_longer(self):
        built = self.built([two_sentence_scene(1), two_sentence_scene(2, "climax")])
        self.assertEqual([item["seconds"] for item in built["pauses"] if item["kind"] == "scene"], [2.0])

    def test_paragraph_and_final_hold(self):
        scene = two_sentence_scene(1, text="One two.\n\nThree four.")
        self.assertEqual(self.built([two_sentence_scene(9), scene])["pauses"][-1]["kind"], "final")
        self.assertEqual(self.built([two_sentence_scene(9), scene])["pauses"][-1]["seconds"], 2.5)
        self.assertEqual(self.built([dict(scene), two_sentence_scene(9)])["pauses"][0]["kind"], "paragraph")

    def test_word_timings_shift_exactly(self):
        built = self.built([two_sentence_scene(1), two_sentence_scene(2)])
        words = built["words"]
        keep = assembly.KEEP_SECONDS
        self.assertAlmostEqual(words[0]["start"], keep, delta=.012)     # the lead silence is gone
        self.assertAlmostEqual(words[1]["end"] - words[0]["start"], 1.0, delta=.002)
        first_piece_end = built["pauses"][0]["at"]
        self.assertAlmostEqual(words[2]["start"], first_piece_end + .35 + keep, delta=.012)
        total = len(built["samples"]) / RATE
        self.assertAlmostEqual(sum(built["durations"]), total, places=3)
        self.assertEqual({item["scene_id"] for item in words}, {1, 2})
        self.assertLessEqual(words[-1]["end"], total)

    def test_scene_boundary_is_the_middle_of_the_pause(self):
        built = self.built([two_sentence_scene(1), two_sentence_scene(2)])
        pause = next(item for item in built["pauses"] if item["kind"] == "scene")
        self.assertAlmostEqual(built["durations"][0], pause["at"] + .75, places=3)

    def test_no_burst_where_files_ended_in_digital_silence(self):
        scene = two_sentence_scene(1)
        scene["samples"] = np.concatenate([scene["samples"], silence(.3)])
        built = self.built([scene, two_sentence_scene(2)])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "n.wav")
            assembly.encode(built["samples"], path)
            self.assertEqual(joins.bursts(assembly.decode(path, joins.RATE)), [])

    def test_estimate_follows_speed_and_pauses(self):
        sections = [{"id": 1, "narration": "One two. Three four."}, {"id": 2, "narration": "One two. Three four.", "section_type": "climax"}]
        words = [dict(item, scene_id=1) for item in two_sentence_scene()["words"]] + \
                [dict(item, scene_id=2) for item in two_sentence_scene()["words"]]
        base = assembly.estimate(sections, words, 1.0, PAUSES)
        self.assertAlmostEqual(base["pause_seconds"], .35 + 2.0 + .35)
        slower = assembly.estimate(sections, words, 2.0, PAUSES)
        self.assertAlmostEqual(slower["speech_seconds"], base["speech_seconds"] * 2, places=0)
        self.assertEqual(slower["pause_seconds"], base["pause_seconds"])


class JoinCheckerTests(unittest.TestCase):
    def test_the_real_excerpts_are_flagged(self):
        for name, at in (("burst_63s.flac", 2.62), ("burst_278s.flac", 2.43)):
            with self.subTest(name=name):
                found = joins.bursts(assembly.decode(F / name, joins.RATE))
                self.assertTrue(any(abs(item["at"] - at) < .06 for item in found), found)

    def test_synthetic_burst_and_clean_file(self):
        rate = joins.RATE
        burst = (0.03 * np.sin(np.arange(int(.08 * rate)) * .3)).astype(np.float32)
        speech = (0.2 * np.sin(np.arange(rate) * .1)).astype(np.float32)
        ramp = np.linspace(1, 0, 3200, dtype=np.float32)
        decayed = np.concatenate([speech, speech[:3200] * ramp])
        quiet = np.zeros(int(.2 * rate), dtype=np.float32)
        found = joins.bursts(np.concatenate([decayed, quiet, burst, quiet, np.flip(decayed)]), rate)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["at"], (len(decayed) + len(quiet)) / rate, delta=.02)
        self.assertEqual(joins.bursts(np.concatenate([decayed, quiet, np.flip(decayed)]), rate), [])

    def test_a_short_word_alone_is_speech(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "n.wav")
            rate = RATE
            burst = (0.1 * np.sin(np.arange(int(.08 * rate)) * .3)).astype(np.float32)
            assembly.encode(np.concatenate([silence(.5), burst, silence(.5)]), path)
            self.assertEqual(len(joins.check(path)["bursts"]), 1)
            spoken = joins.check(path, [word("No.", .5, .58) | {"scene_id": 1}], [{"id": 1}])
            elsewhere = joins.check(path, [word("Yes.", .9, 1.0) | {"scene_id": 1}], [{"id": 1}])
        self.assertEqual(spoken["bursts"], [])
        self.assertEqual(len(elsewhere["bursts"]), 1)

    def test_a_short_scene_gap_is_reported(self):
        sections = [{"id": 1}, {"id": 2, "section_type": "climax"}]
        words = [word("a", 0, 1) | {"scene_id": 1}, word("b", 1.5, 2) | {"scene_id": 2}]
        gaps = joins.short_gaps(words, sections, PAUSES)
        self.assertEqual(gaps[0]["gap"], .5)
        self.assertEqual(gaps[0]["wanted"], 2.0)
        fine = [word("a", 0, 1) | {"scene_id": 1}, word("b", 3, 4) | {"scene_id": 2}]
        self.assertEqual(joins.short_gaps(fine, sections, PAUSES), [])
        self.assertTrue(joins.describe({"short_gaps": gaps, "bursts": []})[0].startswith("junção curta"))


class SpeedTests(unittest.TestCase):
    def test_speed_goes_to_kokoro_and_vocie_rate_does_not(self):
        calls = []

        class Worker:
            def synthesize(self, text, voice, speed, path):
                calls.append(speed)
                local.write_wav(path, [0.0] * 10, 24000)

        with tempfile.TemporaryDirectory() as temp, patch.object(config, "TTS_LOCAL_SPEED", .88), \
                patch.object(config, "TTS_LOCAL_ENGINE", "kokoro"), \
                patch.object(local, "_kokoro_in_process", return_value=False), \
                patch.object(local, "_kokoro_worker", return_value=Worker()), \
                patch("audio.tts.align.align", return_value=([], {"ok": True})):
            local.LocalBackend().synthesize("Hello.", str(Path(temp, "a.wav")), voice="bm_lewis", rate="-5%", pitch="+0Hz")
        self.assertEqual(calls, [.88])

    def test_no_atempo_anywhere_in_the_narration_path(self):
        for path in ("audio/assembly.py", "video/narrator.py", "audio/heard_thoughts.py", "audio/tts/local.py"):
            self.assertNotIn("atempo=", Path(path).read_text(encoding="utf-8"))

    def test_calibration_key_and_identity_carry_the_speed(self):
        with patch.object(config, "TTS_BACKEND", "local"), patch.object(config, "TTS_VOICE", "bm_lewis"), \
                patch.object(config, "TTS_LOCAL_SPEED", .88):
            self.assertEqual(voice_calibration.voice_key(), "local:bm_lewis|speed0.88")
            self.assertEqual(tts.identity()["speed"], .88)
            first = local.LocalBackend().cache_tag()
            with patch.object(config, "TTS_LOCAL_SPEED", .92):
                self.assertNotEqual(local.LocalBackend().cache_tag(), first)
                self.assertEqual(voice_calibration.voice_key(), "local:bm_lewis|speed0.92")

    def test_audition_accepts_a_speed(self):
        seen = {}

        def fake(text, backend, voice, out, sample):
            seen.update(voice=voice, sample=sample, speed=config.TTS_LOCAL_SPEED)
            return {"backend": backend, "voice": voice}
        with patch.object(voice_audition, "_audition", side_effect=fake):
            row = voice_audition.audition("x", "local", "bm_lewis@0.85", Path("."))
        self.assertEqual(seen, {"voice": "bm_lewis", "sample": "bm_lewis@0.85", "speed": .85})
        self.assertEqual(row["voice"], "bm_lewis@0.85")
        self.assertEqual(row["speed"], .85)

    def test_channel_speed_route(self):
        import gui
        with patch.object(gui, "_write_env_settings") as saved, patch.object(config, "TTS_LOCAL_SPEED", 1.0), \
                patch.object(config, "TTS_BACKEND", "edge"), patch.object(config, "TTS_VOICE", ""):
            client = gui.app.test_client()
            good = client.post("/api/voices/channel", json={"backend": "local", "voice": "bm_lewis", "speed": .88}).get_json()
            self.assertTrue(good["ok"])
            self.assertEqual(config.TTS_LOCAL_SPEED, .88)
            self.assertEqual(saved.call_args.args[0]["TTS_LOCAL_SPEED"], "0.88")
            self.assertEqual(client.post("/api/voices/channel", json={"backend": "local", "voice": "x", "speed": 3}).status_code, 400)
            picked = client.post("/api/voices/channel", json={"backend": "local", "voice": "bm_lewis@0.92"}).get_json()
            self.assertEqual(picked["channel"]["voice"], "bm_lewis")
            self.assertEqual(config.TTS_LOCAL_SPEED, .92)


class PlanningTests(unittest.TestCase):
    def test_planner_uses_the_channel_voice_calibration(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "VOICE_CALIBRATION_FILE", str(Path(temp, "c.json"))), \
                patch.object(config, "TTS_BACKEND", "local"), patch.object(config, "TTS_VOICE", "bm_lewis"), \
                patch.object(config, "TTS_LOCAL_SPEED", .88):
            voice_calibration.record(1409, 540)
            self.assertAlmostEqual(voice_calibration.planning_wpm(), 156.6)
            with patch.object(config, "TTS_LOCAL_SPEED", .92), patch.object(voice_calibration, "audition_wpm", return_value=None):
                self.assertEqual(voice_calibration.planning_wpm(), config.NARRATION_WPM)

    def test_new_voice_is_measured_from_its_audition(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "VOICE_CALIBRATION_FILE", str(Path(temp, "c.json"))), \
                patch.object(config, "TTS_BACKEND", "local"), patch.object(config, "TTS_VOICE", "bm_lewis"), \
                patch.object(config, "TTS_LOCAL_SPEED", .88):
            results = Path(temp, "results.json")
            results.write_text(json.dumps({"text": " ".join(["w"] * 80), "rows": [
                {"backend": "local", "voice": "bm_lewis@0.88", "audio_seconds": 30.0}]}), encoding="utf-8")
            self.assertEqual(voice_calibration.audition_wpm(results), 160.0)
            with patch.object(config, "MEASURE_NEW_VOICE", True, create=True), \
                    patch.object(voice_calibration, "audition_wpm", side_effect=[None, 160.0]), \
                    patch.object(voice_audition, "run") as run, patch.object(voice_calibration, "calibrated_wpm", return_value=None):
                self.assertEqual(voice_calibration.ensure_measured(progress=lambda _: None), 160.0)
            self.assertEqual(run.call_args.args[0], [("local", "bm_lewis@0.88")])


WORDS_OLD = [dict(word(text, index * .5, index * .5 + .4, text.endswith(".")), scene_id=1 if index < 4 else 2)
             for index, text in enumerate("One two. Three four. Five six. Seven eight.".split())]
WORDS_NEW = [dict(item, start=item["start"] * 1.2 + (2.0 if item["scene_id"] == 2 else 0),
                  end=item["end"] * 1.2 + (2.0 if item["scene_id"] == 2 else 0)) for item in WORDS_OLD]


class RetimeTests(unittest.TestCase):
    def shot_list(self):
        return {"source_hash": "old", "shots": [
            {"shot_id": "s01_01", "section_id": 1, "narration_excerpt": "One two.", "start": 0, "end": 1, "image_path": "images/a.jpg", "qa": {"reviewed": True}},
            {"shot_id": "s01_02", "section_id": 1, "narration_excerpt": "Three four.", "start": 1, "end": 2, "image_path": "images/b.jpg"},
            {"shot_id": "s02_01", "section_id": 2, "narration_excerpt": "Five six. Seven eight.", "start": 2, "end": 4, "image_path": "images/c.jpg"}]}

    def test_only_times_change(self):
        shot_list = self.shot_list()
        before = json.loads(json.dumps(shot_list))
        retime.retime_shots(shot_list, WORDS_NEW, [3.5, 4.0], [1, 2])
        shots = shot_list["shots"]
        self.assertEqual((shots[0]["start"], shots[0]["end"]), (0.0, round(WORDS_NEW[2]["start"], 3)))
        self.assertEqual((shots[1]["start"], shots[1]["end"]), (round(WORDS_NEW[2]["start"], 3), 3.5))
        self.assertEqual((shots[2]["start"], shots[2]["end"]), (3.5, 7.5))
        self.assertEqual((shots[0]["start_word"], shots[0]["end_word"], shots[2]["start_word"], shots[2]["end_word"]), (0, 1, 4, 7))
        for old, new in zip(before["shots"], shots):
            for key in ("shot_id", "image_path", "narration_excerpt", "qa"):
                self.assertEqual(old.get(key), new.get(key))

    def test_a_changed_script_raises_and_changes_nothing(self):
        shot_list = self.shot_list()
        shot_list["shots"][2]["narration_excerpt"] = "Completely different words here now."
        before = json.loads(json.dumps(shot_list))
        with self.assertRaises(retime.RetimeError):
            retime.retime_shots(shot_list, WORDS_NEW, [3.5, 4.0], [1, 2])
        self.assertEqual(shot_list, before)

    def test_shorts_keep_the_same_sentences(self):
        plan = {"segments": [{"id": "short_01", "start": 1.0, "end": 2.9}]}
        retime.resnap_shorts(plan, WORDS_OLD, WORDS_NEW)
        self.assertEqual(plan["segments"][0]["start"], round(WORDS_NEW[2]["start"], 3))
        self.assertEqual(plan["segments"][0]["end"], round(WORDS_NEW[5]["end"], 3))
        with self.assertRaises(retime.RetimeError):
            retime.resnap_shorts(plan, WORDS_OLD[:-1], WORDS_NEW)

    def test_project_stale_flags_planos_not_stale_video_and_shorts_stale(self):
        from agents import shot_list as shots_module
        from agents.visual_bible import FILE_NAME as VB_FILE
        from studio import projects
        with tempfile.TemporaryDirectory() as temp, patch.object(shots_module, "expected_source_hash", return_value="fresh"):
            folder = Path(temp)
            atomic_write_json(folder / "script.json", {"sections": [], "title": "t"})
            atomic_write_json(folder / VB_FILE, {"locations": [], "characters": []})
            atomic_write_json(folder / shots_module.FILE_NAME, self.shot_list())
            atomic_write_json(folder / "scene_timeline.json", {"scenes": [{"id": 1, "duration": 3.5}, {"id": 2, "duration": 4.0}]})
            atomic_write_json(folder / shorts.WORDS_FILE, {"words": WORDS_NEW})
            atomic_write_json(folder / retime.PREVIOUS_WORDS, {"words": WORDS_OLD})
            atomic_write_json(folder / shorts.FOLDER / shorts.PLAN_FILE, {"segments": [{"id": "short_01", "start": 1.0, "end": 2.9}]})
            report = retime.retime_project(folder, progress=lambda _: None)
            self.assertEqual(report, {"shots": 3, "shorts": 1})
            saved = load_json(folder / shots_module.FILE_NAME)
            self.assertEqual(saved["source_hash"], "fresh")
            self.assertEqual(saved["shots"][0]["image_path"], "images/a.jpg")
            self.assertTrue(load_json(folder / shorts.FOLDER / shorts.PLAN_FILE)["retimed"])
            atomic_write_json(folder / projects.PROJECT_FILE, {"slug": "x"})
            (folder / shorts.FOLDER / "short_01.mp4").write_bytes(b"old")
            (folder / "final_video.mp4").write_bytes(b"old")
            import os
            import time
            old = time.time() - 100
            os.utime(folder / shorts.FOLDER / "short_01.mp4", (old, old))
            os.utime(folder / "final_video.mp4", (old, old))
            statuses = {item["stage"]: item["status"] for item in projects.stage_status(folder)}
        self.assertEqual(statuses["shorts"], "stale")
        self.assertEqual(statuses["video"], "stale")
        self.assertNotEqual(statuses["shots"], "stale")


class MusicTests(unittest.TestCase):
    def test_the_key_keeps_the_music_partly_ducked_in_a_pause(self):
        from video import music
        graph, _ = music.music_filter(20.0, 30.0, 0.5)
        self.assertIn("sine=frequency=110", graph)
        self.assertIn("[speech][tone]amix", graph)
        self.assertIn("[bed][key]sidechaincompress", graph)
        self.assertNotIn("[narr][key]", graph.split("[bed][key]")[0].split("asplit=2")[0])


if __name__ == "__main__":
    unittest.main()
