"""Hiérarchie d'exceptions maVOD.

Toutes les erreurs métier héritent de MavodError. Permet aux handlers
(notamment telegram_bot) de router par type d'erreur sans `except Exception`.
"""

from __future__ import annotations


class MavodError(Exception):
    """Racine de la hiérarchie d'erreurs maVOD."""


# ─── Config ──────────────────────────────────────────────────────────────────

class ConfigError(MavodError):
    """Configuration invalide ou incomplète (variable d'env manquante, etc.)."""


# ─── LLM ─────────────────────────────────────────────────────────────────────

class LLMError(MavodError):
    """Erreur générique de l'API LLM (provider OpenAI-compatible)."""


class LLMTimeout(LLMError):
    """Timeout réseau après épuisement des retries."""


class LLMRateLimit(LLMError):
    """HTTP 429 après épuisement des retries."""


class LLMMalformed(LLMError):
    """Réponse JSON malformée ou champs manquants."""


# ─── Intent parsing ──────────────────────────────────────────────────────────

class IntentParseError(MavodError):
    """Échec du parsing d'une requête utilisateur en Intent."""


class IntentValidationError(IntentParseError):
    """Intent renvoyé par le LLM mais champ invalide (type, year, imdb_id…)."""


# ─── Sources de torrents ─────────────────────────────────────────────────────

class TorrentSourceError(MavodError):
    """Erreur d'un indexer (Prowlarr)."""


class ProwlarrError(TorrentSourceError):
    """Erreur de l'indexer Prowlarr."""


# ─── Ranking ─────────────────────────────────────────────────────────────────

class RankingError(MavodError):
    """Échec du ranking LLM (saturation tokens, output non parsable…)."""


# ─── qBittorrent ─────────────────────────────────────────────────────────────

class QBittorrentError(MavodError):
    """Erreur générique du client qBittorrent."""


class DuplicateTorrent(QBittorrentError):
    """Torrent déjà présent dans qBittorrent (réponse `Fails.`)."""


# ─── Workflow ────────────────────────────────────────────────────────────────

class WorkflowError(MavodError):
    """Erreur d'orchestration du workflow torrent."""


class NoCandidatesFound(WorkflowError):
    """Aucun torrent ne survit aux filtres."""
