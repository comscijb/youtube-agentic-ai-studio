import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

import config
from video.stock import (
    CACHE_TTL_SECONDS,
    OpenverseProvider,
    PixabayProvider,
    ProviderUnavailable,
    StockAsset,
    StockManager,
    download_images,
    load_existing_images,
)


def image_bytes(size=(1280, 720), color=(30, 40, 50), fmt="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b"", headers=None, json_error=None):
        self.status_code = status
        self._payload = payload
        self._content = content
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start:start + chunk_size]


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, kwargs)


def pixabay_hit(asset_id="10", url="https://cdn.example/10.jpg", width=1280, height=720):
    return {
        "id": asset_id,
        "largeImageURL": url,
        "pageURL": f"https://pixabay.com/photos/{asset_id}/",
        "user": "Photographer",
        "imageWidth": width,
        "imageHeight": height,
        "type": "photo",
        "tags": "cinematic, landscape",
    }


def openverse_item(asset_id="ov-1", url="https://media.example/ov.jpg", license_code="cc0"):
    return {
        "id": asset_id,
        "url": url,
        "foreign_landing_url": f"https://source.example/{asset_id}",
        "creator": "Creator",
        "license": license_code,
        "license_url": f"https://creativecommons.org/publicdomain/{license_code}/1.0/",
        "width": 1280,
        "height": 720,
        "tags": [{"name": "abstract"}],
    }


class ProviderTests(unittest.TestCase):
    def test_successful_pixabay_search_and_download(self):
        def handler(url, kwargs):
            if "pixabay.com/api" in url:
                return FakeResponse(payload={"hits": [pixabay_hit()]})
            return FakeResponse(content=image_bytes(), headers={"Content-Type": "image/jpeg"})

        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "PIXABAY_API_KEY", "test-key"):
            manager = StockManager(tmp, session=FakeSession(handler), sleep_fn=lambda _: None,
                                   provider_name="pixabay", fallback_name="none")
            path = manager.acquire("cinematic landscape", "landscape", 1, 1)
            self.assertTrue(Path(path).is_file())
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")

    def test_successful_openverse_fallback(self):
        def handler(url, kwargs):
            if "pixabay.com/api" in url:
                return FakeResponse(payload={"hits": []})
            if "openverse.org" in url:
                return FakeResponse(payload={"results": [openverse_item()]})
            return FakeResponse(content=image_bytes(), headers={"Content-Type": "image/jpeg"})

        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "PIXABAY_API_KEY", "test-key"):
            session = FakeSession(handler)
            manager = StockManager(tmp, session=session, sleep_fn=lambda _: None,
                                   provider_name="auto", fallback_name="openverse")
            path = manager.acquire("abstract", "landscape", 2, 1)
            self.assertTrue(Path(path).exists())
            self.assertEqual(manager.manifest_assets[0]["provider"], "openverse")

    def test_missing_pixabay_api_key(self):
        provider = PixabayProvider("", session=FakeSession(lambda *_: None))
        with self.assertRaisesRegex(ProviderUnavailable, "not configured"):
            provider.search("story", "landscape")

    def test_invalid_key_is_sanitized(self):
        secret = "super-secret-key"
        session = FakeSession(lambda *_: FakeResponse(status=401))
        provider = PixabayProvider(secret, session=session, sleep_fn=lambda _: None)
        with self.assertRaises(ProviderUnavailable) as caught:
            provider.search("story", "landscape")
        self.assertNotIn(secret, str(caught.exception))

    def test_rate_limit_exceeded(self):
        session = FakeSession(lambda *_: FakeResponse(status=429, headers={"Retry-After": "0"}))
        provider = OpenverseProvider(session=session, sleep_fn=lambda _: None)
        with self.assertRaisesRegex(ProviderUnavailable, "rate limit exceeded"):
            provider.search("story", "landscape")
        self.assertEqual(len(session.calls), 3)

    def test_empty_search_results(self):
        session = FakeSession(lambda *_: FakeResponse(payload={"hits": []}))
        self.assertEqual(PixabayProvider("key", session=session).search("x", "landscape"), [])

    def test_network_timeout(self):
        def timeout(*_):
            raise requests.Timeout("socket timed out")
        provider = OpenverseProvider(session=FakeSession(timeout), sleep_fn=lambda _: None)
        with self.assertRaisesRegex(ProviderUnavailable, "unreachable"):
            provider.search("x", "landscape")

    def test_malformed_api_response(self):
        provider = OpenverseProvider(
            session=FakeSession(lambda *_: FakeResponse(payload=None)), sleep_fn=lambda _: None
        )
        with self.assertRaisesRegex(ProviderUnavailable, "malformed"):
            provider.search("x", "landscape")

    def test_horizontal_and_vertical_requests(self):
        session = FakeSession(lambda *_: FakeResponse(payload={"hits": []}))
        provider = PixabayProvider("key", session=session)
        provider.search("x", "landscape")
        provider.search("x", "portrait")
        self.assertEqual(session.calls[0][1]["params"]["orientation"], "horizontal")
        self.assertEqual(session.calls[1][1]["params"]["orientation"], "vertical")
        self.assertEqual(session.calls[0][1]["params"]["safesearch"], "true")

    def test_openverse_license_filtering(self):
        items = [
            openverse_item("ok-cc0", license_code="cc0"),
            openverse_item("ok-pdm", license_code="pdm"),
            openverse_item("reject", license_code="by"),
            {**openverse_item("missing", license_code="cc0"), "license_url": ""},
        ]
        provider = OpenverseProvider(session=FakeSession(lambda *_: FakeResponse(payload={"results": items})))
        self.assertEqual([asset.asset_id for asset in provider.search("x", "landscape")], ["ok-cc0", "ok-pdm"])


