"""Загрузка наборов у организаторов (GET по group_id) — через сервер, без CORS."""
from __future__ import annotations

import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import get_settings


class OrganizersAPIError(RuntimeError):
    pass


def build_url(base_url: str, group_id: str) -> str:
    parts = urlsplit(base_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "group_id"]
    query.append(("group_id", group_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def raw_cache_path(group_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", group_id).lower()
    return Path(get_settings().raw_data_dir) / f"{safe}_dataset.json"


def fetch_dataset(group_id: str, timeout: float = 120.0) -> dict[str, Any]:
    """GET у организаторов. Ответ сохраняется в data/raw/<group>_dataset.json как есть."""
    settings = get_settings()
    if not settings.organizers_api_url:
        raise OrganizersAPIError("Не задан ORGANIZERS_API_URL в .env")
    url = build_url(settings.organizers_api_url, group_id)
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise OrganizersAPIError(f"API организаторов недоступен: {exc}") from exc
    if resp.status_code != 200:
        raise OrganizersAPIError(f"API организаторов вернул HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OrganizersAPIError("API организаторов вернул не JSON") from exc
    if not payload.get("isSuccess", False):
        raise OrganizersAPIError(f"API организаторов: isSuccess=false, error={payload.get('error')!r}")
    path = raw_cache_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_cached_dataset(group_id: str) -> dict[str, Any] | None:
    path = raw_cache_path(group_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


# --------------------------------------------------------------------------- аудио
_SOURCE_RE = re.compile(r"<source[^>]+src=\"([^\"]+)\"", re.I)
_AUDIO_RE = re.compile(r"<audio[^>]+src=\"([^\"]+)\"", re.I)
_audio_cache: dict[str, tuple[str, float]] = {}


def resolve_audio_src(audio_url: str, timeout: float = 30.0) -> str:
    """audio_url у организаторов — HTML-страница с плеером; mp3-ссылка внутри живёт ~1 час.

    Каждый раз берём свежую ссылку (кэш 40 минут). Если по адресу сразу аудио — отдаём его.
    """
    cached = _audio_cache.get(audio_url)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        with httpx.stream("GET", audio_url, timeout=timeout, follow_redirects=True) as resp:
            ctype = resp.headers.get("content-type", "")
            if ctype.startswith("audio/") or ctype.startswith("video/"):
                src = str(resp.url)  # прямая ссылка на файл — тело не скачиваем
                html = ""
            else:
                src = ""
                html = resp.read().decode(resp.encoding or "utf-8", errors="replace")
    except httpx.HTTPError as exc:
        raise OrganizersAPIError(f"Страница записи недоступна: {exc}") from exc
    if not src:
        m = _SOURCE_RE.search(html) or _AUDIO_RE.search(html)
        if not m:
            raise OrganizersAPIError("На странице записи не найден аудиофайл")
        src = unescape(m.group(1))
    _audio_cache[audio_url] = (src, time.time() + 40 * 60)
    return src
