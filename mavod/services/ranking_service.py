"""Service de ranking : filter local + scoring + LLM.

Wrappe le pipeline legacy `torrent_filter.filter_and_select_torrents` +
le ranker LLM mais expose une interface typée `Torrent` →
`RankingDecision`.

À terme, le filter local pourra être réécrit ici en consommant
directement les `Torrent` dataclasses. Pour l'instant on conserve le
filter legacy (700 lignes) en délégant via conversion dict ↔ Torrent.
"""

from __future__ import annotations

import re
from dataclasses import asdict, replace
from typing import List, Mapping, Optional, Protocol, Sequence

from mavod.adapters.bencode import parse_torrent_bytes
from mavod.adapters.llm import LLMAdapter
from mavod.adapters.llm.prompts import load_ranker_prompt, prompt_hash
from mavod.config import Settings
from mavod.domain import Intent, RankingDecision, Torrent, TorrentFile
from mavod.exceptions import LLMError, LLMMalformed, RankingError
from mavod.logging_setup import get_logger


log = get_logger(__name__)


# ─── Strategy Protocol ───────────────────────────────────────────────────────


class RankingStrategy(Protocol):
    def rank(
        self,
        intent: Intent,
        candidates: Sequence[Torrent],
    ) -> RankingDecision: ...


# ─── LLM Strategy ────────────────────────────────────────────────────────────


