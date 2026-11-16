"""Phase 4.1-C: the attention map (Gemini mocked)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from agents import attention, attention_map
from agents.narrative import NarrativeValidationError

SECTIONS = [{"id": 1, "outline_scene_id": "s01", "section_type": "cold_open", "title": "Cold", "narration": "Who signed the sky away?"},
            {"id": 2, "outline_scene_id": "s02", "section_type": "opening", "title": "Deed",
             "narration": "The Merchant pressed his ring into the wax. The valley was quiet. \"It is mine,\" he said."}]


def reply(extra=None):
    items = [{"index": 0, "tags": [{"tag": "question", "quote": "Who signed the sky away?"},
                                   {"tag": "open_loop:who_signed", "quote": "Who signed"}]},
             {"index": 1, "tags": [{"tag": "escalation", "quote": "pressed his ring into the wax"}]},
             {"index": 2, "tags": [{"tag": "description", "quote": "The valley was quiet"},
                                   {"tag": "reveal", "quote": "words that are not in the sentence"}]},
             {"index": 3, "tags": [{"tag": "dialogue_turn", "quote": "\"It is mine,\" he said"},
                                   {"tag": "close_loop:who_signed", "quote": "It is mine"}, {"tag": "made_up", "quote": "mine"}]}]
    return json.dumps({"sentences": items + (extra or [])})


class MapTests(unittest.TestCase):
    def test_unverifiable_quotes_and_unknown_tags_are_dropped(self):
        sentences = attention.timing(SECTIONS, 150)
        found = attention_map.validate(json.loads(reply()), sentences)
        self.assertEqual(found["discarded_tags"], 2)
        third = next(item for item in found["sentences"] if item["sentence"] == 2)
        self.assertEqual([tag["tag"] for tag in third["tags"]], ["description"])
        with self.assertRaises(NarrativeValidationError):
            attention_map.validate({"sentences": []}, sentences)
        bad_loop = {"sentences": [{"index": 0, "tags": [{"tag": "open_loop:Who Signed!", "quote": "Who signed"}]},
                                  {"index": 1, "tags": []}, {"index": 2, "tags": []}]}
        self.assertEqual(attention_map.validate(bad_loop, sentences)["discarded_tags"], 1)

    def test_one_call_cached_with_timestamps(self):
        calls = []

        def generator(prompt, *args, **kwargs):
            calls.append(prompt)
            return reply()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            first = attention_map.build(temp, SECTIONS, generator=generator, words_per_minute=150)
            attention_map.build(temp, SECTIONS, generator=generator, words_per_minute=150)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["sentences"][1]["tags"][0]["tag"], "escalation")
            self.assertGreater(first["sentences"][1]["start"], 0)
            self.assertEqual(attention_map.load(temp)["key"], first["key"])

    def test_blocks_and_loops(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            found = attention_map.build(temp, SECTIONS, generator=lambda *a, **k: reply(), words_per_minute=150)
        loops = attention_map.live_loops(found)
        self.assertEqual(loops[0]["id"], "who_signed")
        self.assertTrue(loops[0]["closed"])
        blocks = attention_map.blocks(found)
        self.assertEqual(blocks[0]["colour"], "green")
        flat = {"duration": 30, "sentences": [{"sentence": 0, "start": 16, "end": 20, "tags": [{"tag": "description"}]}]}
        self.assertEqual([item["colour"] for item in attention_map.blocks(flat)], ["orange", "red"])

    def test_the_map_feeds_the_checks(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            found = attention_map.build(temp, SECTIONS, generator=lambda *a, **k: reply(), words_per_minute=150)
        result = attention.check(SECTIONS, found, words_per_minute=150)
        self.assertEqual(result["source"], "map")
        self.assertNotIn("first_5_seconds", {item["rule"] for item in result["findings"]})


if __name__ == "__main__":
    unittest.main()
