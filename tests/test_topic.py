"""Phase 4.0-A: topic clarity in every package."""
import io
import json
import unittest
from contextlib import redirect_stdout

from agents import packaging, topic
from agents.narrative import NarrativeValidationError

TYPE_B = "philosophical_thought_experiment"


class TopicScoreTests(unittest.TestCase):
    def test_a_searchable_concept_scores_five(self):
        self.assertEqual(topic.topic_score("He Woke Up Next to His Own Face", TYPE_B), 5)
        self.assertEqual(topic.topic_score("The Mirror That Lied Back"), 5)
        self.assertEqual(topic.topic_score("For One Day, Everyone Had It", TYPE_B), 1)
        self.assertEqual(topic.topic_score("For One Day, Everyone Had It", TYPE_B, ["everyone"]), 5)

    def test_the_topic_counts_in_the_total_and_old_scores_keep_their_total(self):
        full = {"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 5, "length": 5}
        self.assertEqual(packaging.total_score(full), 100)
        self.assertEqual(packaging.total_score({**full, "topic": 5}), 100)
        self.assertEqual(packaging.total_score({**full, "topic": 1}), 90)
        self.assertAlmostEqual(sum(packaging.TITLE_WEIGHTS.values()), 1.0)

    def test_scored_titles_carry_the_topic_and_rank_by_it(self):
        brief = {"narrative_type": TYPE_B, "logline": "x"}
        same = {"curiosity": 4, "concreteness": 4, "emotion": 4, "clarity": 4}
        reply = json.dumps({"scores": [dict(index=index, note="n", **same) for index in range(2)]})
        with redirect_stdout(io.StringIO()):
            rows = packaging.score_titles(brief, ["For One Day, Everyone Had It", "A Thought Experiment About His Face"],
                                          generator=lambda *a, **k: reply)
        self.assertEqual(rows[0]["title"], "A Thought Experiment About His Face")
        self.assertEqual([row["scores"]["topic"] for row in rows], [5, 1])


class TemplateTests(unittest.TestCase):
    def test_series_line_and_hashtags_per_type(self):
        self.assertEqual(topic.series_line(TYPE_B), "A Private Gravity philosophical thought experiment.")
        self.assertEqual(topic.series_line("dark_human_parable"), "A Private Gravity dark parable.")
        self.assertEqual(topic.series_line("realistic_psychological_fiction"), "Private Gravity · realistic psychological fiction.")
        self.assertEqual(topic.hashtags(TYPE_B, ["ai clone"]), "#PrivateGravity #thoughtexperiment #AiClone")
        self.assertEqual(topic.hashtags("dark_human_parable"), "#PrivateGravity #parable")

    def test_tags_at_most_eight_and_never_a_blocked_name(self):
        found = topic.tags(TYPE_B, ["ai clone", "identity"], ["Some Channel Clips", "a", "b", "c", "d", "e", "f"],
                           blocked_names=["Some Channel"])
        self.assertLessEqual(len(found), 8)
        self.assertEqual(found[:4], ["ai clone", "identity", "thought experiment", "philosophical story"])
        self.assertNotIn("Some Channel Clips", found)

    def test_description_parts_need_topic_words_a_premise_and_a_topical_chapter(self):
        good = {"topic_words": ["ai clone", "identity"], "premise_line": "A man meets an AI clone that wears his face.",
                "hook": "He met himself.", "chapter_titles": ["Prologue", "The AI Clone", "Two Chairs"],
                "comment_question": "Would you talk to yourself?", "pinned_comment": "Would you? Tell us."}
        self.assertEqual(packaging.validate_description_parts(good, 3)["topic_words"], ["ai clone", "identity"])
        for change, message in ((dict(premise_line="A man meets someone new."), "premise_line must use"),
                                (dict(chapter_titles=["Prologue", "Two Chairs", "Morning"]), "chapter title after the first"),
                                (dict(topic_words=[]), "topic_words")):
            with self.assertRaises(NarrativeValidationError) as caught:
                packaging.validate_description_parts({**good, **change}, 3)
            self.assertIn(message, str(caught.exception))


class StuffingTests(unittest.TestCase):
    def test_a_keyword_more_than_twice_in_the_prose_blocks(self):
        text = ("A man meets an AI clone.\nThe AI clone talks.\nNobody trusts the AI clone.\n\n"
                "Chapters\n0:00 Prologue\n1:00 The AI Clone\n\n#PrivateGravity #AiClone")
        findings = topic.validate("He Met Himself", text, TYPE_B, ["ai clone"])
        self.assertEqual([item["rule"] for item in findings], ["keyword_stuffing"])
        self.assertIn("3 times", findings[0]["claim"])

    def test_chapters_and_hashtags_are_not_prose(self):
        text = "A man meets an AI clone.\nThe AI clone talks.\n\nChapters\n0:00 The AI Clone\n\n#AiClone"
        self.assertEqual(topic.validate("He Met Himself", text, TYPE_B, ["ai clone"]), [])

    def test_the_topic_must_be_in_the_title_or_the_first_line(self):
        findings = topic.validate("He Met Himself", "Something happened.\nAn AI clone appears later.", TYPE_B, ["ai clone"])
        self.assertEqual(findings[0]["rule"], "no_topic")


if __name__ == "__main__":
    unittest.main()
