"""Phase 3.9-C: variation guard — theme and structure rotation, title shape, cadence, backfill caching."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agents.narrative import atomic_write_json
from studio import variation
from tests.style_isolation import setUpModule, tearDownModule  # noqa: F401

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def ep(title, tags, kind="realistic_psychological_fiction", device="cold-open line", composition=""):
    return {"title": title, "theme_tags": tags, "primary": tags[0] if tags else "", "narrative_type": kind,
            "opening_device": device, "thumbnail_composition": composition}


class RotationTests(unittest.TestCase):
    def test_theme_repeats_and_loss_twice_in_three(self):
        past = [ep("The Man Who Needed Everyone to Envy Him", ["status/envy"]),
                ep("He Cut Off His Daughter for a Warm Glow", ["loss/grief", "loneliness"])]
        current = ep("She Said Yes to Everyone for 20 Years", ["loss/grief", "self-erasure"])
        kinds = [item["kind"] for item in variation.checks(current, past)]
        self.assertIn("theme", kinds)
        self.assertIn("loss", kinds)
        fresh = ep("She Said Yes to Everyone for 20 Years", ["self-erasure", "family"])
        self.assertNotIn("theme", [item["kind"] for item in variation.checks(fresh, past)])
        self.assertNotIn("loss", [item["kind"] for item in variation.checks(fresh, past)])

    def test_structure_rule_and_the_outline_hint(self):
        past = [ep("A", ["control"]), ep("B", ["ambition"])]
        same = variation.checks(ep("C", ["freedom"]), past)
        self.assertIn("structure", [item["kind"] for item in same])
        other_type = variation.checks(ep("C", ["freedom"], kind="dark_human_parable"), past)
        self.assertNotIn("structure", [item["kind"] for item in other_type])
        hint = variation.opening_hint(past)
        self.assertIn("opened with a cold-open line", hint)
        self.assertIn("dialogue", hint)
        self.assertEqual(variation.opening_hint([ep("A", [], device="object"), ep("B", [], device="dialogue")]), "")

    def test_opening_device_and_tags(self):
        script = lambda text: {"sections": [{"narration": text}]}
        self.assertEqual(variation.opening_device(script('"That\'s the best conversation." He laughed.')), "dialogue")
        self.assertEqual(variation.opening_device(script("Who would he call tomorrow? Nobody.")), "question")
        self.assertEqual(variation.opening_device(script("The phone glowed on the piano. Walter waited.")), "object")
        self.assertEqual(variation.opening_device(script("Walter Hale sat in darkness.")), "cold-open line")
        self.assertEqual(variation.normalise_tags(["Loss/Grief", "nonsense", "loneliness"]), ["loss/grief", "loneliness"])
        self.assertEqual(variation.normalise_tags(None, "a widower's grief")[0], "loss/grief")

    def test_thumbnail_composition_repeat(self):
        past = [ep("A", ["control"], composition="big_face")]
        self.assertIn("thumbnail", [item["kind"] for item in variation.checks(ep("B", ["freedom"], composition="big_face"), past)])


class TitleTests(unittest.TestCase):
    def test_similar_titles_are_named_and_penalised(self):
        published = ["He Cut Off His Daughter for a Warm Glow", "The Man Who Needed Everyone to Envy Him"]
        similar = variation.title_similarity("He Lied About His Dead Wife to See What It Would Say", published)
        self.assertEqual([item["title"] for item in similar], ["He Cut Off His Daughter for a Warm Glow"])
        self.assertEqual(similar[0]["reasons"], ["same first word"])
        warnings = variation.checks(ep("He Lied About His Dead Wife to See What It Would Say", ["guilt/shame"]),
                                    [ep(title, ["control"], device="object") for title in published])
        self.assertIn("Título parecido com: He Cut Off His Daughter for a Warm Glow",
                      next(item["message"] for item in warnings if item["kind"] == "title"))
        self.assertEqual(variation.title_penalty("He Lied About His Dead Wife", published),
                         (variation.TITLE_PENALTY, "He Cut Off His Daughter for a Warm Glow"))
        self.assertEqual(variation.title_penalty("Nobody Stayed for the Second Cup", published), (0, ""))
        frame = variation.title_similarity("He Asked His Phone to Forgive Him",
                                           ["He Lied About His Dead Wife to See What It Would Say"])
        self.assertIn("same frame", frame[0]["reasons"])                     # "he … his … to …"

    def test_title_scoring_applies_the_penalty(self):
        from agents import packaging
        rows = [{"title": "He Lied to His Phone", "passes": True, "findings": []},
                {"title": "Nobody Stayed for the Second Cup", "passes": True, "findings": []}]
        answer = json.dumps({"scores": [{"curiosity": 5, "concreteness": 5, "emotion": 5, "clarity": 5, "note": "x"}] * 2})
        with patch.object(packaging, "_published_titles", return_value=["He Cut Off His Daughter for a Warm Glow"]):
            scored = packaging.score_titles({}, rows, generator=lambda *a, **k: answer)
        by_title = {row["title"]: row for row in scored}
        self.assertEqual(by_title["He Lied to His Phone"]["similar_to"], "He Cut Off His Daughter for a Warm Glow")
        self.assertLess(by_title["He Lied to His Phone"]["total"], by_title["Nobody Stayed for the Second Cup"]["total"])


class CadenceTests(unittest.TestCase):
    def test_episodes_per_week_and_shorts_per_day(self):
        ledger = [{"video_id": "a", "state": "published", "published_at": (NOW - timedelta(days=2)).isoformat()},
                  {"video_id": "b", "state": "published", "published_at": (NOW - timedelta(days=5)).isoformat()},
                  {"video_id": "s", "state": "published", "parent_video_id": "b", "published_at": NOW.isoformat()}]
        calm = variation.cadence(ledger, [], now=NOW)
        self.assertEqual((calm["episodes_per_week"], calm["shorts_per_day"], calm["warnings"]), (2, 1, []))
        busy = variation.cadence(ledger, [{"kind": "episode", "at": NOW},
                                          {"kind": "short", "at": NOW + timedelta(hours=2)}], now=NOW)
        self.assertEqual((busy["episodes_per_week"], busy["shorts_per_day"]), (3, 2))
        self.assertEqual(len(busy["warnings"]), 2)


class BackfillTests(unittest.TestCase):
    def test_one_call_per_episode_cached_and_edits_kept(self):
        ledger = [{"video_id": "ep1", "state": "published", "title": "One", "project": "missing",
                   "psychological_theme": "Envy", "published_at": "2026-10-28T00:00:00+00:00"},
                  {"video_id": "sh", "state": "published", "parent_video_id": "ep1", "title": "S #Shorts"}]
        calls = []

        def generator(prompt, **kwargs):
            calls.append(prompt)
            return json.dumps({"theme_tags": ["status/envy", "love/intimacy"], "opening_device": "object"})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "profiles.json")
            first = variation.backfill(ledger, generator=generator, path=path)
            variation.save_profile("ep1", {**first["ep1"], "opening_device": "question"}, path=path)   # edited
            again = variation.backfill(ledger, generator=generator, path=path)
        self.assertEqual(len(calls), 1)                                   # Shorts are not episodes; cached after
        self.assertEqual(first["ep1"]["theme_tags"], ["status/envy", "love/intimacy"])
        self.assertEqual(again["ep1"]["opening_device"], "question")

    def test_gui_endpoints(self):
        import config
        import gui
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "OUTPUT_DIR", temp), \
                patch.object(config, "EPISODE_PROFILES_FILE", str(Path(temp, "p.json")), create=True), \
                patch("uploader.publications.load_ledger", return_value={"publications": []}):
            atomic_write_json(Path(temp, "story_brief.json"), {"psychological_theme": "grief", "theme_tags": ["loss/grief"]})
            client = gui.app.test_client()
            report = client.get("/api/variation").get_json()
            bad = client.post("/api/variation/profile", json={"video_id": "x", "opening_device": "song"})
        self.assertEqual(report["profile"]["primary"], "loss/grief")
        self.assertEqual(bad.status_code, 400)
        page = Path("gui_templates/index.html").read_text(encoding="utf-8")
        for text in ("loadVariation()", "parecido com:", "plan.cadence_text"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
