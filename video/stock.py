"""Stock-image acquisition with Pixabay, Openverse and local fallbacks.

Public contract:
    download_images(script, output_dir) -> {section_id: images}

Shorts always receive lists. Regular videos receive a list of JPEG paths or
``None`` per section, matching the renderer's existing B-roll contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

import config
from video import stock_scoring as scoring


PIXABAY_SEARCH = "https://pixabay.com/api/"
OPENVERSE_SEARCH = "https://api.openverse.org/v1/images/"
CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_PROVIDER_SEARCHES_PER_JOB = 40
MIN_LANDSCAPE = (640, 360)
MIN_PORTRAIT = (360, 640)
ALLOWED_OPENVERSE_LICENSES = {"cc0", "pdm"}
FALLBACK_QUERIES = (
    "cinematic psychological story",
    "moody abstract human silhouette",
)
PEOPLE_TERMS = {
    "person", "people", "man", "woman", "boy", "girl", "face", "portrait",
    "couple", "family", "crowd", "human", "businessman", "businesswoman",
}


class StockError(RuntimeError):
    """A sanitized stock-provider error safe to show in logs and the GUI."""


class ProviderUnavailable(StockError):
    pass


@dataclass
class StockAsset:
    provider: str
    asset_id: str
    creator: str = ""
    source_url: str = ""
    license: str = ""
    license_url: str = ""
    download_url: str = ""
    width: int = 0
    height: int = 0
    asset_type: str = "image"
    tags: str = ""
    upstream_provider: str = ""
    upstream_source: str = ""
    downloaded_at: str = ""
    local_path: str = ""
    manual_review_required: bool = False
    review_reason: str = ""
    title: str = ""
    selection: dict = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        return f"{self.provider}:{self.asset_id}"

    def public_metadata(self) -> dict:
        data = asdict(self)
        data.pop("download_url", None)
        data["source_url"] = _redact_credential_values(self.source_url)
        data["license_url"] = _redact_credential_values(self.license_url)
        data["original_download_url"] = _redact_credential_values(self.download_url)
        return data

    @classmethod
    def from_cache(cls, data: dict) -> "StockAsset":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in data.items() if key in allowed})


class StockProvider:
    name = "provider"

    def search(self, query: str, orientation: str, count: int = 12) -> list[StockAsset]:
        raise NotImplementedError


class _HttpProvider(StockProvider):
    def __init__(self, session=None, retries: int = 2,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self.session = session or requests.Session()
        self.retries = max(0, retries)
        self.sleep_fn = sleep_fn

    def _get_json(self, url: str, *, params: dict) -> dict:
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=(5, 15))
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < self.retries:
                    self.sleep_fn(0.25 * (attempt + 1))
                    continue
                raise ProviderUnavailable(f"{self.name} is temporarily unreachable") from None

            status = response.status_code
            if status in (401, 403):
                raise ProviderUnavailable(f"{self.name} rejected the configured credentials")
            if status == 429 or 500 <= status < 600:
                if attempt < self.retries:
                    retry_after = response.headers.get("Retry-After", "0")
                    try:
                        delay = min(float(retry_after), 2.0)
                    except (TypeError, ValueError):
                        delay = 0.25 * (attempt + 1)
                    self.sleep_fn(max(delay, 0.05))
                    continue
                message = "rate limit exceeded" if status == 429 else "service unavailable"
                raise ProviderUnavailable(f"{self.name} {message}")
            if status >= 400:
                # Pixabay commonly reports an invalid key as 400. Never include
                # response text/request URLs because they may contain the key.
                raise ProviderUnavailable(f"{self.name} rejected the request (HTTP {status})")
            try:
                payload = response.json()
            except (ValueError, TypeError) as exc:
                raise ProviderUnavailable(f"{self.name} returned malformed data") from exc
            if not isinstance(payload, dict):
                raise ProviderUnavailable(f"{self.name} returned malformed data")
            return payload
        raise ProviderUnavailable(f"{self.name} request failed")


class PixabayProvider(_HttpProvider):
    name = "pixabay"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = (api_key or "").strip()

    def search(self, query: str, orientation: str, count: int = 12) -> list[StockAsset]:
        if not _valid_key(self.api_key):
            raise ProviderUnavailable("pixabay API key is not configured")
        payload = self._get_json(PIXABAY_SEARCH, params={
            "key": self.api_key,
            "q": query,
            "lang": "en",
            "image_type": "all",
            "orientation": "vertical" if orientation == "portrait" else "horizontal",
            "safesearch": "true",
            "per_page": min(max(count, 3), 50),
            "page": 1,
            "min_width": MIN_PORTRAIT[0] if orientation == "portrait" else MIN_LANDSCAPE[0],
            "min_height": MIN_PORTRAIT[1] if orientation == "portrait" else MIN_LANDSCAPE[1],
        })
        hits = payload.get("hits", [])
        if not isinstance(hits, list):
            raise ProviderUnavailable("pixabay returned malformed data")
        assets = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            download_url = hit.get("largeImageURL") or hit.get("webformatURL")
            asset_id = hit.get("id")
            source_url = hit.get("pageURL")
            if not download_url or asset_id is None or not source_url:
                continue
            tags = str(hit.get("tags") or "")
            asset_type = str(hit.get("type") or "image")
            needs_review = asset_type == "photo" and bool(
                PEOPLE_TERMS.intersection(re.findall(r"[a-z]+", tags.lower()))
            )
            assets.append(StockAsset(
                provider=self.name,
                asset_id=str(asset_id),
                creator=str(hit.get("user") or ""),
                source_url=str(source_url),
                license="Pixabay Content License",
                license_url="https://pixabay.com/service/license-summary/",
                download_url=str(download_url),
                width=_as_int(hit.get("imageWidth")),
                height=_as_int(hit.get("imageHeight")),
                asset_type=asset_type,
                tags=tags,
                upstream_provider="pixabay",
                upstream_source="pixabay",
                manual_review_required=needs_review,
                review_reason=(
                    "A photographic asset may depict a recognizable person; review fictional/sensitive context manually."
                    if needs_review else ""
                ),
            ))
        return assets


class OpenverseProvider(_HttpProvider):
    name = "openverse"

    def search(self, query: str, orientation: str, count: int = 12) -> list[StockAsset]:
        payload = self._get_json(OPENVERSE_SEARCH, params={
            "q": query,
            "page_size": min(max(count, 1), 20),
            "license": "cc0,pdm",
            "aspect_ratio": "tall" if orientation == "portrait" else "wide",
            "mature": "false",
            "filter_dead": "true",
        })
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise ProviderUnavailable("openverse returned malformed data")
        assets = []
        for item in results:
            if not isinstance(item, dict):
                continue
            license_code = str(item.get("license") or "").strip().lower()
            license_url = str(item.get("license_url") or "").strip()
            if license_code not in ALLOWED_OPENVERSE_LICENSES or not license_url:
                continue
            download_url = item.get("url")
            asset_id = item.get("id")
            source_url = item.get("foreign_landing_url") or item.get("detail_url")
            if not download_url or not asset_id or not source_url:
                continue
            tags = item.get("tags") or []
            if isinstance(tags, list):
                tags = ", ".join(
                    str(tag.get("name") if isinstance(tag, dict) else tag) for tag in tags
                )
            tags = str(tags)
            needs_review = bool(PEOPLE_TERMS.intersection(re.findall(r"[a-z]+", tags.lower())))
            assets.append(StockAsset(
                provider=self.name,
                asset_id=str(asset_id),
                creator=str(item.get("creator") or ""),
                source_url=str(source_url),
                license=license_code.upper(),
                license_url=license_url,
                download_url=str(download_url),
                width=_as_int(item.get("width")),
                height=_as_int(item.get("height")),
                asset_type="image",
                tags=tags,
                title=str(item.get("title") or ""),
                upstream_provider=str(item.get("provider") or "openverse"),
                upstream_source=str(item.get("source") or ""),
                manual_review_required=needs_review,
                review_reason=(
                    "Asset tags suggest a person may be recognizable; review fictional/sensitive context manually."
                    if needs_review else ""
                ),
            ))
        return assets


@dataclass
class StockManager:
    output_dir: str
    session: object = None
    now_fn: Callable[[], float] = time.time
    sleep_fn: Callable[[float], None] = time.sleep
    provider_name: str = field(default_factory=lambda: str(getattr(config, "STOCK_PROVIDER", "auto")))
    fallback_name: str = field(default_factory=lambda: str(getattr(config, "STOCK_FALLBACK_PROVIDER", "openverse")))
    cache_enabled: bool = field(default_factory=lambda: bool(getattr(config, "STOCK_CACHE_ENABLED", True)))
    license_log_enabled: bool = field(default_factory=lambda: bool(getattr(config, "STOCK_LICENSE_LOG_ENABLED", True)))
    local_asset_dir: str = field(default_factory=lambda: str(getattr(config, "STOCK_LOCAL_ASSET_DIR", "assets/stock_approved")))
    character_names: tuple = ()

    def __post_init__(self):
        self.output_path = Path(self.output_dir)
        self.images_dir = self.output_path / "images"
        self.cache_dir = self.output_path / ".stock_cache"
        self.search_cache_dir = self.cache_dir / "search"
        self.asset_cache_dir = self.cache_dir / "assets"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        if self.cache_enabled:
            self.search_cache_dir.mkdir(parents=True, exist_ok=True)
            self.asset_cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = self.session or requests.Session()
        self.used_ids: set[str] = set()
        self.used_hashes: set[str] = set()
        self.manifest_assets: list[dict] = []
        self.section_status: list[dict] = []
        self.provider_search_counts: dict[str, int] = {}
        self.rejections: list[dict] = []
        self._providers = {
            "pixabay": PixabayProvider(
                getattr(config, "PIXABAY_API_KEY", ""), session=self.session, sleep_fn=self.sleep_fn
            ),
            "openverse": OpenverseProvider(session=self.session, sleep_fn=self.sleep_fn),
        }

    def provider_order(self) -> list[str]:
        if not enabled():
            return []                 # STOCK_ENABLED is false: never query Pixabay/Openverse
        selected = self.provider_name.strip().lower()
        fallback = self.fallback_name.strip().lower()
        if selected == "openverse":
            return ["openverse"]
        if selected == "pixabay":
            order = ["pixabay"]
        else:
            order = ["pixabay"] if _valid_key(getattr(config, "PIXABAY_API_KEY", "")) else []
        if fallback in self._providers and fallback not in order:
            order.append(fallback)
        return order

    def acquire(self, query: str, orientation: str, section_id: int,
                image_number: int, topic_hint: str = "") -> Optional[str]:
        target = self.images_dir / f"section_{int(section_id):02d}_img{image_number:02d}.jpg"

        local_asset = self._find_local_asset(query, orientation)
        if local_asset:
            asset = StockAsset(
                provider="local-approved",
                asset_id=hashlib.sha256(str(local_asset.resolve()).encode()).hexdigest()[:16],
                creator="Local approved asset library",
                source_url=str(local_asset),
                license="Manually approved local asset",
                license_url="",
                download_url=str(local_asset),
            )
            accepted = self._normalize_file(local_asset, target, orientation)
            if accepted and self._accept_unique(asset, target):
                self._record(asset, target, section_id, query)
                return str(target)

        names = tuple(self.character_names)
        character_shot = scoring.is_character_shot(query, names)
        attempts = [*scoring.concise_queries(query, names), *FALLBACK_QUERIES]
        min_relevance = float(getattr(config, "STOCK_MIN_RELEVANCE", 0.4))
        rejected = 0
        for provider_name in self.provider_order():
            provider = self._providers[provider_name]
            for attempt_query in attempts:
                try:
                    candidates = self._search(provider, attempt_query, orientation)
                except ProviderUnavailable as exc:
                    print(f"   WARNING: {exc}")
                    break
                for asset in candidates:
                    if asset.unique_id in self.used_ids:
                        continue
                    score = scoring.relevance(attempt_query, asset.tags, asset.title)
                    reason = scoring.tag_rejection(asset.tags, asset.title,
                                                   character_shot=character_shot)
                    if reason is None and score < min_relevance:
                        reason = f"relevance {score:.2f} < {min_relevance:g} for '{attempt_query}'"
                    if reason:
                        rejected += 1
                        self._reject(asset, section_id, reason)
                        continue
                    cached = self.asset_cache_dir / f"{asset.provider}_{_safe_id(asset.asset_id)}.jpg"
                    if self.cache_enabled:
                        if cached.exists() and self._normalize_file(cached, target, orientation):
                            pass
                        elif not self._download_and_validate(asset, cached, orientation):
                            continue
                        elif not self._normalize_file(cached, target, orientation):
                            continue
                    elif not self._download_and_validate(asset, target, orientation):
                        continue
                    image_checks = self._image_checks(target)
                    if image_checks.get("rejection"):
                        rejected += 1
                        self._reject(asset, section_id, image_checks["rejection"])
                        target.unlink(missing_ok=True)
                        continue
                    if not self._accept_unique(asset, target):
                        target.unlink(missing_ok=True)
                        continue
                    asset.selection = {
                        "query_variant": attempt_query, "relevance": round(score, 2),
                        "character_shot": character_shot, "rejected_candidates": rejected,
                        **{key: value for key, value in image_checks.items() if key != "rejection"},
                    }
                    self._record(asset, target, section_id, query)
                    return str(target)
        return None

    def _image_checks(self, path: Path) -> dict:
        """Tone and text checks on the normalised file; adds 'rejection' when it fails."""
        try:
            with Image.open(path) as image:
                measured = scoring.tone(image)
                measured["text_likelihood"] = scoring.text_likelihood(image)
                reason = scoring.tone_rejection(measured) or scoring.text_rejection(image)
        except (OSError, UnidentifiedImageError):
            return {"rejection": "unreadable image"}
        if reason:
            measured["rejection"] = reason
        return measured

    def _reject(self, asset: StockAsset, section_id: int, reason: str):
        if len(self.rejections) < 500:
            self.rejections.append({"section_id": section_id, "asset": asset.unique_id,
                                    "tags": asset.tags[:120], "reason": reason})

    def mark_section(self, section_id: int, paths: list[str], expected: int):
        if not paths:
            status = "rendering_fallback"
        elif len(paths) < expected:
            status = "partial"
        else:
            status = "complete"
        self.section_status.append({
            "section_id": section_id,
            "valid_assets": len(paths),
            "expected_assets": expected,
            "status": status,
            "used_rendering_fallback": not bool(paths),
        })

    def write_manifest(self):
        manifest = {
            "generated_at": _utc_now(),
            "legal_notice": (
                "License metadata records provider-supplied provenance and is not definitive legal clearance. "
                "Review recognizable people, trademarks and sensitive fictional contexts before publication."
            ),
            "provider_selection": self.provider_name,
            "fallback_provider": self.fallback_name,
            "sections": self.section_status,
            "assets": self.manifest_assets if self.license_log_enabled else [],
            "rejected_candidates": self.rejections,
        }
        path = self.output_path / "asset_manifest.json"
        # Music and generated-image provenance are written by other stages; keep them.
        try:
            previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            previous = {}
        for key in ("music", "generated_assets", "synthetic_media"):
            if key in previous:
                manifest[key] = previous[key]
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    def _search(self, provider: StockProvider, query: str, orientation: str) -> list[StockAsset]:
        key = hashlib.sha256(f"{provider.name}|{orientation}|{query.lower()}".encode()).hexdigest()
        cache_path = self.search_cache_dir / f"{key}.json"
        if self.cache_enabled and cache_path.exists():
            age = self.now_fn() - cache_path.stat().st_mtime
            if age <= CACHE_TTL_SECONDS:
                try:
                    raw = json.loads(cache_path.read_text(encoding="utf-8"))
                    return [StockAsset.from_cache(item) for item in raw.get("assets", [])]
                except (OSError, ValueError, TypeError):
                    pass
        searches = self.provider_search_counts.get(provider.name, 0)
        if searches >= MAX_PROVIDER_SEARCHES_PER_JOB:
            raise ProviderUnavailable(f"{provider.name} per-job search limit reached")
        self.provider_search_counts[provider.name] = searches + 1
        assets = provider.search(query, orientation, count=20)
        if self.cache_enabled:
            payload = {"cached_at": _utc_now(), "assets": [asdict(asset) for asset in assets]}
            cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return assets

    def _download_and_validate(self, asset: StockAsset, cache_path: Path, orientation: str) -> bool:
        response = None
        for attempt in range(3):
            try:
                response = self.session.get(asset.download_url, timeout=(5, 30), stream=True)
            except (requests.Timeout, requests.ConnectionError):
                if attempt < 2:
                    self.sleep_fn(0.25 * (attempt + 1))
                    continue
                return False
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < 2:
                    self.sleep_fn(0.25 * (attempt + 1))
                    continue
                return False
            break
        if response is None or response.status_code != 200:
            return False
        try:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if content_type and not content_type.startswith("image/"):
                return False
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if chunk:
                    content.extend(chunk)
                    if len(content) > MAX_DOWNLOAD_BYTES:
                        return False
            if not content:
                return False
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(BytesIO(content)) as image:
                image.load()
                image = ImageOps.exif_transpose(image)
                if not _usable_dimensions(image.width, image.height, orientation):
                    return False
                image.convert("RGB").save(cache_path, "JPEG", quality=92, optimize=True)
            return True
        except (requests.RequestException, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            return False

    def _normalize_file(self, source: Path, target: Path, orientation: str) -> bool:
        try:
            with Image.open(source) as image:
                image.load()
                image = ImageOps.exif_transpose(image)
                if not _usable_dimensions(image.width, image.height, orientation):
                    return False
                image.convert("RGB").save(target, "JPEG", quality=92, optimize=True)
            return True
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            return False

    def _accept_unique(self, asset: StockAsset, path: Path) -> bool:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if asset.unique_id in self.used_ids or digest in self.used_hashes:
            return False
        self.used_ids.add(asset.unique_id)
        self.used_hashes.add(digest)
        return True

    def _record(self, asset: StockAsset, path: Path, section_id: int, query: str):
        asset.downloaded_at = _utc_now()
        asset.local_path = str(path)
        entry = asset.public_metadata()
        entry.update({"section_id": section_id, "query": query, "downloaded_filename": path.name})
        self.manifest_assets.append(entry)

    def _find_local_asset(self, query: str, orientation: str) -> Optional[Path]:
        root = Path(self.local_asset_dir)
        if not root.is_dir():
            return None
        query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        ranked = []
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            name_terms = set(re.findall(r"[a-z0-9]+", path.stem.lower()))
            score = len(query_terms.intersection(name_terms))
            if score:
                ranked.append((score, path))
        for _, path in sorted(ranked, key=lambda item: (-item[0], str(item[1]))):
            try:
                with Image.open(path) as image:
                    if _usable_dimensions(image.width, image.height, orientation):
                        asset_id = "local-approved:" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
                        if asset_id not in self.used_ids:
                            return path
            except (OSError, UnidentifiedImageError):
                continue
        return None


def enabled() -> bool:
    """Stock imagery is opt-in (``STOCK_ENABLED``); off by default."""
    return bool(getattr(config, "STOCK_ENABLED", False))


def download_images(script: dict, output_dir: str) -> dict:
    """Download normalized local JPEGs while preserving renderer contracts."""
    if not enabled():
        print("   -> Stock imagery is disabled (STOCK_ENABLED=false); no Pixabay/Openverse request is made")
        return {section["id"]: None for section in script.get("sections", [])}
    names =tuple(str(character.get("name", "")) for character in script.get("characters", [])
                  if isinstance(character, dict) and character.get("name"))
    manager = StockManager(output_dir, character_names=names)
    is_shorts = script.get("video_type") == "shorts"
    orientation = "portrait" if is_shorts else "landscape"
    raw_title = str(script.get("title") or "")
    topic_hint = " ".join(raw_title.split()[:4])
    image_map = {}

    for section in script.get("sections", []):
        sid = section["id"]
        queries = [str(section.get("image_query") or "cinematic abstract story")]
        for key in ("image_query_2", "image_query_3", "image_query_4"):
            value = str(section.get(key) or "").strip()
            if value:
                queries.append(value)
        queries = queries if is_shorts else queries[:3]
        print(f"   -> [{sid}] stock: {len(queries)} bounded queries ({orientation})")
        paths = []
        for number, query in enumerate(queries, start=1):
            path = manager.acquire(query, orientation, sid, number, topic_hint)
            if path:
                paths.append(path)
        image_map[sid] = paths if (is_shorts or paths) else None
        manager.mark_section(sid, paths, len(queries))
        if paths:
            print(f"         saved {len(paths)} validated image(s)")
        else:
            print("         no valid assets; renderer will use its visual fallback")

    manager.write_manifest()
    return image_map


def load_existing_images(script: dict, output_dir: str) -> dict:
    """Rebuild an image map from downloader filenames for GUI partial reruns."""
    images_dir = Path(output_dir) / "images"
    is_shorts = script.get("video_type") == "shorts"
    image_map = {}
    for section in script.get("sections", []):
        sid = int(section["id"])
        paths = [str(path) for path in sorted(images_dir.glob(f"section_{sid:02d}_img*.jpg"))]
        image_map[sid] = paths if (is_shorts or paths) else None
    return image_map


def _usable_dimensions(width: int, height: int, orientation: str) -> bool:
    minimum = MIN_PORTRAIT if orientation == "portrait" else MIN_LANDSCAPE
    if width < minimum[0] or height < minimum[1]:
        return False
    return height > width if orientation == "portrait" else width > height


def _valid_key(value: str) -> bool:
    value = str(value or "").strip()
    return bool(value and not value.startswith("YOUR_") and "your_" not in value.lower())


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:80]


def _redact_credential_values(value: str) -> str:
    redacted = str(value or "")
    for candidate in (
        getattr(config, "PIXABAY_API_KEY", ""),
        getattr(config, "GEMINI_API_KEY", ""),
        getattr(config, "ELEVENLABS_API_KEY", ""),
    ):
        if _valid_key(candidate):
            redacted = redacted.replace(str(candidate), "[REDACTED]")
    return redacted


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
