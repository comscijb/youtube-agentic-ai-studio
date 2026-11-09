"""Phase 3.9.2-E: heard thoughts — quieter audio inside the spans only, same duration, italics in captions."""
import io
import json
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from agents.narrative import atomic_write_json
from audio import heard_thoughts
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401
from video import captions, tape
from video.timeline import parse_srt, write_shifted_srt

THOUGHT_BRIEF = {"narrative_type": "philosophical_thought_experiment",
                 "author_brief": {"setting": "A town where anyone hears your thoughts."}}


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples ** 2)))


def words_for(text: str, seconds: float = 1.0) -> list[dict]:
    return [{"text": token, "start": index * seconds, "end": (index + 1) * seconds,
             "sentence_end": bool(captions._SENTENCE_END.search(token))}
            for index, token in enumerate(text.split())]


class MappingTests(unittest.TestCase):
    def test_spans_map_to_the_seconds_of_their_words(self):
        text = "Wickham's baker smiles. 'I don't want this,' he thinks. 'Nor I.'"
        words = captions.mark_thoughts(text, words_for(text))
        self.assertEqual([word["text"] for word in words if word.get("thought")],
                         ["'I", "don't", "want", "this,'", "'Nor", "I.'"])
        self.assertEqual(heard_thoughts.intervals(words), [(3.0, 7.0), (9.0, 11.0)])

    def test_with_aligned_words_a_punctuation_token_is_skipped(self):
        text = "He stops — 'Go on,' he thinks."
        words = captions.mark_thoughts(text, captions.align_words(text, [
            {"text": token, "start_ms": index * 100, "end_ms": index * 100 + 90}
            for index, token in enumerate(["He", "stops", "Go", "on", "he", "thinks"])]))
        self.assertEqual([word["text"] for word in words if word.get("thought")], ["'Go", "on,'"])

    def test_no_thoughts_no_intervals(self):
        self.assertEqual(heard_thoughts.intervals(words_for("Don't go to Wickham's.")), [])


class MixTests(unittest.TestCase):
    def signal(self, seconds=2.0):
        wave_ = 0.5 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * heard_thoughts.RATE)) / heard_thoughts.RATE)
        return np.repeat(wave_.astype(np.float32), heard_thoughts.CHANNELS)

    def test_gain_only_inside_the_spans(self):
        dry = self.signal()
        mixed = heard_thoughts.mix(dry, dry.copy(), [(0.5, 1.5)]).reshape(-1, 2)
        channels = dry.reshape(-1, 2)
        rate = heard_thoughts.RATE
        np.testing.assert_array_almost_equal(mixed[:int(.49 * rate)], channels[:int(.49 * rate)])
        np.testing.assert_array_almost_equal(mixed[int(1.51 * rate):], channels[int(1.51 * rate):])
        inside = 20 * np.log10(rms(mixed[int(.6 * rate):int(1.4 * rate)]) / rms(channels[int(.6 * rate):int(1.4 * rate)]))
        self.assertAlmostEqual(inside, -5.0, places=1)

    def test_fades_at_the_boundaries(self):
        shape = heard_thoughts.envelope(44100, 44100, [(0.25, 0.75)])
        self.assertEqual(shape[int(.2 * 44100)], 0)
        self.assertEqual(shape[int(.5 * 44100)], 1)
        ramp = shape[int(.25 * 44100):int(.28 * 44100)]
        self.assertTrue(np.all(np.diff(ramp) >= 0))
        self.assertLess(ramp[0], 0.05)
        self.assertLess(shape[int(.75 * 44100) - 2], 0.1)

    def test_real_file_keeps_its_duration(self):
        from moviepy.editor import AudioFileClip
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "narration.wav")
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(heard_thoughts.RATE)
                handle.writeframes((self.signal(3.0) * 32767).astype("<i2").tobytes())
            before = heard_thoughts._decode(path, tape.ffmpeg_exe())
            self.assertEqual(heard_thoughts.apply(path, [(1.0, 2.0)]), 1)
            after = heard_thoughts._decode(path, tape.ffmpeg_exe())
            self.assertEqual(len(after), len(before))
            rate = heard_thoughts.RATE * 2
            self.assertLess(rms(after[int(1.2 * rate):int(1.8 * rate)]), rms(before[int(1.2 * rate):int(1.8 * rate)]) * .8)
            self.assertAlmostEqual(rms(after[:int(.9 * rate)]), rms(before[:int(.9 * rate)]), places=3)
            self.assertEqual(heard_thoughts.apply(path, []), 0)
            mp3 = Path(temp, "narration.mp3")
            from video.tape import ffmpeg_exe
            import subprocess
            subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(path), str(mp3)], check=True)
            clip = AudioFileClip(str(mp3))
            duration = clip.duration
            clip.close()
            heard_thoughts.apply(mp3, [(1.0, 2.0)])
            clip = AudioFileClip(str(mp3))
            self.assertAlmostEqual(clip.duration, duration, delta=0.06)
            clip.close()


