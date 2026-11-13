"""Fixed publishing slots (Phase 4.0-F).

Each narrative type has a weekly slot in the main audience's time zone (``PUBLISH_SLOTS`` in America/New_York,
editable in Settings): realistic fiction on Tuesday, thought experiments on Thursday, parables on Saturday, 18:00.
The upload dialog proposes "Agendar para o próximo horário do tipo" (uploaded private + ``publishAt``) by default;
"Publicar agora" stays available. Slots are computed in the slot's own time zone, so a daylight-saving change keeps
18:00 local (the UTC offset moves, the wall clock does not).

Shorts start at the episode's slot and spread through the week (``SHORTS_SCHEDULE``); ``spread`` keeps any two of
them at least ``SHORTS_MIN_GAP_HOURS`` (3) apart.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAYS_PT = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
MIN_LEAD = timedelta(hours=1)          # the upload and YouTube's processing need time before publishAt


def settings() -> dict:
    from uploader import discovery_settings
    data = discovery_settings.load()
    return {"slots": data["publish_slots"], "timezone": data["publish_timezone"]}


def parse(slot: str) -> tuple[int, int, int]:
    day, clock = str(slot).strip().upper().split()
    hour, minute = (int(part) for part in clock.split(":"))
    return DAYS.index(day), hour, minute


def next_slot(kind: str, *, now: datetime | None = None, slots: dict | None = None, zone: str | None = None,
              lead: timedelta = MIN_LEAD) -> datetime | None:
    """The next occurrence of the type's slot at least ``lead`` from now, aware in the slot's time zone."""
    config_now = settings() if slots is None or zone is None else {}
    slots = slots if slots is not None else config_now["slots"]
    tz = ZoneInfo(zone or config_now["timezone"])
    if kind not in slots:
        return None
    weekday, hour, minute = parse(slots[kind])
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    for days in range(0, 15):
        day = (now + timedelta(days=days)).date()
        if day.weekday() != weekday:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if candidate - now >= lead:
            return candidate
    return None


def rfc3339(when: datetime) -> str:
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def label(when: datetime) -> str:
    """"quinta 13/11 18:00 (Nova York) · 20:00 aqui"."""
    local = when.astimezone()
    zone = str(getattr(when.tzinfo, "key", "")).split("/")[-1].replace("_", " ")
    return f"{DAYS_PT[when.weekday()]} {when:%d/%m %H:%M} ({zone or 'fuso do horário'}) · {local:%H:%M} aqui"


def spread(times: list, min_gap_hours: float | None = None, *, now: datetime | None = None) -> list:
    """Push each scheduled Short so no two are closer than the minimum gap; ``None`` (public now) counts as ``now``."""
    gap = timedelta(hours=float(min_gap_hours if min_gap_hours is not None else getattr(config, "SHORTS_MIN_GAP_HOURS", 3)))
    found, last = [], None
    for when in times:
        if when is not None and last is not None and when - last < gap:
            when = last + gap
        found.append(when)
        last = when if when is not None else now
    return found
