"""Why the YouTube data is blocked, and what to do (Phase 4.1-A).

``explain`` turns a Google ``HttpError`` into ``{reason, message, project, fix}`` in plain Portuguese:

* ``accessNotConfigured`` / "has not been used in project … or it is disabled" / ``SERVICE_DISABLED`` — the
  YouTube Analytics API and the YouTube Reporting API must be enabled in the Google Cloud project (direct links
  with the project number taken from the message);
* ``insufficientPermissions`` / a missing scope — authorize again and pick the Private Gravity channel;
* ``forbidden`` with an identity that is not the channel — the authorized account is not the channel's.

``diagnose`` (``python -m scripts.analytics_doctor`` and the GUI's "Diagnóstico de dados") is read-only and never
prints a token: the channel the credentials resolve to (``channels.list(mine=true)``), the granted scopes (from the
credentials object), one minimal Analytics query and one Reporting ``reportTypes.list``, then a checklist.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import config

ENABLE_URL = "https://console.cloud.google.com/apis/library/{api}?project={project}"
APIS = {"youtubeanalytics.googleapis.com": "YouTube Analytics API", "youtubereporting.googleapis.com": "YouTube Reporting API"}


def expected_channel() -> str:
    return str(getattr(config, "EXPECTED_CHANNEL_ID", "UCLXUyBGw-ojvJDAT5RSOjZQ"))


def _payload(error) -> dict:
    content = getattr(error, "content", b"") or b""
    try:
        data = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
    except (ValueError, AttributeError):
        return {}
    return data.get("error") or {} if isinstance(data, dict) else {}


def explain(error) -> dict:
    """{status, reason, message, project, fix} for an HttpError (or any exception)."""
    payload = _payload(error)
    message = str(payload.get("message") or getattr(error, "reason", "") or error)[:500]
    reasons = [str(item.get("reason", "")) for item in payload.get("errors") or [] if isinstance(item, dict)]
    reasons += [str(item.get("reason", "")) for item in payload.get("details") or [] if isinstance(item, dict) and item.get("reason")]
    status = getattr(getattr(error, "resp", None), "status", None) or payload.get("code")
    project = (re.search(r"project[s]?[ /](\d{5,})", message) or [None, None])[1]
    lowered = message.lower()
    if "accessNotConfigured" in reasons or "SERVICE_DISABLED" in reasons or "has not been used in project" in lowered \
            or "it is disabled" in lowered:
        reason = "accessNotConfigured"
        links = [ENABLE_URL.format(api=api, project=project or "SEU_PROJETO") for api in APIS]
        fix = ("Ative a YouTube Analytics API e a YouTube Reporting API no projeto do Google Cloud"
               + (f" {project}" if project else "") + ": " + " e ".join(links) + ". Espere alguns minutos e atualize de novo.")
    elif "insufficientPermissions" in reasons or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reasons or "insufficient" in lowered and "scope" in lowered:
        reason = "insufficientPermissions"
        fix = ("Autorize de novo (botão \"Autorizar de novo\") e, na tela da conta Google, escolha o canal Private Gravity "
               "e marque todas as permissões pedidas.")
    elif "forbidden" in reasons or status == 403:
        reason = "forbidden"
        fix = ("A conta autorizada não é a do canal: escolha o canal Private Gravity na tela de autorização (contas de "
               "marca aparecem como canais separados), ou dê a essa conta acesso ao canal em Studio → Configurações → "
               "Permissões.")
    else:
        reason = reasons[0] if reasons else error.__class__.__name__
        fix = "Veja a mensagem do Google acima; rode python -m scripts.analytics_doctor para o diagnóstico completo."
    return {"status": status, "reason": reason, "message": message, "project": project, "fix": fix}


def _scopes(credentials) -> list[str]:
    return sorted(set(getattr(credentials, "granted_scopes", None) or getattr(credentials, "scopes", None) or []))


def resolve_channel(youtube) -> dict:
    items = youtube.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    return [{"id": item["id"], "title": item.get("snippet", {}).get("title", "")} for item in items]


def diagnose(*, credentials=None, youtube=None, analytics=None, reporting=None, today: date | None = None) -> dict:
    """Read-only checks; every failure carries its parsed reason. Never includes a token."""
    from uploader import analytics as analytics_module
    report = {"expected_channel": expected_channel(), "checks": [], "todo": []}

    def check(name, ok, detail, explained=None):
        report["checks"].append({"name": name, "ok": ok, "detail": detail, **({"error": explained} if explained else {})})
        if explained:
            report["todo"].append(explained["fix"])

    if credentials is None and youtube is None:
        try:
            from uploader.youtube import load_credentials
            credentials = load_credentials(interactive=False)
        except Exception as exc:                         # noqa: BLE001
            check("credenciais", False, str(exc)[:300])
            report["todo"].append("Autorize de novo (\"Autorizar de novo\" em Desempenho, ou python -m scripts.sync_publications).")
            return report
    if credentials is not None:
        scopes = _scopes(credentials)
        missing = [scope for scope in getattr(config, "YOUTUBE_SCOPES", []) if scope not in scopes]
        check("escopos", not missing, ("todos os escopos concedidos" if not missing else "faltam: " + ", ".join(missing)))
        report["scopes"] = scopes
        if missing:
            report["todo"].append("Autorize de novo e aceite todas as permissões (falta escopo).")
    if youtube is None or analytics is None or reporting is None:
        from googleapiclient.discovery import build
        youtube = youtube or build("youtube", "v3", credentials=credentials, cache_discovery=False)
        analytics = analytics or build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
        reporting = reporting or build("youtubereporting", "v1", credentials=credentials, cache_discovery=False)
    try:
        channels = resolve_channel(youtube)
        report["channels"] = channels
        right = any(item["id"] == expected_channel() for item in channels)
        check("canal", right, ", ".join(f"{item['title']} ({item['id']})" for item in channels) or "nenhum canal nesta conta")
        if not right:
            report["todo"].append("A conta autorizada não é a do canal Private Gravity: autorize de novo escolhendo o canal "
                                  f"{expected_channel()} (contas de marca aparecem como canais separados).")
    except Exception as exc:                             # noqa: BLE001
        check("canal", False, "channels.list falhou", explain(exc))
        channels = []
    ids = f"channel=={expected_channel()}" if any(item["id"] == expected_channel() for item in channels) else "channel==MINE"
    end = today or date.today()
    try:
        response = analytics.reports().query(ids=ids, startDate=(end - timedelta(days=7)).isoformat(), endDate=end.isoformat(),
                                             metrics="views").execute()
        total = analytics_module.rows(response)
        check("analytics", True, f"{ids}: {total[0]['views'] if total else 0} views nos últimos 7 dias")
    except Exception as exc:                             # noqa: BLE001
        check("analytics", False, f"consulta mínima ({ids}) falhou", explain(exc))
    try:
        types = reporting.reportTypes().list().execute().get("reportTypes", [])
        check("reporting", True, f"{len(types)} tipos de relatório disponíveis")
    except Exception as exc:                             # noqa: BLE001
        check("reporting", False, "reportTypes.list falhou", explain(exc))
    report["todo"] = list(dict.fromkeys(report["todo"]))
    report["ok"] = all(item["ok"] for item in report["checks"])
    report["ids"] = ids
    return report


def lines(report: dict) -> list[str]:
    out = [f"Canal esperado: Private Gravity ({report['expected_channel']})"]
    for item in report["checks"]:
        out.append(f"[{'ok' if item['ok'] else 'X'}] {item['name']}: {item['detail']}")
        if item.get("error"):
            out.append(f"     motivo: {item['error']['reason']} — {item['error']['message'][:240]}")
    if report["todo"]:
        out.append("O que fazer:")
        out += [f"  - {item}" for item in report["todo"]]
    else:
        out.append("Tudo certo: os dados podem chegar.")
    return out
