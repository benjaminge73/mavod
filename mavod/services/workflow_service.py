"""Service workflow torrent end-to-end.

Remplace `mavod/workflow.py` (335 lignes) en orchestrant les services
typés. Découpe en étapes (search → rank → submit → persist) testables
isolément.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from mavod.adapters.qbittorrent import QBittorrentAdapter
from mavod.config import Settings
from mavod.domain import (
    Intent,
    QbSubmitResult,
    RankingDecision,
    Torrent,
    WorkflowResult,
)
from mavod.domain.workflow_result import SCHEMA_VERSION
from mavod.exceptions import (
    DuplicateTorrent,
    NoCandidatesFound,
    QBittorrentError,
    RankingError,
)
from mavod.logging_setup import get_logger
from mavod.services.ranking_service import RankingService
from mavod.services.search_service import SearchService


log = get_logger(__name__)


_SAFE_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")

_HTTP_REDIRECT_HOPS = 5


@dataclass(frozen=True, slots=True)
class _ResolvedSource:
    """Source résolue depuis un torrent_url HTTP : magnet ou bytes."""
    kind: str  # "magnet" | "bytes"
    payload: object  # str (magnet) ou bytes (.torrent)


def _resolve_http_torrent_url(url: str) -> _ResolvedSource:
    """Suit la chaîne de redirections d'un downloadUrl HTTP.

    Certains indexers (YTS via Prowlarr) répondent 302 avec
    `Location: magnet:…` — `requests` ne gère pas ce schéma en suivi
    auto. On suit nous-mêmes avec `allow_redirects=False` jusqu'à
    rencontrer soit un magnet, soit une réponse 200 (bytes .torrent).
    """
    import requests

    current = url
    headers = {"User-Agent": "AgentTorrent/1.0"}
    for _ in range(_HTTP_REDIRECT_HOPS):
        resp = requests.get(current, timeout=30, headers=headers, allow_redirects=False)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc.startswith("magnet:"):
                return _ResolvedSource(kind="magnet", payload=loc)
            if loc.startswith(("http://", "https://")):
                current = loc
                continue
            raise RuntimeError(f"Redirect vers schéma inattendu: {loc[:60]!r}")
        resp.raise_for_status()
        return _ResolvedSource(kind="bytes", payload=resp.content)
    raise RuntimeError(f"Trop de redirections HTTP (>{_HTTP_REDIRECT_HOPS}) pour {url}")


def sanitize_filename(name: str) -> str:
    """Remplace les caractères interdits par `_` et tronque à 100 chars."""
    s = _SAFE_FILENAME_RE.sub("_", name)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:100]


def build_search_id(intent: Intent, *, now: Optional[datetime] = None) -> str:
    """search_id stable et lexico-triable (YYYYMMDD_HHMMSS_<title_safe>)."""
    now = now or datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    title_safe = sanitize_filename(intent.title)
    if intent.type == "serie" and intent.season and intent.episode:
        return f"{ts}_{title_safe}_S{intent.season:02d}E{intent.episode:02d}"
    if intent.type == "serie" and intent.season:
        return f"{ts}_{title_safe}_S{intent.season:02d}"
    return f"{ts}_{title_safe}"


@dataclass(frozen=True, slots=True)
class WorkflowSteps:
    """Container exposant les étapes individuelles (utile pour tests)."""

    intent: Intent
    search_id: str
    raw_pool: List[Torrent]
    candidates: List[Torrent]
    decision: Optional[RankingDecision]
    submit: Optional[QbSubmitResult]


class WorkflowService:
    """Pipeline complet : search → filter+rank → qB submit → persist result.json."""

    def __init__(
        self,
        settings: Settings,
        *,
        search: Optional[SearchService] = None,
        ranking: Optional[RankingService] = None,
        qb: Optional[QBittorrentAdapter] = None,
    ):
        self._settings = settings
        self._search = search or SearchService(settings)
        self._ranking = ranking or RankingService(settings)
        self._qb = qb  # Lazy : QBittorrentAdapter ouvre une session HTTP à l'instanciation.

    def run(
        self,
        intent: Intent,
        *,
        skip_qb: bool = False,
        persist: bool = True,
    ) -> WorkflowResult:
        """Exécute le pipeline complet pour un Intent. Renvoie WorkflowResult."""
        search_id = build_search_id(intent)
        temp_dir = self._settings.torrents_dir / search_id
        log.info("workflow.start", extra={"search_id": search_id, "intent": asdict(intent)})

        try:
            # ── Search ───────────────────────────────────────────────────
            outcome = self._search.search(intent)
            raw_pool = list(outcome.raw_pool)
            if not raw_pool:
                log.info("workflow.no_results", extra={"search_id": search_id})
                return self._build_result(
                    intent, search_id, raw_pool=[], candidates=[],
                    decision=None, submit=None,
                    error="Aucun candidat trouvé",
                )

            # ── Filter + scoring local ───────────────────────────────────
            temp_dir.mkdir(parents=True, exist_ok=True)
            candidates = self._ranking.filter_and_score(
                intent, raw_pool, str(temp_dir),
            )
            if not candidates:
                raise NoCandidatesFound("Aucun candidat ne survit aux filtres")

            # ── LLM Ranking ──────────────────────────────────────────────
            decision = self._ranking.rank(intent, candidates)

            # ── qBittorrent submit ───────────────────────────────────────
            submit: Optional[QbSubmitResult] = None
            if decision.has_choice and not skip_qb:
                submit = self._submit_to_qb(decision.best, search_id)

            result = self._build_result(
                intent, search_id,
                raw_pool=raw_pool, candidates=candidates,
                decision=decision, submit=submit,
            )

            if persist:
                self._persist_result(temp_dir, result)

            log.info(
                "workflow.success",
                extra={
                    "search_id": search_id,
                    "candidates": len(candidates),
                    "best": decision.best.title if decision.best else None,
                    "submitted": submit is not None,
                },
            )
            return result

        except NoCandidatesFound as e:
            log.warning("workflow.no_candidates", extra={"search_id": search_id, "err": str(e)})
            return self._build_result(
                intent, search_id, raw_pool=raw_pool, candidates=[],
                decision=None, submit=None, error=str(e),
            )
        except RankingError as e:
            log.error("workflow.ranking_failed", extra={"search_id": search_id, "err": str(e)})
            return self._build_result(
                intent, search_id, raw_pool=raw_pool, candidates=candidates,
                decision=None, submit=None, error=f"ranking: {e}",
            )
        except (QBittorrentError, Exception) as e:
            log.exception("workflow.error", extra={"search_id": search_id})
            return self._build_result(
                intent, search_id,
                raw_pool=[] if "raw_pool" not in locals() else raw_pool,
                candidates=[] if "candidates" not in locals() else candidates,
                decision=None if "decision" not in locals() else decision,
                submit=None, error=str(e),
            )

    # ─── Étapes internes ───────────────────────────────────────────────────

    def _submit_to_qb(self, torrent: Torrent, search_id: str) -> Optional[QbSubmitResult]:
        """Ajoute le torrent gagnant à qBittorrent (bytes > magnet > URL ; doublon = succès)."""
        qb = self._qb or QBittorrentAdapter(self._settings)
        source: object

        # Défense en profondeur : un magnet peut atterrir dans `torrent_url`
        # si un adapter upstream loupe la détection. On le récupère ici plutôt
        # que de cracher dans `requests.get("magnet:...")`.
        magnet = torrent.magnet or (
            torrent.torrent_url
            if torrent.torrent_url and torrent.torrent_url.startswith("magnet:")
            else None
        )

        if torrent.torrent_bytes:
            log.info("workflow.qb_source", extra={"search_id": search_id, "kind": "bytes"})
            source = torrent.torrent_bytes
        elif magnet:
            log.info("workflow.qb_source", extra={"search_id": search_id, "kind": "magnet"})
            source = magnet
        elif torrent.torrent_url and torrent.torrent_url.startswith(("http://", "https://")):
            # YTS via Prowlarr renvoie un `downloadUrl` HTTP qui répond 302
            # avec `Location: magnet:…`. `requests` ne suit pas ce schéma en
            # auto (InvalidSchema), et qBittorrent côté serveur n'accepte
            # pas non plus un HTTP→magnet via le champ `urls`. On résout
            # nous-mêmes la chaîne de redirections jusqu'au magnet ou aux
            # bytes .torrent.
            resolved = _resolve_http_torrent_url(torrent.torrent_url)
            log.info(
                "workflow.qb_source",
                extra={"search_id": search_id, "kind": f"http→{resolved.kind}"},
            )
            source = resolved.payload
        else:
            log.warning(
                "workflow.qb_skip_no_source",
                extra={"search_id": search_id, "torrent_url": torrent.torrent_url},
            )
            return None

        try:
            infohash = qb.add(source, tags=f"mavod-{search_id}")
        except DuplicateTorrent as e:
            log.info("workflow.qb_duplicate", extra={"search_id": search_id, "err": str(e)})
            # On considère le doublon comme succès (le torrent existe déjà)
            inferred_hash = torrent.infohash or ""
            return QbSubmitResult(
                infohash=inferred_hash,
                name=torrent.title,
                submitted_at=time.time(),
                tags=f"mavod-{search_id}",
            )

        return QbSubmitResult(
            infohash=infohash,
            name=torrent.title,
            submitted_at=time.time(),
            tags=f"mavod-{search_id}",
        )

    def _build_result(
        self,
        intent: Intent,
        search_id: str,
        *,
        raw_pool: List[Torrent],
        candidates: List[Torrent],
        decision: Optional[RankingDecision],
        submit: Optional[QbSubmitResult],
        error: Optional[str] = None,
    ) -> WorkflowResult:
        """Assemble le WorkflowResult final (schema v2) à partir des sorties d'étapes."""
        return WorkflowResult(
            schema_version=SCHEMA_VERSION,
            search_id=search_id,
            title=intent.title,
            media_type=intent.type,
            year=intent.year,
            season=intent.season,
            episode=intent.episode,
            imdb_id=intent.imdb_id,
            candidates=tuple(_torrent_to_ui_dict(t) for t in candidates),
            best_choice=(_torrent_to_ui_dict(decision.best) if decision and decision.best else None),
            llm_reasoning=(decision.reasoning if decision else None),
            llm_response=(decision.raw_response if decision else None),
            qb_submit=submit,
            error=error,
            created_at=time.time(),
        )

    def _persist_result(self, temp_dir: Path, result: WorkflowResult) -> None:
        """Écrit `result.json` dans `torrents/<search_id>/` (consommé par mavod-ui)."""
        try:
            result.write(temp_dir / "result.json")
        except OSError as e:
            log.warning("workflow.persist_failed", extra={"err": str(e)})

    def ui_url(self, search_id: str) -> str:
        """Lien profond vers mavod-ui pour visualiser cette recherche."""
        base = self._settings.mavod_ui_url.rstrip("/")
        return f"{base}/?search_id={quote(search_id)}"


def _torrent_to_ui_dict(t: Torrent) -> dict:
    """Convertit un Torrent en dict consommable par mavod-ui (rétrocompat result.json)."""
    return {
        "title":     t.title,
        "name":      t.title,  # alias UI
        "indexer":   t.indexer,
        "size":      t.size_bytes,
        "size_gb":   round(t.size_gb, 2),
        "num_files": t.num_files,
        "seeders":   t.seeders,
        "leechers":  t.leechers,
        "infohash":  t.infohash,
        "magnet":    t.magnet,
        "torrent_url": t.torrent_url,  # consommé par mavod-ui pour le download alternatif
        "score":     t.score,
        "episode_match": t.episode_match,
    }
