"""Phase 4.1-B: the deterministic attention contract (no Gemini)."""
import unittest

from agents import attention

WPM = 150.0
SUNRISE_COLD = "This is the story of the man who owned the morning, and of the last sunrise he ever shared."
SUNRISE_OPENING = (
    "The old lord of Eastmere sat in the drafty study, his hands trembling as he signed away his last remaining asset "
    "for a handful of silver. The Merchant did not look at the man, nor did he care for the title of the decaying "
    "manor. He reached for the heavy gold signet ring on his thumb, pressing it firmly into a pool of melting red wax "
    "upon the parchment. The ink and seal declared an impossible possession: the eastern sky, the sunrise, and the "
    "right to all who look upon it. He folded the heavy paper into his dark fur-trimmed coat, feeling the solid weight "
    "of absolute dominion over the horizon. What is free is never valued, he reminded himself in the quiet room. The "
    "following morning, he climbed the ridge with the parchment rolled tightly in his pocket, watching the valley stir "
    "beneath him.")
REWRITTEN_COLD = "The Merchant pressed his ring into the hot wax, and the sunrise was sealed as his. \"Nobody looks east for free,\" he said."


def sections(*parts):
    kinds = ["cold_open", "opening", "setup", "development", "reversal", "climax"]
    return [{"id": index + 1, "outline_scene_id": f"s{index + 1:02d}", "section_type": kinds[index], "title": f"Scene {index + 1}",
             "narration": text} for index, text in enumerate(parts)]


class TimingTests(unittest.TestCase):
    def test_seconds_follow_the_rate_and_the_pauses(self):
        lines = attention.timing(sections("One two three four five.", "Six seven. Eight nine ten."), WPM)
        self.assertEqual([item["words"] for item in lines], [5, 2, 3])
        self.assertAlmostEqual(lines[0]["end"], 2.0, places=2)                 # 5 words at 150 wpm
        self.assertAlmostEqual(lines[1]["start"], 2.0 + 1.5, places=2)         # a scene pause after the cold open
        self.assertAlmostEqual(lines[2]["start"], lines[1]["end"] + 0.35, places=2)
        self.assertEqual(attention.cold_open_end(lines), lines[0]["end"])

    def test_paragraph_pause(self):
        lines = attention.timing(sections("A b c.\n\nD e f."), WPM)
        self.assertAlmostEqual(lines[1]["start"] - lines[0]["end"], 0.7, places=2)


class OpeningTests(unittest.TestCase):
    def test_the_sunrise_opening_fails(self):
        found = attention.check(sections(SUNRISE_COLD, SUNRISE_OPENING), words_per_minute=WPM)
        rules = {item["rule"] for item in found["findings"] if item["severity"] == "major"}
        self.assertTrue(rules & {"first_5_seconds", "inciting_event"})
        self.assertFalse(found["passes"])

    def test_opening_on_the_deed_passes_the_first_seconds(self):
        found = attention.check(sections(REWRITTEN_COLD, SUNRISE_OPENING), words_per_minute=WPM)
        self.assertNotIn("first_5_seconds", {item["rule"] for item in found["findings"]})

    def test_weather_first_fails(self):
        text = "Dawn filtered over frost-rimmed slate. A wooden peel scraped across the counter."
        found = attention.first_seconds(attention.timing(sections(text), WPM),
                                        attention.tags_for(attention.timing(sections(text), WPM), None))
        self.assertEqual(found[0]["rule"], "first_5_seconds")

    def test_the_inciting_event_uses_the_map(self):
        lines = attention.timing(sections("\"Run,\" she said.", "The valley was quiet. The river was slow."), WPM)
        quiet = {"sentences": [{"sentence": 0, "tags": [{"tag": "dialogue_turn"}]}]}
        self.assertEqual(attention.inciting_event(lines, attention.tags_for(lines, quiet))[0]["rule"], "inciting_event")
        moving = {"sentences": [{"sentence": 0, "tags": [{"tag": "dialogue_turn"}]}, {"sentence": 2, "tags": [{"tag": "escalation"}]}]}
        self.assertEqual(attention.inciting_event(lines, attention.tags_for(lines, moving)), [])


