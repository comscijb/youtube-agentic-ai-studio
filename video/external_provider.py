"""An optional "hard shot" provider (Phase 3.9.9-F): redo a shot with an external image API, budgeted and never automatic.

The user already fixed the hardest shots by hand in GPT Image; this makes that round trip a supported option. Off unless
``EXTERNAL_IMAGE_PROVIDER`` is set ("openai") and the API key is in the environment (``OPENAI_API_KEY``, never printed).
Each request sends the composed prompt (with the staging phrases) and the character sheets and location reference as
input images; an edit also sends the current image and a mask. The result goes through the import path of 3.9.4-D (fit
to 1920x1080, margin and copy checks, version history, ``source: "external"``) and the identity check.

Budget: a per-project cap in project.json, a running total, prices from config (``EXTERNAL_IMAGE_PRICE``, filled in by
the user: nothing is hard-coded because prices change), a confirmation before every batch.
"""
from __future__ import annotations

import base64
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.narrative import atomic_write_json, load_json

OPENAI_EDITS = "https://api.openai.com/v1/images/edits"
SIZE = "1536x1024"


class ProviderDisabled(RuntimeError):
    """No external provider configured, or its key is missing."""


class BudgetExceeded(RuntimeError):
    """The request would pass the project's cap (or the price is not configured)."""


class OpenAIImages:
    name = "openai"

    def __init__(self, session=None, key: str | None = None):
        self.session = session
        self._key = key

    @property
    def model(self) -> str:
        return str(getattr(config, "EXTERNAL_IMAGE_MODEL", "gpt-image-1"))

    def key(self) -> str:
        return self._key if self._key is not None else os.environ.get("OPENAI_API_KEY", "")

    def available(self) -> bool:
        return bool(self.key())

    def edit(self, prompt: str, images: list, mask=None) -> bytes:
        """One image from the prompt and the input images (the first one is edited when a mask is given)."""
        import requests
        session = self.session or requests
        files = [("image[]", (Path(path).name, open(path, "rb"), "image/png")) for path in images]
        if mask:
            files.append(("mask", (Path(mask).name, open(mask, "rb"), "image/png")))
        try:
            response = session.post(OPENAI_EDITS, headers={"Authorization": f"Bearer {self.key()}"},
                                    data={"model": self.model, "prompt": prompt[:3900], "size": SIZE, "n": "1"},
                                    files=files, timeout=float(getattr(config, "EXTERNAL_IMAGE_TIMEOUT", 300)))
        finally:
            for _, item in files:
                item[1].close()
        if getattr(response, "status_code", 200) >= 400:
            raise RuntimeError(f"{self.name}: HTTP {response.status_code}: {str(getattr(response, 'text', ''))[:200]}")
        data = response.json().get("data") or []
        if not data or not data[0].get("b64_json"):
            raise RuntimeError(f"{self.name}: no image in the answer")
        return base64.b64decode(data[0]["b64_json"])


PROVIDERS = {"openai": OpenAIImages}


def provider(session=None):
    """The configured provider, or ProviderDisabled with the reason (off by default)."""
    name = str(getattr(config, "EXTERNAL_IMAGE_PROVIDER", "") or "").strip().lower()
    if not name:
        raise ProviderDisabled("provedor externo desligado (EXTERNAL_IMAGE_PROVIDER vazio)")
    if name not in PROVIDERS:
        raise ProviderDisabled(f"provedor desconhecido: {name}")
    found = PROVIDERS[name](session=session)
    if not found.available():
        raise ProviderDisabled(f"{name}: chave de API ausente no ambiente")
    return found


def price() -> float:
    return float(getattr(config, "EXTERNAL_IMAGE_PRICE", 0) or 0)


def budget(output) -> dict:
    """{"cap", "spent", "calls"} from project.json (the cap is set per project in the GUI)."""
    data = (load_json(Path(output) / "project.json") or {}).get("external_images") or {}
    return {"cap": float(data.get("cap", getattr(config, "EXTERNAL_IMAGE_BUDGET", 0)) or 0), "spent": float(data.get("spent", 0) or 0),
            "calls": list(data.get("calls") or [])}


def set_cap(output, cap: float) -> dict:
    path = Path(output) / "project.json"
    data = load_json(path) or {}
    data.setdefault("external_images", {}).update(cap=round(max(0.0, float(cap)), 2))
    atomic_write_json(path, data)
    return budget(output)


def estimate(output, count: int) -> dict:
    state = budget(output)
    cost = round(count * price(), 4)
    return {**state, "count": count, "price": price(), "cost": cost, "remaining": round(state["cap"] - state["spent"], 4),
            "allowed": price() > 0 and state["spent"] + cost <= state["cap"]}


def check(output, count: int) -> dict:
    found = estimate(output, count)
    if found["price"] <= 0:
        raise BudgetExceeded("configure EXTERNAL_IMAGE_PRICE (o preço por imagem) antes de usar o provedor externo")
    if not found["allowed"]:
        raise BudgetExceeded(f"passaria do teto do projeto: US$ {found['spent'] + found['cost']:.2f} de US$ {found['cap']:.2f}")
    return found


def _record(output, shot_id: str, cost: float, provider_name: str, model: str) -> None:
    path = Path(output) / "project.json"
    data = load_json(path) or {}
    state = data.setdefault("external_images", {})
    state["spent"] = round(float(state.get("spent", 0) or 0) + cost, 4)
    state.setdefault("calls", []).append({"shot_id": shot_id, "cost": cost, "provider": provider_name, "model": model,
                                          "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()})
    atomic_write_json(path, data)


def redo_shot(output, shot: dict, vb: dict, *, confirmed: bool, edit: bool = False, mask=None, chosen=None, detector=None) -> dict:
    """"Refazer com provedor externo" for one shot. Needs ``confirmed`` (the GUI asked), a configured provider and room in
    the budget. Returns the import action (with the identity scores)."""
    from agents import shot_list as shots_module
    from video import external_images
    from video.qa import fixes
    if not confirmed:
        raise BudgetExceeded("confirme o custo antes de enviar")
    chosen = chosen or provider()
    check(output, 1)
    output = Path(output)
    composed = shots_module.compose_shot(shot, vb, output)
    images = [path for path, slot in zip(composed["references"], composed["slots"]) if slot["kind"] != "style" and Path(path).is_file()]
    current = output / str(shot.get("image_path", "")).replace("\\", "/")
    if edit and current.is_file():
        images = [str(current)] + images
    data = chosen.edit(composed["prompt"], images, mask if edit else None)
    _record(output, shot["shot_id"], price(), chosen.name, getattr(chosen, "model", ""))
    with tempfile.TemporaryDirectory() as temp:
        held = Path(temp) / "external.png"
        held.write_bytes(data)
        detector = detector if detector is not None else fixes._detector()
        action = external_images.import_image(output, shot, held, "external.png", mode="crop", position=0.5, detector=detector)
    shot["external_provider"] = {"provider": chosen.name, "model": getattr(chosen, "model", ""), "cost": price(), "edit": bool(edit),
                                 "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    action["provider"] = chosen.name
    if detector is not None and shot.get("characters"):
        try:
            from video.qa.identity_check import IdentityCheck
            action["identity"] = IdentityCheck(detector, vb, output, []).score(current, shot["characters"], shot_type=shot.get("shot_type", ""))
            fixes.state(shot)["identity"] = action["identity"]
        except Exception:                                # noqa: BLE001 — scoring is advisory here
            pass
    return action