class ManagerTests(unittest.TestCase):
    def test_corrupt_image_download_is_rejected(self):
        session = FakeSession(lambda *_: FakeResponse(
            content=b"not an image", headers={"Content-Type": "image/jpeg"}
        ))
        with tempfile.TemporaryDirectory() as tmp:
            manager = StockManager(tmp, session=session, cache_enabled=False)
            asset = StockAsset(provider="test", asset_id="1", download_url="https://media.example/x")
            self.assertFalse(manager._download_and_validate(asset, Path(tmp) / "x.jpg", "landscape"))

    def test_cache_hit_and_expiration(self):
        class DummyProvider:
            name = "dummy"
            calls = 0
            def search(self, query, orientation, count=20):
                self.calls += 1
                return [StockAsset(provider="dummy", asset_id=str(self.calls), download_url="x")]

        with tempfile.TemporaryDirectory() as tmp:
            manager = StockManager(tmp, cache_enabled=True)
            provider = DummyProvider()
            first = manager._search(provider, "query", "landscape")
            second = manager._search(provider, "query", "landscape")
            self.assertEqual(provider.calls, 1)
            self.assertEqual(first[0].asset_id, second[0].asset_id)
            cache_file = next(manager.search_cache_dir.glob("*.json"))
            expired = manager.now_fn() - CACHE_TTL_SECONDS - 5
            os.utime(cache_file, (expired, expired))
            third = manager._search(provider, "query", "landscape")
            self.assertEqual(provider.calls, 2)
            self.assertEqual(third[0].asset_id, "2")

    def test_duplicate_content_prevention(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = StockManager(tmp)
            first = Path(tmp) / "first.jpg"
            second = Path(tmp) / "second.jpg"
            data = image_bytes()
            first.write_bytes(data)
            second.write_bytes(data)
            self.assertTrue(manager._accept_unique(StockAsset("p", "1"), first))
            self.assertFalse(manager._accept_unique(StockAsset("p", "2"), second))

    def test_manifest_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = StockManager(tmp)
            path = manager.images_dir / "section_01_img01.jpg"
            path.write_bytes(image_bytes())
            asset = StockAsset("openverse", "id", creator="C", source_url="https://source",
                               license="CC0", license_url="https://license")
            manager._record(asset, path, 1, "query")
            manager.mark_section(1, [str(path)], 1)
            manager.write_manifest()
            data = json.loads((Path(tmp) / "asset_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(data["assets"][0]["section_id"], 1)
            self.assertEqual(data["assets"][0]["downloaded_filename"], path.name)
            self.assertNotIn("download_url", data["assets"][0])
            self.assertIn("original_download_url", data["assets"][0])

    def test_approved_local_asset_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            approved = Path(tmp) / "approved"
            approved.mkdir()
            (approved / "moody_city.jpg").write_bytes(image_bytes())
            session = FakeSession(lambda *_: self.fail("network should not be used"))
            manager = StockManager(tmp, session=session, local_asset_dir=str(approved),
                                   provider_name="openverse")
            path = manager.acquire("moody city", "landscape", 1, 1)
            self.assertTrue(Path(path).exists())
            self.assertEqual(manager.manifest_assets[0]["provider"], "local-approved")

    def test_provider_order_supports_openverse_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = StockManager(tmp, provider_name="openverse", fallback_name="openverse")
            self.assertEqual(manager.provider_order(), ["openverse"])


class ContractAndSecurityTests(unittest.TestCase):
    def test_regular_and_shorts_contracts(self):
        scripts = [
            ({"sections": [{"id": 1, "image_query": "one"}]}, False),
            ({"video_type": "shorts", "sections": [{"id": 1, "image_query": "one"}]}, True),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = str(Path(tmp) / "fake.jpg")
            with patch.object(StockManager, "acquire", return_value=fake_path):
                for script, is_shorts in scripts:
                    result = download_images(script, tmp)
                    self.assertIsInstance(result[1], list)
                    self.assertEqual(bool(script.get("video_type") == "shorts"), is_shorts)

    def test_regular_none_and_shorts_empty_list_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(StockManager, "acquire", return_value=None):
            regular = download_images({"sections": [{"id": 1, "image_query": "x"}]}, tmp)
            shorts = download_images({"video_type": "shorts", "sections": [{"id": 1, "image_query": "x"}]}, tmp)
            self.assertIsNone(regular[1])
            self.assertEqual(shorts[1], [])

    def test_existing_image_filename_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp) / "images"
            images.mkdir()
            (images / "section_01_img01.jpg").write_bytes(image_bytes())
            script = {"sections": [{"id": 1}]}
            self.assertEqual(len(load_existing_images(script, tmp)[1]), 1)

    def test_credentials_not_exposed_in_logs_or_settings(self):
        secret = "DO-NOT-LEAK-THIS"
        provider = PixabayProvider(
            secret, session=FakeSession(lambda *_: FakeResponse(status=403)), sleep_fn=lambda _: None
        )
        output = io.StringIO()
        with redirect_stdout(output):
            try:
                provider.search("x", "landscape")
            except ProviderUnavailable as exc:
                print(exc)
        self.assertNotIn(secret, output.getvalue())

        import gui
        with patch.object(config, "PIXABAY_API_KEY", secret), patch.object(config, "GEMINI_API_KEY", "GEMINI-SECRET"):
            response = gui.app.test_client().get("/api/settings")
            body = response.get_data(as_text=True)
            self.assertNotIn(secret, body)
            self.assertNotIn("GEMINI-SECRET", body)
            self.assertTrue(response.get_json()["pixabay_configured"])

    def test_saving_pixabay_does_not_overwrite_gemini(self):
        import gui
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("GEMINI_API_KEY=keep-me\nPIXABAY_API_KEY=old\n", encoding="utf-8")
            with patch.object(gui, "ROOT", tmp):
                gui._write_env_settings({"PIXABAY_API_KEY": "new"})
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("GEMINI_API_KEY=keep-me", text)
            self.assertIn("PIXABAY_API_KEY=new", text)


@unittest.skipUnless(
    os.getenv("RUN_PIXABAY_SMOKE_TEST") == "1" and os.getenv("PIXABAY_API_KEY"),
    "Set RUN_PIXABAY_SMOKE_TEST=1 and PIXABAY_API_KEY for the opt-in live smoke test",
)
class PixabayIntegrationSmokeTest(unittest.TestCase):
    def test_one_live_search(self):
        assets = PixabayProvider(os.environ["PIXABAY_API_KEY"], retries=0).search(
            "cinematic night landscape", "landscape", count=3
        )
        self.assertTrue(assets)


if __name__ == "__main__":
    unittest.main()