class LLMRankingStrategy:
    """Stratégie de ranking via API LLM + prompt v2 externalisé."""

    # Le prompt demande `**Best choice:** Torrent N`, mais aucun modèle ne le
    # rend verbatim à 100 % : gras absent ou placé autrement (`**Best choice**:`),
    # numéro nu (`Best choice: 2`), casse variable. On normalise le markdown
    # (cf. `_strip_markdown`) puis on parse tolérant — un écart de formatage ne
    # doit jamais coûter un téléchargement.
    _BEST_RE = re.compile(
        r"best\s*choice\s*[:\-–—]?\s*(?:torrent|candidate|option|n[o°]\.?|#)?\s*(\d+)",
        re.IGNORECASE,
    )
    _FINAL_RANKING_RE = re.compile(
        r"final\s*ranking\s*[:\-–—]?\s*(.+)", re.IGNORECASE
    )
    _TORRENT_IDX_RE = re.compile(r"torrent\s*(\d+)", re.IGNORECASE)
    _BARE_IDX_RE = re.compile(r"\d+")

    def __init__(self, settings: Settings, *, adapter: Optional[LLMAdapter] = None):
        self._settings = settings
        self._adapter = adapter or LLMAdapter(settings)
        self._system_prompt = load_ranker_prompt()
        log.info(
            "ranker.init",
            extra={"prompt_hash": prompt_hash(self._system_prompt), "model": self._adapter.model},
        )

    def rank(
        self,
        intent: Intent,
        candidates: Sequence[Torrent],
    ) -> RankingDecision:
        """Envoie les candidats au LLM, parse la sortie texte (regex), retourne best+ranked."""
        if not candidates:
            return RankingDecision(ranked=(), best=None)

        user_msg = self._format_candidates(intent, candidates)
        content, reasoning, usage = "", None, {}
        fallback_reason: Optional[str] = None
        try:
            content, reasoning, usage = self._adapter.chat_with_usage(
                system=self._system_prompt,
                user=user_msg,
                max_tokens=self._settings.llm_ranker_max_tokens,
                temperature=0.1,
            )
        except LLMMalformed as e:
            # Réponse vide / illisible : le LLM n'arbitre pas, mais les
            # candidats sont déjà triés par score local → on dégrade au lieu
            # d'annuler le téléchargement.
            log.warning("ranker.llm_unusable", extra={"err": str(e)})
            fallback_reason = "llm_empty"
        except LLMError as e:
            # Réseau / quota / HTTP : là, quelque chose ne va pas globalement,
            # on remonte l'erreur au workflow.
            raise RankingError(f"LLM KO: {e}") from e

        # Un reasoner tronqué peut laisser son verdict dans `reasoning_content`
        # alors que `content` est vide : on cherche dans les deux.
        best = self._parse_best_choice(content, candidates)
        if best is None and reasoning:
            best = self._parse_best_choice(reasoning, candidates)
            if best is not None:
                fallback_reason = None
                log.info("ranker.best_from_reasoning")

        ranked = (
            self._parse_ranking(content, candidates)
            or self._parse_ranking(reasoning or "", candidates)
            or list(candidates)
        )

        if best is None:
            # Dernier filet : `candidates` est trié par score local décroissant
            # (cf. torrent_filter.filter_top_torrents) → le premier de `ranked`
            # reste un choix défendable, et l'utilisateur obtient son film.
            best = ranked[0]
            fallback_reason = fallback_reason or "llm_unparsable"
            log.warning(
                "ranker.fallback_local",
                extra={
                    "reason": fallback_reason,
                    "candidates": len(candidates),
                    "picked": best.title,
                    "raw_head": (content or "")[:200],
                },
            )

        log.info(
            "ranker.done",
            extra={
                "candidates": len(candidates),
                "best_idx": (ranked.index(best) + 1) if best in ranked else None,
                "fallback": fallback_reason,
                "cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
                "prompt_tokens": usage.get("prompt_tokens", 0),
            },
        )
        return RankingDecision(
            ranked=tuple(ranked),
            best=best,
            reasoning=reasoning,
            raw_response=content,
            usage=usage,
            fallback_reason=fallback_reason,
        )

    # ─── Internal ────────────────────────────────────────────────────────

    def _format_candidates(self, intent: Intent, candidates: Sequence[Torrent]) -> str:
        """Met les candidats au format attendu par le prompt v2 (numéro + breakdown fichiers)."""
        lines: List[str] = []
        for i, t in enumerate(candidates, 1):
            header = (
                f"{i}. {t.title} "
                f"({t.size_gb:.2f} GB, {t.num_files} files, {t.seeders} seeders)"
            )
            lines.append(header)
            if t.files:
                shown = list(t.files)[: self._settings.max_files_per_torrent]
                for f in shown:
                    lines.append(f"   - {f.name} ({f.size_gb:.2f} GB)")
                remainder = len(t.files) - len(shown)
                if remainder > 0:
                    lines.append(f"   - … ({remainder} more files truncated)")

        body = "\n".join(lines)
        header = f"Request: *{intent.title}*"
        if intent.season is not None and intent.episode is None:
            header += f"\nUser wants the full season S{intent.season:02d} — prefer season packs over single episodes."
        elif intent.episode is not None:
            header += f"\nUser wants episode E{intent.episode:02d} specifically."
        return f"{header}\n\n{body}"

    def _parse_best_choice(
        self,
        response: str,
        candidates: Sequence[Torrent],
    ) -> Optional[Torrent]:
        """Extrait l'index `Best choice: Torrent N` du texte LLM. None si absent/invalide.

        Tolère les variantes de formatage (gras, numéro nu, casse) et retient la
        DERNIÈRE occurrence valide : un modèle qui se reprend écrit son verdict
        définitif en fin de réponse.
        """
        text = _strip_markdown(response)
        picked: Optional[Torrent] = None
        for m in self._BEST_RE.finditer(text):
            idx = int(m.group(1))
            if 1 <= idx <= len(candidates):
                picked = candidates[idx - 1]
        return picked

    def _parse_ranking(
        self,
        response: str,
        candidates: Sequence[Torrent],
    ) -> List[Torrent]:
        """Extrait la liste ordonnée `Final ranking: Torrent X, Torrent Y, …` (déduplique)."""
        m = self._FINAL_RANKING_RE.search(_strip_markdown(response))
        if not m:
            return []
        line = m.group(1)
        # `Torrent N` d'abord ; à défaut, des numéros nus (`2, 1, 3`). L'ordre
        # importe : chercher les numéros nus en premier ferait matcher « 1080 »
        # dans un titre recopié par le modèle.
        indices = self._TORRENT_IDX_RE.findall(line) or self._BARE_IDX_RE.findall(line)
        out: List[Torrent] = []
        seen: set = set()
        for raw in indices:
            i = int(raw)
            if 1 <= i <= len(candidates) and i not in seen:
                seen.add(i)
                out.append(candidates[i - 1])
        return out


# ─── Ranking Service (filter + LLM) ──────────────────────────────────────────


