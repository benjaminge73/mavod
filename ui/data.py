"""
Data layer for the mavod-ui viewer.

Reads result.json files written by mavod.services.workflow_service into
each search folder under torrents/. Read-only except for
download_torrent() which sends a torrent to qBittorrent for manual
override.

Supports two result.json schemas:
- Legacy v1 (no schema_version field): query/torrents/llm_choice_id etc.
- v2 (schema_version=2): title/media_type/candidates/best_choice
  (written by mavod.services.workflow_service.WorkflowService).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

# In Docker: ui files are at /app/, torrents bind-mounted at /app/torrents.
# Locally: ui/ is one level below the project root.
_HERE = Path(__file__).resolve().parent
APP_ROOT = _HERE if (_HERE / "torrents").exists() else _HERE.parent

TORRENTS_ROOT = APP_ROOT / "torrents"

# Make `mavod` importable when running locally outside Docker (Dockerfile.ui
# already COPYs mavod/ + installs requests/bencodepy, so the import works
# in the container without this).
_MAVOD_ROOT = APP_ROOT
if str(_MAVOD_ROOT) not in sys.path:
    sys.path.insert(0, str(_MAVOD_ROOT))


def _extract_year(text: str) -> Optional[int]:
    """Extrait une année 19xx ou 200x/201x/202x depuis une chaîne libre."""
    m = re.search(r"\b(19\d{2}|20[0-2]\d)\b", text)
    return int(m.group(1)) if m else None


def _strip_year_suffix(text: str) -> str:
    """Remove a trailing ' (YYYY)' from a query so the UI can re-add it cleanly."""
    return re.sub(r"\s*\(\s*(?:19\d{2}|20[0-2]\d)\s*\)\s*$", "", text).strip()


def _result_to_ui_dict(result: dict) -> dict:
    """Convert result.json to the dict consumed by main.py.

    Auto-detects v1 (legacy) vs v2 (schema_version=2) format.
    """
    if int(result.get("schema_version", 1)) >= 2:
        return _v2_to_ui_dict(result)
    return _v1_to_ui_dict(result)


def _v1_to_ui_dict(result: dict) -> dict:
    """Legacy schema written by mavod/workflow.py (pre-V2 refactor)."""
    query = result.get("query", "Unknown")
    year = _extract_year(query)
    title = _strip_year_suffix(query) if year else query

    llm_id = result.get("llm_choice_id")
    torrents = result.get("torrents", [])

    ui_torrents = []
    for t in torrents:
        t_id = t.get("id")
        size_gb = t.get("size_gb", 0)
        ui_torrents.append({
            "rank":    t_id,
            "name":    t.get("name", "?"),
            "size":    f"{size_gb:.2f} GB" if size_gb else "—",
            "seeds":   t.get("seeders", 0),
            "indexer": t.get("indexer", ""),
            "dl":      t.get("path") or None,
            "magnet":  t.get("magnet") or None,
            "url":     t.get("torrent_url") or None,
            "llm":     (t_id == llm_id),
        })

    return {
        "id":       result["search_id"],
        "title":    title,
        "year":     year or "",
        "type":     result.get("type", ""),
        "season":   result.get("season"),
        "episode":  result.get("episode"),
        "llm_choice": {
            "rank":              llm_id,
            "name":              result.get("llm_choice_name", ""),
            "reasoning":         result.get("llm_response", ""),
            "reasoning_content": result.get("llm_reasoning", ""),
        },
        "torrents":  ui_torrents,
        "_path":     result.get("_path", ""),
    }


def _v2_to_ui_dict(result: dict) -> dict:
    """New schema written by mavod.services.workflow_service (schema_version=2)."""
    candidates = result.get("candidates", []) or []
    best_choice = result.get("best_choice") or {}
    best_title = best_choice.get("title") or best_choice.get("name", "")

    ui_torrents = []
    for i, c in enumerate(candidates, 1):
        size_gb = c.get("size_gb")
        if size_gb is None and c.get("size"):
            size_gb = round(c["size"] / (1024 ** 3), 2)
        ui_torrents.append({
            "rank":    i,
            "name":    c.get("title") or c.get("name", "?"),
            "size":    f"{size_gb:.2f} GB" if size_gb else "—",
            "seeds":   c.get("seeders", 0),
            "indexer": c.get("indexer", ""),
            "dl":      c.get("path") or None,
            "magnet":  c.get("magnet") or None,
            "url":     c.get("torrent_url") or None,
            "llm":     bool(best_title and c.get("title") == best_title),
        })

    return {
        "id":       result["search_id"],
        "title":    result.get("title", ""),
        "year":     result.get("year") or "",
        "type":     result.get("media_type", result.get("type", "")),
        "season":   result.get("season"),
        "episode":  result.get("episode"),
        "llm_choice": {
            "rank":              None,
            "name":              best_title,
            "reasoning":         result.get("llm_response", ""),
            "reasoning_content": result.get("llm_reasoning", ""),
        },
        "torrents":  ui_torrents,
        "_path":     result.get("_path", ""),
    }


# ── Cache ────────────────────────────────────────────────────────────────────

_cache: Optional[dict] = None
_cache_mtime: float = 0.0


def invalidate_cache() -> None:
    """Force le rechargement au prochain accès (bouton `↻` de l'UI)."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = 0.0


def _newest_mtime() -> float:
    """Return the most recent mtime across TORRENTS_ROOT and its subfolders.

    New searches show up as new subdirs (mkdir bumps TORRENTS_ROOT mtime);
    we also walk subdirs in case a result.json was rewritten in place.
    """
    if not TORRENTS_ROOT.exists():
        return 0.0
    latest = TORRENTS_ROOT.stat().st_mtime
    for sub in TORRENTS_ROOT.iterdir():
        try:
            if sub.is_dir():
                latest = max(latest, sub.stat().st_mtime)
        except OSError:
            continue
    return latest


def _load_all() -> dict:
    """Scanne `torrents/*/result.json` et retourne {search_id: ui_dict}. Trié desc."""
    if not TORRENTS_ROOT.exists():
        return {}

    result: dict = {}
    for folder in sorted(TORRENTS_ROOT.iterdir(), key=lambda p: p.name, reverse=True):
        if not folder.is_dir() or folder.name.startswith("."):
            continue

        result_path = folder / "result.json"
        if not result_path.exists():
            continue

        try:
            with open(result_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            print(f"[WARN] Could not read {result_path}: {exc}")
            continue

        data["_path"] = str(result_path)
        search_id = data.get("search_id", folder.name)
        result[search_id] = _result_to_ui_dict(data)

    return result


def _get_cache() -> dict:
    """Renvoie le cache, rechargé automatiquement si un result.json a été touché."""
    global _cache, _cache_mtime
    current_mtime = _newest_mtime()
    if _cache is None or current_mtime > _cache_mtime:
        _cache = _load_all()
        _cache_mtime = current_mtime
    return _cache


# ── Public API ───────────────────────────────────────────────────────────────

def get_all_searches() -> dict:
    """Toutes les recherches indexées par search_id (tri desc = plus récente en tête)."""
    return _get_cache()


def display_label(search_id: str) -> str:
    """Label court affiché dans le selectbox de l'UI (ex. `Dune (2021)`)."""
    data  = _get_cache().get(search_id, {})
    title = data.get("title", search_id)
    year  = data.get("year", "")
    return f"{title} ({year})" if year else title


_HTTP_REDIRECT_HOPS = 5


def _resolve_torrent_source(torrent_ui: dict):
    """Résout la source soumissible d'un candidat : magnet > url HTTP > bytes disque.

    Le pipeline V2 ne pose plus de `.torrent` sur disque : le `result.json`
    porte `magnet` et/ou `torrent_url`. On privilégie le magnet, sinon on suit
    la chaîne de redirections de l'URL HTTP (certains indexers répondent 302
    avec `Location: magnet:…`, que qBittorrent ne sait pas suivre via `urls`).
    Le `path` disque reste géré en dernier recours pour les anciens result.json
    v1 qui le portaient.
    """
    magnet = torrent_ui.get("magnet")
    if magnet:
        return magnet

    url = torrent_ui.get("url")
    if url and url.startswith("magnet:"):
        return url
    if url and url.startswith(("http://", "https://")):
        return _resolve_http_url(url)

    dl_path = torrent_ui.get("dl")
    if dl_path:
        path = Path(dl_path)
        if not path.exists():
            raise FileNotFoundError(f"Fichier introuvable : {dl_path}")
        return path.read_bytes()

    raise FileNotFoundError(
        "Aucune source soumissible enregistrée pour ce candidat "
        "(recherche antérieure au correctif torrent_url — relancez la recherche "
        "pour régénérer le result.json)"
    )


def _resolve_http_url(url: str):
    """Suit la chaîne de redirects d'un downloadUrl HTTP → magnet (str) ou bytes."""
    import requests

    current = url
    headers = {"User-Agent": "AgentTorrent/1.0"}
    for _ in range(_HTTP_REDIRECT_HOPS):
        resp = requests.get(current, timeout=30, headers=headers, allow_redirects=False)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc.startswith("magnet:"):
                return loc
            if loc.startswith(("http://", "https://")):
                current = loc
                continue
            raise RuntimeError(f"Redirect vers schéma inattendu: {loc[:60]!r}")
        resp.raise_for_status()
        return resp.content
    raise RuntimeError(f"Trop de redirections HTTP (>{_HTTP_REDIRECT_HOPS}) pour {url}")


def download_torrent(search_id: str, torrent_id: int) -> str:
    """
    Send a specific torrent to qBittorrent for manual override.
    Returns the torrent hash.
    """
    cache = _get_cache()
    search_data = cache.get(search_id)
    if not search_data:
        raise ValueError(f"Unknown search_id: {search_id!r}")

    torrent_ui = next(
        (t for t in search_data["torrents"] if t["rank"] == torrent_id), None
    )
    if not torrent_ui:
        raise ValueError(f"Torrent id {torrent_id} not found in {search_id!r}")

    source = _resolve_torrent_source(torrent_ui)

    # mavod is on sys.path via the top-of-module insertion. The legacy client
    # is kept as-is here for backward compat — the new typed adapter
    # (mavod.adapters.qbittorrent.QBittorrentAdapter) requires Settings, which
    # the UI does not currently load (it has its own minimal env).
    from mavod.qbittorrent_client import QBittorrentClient

    client = QBittorrentClient.from_env()
    return client.add_torrent(source, tags="ui-override")