def long_scene(count, sentence="The road was long and grey and empty."):
    return " ".join([sentence] * count)


class GapAndRunTests(unittest.TestCase):
    def test_dead_air_minor_and_major(self):
        lines = attention.timing(sections("\"Stop!\" he said.", long_scene(9)), WPM)       # ~30 s of description
        found = attention.dead_air(lines, attention.tags_for(lines, None))
        self.assertEqual(found[0]["severity"], "minor")
        lines = attention.timing(sections("\"Stop!\" he said.", long_scene(14)), WPM)      # ~50 s
        self.assertEqual(attention.dead_air(lines, attention.tags_for(lines, None))[0]["severity"], "major")

    def test_description_run(self):
        lines = attention.timing(sections(long_scene(3) + " \"Who's there?\" he asked."), WPM)
        found = attention.description_runs(lines, attention.tags_for(lines, None))
        self.assertEqual(len(found), 1)
        self.assertIn("3 frases seguidas", found[0]["claim"])
        self.assertEqual(found[0]["severity"], "major")                       # in the opening window

    def test_the_sunrise_exposition_after_its_cold_open_is_major(self):
        lines = attention.timing(sections(SUNRISE_COLD, SUNRISE_OPENING), WPM)
        mapped = {"sentences": [{"sentence": 0, "tags": [{"tag": "open_loop:last_sunrise"}]},
                                *({"sentence": index, "tags": [{"tag": "description"}]} for index in (1, 2, 3)),
                                {"sentence": 4, "tags": [{"tag": "reveal"}]}]}
        found = attention.check(sections(SUNRISE_COLD, SUNRISE_OPENING), mapped, words_per_minute=WPM)
        early = [item for item in found["findings"] if item["severity"] == "major" and item["start"] < 45]
        self.assertEqual([item["rule"] for item in early], ["description_run"])


class EarTests(unittest.TestCase):
    def test_long_sentences(self):
        long = " ".join(["word"] * 40) + "."
        lines = attention.timing(sections(long + " Short one. Short two."), WPM)
        rules = [item["rule"] for item in attention.ear_sentences(lines)]
        self.assertIn("ear_too_long", rules)
        self.assertIn("ear_average", rules)
        self.assertIn("ear_long_share", rules)
        good = attention.timing(sections("He ran. She followed him into the dark. Nobody spoke."), WPM)
        self.assertEqual(attention.ear_sentences(good), [])


class LoopTests(unittest.TestCase):
    def map_for(self, tags):
        return {"sentences": [{"sentence": index, "tags": [{"tag": tag} for tag in found]} for index, found in tags.items()]}

    def test_unclosed_loop_and_the_final_question(self):
        text = [" ".join(["Line one here."] * 4), " ".join(["Line two here."] * 4), "The end came. Why?"]
        lines = attention.timing(sections(*text), WPM)
        found = attention.loops(lines, attention.tags_for(lines, self.map_for({0: ["open_loop:who"], 9: ["open_loop:why"]})),
                                self.map_for({}))
        claims = " ".join(item["claim"] for item in found if item["rule"] == "loop_unclosed")
        self.assertIn("\"who\"", claims)
        self.assertNotIn("\"why\"", claims)                                   # the last scene's question may stay open

    def test_no_live_loop_and_scene_without_loop(self):
        text = ["A b c d e f g h i j.", long_scene(30), long_scene(5)]
        lines = attention.timing(sections(*text), WPM)
        tags = attention.tags_for(lines, self.map_for({1: ["open_loop:a"], 2: ["close_loop:a"]}))
        found = attention.loops(lines, tags, self.map_for({1: ["open_loop:a"]}))
        rules = {item["rule"]: item["severity"] for item in found}
        self.assertEqual(rules.get("no_live_loop"), "major")
        self.assertIn("scene_without_loop", rules)

    def test_without_a_map_loops_are_not_checked(self):
        lines = attention.timing(sections("A b c."), WPM)
        self.assertEqual(attention.loops(lines, attention.tags_for(lines, None), None), [])


if __name__ == "__main__":
    unittest.main()