class RankingService:
    """Pipeline complet : filter local → top-N → LLM rank.

    Délègue le filtrage à `torrents_search_download.torrent_filter` (V1
    intact) via conversion `Torrent` ↔ dict.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        llm_strategy: Optional[LLMRankingStrategy] = None,
    ):
        self._settings = settings
        self._llm = llm_strategy or LLMRankingStrategy(settings)

    def filter_and_score(
        self,
        intent: Intent,
        raw_pool: Sequence[Torrent],
        download_dir: str,
    ) -> List[Torrent]:
        """Étape 1+2 : filter durs + scoring local, cap à `max_torrents_for_llm`."""
        from torrents_search_download.torrent_filter import filter_and_select_torrents

        # Conversion Torrent → dict legacy
        legacy_pool = [_torrent_to_legacy_dict(t) for t in raw_pool]
        releases, _stats = filter_and_select_torrents(
            raw_results=legacy_pool,
            media_type=intent.type,
            download_dir=download_dir,
            max_torrents=self._settings.max_torrents_for_llm,
            year=intent.year,
            season=intent.season,
            episode=intent.episode,
            search_title=intent.title,
            verbose=False,
            imdb_id=intent.imdb_id,
        )
        # Conversion dict → Torrent
        return [_legacy_dict_to_torrent(d) for d in releases]

    def rank(
        self,
        intent: Intent,
        candidates: Sequence[Torrent],
    ) -> RankingDecision:
        """Étape 3 : ranking LLM. Enrichit avec breakdown fichiers si bytes dispo."""
        enriched = [_enrich_files_from_bytes(t) for t in candidates]
        return self._llm.rank(intent, enriched)


# ─── Parsing helpers ─────────────────────────────────────────────────────────


_MARKDOWN_NOISE_RE = re.compile(r"[*_`]+")


def _strip_markdown(text: Optional[str]) -> str:
    """Retire l'emphase markdown (`**`, `_`, backticks) avant parsing.

    Neutralise d'un coup toutes les positions possibles du gras autour du
    libellé (`**Best choice:**`, `**Best choice**:`, `*Best choice*: `…).
    """
    return _MARKDOWN_NOISE_RE.sub("", text or "")


# ─── Conversion helpers ──────────────────────────────────────────────────────


def _torrent_to_legacy_dict(t: Torrent) -> dict:
    """Convertit un Torrent en dict consommable par `torrent_filter`."""
    url = t.magnet or t.torrent_url or ""
    extra = dict(t.extra) if isinstance(t.extra, Mapping) else {}
    return {
        "title":       t.title,
        "indexer":     t.indexer,
        "downloadUrl": url,
        "infoHash":    t.infohash or "",
        "size":        t.size_bytes,
        "seeders":     t.seeders,
        "leechers":    t.leechers,
        "is_magnet":   bool(t.magnet),
        "guid":        extra.get("guid", ""),
        "categories":  extra.get("categories", []),
        "publishDate": extra.get("publishDate", ""),
        "downloads":   extra.get("downloads", 0),
        # Traçabilité de la dédup cross-tracker (cf. services/dedup.py)
        "duplicate_count":    extra.get("duplicate_count", 1),
        "duplicate_indexers": extra.get("duplicate_indexers", ()),
        # Conserve les bytes pour enrichissement downstream
        "_torrent_bytes": t.torrent_bytes,
    }


def _legacy_dict_to_torrent(d: dict) -> Torrent:
    """Convertit un dict legacy (post-scoring) en Torrent."""
    url = d.get("downloadUrl", "")
    is_magnet = bool(d.get("is_magnet")) or url.startswith("magnet:")
    return Torrent(
        title=d.get("title", ""),
        indexer=d.get("indexer", ""),
        size_bytes=int(d.get("size") or 0),
        seeders=int(d.get("seeders") or 0),
        leechers=int(d.get("leechers") or 0),
        infohash=d.get("infoHash") or None,
        magnet=url if is_magnet else None,
        torrent_url=url if (url and not is_magnet) else None,
        torrent_bytes=d.get("_torrent_bytes"),
        episode_match=d.get("_episode_match"),
        score=d.get("_score"),
        extra={
            "guid":        d.get("guid", ""),
            "categories":  d.get("categories", []),
            "publishDate": d.get("publishDate", ""),
            "downloads":   d.get("downloads", 0),
            "profile_name": d.get("profile_name", ""),
            "duplicate_count":    d.get("duplicate_count", 1),
            "duplicate_indexers": d.get("duplicate_indexers", ()),
        },
    )


def _enrich_files_from_bytes(t: Torrent) -> Torrent:
    """Si on a les bytes .torrent et pas encore de breakdown, on le calcule."""
    if t.files or not t.torrent_bytes:
        return t
    try:
        parsed = parse_torrent_bytes(t.torrent_bytes)
    except Exception as e:
        log.warning("ranker.bencode_failed", extra={"title": t.title, "err": str(e)})
        return t
    files: List[TorrentFile] = list(parsed["files"])
    return t.with_files(files)
