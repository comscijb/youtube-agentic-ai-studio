"""Phase 3.9.5: one sentence per Kokoro call, our own trim and joins, the stutter check and its re-synthesis.
No live calls: the Kokoro worker and faster-whisper are mocked."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from agents.narrative import atomic_write_json
from audio import assembly
from audio.qa import joins, stutter
from audio.tts import kokoro_worker, local, sentences
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import retime

FIXTURES = json.loads(Path("tests/fixtures/long_sentences.json").read_text(encoding="utf-8"))
RATE = assembly.RATE


def count(text: str) -> int:
    """Close to the real tokenizer: about one phoneme per character."""
    return len(text)


def tone(seconds: float, amplitude: float = 0.2) -> np.ndarray:
    return (amplitude * np.sin(2 * np.pi * 180 * np.arange(int(seconds * RATE)) / RATE)).astype(np.float32)


def spoken(seconds: float = 0.5) -> np.ndarray:
    """A tone that rises and decays like a word, with silence around it."""
    body = tone(seconds) * np.hanning(int(seconds * RATE)).astype(np.float32)
    pad = np.zeros(int(.05 * RATE), dtype=np.float32)
    return np.concatenate([pad, body, pad])


def heard_for(text: str, seconds: float = 0.5, begin: float = 0.0) -> list[dict]:
    words = text.split()
    step = seconds * 1000 / max(len(words), 1)
    return [{"text": word, "start_ms": begin + step * index, "end_ms": begin + step * (index + 1)} for index, word in enumerate(words)]


class SplitTests(unittest.TestCase):
    def test_sentences_paragraphs_and_the_last_one(self):
        pieces = sentences.split_text("One two. Three four!\n\nFive six? Seven.")
        self.assertEqual([piece["text"] for piece in pieces], ["One two.", "Three four!", "Five six?", "Seven."])
        self.assertEqual([piece["after"] for piece in pieces], ["sentence", "paragraph", "sentence", "scene"])

    def test_a_quotation_stays_whole_and_an_apostrophe_is_not_a_quote(self):
        text = "She said: 'I can't. I won't. Don't ask.' Then she left. It wasn't over."
        self.assertEqual([piece["text"] for piece in sentences.split_text(text)],
                         ["She said: 'I can't. I won't. Don't ask.'", "Then she left.", "It wasn't over."])
        double = sentences.split_text('"Stop. Now," he said. "Please."')
        self.assertEqual([piece["text"] for piece in double], ['"Stop. Now," he said.', '"Please."'])

    def test_an_abbreviation_does_not_end_a_sentence(self):
        self.assertEqual(len(sentences.split_text("Mr. Wren waits. He waits.")), 2)

    def test_clause_order_semicolon_conjunction_comma(self):
        self.assertEqual(sentences.best_boundary("Alpha beta, gamma delta epsilon; zeta eta theta, iota kappa lambda."), 5)
        self.assertEqual(sentences.best_boundary("Alpha beta, gamma delta epsilon zeta, and eta theta iota, kappa lambda."), 6)
        self.assertEqual(sentences.best_boundary("Alpha beta gamma, delta epsilon zeta, eta theta iota kappa lambda mu."), 6)

    def test_no_split_inside_quotes_while_anything_else_exists(self):
        text = "He said it, 'one, two, three, four, five, six, seven, eight, nine, ten' and walked away from all of it."
        cut = sentences.best_boundary(text)
        self.assertEqual(" ".join(text.split()[:cut]), "He said it,")

    def test_the_project_long_sentences_respect_the_limit(self):
        for text in FIXTURES:
            pieces = sentences.plan(text, count, 90)
            self.assertTrue(all(count(piece["text"]) <= 90 for piece in pieces), text)
            self.assertEqual(" ".join(piece["text"] for piece in pieces), " ".join(text.split()))      # nothing lost
            self.assertIn("clause", [piece["after"] for piece in pieces[:-1]])
            self.assertEqual(pieces[-1]["after"], "scene")

    def test_a_short_sentence_is_left_alone(self):
        self.assertEqual(sentences.plan("Short one.", count), [{"text": "Short one.", "after": "scene"}])

    def test_a_quotation_over_the_limit_still_cannot_reach_the_worker_whole(self):
        text = "'" + " ".join(["This goes on and on."] * 20) + "'"
        self.assertTrue(all(count(piece["text"]) <= 100 for piece in sentences.plan(text, count, 100)))


class FakeTokenizer:
    def __init__(self, size):
        self.size = size

    def phonemize(self, text, lang):
        return "x" * self.size


class FakeModel:
    def __init__(self, size=100):
        self.tokenizer, self.calls = FakeTokenizer(size), []

    def create(self, text, **kwargs):
        self.calls.append(kwargs)
        return np.zeros(100, dtype=np.float32), 24000


class WorkerTests(unittest.TestCase):
    def test_trim_is_off_and_the_speed_is_explicit(self):
        model = FakeModel()
        with tempfile.TemporaryDirectory() as temp:
            reply = kokoro_worker.handle(model, {"text": "Hi.", "voice": "bm_lewis", "speed": .88, "out": str(Path(temp, "a.wav"))})
        self.assertTrue(reply["ok"])
        self.assertEqual(model.calls, [{"voice": "bm_lewis", "speed": .88, "lang": "en-gb", "trim": False}])

    def test_refuses_at_the_limit_so_kokoro_never_batches(self):
        model = FakeModel(kokoro_worker.MAX_PHONEMES)
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()) as errors:
            with self.assertRaises(ValueError):
                kokoro_worker.handle(model, {"text": "Long.", "voice": "bm_lewis", "out": str(Path(temp, "a.wav"))})
        self.assertEqual(model.calls, [])
        self.assertIn("refused", errors.getvalue())
        accepted = FakeModel(kokoro_worker.MAX_PHONEMES - 1)
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(kokoro_worker.handle(accepted, {"text": "x", "voice": "am_adam", "out": str(Path(temp, "a.wav"))})["ok"])

    def test_phonemes_op_uses_the_tokenizer_and_synthesizes_nothing(self):
        model = FakeModel(321)
        self.assertEqual(kokoro_worker.handle(model, {"op": "phonemes", "text": "x", "voice": "bm_lewis"}), {"ok": True, "phonemes": 321})
        self.assertEqual(model.calls, [])


class AssemblyTests(unittest.TestCase):
    def scene(self):
        samples = np.concatenate([tone(.5), tone(.5), tone(.5)])
        words = [{"text": "One", "start": .05, "end": .45}, {"text": "two,", "start": .55, "end": .95}, {"text": "three.", "start": 1.05, "end": 1.45}]
        return {"id": 1, "text": "One two, three.", "samples": samples, "words": words,
                "cuts": [{"end": .5, "after": "clause"}, {"end": 1.0, "after": "sentence"}, {"end": 1.5, "after": "scene"}]}

    def test_clause_pause_is_exact_and_the_join_is_faded(self):
        built = assembly.assemble([self.scene()])
        self.assertEqual([(pause["kind"], pause["seconds"]) for pause in built["pauses"]], [("clause", .12), ("sentence", .35)])
        at = int(built["pauses"][0]["at"] * RATE)
        gap = built["samples"][at:at + int(.12 * RATE)]
        self.assertEqual(float(np.abs(gap).max()), 0.0)
        before = built["samples"][at - int(assembly.FADE_SECONDS * RATE):at]
        self.assertLess(abs(float(before[-1])), .03)                            # faded out, no click
        self.assertAlmostEqual(assembly.DEFAULT_PAUSES["clause"], .12)

    def test_cuts_decide_the_pieces_and_word_times_follow(self):
        built = assembly.assemble([self.scene()])
        words = built["words"]
        self.assertEqual([word["text"] for word in words], ["One", "two,", "three."])
        self.assertGreater(words[1]["start"], built["pauses"][0]["at"])
        self.assertGreater(words[2]["start"], built["pauses"][1]["at"])
        self.assertEqual([piece["index"] for piece in built["pieces"]], [0, 1, 2])

    def test_a_cut_on_touching_words_moves_to_the_quiet_nearby(self):
        speech = np.concatenate([tone(.5), np.zeros(int(.06 * RATE), dtype=np.float32), tone(.5)])
        cut = assembly._cut_between(speech, RATE, .42, .42)                     # the recogniser says they touch, 80 ms early
        self.assertTrue(int(.5 * RATE) <= cut <= int(.56 * RATE), cut / RATE)

    def test_clipped_edges_are_reported_only_on_speech(self):
        rate = joins.RATE
        tail = (np.exp(-np.arange(int(.2 * rate)) / (.015 * rate)) * .2).astype(np.float32)
        clean = np.concatenate([tail, np.zeros(int(.35 * rate), dtype=np.float32), tail * 0, tail])
        self.assertEqual(joins.clipped_edges(clean, [{"at": .2, "seconds": .35, "kind": "sentence"}]), [])
        cut = np.concatenate([np.full(int(.2 * rate), .2, dtype=np.float32), np.zeros(int(.35 * rate), dtype=np.float32),
                              np.full(int(.2 * rate), .2, dtype=np.float32)])
        found = joins.clipped_edges(cut, [{"at": .2, "seconds": .35, "kind": "sentence"}])
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]["before_db"], joins.EDGE_DB)
        self.assertTrue(any("fala cortada" in line for line in joins.describe({"clipped_edges": found})))

    def test_retiming_after_a_rebuild_keeps_images_and_approvals(self):
        scene = {"id": 1, "text": "One two. Three four.", "samples": np.concatenate([tone(.5), tone(.5)]), "cuts": [{"end": .5, "after": "sentence"}, {"end": 1.0, "after": "scene"}],
                 "words": [{"text": "One", "start": .05, "end": .2}, {"text": "two.", "start": .25, "end": .45, "sentence_end": True},
                           {"text": "Three", "start": .55, "end": .7}, {"text": "four.", "start": .75, "end": .95, "sentence_end": True}]}
        built = assembly.assemble([scene])
        shots = {"shots": [{"shot_id": "a", "section_id": 1, "narration_excerpt": "One two.", "start": 0, "end": 9, "image_path": "images/a.jpg", "qa": {"reviewed": True}},
                           {"shot_id": "b", "section_id": 1, "narration_excerpt": "Three four.", "start": 9, "end": 18, "image_path": "images/b.jpg"}]}
        retime.retime_shots(shots, built["words"], built["durations"], [1])
        self.assertEqual([shot["image_path"] for shot in shots["shots"]], ["images/a.jpg", "images/b.jpg"])
        self.assertEqual(shots["shots"][0]["qa"], {"reviewed": True})
        self.assertLess(shots["shots"][1]["end"], 9)


class RepetitionTests(unittest.TestCase):
    def kinds(self, text, heard):
        return stutter.repetitions(text.split(), heard_for(heard))

    def test_a_doubled_word_or_sequence(self):
        self.assertEqual(self.kinds("he saw the flame", "he saw the the flame")[0]["word"], "the")
        self.assertEqual(self.kinds("he saw the flame", "he saw the flame the flame")[0]["word"], "the flame")

    def test_a_syllable_before_its_word(self):
        self.assertEqual(self.kinds("he saw the flame", "he saw the fla- flame")[0]["word"], "fla-flame")
        self.assertEqual(self.kinds("he saw the flame", "he saw the fla-flame")[0]["word"], "fla-flame")

    def test_what_the_text_really_says_twice_is_fine(self):
        self.assertEqual(self.kinds("he had had enough", "he had had enough"), [])
        self.assertEqual(self.kinds("flour-dusted hands", "flour dusted hands"), [])

    def test_case_and_punctuation_do_not_matter(self):
        self.assertEqual(self.kinds("The flame, the flame.", "the flame the flame"), [])


class WordChangeTests(unittest.TestCase):
    def changes(self, text, heard):
        return stutter.word_changes(text.split(), heard_for(heard))

    def test_an_omitted_or_inserted_content_word(self):
        self.assertEqual([(hit["kind"], hit["word"]) for hit in self.changes("the dark green coat", "the dark coat")], [("omission", "green")])
        self.assertEqual([(hit["kind"], hit["word"]) for hit in self.changes("the dark coat", "the dark heavy coat")], [("insertion", "heavy")])

    def test_stopwords_numbers_and_misheard_words_are_not_hits(self):
        self.assertEqual(self.changes("he saw the coat", "he saw coat"), [])
        self.assertEqual(self.changes("twenty six years passed", "26 years passed"), [])
        self.assertEqual(self.changes("Thomas Wren waits", "Thomas Ren waits"), [])


class DurationTests(unittest.TestCase):
    def events(self, seconds):
        return [{"text": "first", "start_ms": 0, "end_ms": 300}, {"text": "go", "start_ms": 300, "end_ms": 300 + seconds * 1000},
                {"text": "last", "start_ms": 2000, "end_ms": 2300}]

    def test_a_word_far_over_the_median_for_its_length(self):
        self.assertEqual(stutter.long_words(self.events(.9))[0]["word"], "go")
        self.assertEqual(stutter.long_words(self.events(.2)), [])

    def test_slower_speech_is_allowed_more_time(self):
        limit = stutter.DURATION_RATIO * stutter.median_for(2, .5)
        self.assertEqual(stutter.long_words(self.events(limit - .01), .5), [])

    def test_silence_the_recogniser_gave_the_word_does_not_count(self):
        events = self.events(.9)
        mostly_silent = np.concatenate([np.zeros(int(.95 * RATE), dtype=np.float32), tone(.25), np.zeros(int(1.2 * RATE), dtype=np.float32)])
        self.assertEqual(stutter.long_words(events, 1.0, mostly_silent), [])
        held = np.concatenate([np.zeros(int(.3 * RATE), dtype=np.float32), tone(.9), np.zeros(int(1.2 * RATE), dtype=np.float32)])
        self.assertEqual(stutter.long_words(events, 1.0, held)[0]["word"], "go")

    def test_first_last_interpolated_and_before_punctuation_are_skipped(self):
        long_word = [{"text": "One", "start_ms": 0, "end_ms": 2000}, {"text": "two,", "start_ms": 2000, "end_ms": 4000},
                     {"text": "go", "start_ms": 4000, "end_ms": 6000, "interpolated": True}, {"text": "end", "start_ms": 6000, "end_ms": 9000}]
        self.assertEqual(stutter.long_words(long_word), [])


class AcousticTests(unittest.TestCase):
    def speech(self, seconds=0.6):
        """Noise under a moving filter: changes all the time, as speech does, and never repeats."""
        rng = np.random.default_rng(3)
        samples = np.convolve(rng.normal(0, 0.2, int(seconds * RATE)), np.ones(4) / 4, mode="same")
        return samples.astype(np.float32)

    def event(self, seconds):
        return [{"text": "word", "start_ms": 0, "end_ms": seconds * 1000}]

    def test_a_duplicated_120_ms_segment_is_flagged(self):
        clean = self.speech()
        cut = int(.2 * RATE)
        size = int(.12 * RATE)
        doubled = np.concatenate([clean[:cut + size], clean[cut:cut + size], clean[cut + size:]])
        hits = stutter.acoustic(doubled, self.event(len(doubled) / RATE))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "acoustic")
        self.assertAlmostEqual(hits[0]["lag"], .12, delta=.005)
        self.assertGreater(hits[0]["ncc"], stutter.ACOUSTIC_NCC)

    def test_the_rate_of_the_samples_is_respected(self):
        clean = self.speech(1.0)
        at, size = int(.5 * RATE), int(.12 * RATE)
        doubled = np.concatenate([clean[:at + size], clean[at:at + size], clean[at + size:]])
        reduced = np.interp(np.arange(int(len(doubled) * 16000 / RATE)) * RATE / 16000, np.arange(len(doubled)), doubled).astype(np.float32)
        word = [{"text": "word", "start_ms": 450, "end_ms": 800}]
        self.assertEqual(len(stutter.acoustic(reduced, word, 16000)), 1)
        self.assertEqual(stutter.acoustic(reduced, [{"text": "word", "start_ms": 10, "end_ms": 300}], 16000), [])     # elsewhere: clean

    def test_a_clean_clip_is_not_flagged(self):
        clean = self.speech()
        self.assertEqual(stutter.acoustic(clean, self.event(.6)), [])

    def test_a_steady_vowel_is_not_a_stutter(self):
        self.assertEqual(stutter.acoustic(tone(.6), self.event(.6)), [])

    def test_a_loud_window_never_matches_near_silence(self):
        clip = np.concatenate([self.speech(.1), np.full(int(.3 * RATE), 1e-6, dtype=np.float32)])
        self.assertEqual(stutter.acoustic(clip, self.event(.4)), [])

    def test_silence_is_ignored(self):
        self.assertEqual(stutter.acoustic(np.zeros(RATE // 2, dtype=np.float32), self.event(.5)), [])


class RepairTests(unittest.TestCase):
    def test_a_clean_first_attempt_costs_nothing(self):
        calls = []

        def render(text, speed):
            calls.append((text, speed))
            return {"text": text, "hits": []}

        self.assertEqual(stutter.repair("Some words here.", .88, render, lambda *_: self.fail("no split"))["tries"], 0)
        self.assertEqual(len(calls), 1)

    def test_escalation_order_speed_comma_split(self):
        calls = []

        def render(text, speed):
            calls.append((text, speed))
            return {"text": text, "speed": speed, "hits": [{"kind": "repetition", "index": 1}]}

        def split(text, speed):
            calls.append(("split", speed))
            return {"text": text, "speed": speed, "hits": [{"kind": "repetition"}]}

        best = stutter.repair("One two, three four five.", .88, render, split)
        self.assertEqual(calls, [("One two, three four five.", .88), ("One two, three four five.", .86), ("One two three four five.", .88), ("split", .88)])
        self.assertEqual(best["tries"], 3)
        self.assertEqual((best["text"], best["speed"]), ("One two, three four five.", .88))   # a tie keeps the first

    def test_keeps_the_attempt_with_the_fewest_hits_and_stops_when_clean(self):
        results = iter([[{"kind": "a"}, {"kind": "b"}], [{"kind": "a"}], []])

        def render(text, speed):
            return {"text": text, "speed": speed, "hits": next(results)}

        best = stutter.repair("One two three four.", .88, render, lambda *_: self.fail("clean before the split"))
        self.assertEqual((best["hits"], best["tries"]), ([], 2))

    def test_best_of_a_failing_ladder(self):
        results = iter([[1, 2, 3], [1], [1, 2]])

        def render(text, speed):
            return {"text": text, "speed": speed, "hits": [{"kind": str(n)} for n in next(results)]}

        best = stutter.repair("One two three four.", .88, render, lambda *_: None)
        self.assertEqual((len(best["hits"]), best["speed"]), (1, .86))
        self.assertEqual(best["tries"], 2)                                      # no clause to split at

    def test_comma_variant(self):
        self.assertEqual(stutter.comma_variant("One two, three four.", {"index": 2}), "One two three four.")
        self.assertEqual(stutter.comma_variant("One two three four five.", {"index": 2}), "One two, three four five.")
        self.assertIsNone(stutter.comma_variant("One two.", {"index": 0}))


def fake_synthesis(temp, calls, *, hits=None, **kwargs):
    def synth(text, voice, speed, path):
        calls.append(text)
        local.write_wav(path, spoken(), RATE)

    def transcribe(path):
        return heard_for(calls[-1])

    return sentences.SceneSynthesis(voice="bm_lewis", speed=.88, synth=synth, count=count, cache_dir=Path(temp, "sentences"), model="kokoro-v1.0.onnx",
                                    transcribe=transcribe, **kwargs)


class CacheTests(unittest.TestCase):
    def test_editing_one_sentence_resynthesizes_only_it(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            synthesis = fake_synthesis(temp, calls)
            first = synthesis.scene("One two three. Four five six. Seven eight nine.")
            self.assertEqual(calls, ["One two three.", "Four five six.", "Seven eight nine."])
            self.assertEqual(first["stats"]["reused"], 0)
            calls.clear()
            again = synthesis.scene("One two three. Four five CHANGED. Seven eight nine.")
        self.assertEqual(calls, ["Four five CHANGED."])
        self.assertEqual(again["stats"]["reused"], 2)

    def test_the_key_covers_voice_speed_model_and_text_but_not_spacing(self):
        base = dict(backend="local", voice="bm_lewis", speed=.88, model="m1")
        key = sentences.cache_key("Hello there.", **base)
        self.assertEqual(key, sentences.cache_key("Hello   there.\n", **base))
        for change in ({"voice": "bm_george"}, {"speed": .9}, {"model": "m2"}, {"backend": "x"}):
            self.assertNotEqual(key, sentences.cache_key("Hello there.", **{**base, **change}))
        self.assertNotEqual(key, sentences.cache_key("Hello you.", **base))

    def test_pronunciations_reach_the_key_and_the_synthesis(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            fake_synthesis(temp, calls, apply=lambda text: text.replace("Wren", "Ren")).scene("Thomas Wren waits here.")
        self.assertEqual(calls, ["Thomas Ren waits here."])

    def test_a_clip_with_hits_is_reported_with_its_file_and_not_retried_from_cache(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp, patch.object(stutter, "check", return_value=[{"kind": "duration", "word": "two", "at": .1}]):
            synthesis = fake_synthesis(temp, calls)
            scene = synthesis.scene("One two three four.")
            flagged = scene["stats"]["flagged"][0]
            self.assertTrue(Path(flagged["clip"]).is_file())
            self.assertEqual(flagged["tries"], 2)                               # speed, comma; no clause to split at
            calls.clear()
            again = synthesis.scene("One two three four.")
        self.assertEqual(calls, [])
        self.assertEqual(len(again["stats"]["flagged"]), 1)


class BackendTests(unittest.TestCase):
    def test_previews_go_through_the_sentences(self):
        calls = []

        class Worker:
            def synthesize(self, text, voice, speed, path):
                calls.append((text, speed))
                local.write_wav(path, tone(.5), RATE)

            def phonemes(self, text, voice):
                return len(text)

        with tempfile.TemporaryDirectory() as temp, patch.object(config, "TTS_LOCAL_SPEED", .88), patch.object(config, "TTS_LOCAL_ENGINE", "kokoro"), \
                patch.object(local, "_kokoro_in_process", return_value=False), patch.object(local, "_kokoro_worker", return_value=Worker()), \
                patch("audio.tts.align.transcribe", side_effect=lambda path: heard_for(calls[-1][0])):
            result = local.LocalBackend().synthesize("One two three. Four five six.", str(Path(temp, "a.mp3")), voice="bm_lewis", rate="+0%", pitch="+0Hz")
            self.assertTrue(Path(temp, "a.mp3").stat().st_size > 0)
        self.assertEqual(calls, [("One two three.", .88), ("Four five six.", .88)])
        self.assertEqual(len(result["words"]), 6)
        self.assertEqual(result["stutter"]["sentences"], 2)
        self.assertGreater(result["words"][3]["start_ms"] - result["words"][2]["end_ms"], 300)      # the sentence pause

    def test_worker_phonemes_protocol(self):
        sent = []

        class Process:
            class stdin:
                write = staticmethod(lambda line: sent.append(json.loads(line)))
                flush = staticmethod(lambda: None)

            class stdout:
                readline = staticmethod(lambda: json.dumps({"ok": True, "phonemes": 42}))

        worker = local.KokoroWorker.__new__(local.KokoroWorker)
        worker.process = Process
        self.assertEqual(worker.phonemes("Hello.", "bm_lewis"), 42)
        self.assertEqual(sent, [{"op": "phonemes", "text": "Hello.", "voice": "bm_lewis"}])


class NarrationTests(unittest.TestCase):
    def test_a_narration_writes_the_stage_report_and_keeps_scene_order(self):
        from studio import projects
        from video import narrator
        calls = []
        script = {"sections": [{"id": 1, "narration": "One two three. Four five six."}, {"id": 2, "narration": "Seven eight nine."}]}
        hit = [{"kind": "duration", "word": "eight", "at": .2}]

        class Backend:
            name, audio_ext, sentence_mode = "local", "wav", True

            def __init__(self, temp):
                self.temp = temp

            def sentence_synthesis(self, voice, cache_dir, apply):
                return fake_synthesis(Path(cache_dir).parent, calls)

            def close(self):
                pass

        real_check = stutter.check

        def check(samples, text, heard, events, **kwargs):
            return hit if "eight" in text else real_check(samples, text, heard, events, **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(narrator.tts, "get", return_value=Backend(temp)), patch.object(stutter, "check", side_effect=check), \
                patch.object(config, "VOICE_CALIBRATION_FILE", str(Path(temp, "cal.json"))), redirect_stdout(io.StringIO()) as printed:
            narrator.generate_narration(script, temp)
            report = json.loads(Path(temp, "narration_stutter.json").read_text(encoding="utf-8"))
            words = json.loads(Path(temp, "narration_words.json").read_text(encoding="utf-8"))["words"]
            joined = json.loads(Path(temp, "narration_joins.json").read_text(encoding="utf-8"))
            self.assertTrue(Path(temp, report["flagged"][0]["clip"]).is_file())
        self.assertEqual((report["sentences"], report["reused"]), (3, 0))
        self.assertEqual(report["flagged"][0]["scene_id"], 2)
        self.assertEqual(report["flagged"][0]["hits"][0]["word"], "eight")
        first_of_scene_two = next(word for word in words if word["scene_id"] == 2)
        self.assertGreater(report["flagged"][0]["at"], first_of_scene_two["start"] - .1)
        self.assertEqual(joined["clipped_edges"], [])
        self.assertIn("possivel gagueira", printed.getvalue())
        self.assertEqual([word["text"] for word in words][:3], ["One", "two", "three."])
        self.assertTrue(callable(projects.stutter_note))

    def test_stage_card_and_play_route(self):
        import gui
        from studio import projects
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp):
            clip = Path(temp, ".tts_cache/sentences/abc.wav")
            clip.parent.mkdir(parents=True)
            local.write_wav(clip, tone(.1), RATE)
            atomic_write_json(Path(temp, "narration_stutter.json"), {"sentences": 12, "resynthesized": 2, "reused": 0, "flagged": [
                {"at": 12.5, "text": "One two.", "hits": [{"kind": "duration", "word": "two", "at": 12.5}], "clip": ".tts_cache/sentences/abc.wav"},
                {"at": 30.0, "text": "Escape.", "hits": [], "clip": "../../outside.wav"}]})
            detail, flagged = projects.stutter_note(Path(temp))
            client = gui.app.test_client()
            played = client.get("/api/narration/flagged/0")
            self.assertEqual(played.status_code, 200)
            played.close()
            self.assertEqual(client.get("/api/narration/flagged/1").status_code, 404)
            self.assertEqual(client.get("/api/narration/flagged/9").status_code, 404)
        self.assertIn("12 frases", detail)
        self.assertIn("1", detail)
        self.assertEqual(flagged[0]["url"], "/api/narration/flagged/0")
        self.assertIn("duration", flagged[0]["kinds"])


class AuditionTests(unittest.TestCase):
    def test_local_samples_get_a_new_name_and_report_hits(self):
        from scripts import voice_audition
        self.assertNotEqual(voice_audition.sample_name("local", "bm_lewis", "x"), "local_bm_lewis_" + voice_audition.text_hash("x") + ".mp3")
        self.assertEqual(voice_audition.sample_name("edge", "en-GB-RyanNeural", "x"), "edge_en-GB-RyanNeural_" + voice_audition.text_hash("x") + ".mp3")
        self.assertEqual(voice_audition.stutter_hits({"stutter": {"flagged": [{"hits": [1, 2]}, {"hits": [3]}]}}), 3)
        self.assertIsNone(voice_audition.stutter_hits({}))


if __name__ == "__main__":
    unittest.main()
