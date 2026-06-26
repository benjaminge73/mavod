"""Torrent : représentation typée d'un candidat de téléchargement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True, slots=True)
class TorrentFile:
    """Fichier individuel d'un torrent (extrait du bencode)."""

    name: str
    size_bytes: int
    index: Optional[int] = None

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)


@dataclass(frozen=True, slots=True)
class Torrent:
    """Candidat torrent normalisé (Prowlarr).

    Tous les services en aval (filter, ranker, qBittorrent submit)
    consomment des Torrent et non plus des dict bruts.
    """

    title: str
    indexer: str
    size_bytes: int
    seeders: int
    leechers: int = 0
    infohash: Optional[str] = None
    magnet: Optional[str] = None
    torrent_url: Optional[str] = None
    torrent_bytes: Optional[bytes] = None
    files: Sequence[TorrentFile] = field(default_factory=tuple)
    episode_match: Optional[bool] = None
    score: Optional[float] = None
    extra: Mapping[str, object] = field(default_factory=dict)

    @property
    def num_files(self) -> int:
        return len(self.files) if self.files else 1

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)

    @property
    def has_metadata(self) -> bool:
        return bool(self.files)

    def with_files(self, files: Sequence[TorrentFile]) -> "Torrent":
        """Retourne une copie avec les fichiers ajoutés (les dataclasses sont frozen)."""
        return _replace_torrent(self, files=tuple(files))

    def with_episode_match(self, matched: bool) -> "Torrent":
        return _replace_torrent(self, episode_match=matched)

    def with_score(self, score: float) -> "Torrent":
        return _replace_torrent(self, score=score)


def _replace_torrent(t: Torrent, **changes) -> Torrent:
    """Équivalent dataclasses.replace pour les torrents frozen+slots."""
    from dataclasses import replace
    return replace(t, **changes)


@dataclass(frozen=True, slots=True)
class RankingDecision:
    """Résultat d'un ranker : liste ordonnée + best choice + raisonnement."""

    ranked: Sequence[Torrent]
    best: Optional[Torrent]
    reasoning: Optional[str] = None
    raw_response: Optional[str] = None
    usage: Mapping[str, int] = field(default_factory=dict)

    @property
    def has_choice(self) -> bool:
        return self.best is not None