class ConventionTests(unittest.TestCase):
    def test_on_for_the_thought_rule_off_for_type_a_and_by_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            atomic_write_json(Path(temp, "story_brief.json"), THOUGHT_BRIEF)
            self.assertTrue(heard_thoughts.enabled(temp))
            with patch.object(config, "HEARD_THOUGHT_STYLE", "off"):
                self.assertFalse(heard_thoughts.enabled(temp))
            self.assertFalse(heard_thoughts.enabled(temp, {"narrative_type": "realistic_psychological_fiction"}))
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(heard_thoughts.enabled(temp))              # no brief at all


class CaptionTests(unittest.TestCase):
    def test_italics_in_the_cue_only_around_the_thought(self):
        text = "He smiled. 'I am so tired.' She nodded."
        cues = captions.build_cues(captions.mark_thoughts(text, words_for(text, 0.3)))
        joined = [cue[2] for cue in cues]
        self.assertIn("<i>'I am so tired.'</i>", joined)
        self.assertEqual(joined[0], "He smiled.")
        self.assertNotIn("<i>", joined[-1])

    def test_a_thought_across_two_cues_is_closed_in_each(self):
        text = "'One two three four five six seven eight nine ten.' He stopped."
        cues = captions.build_cues(captions.mark_thoughts(text, words_for(text, 0.2)))
        self.assertTrue(cues[0][2].startswith("<i>") and cues[0][2].endswith("</i>"))
        self.assertTrue(cues[1][2].startswith("<i>") and cues[1][2].endswith("</i>"))

    def test_plain_cues_have_no_tags_without_the_flag(self):
        text = "He smiled. 'I am so tired.' She nodded."
        self.assertNotIn("<i>", " ".join(cue[2] for cue in captions.build_cues(words_for(text, 0.3))))

    def test_srt_keeps_tags_for_upload_and_readers_get_plain_text(self):
        with tempfile.TemporaryDirectory() as temp:
            scene = Path(temp, "scene.srt")
            scene.write_text("1\n00:00:01,000 --> 00:00:02,000\n<i>'I am tired.'</i>\n\n", encoding="utf-8")
            final = Path(temp, "narration.srt")
            write_shifted_srt(final, [(str(scene), 1.0)])
            self.assertIn("<i>'I am tired.'</i>", final.read_text(encoding="utf-8"))
            self.assertEqual(parse_srt(final)[0][2], "'I am tired.'")

    def test_ass_turns_tags_into_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "c.ass")
            tape.write_ass([{"start": 0, "end": 1, "text": "<i>'I am tired.'</i> He sat."}], path, (1920, 1080),
                           font_path=None, font_size=36, margin_bottom=90)
            body = path.read_text(encoding="utf-8")
        self.assertIn("{\\i1}'I am tired.'{\\i0} He sat.", body)

    def test_frames_are_drawn_plain(self):
        from video import creator
        self.assertEqual(creator._get_caption_at([{"start": 0, "end": 1, "text": "<i>'x'</i>"}], 0.5), "'x'")


class NarrationTests(unittest.TestCase):
    def test_generate_narration_flags_words_and_softens_the_file(self):
        from video import narrator
        text = "He smiled at her. 'I am so tired today,' he thought. Then he left the room for good."
        script = {"narrative_type": THOUGHT_BRIEF["narrative_type"], "sections": [{"id": 1, "narration": text}]}

        class Backend:
            name = "edge"
            supports_style = False

            def synthesize(self, spoken, path, **kwargs):
                from video.tape import ffmpeg_exe
                import subprocess
                subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=440:duration=3",
                                str(path)], check=True)
                return {"words": []}

            def cache_tag(self):
                return "fake"

        with tempfile.TemporaryDirectory() as temp, patch.object(narrator.tts, "get", return_value=Backend()), \
                patch.object(narrator, "record_calibration", return_value=None), \
                patch.object(narrator.heard_thoughts, "apply", wraps=heard_thoughts.apply) as applied,                 redirect_stdout(io.StringIO()):
            atomic_write_json(Path(temp, "story_brief.json"), THOUGHT_BRIEF)
            narrator.generate_narration(script, temp)
            saved = json.loads(Path(temp, "narration_words.json").read_text(encoding="utf-8"))
            srt = Path(temp, "narration.srt").read_text(encoding="utf-8")
        self.assertEqual(len(applied.call_args.args[1]), 1)
        self.assertEqual([word["text"] for word in saved["words"] if word.get("thought")],
                         ["'I", "am", "so", "tired", "today,'"])
        self.assertIn("<i>", srt)


if __name__ == "__main__":
    unittest.main()
