"""Phase 4.2-B: key lines on screen (choice, no spoilers, faces and captions)."""
import unittest

from PIL import Image

from video import key_lines

SENTENCES = [
    ("cold_open", "Who sold the sky?", [{"tag": "question"}]),
    ("cold_open", "It was the Merchant.", [{"tag": "reveal"}]),
    ("opening", "The valley was quiet.", [{"tag": "description"}]),
    ("opening", "He pressed the ring into the wax.", [{"tag": "escalation"}]),
    ("reversal", "The deed had a second page.", [{"tag": "reveal"}]),
    ("aftermath", "Who owns the morning now?", [{"tag": "question"}]),
]


def fixture():
    words, sentences, clock = [], [], 0.0
    for index, (kind, text, tags) in enumerate(SENTENCES):
        start = clock
        for token in text.split():
            words.append({"text": token, "start": round(clock, 2), "end": round(clock + 0.35, 2)})
            clock += 0.4
        sentences.append({"sentence": index, "section_type": kind, "text": text, "tags": tags, "start": start})
        clock += 12.0                                        # spread the lines in time
    return {"sentences": sentences}, words


class ChoiceTests(unittest.TestCase):
    def test_questions_and_reveals_are_chosen_and_the_cold_open_shows_only_its_question(self):
        attention_map, words = fixture()
        lines = key_lines.choose(attention_map, words)
        texts = [line["text"] for line in lines]
        self.assertIn("Who sold the sky?", texts)
        self.assertNotIn("It was the Merchant.", texts)                       # the teaser's answer never appears
        self.assertIn("Who owns the morning now?", texts)
        self.assertNotIn("The valley was quiet.", texts)
        self.assertEqual(next(line for line in lines if line["text"].startswith("Who owns"))["kind"], "final_question")
        starts = sorted(line["start"] for line in lines)
        self.assertTrue(all(b - a >= key_lines.MIN_GAP for a, b in zip(starts, starts[1:])))

    def test_only_spoken_words_are_visible(self):
        attention_map, words = fixture()
        line = key_lines.choose(attention_map, words)[0]
        self.assertEqual(key_lines.visible_text(line, line["start"] - 0.1), "")
        self.assertEqual(key_lines.visible_text(line, line["words"][1]["start"] + 0.01), "Who sold")
        self.assertEqual(key_lines.visible_text(line, line["words"][-1]["start"] + 0.2), "Who sold the sky?")


class PlacementTests(unittest.TestCase):
    def test_upper_third_unless_a_face_is_there(self):
        self.assertEqual(key_lines.placement([[0.4, 0.5, 0.6, 0.8]], captions_on=True), {"band": "upper", "hide_captions": False})
        self.assertEqual(key_lines.placement([[0.4, 0.1, 0.6, 0.3]], captions_on=True), {"band": "lower", "hide_captions": True})

    def test_captions_are_cut_out_under_a_lower_key_line(self):
        cues = [{"start": 0.0, "end": 10.0, "text": "a"}, {"start": 12.0, "end": 14.0, "text": "b"}]
        found = key_lines.without_hidden(cues, [(3.0, 6.0)])
        self.assertEqual([(cue["start"], cue["end"]) for cue in found], [(0.0, 3.0), (6.0, 10.0), (12.0, 14.0)])

    def test_contrast_and_drawing(self):
        self.assertGreaterEqual(key_lines.contrast_ratio(), 4.5)
        attention_map, words = fixture()
        line = key_lines.choose(attention_map, words)[0]
        from video.creator import _load_font
        frame = Image.new("RGB", (640, 360), (90, 90, 90))
        drawn = key_lines.draw(frame.copy(), line, line["end"] - 0.5, "upper", _load_font)
        upper = drawn.crop((0, 20, 640, 120))
        self.assertGreater(max(upper.convert("L").getextrema()), 200)              # ivory text in the upper band
        lower = drawn.crop((0, 260, 640, 360))
        self.assertEqual(lower.convert("L").getextrema(), (90, 90))                 # nothing drawn over the lower part


if __name__ == "__main__":
    unittest.main()
