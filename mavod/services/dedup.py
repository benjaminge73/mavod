"""Déduplication cross-tracker des candidats torrent.

Prowlarr agrège N indexers : la même release remonte donc souvent 3–4 fois,
une par tracker. La dédup historique se faisait uniquement sur `infoHash`,
ce qui laissait passer la quasi-totalité des doublons réels :

* beaucoup d'indexers ne renvoient pas `infoHash` du tout (résultat en
  fichier `.torrent` plutôt qu'en magnet) — sans hash, l'ancienne boucle
  ne comparait rien et gardait tout ;
* les trackers privés (YGG, C411…) re-packagent le `.torrent` avec leur
  propre `announce` : l'infohash diffère pour une release strictement
  identique.

Résultat : les 10 slots envoyés au LLM étaient mangés par 3 copies du même
pack, et l'utilisateur voyait trois fois la même série.

Stratégie en cascade (du signal le plus fort au plus faible) :

1. `infohash` identique                                    → doublon certain
2. nom de release normalisé identique + taille compatible  → doublon
   (tolérance `_SIZE_TOLERANCE`, les trackers arrondissent différemment)
3. taille en octets strictement identique + marqueurs (saison, épisode,
   résolution, langue) concordants + titres très proches
   (Jaccard ≥ `_TOKEN_SIMILARITY`)                         → doublon

Deux titres normalisés identiques mais de tailles franchement différentes
(re-encode, autre source) restent deux candidats distincts : on préfère un
doublon résiduel à la perte d'une release légitime.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from mavod.domain import Torrent


# Écart de taille toléré entre deux copies d'une même release (2 %).
_SIZE_TOLERANCE = 0.02

# Similarité de tokens minimale quand la taille est identique à l'octet près.
# Seuil volontairement bas : il n'est atteignable qu'avec une taille
# strictement égale et des marqueurs (saison/épisode/résolution/langue)
# concordants.
_TOKEN_SIMILARITY = 0.7

# Tokens ajoutés par les trackers/uploaders, sans valeur discriminante.
_NOISE_TOKENS = frozenset({
    "www", "com", "org", "net", "info", "eu", "fr", "to", "tv", "xx",
    "torrent", "torrents", "torrent9", "torrent911", "oxtorrent",
    "ygg", "yggtorrent", "eztv", "rarbg", "tgx", "torrentgalaxy",
    "1337x", "nyaa", "extreme", "download", "downloads", "cpasbien",
})

# Segments entre crochets/parenthèses qui ne sont que de la pub tracker.
_TRACKER_BRACKET_RE = re.compile(
    r"[\[\(\{][^\]\)\}]*"
    r"(?:www\s*\.|\.com|\.org|\.net|\.info|\.xx|torrent|tracker|ygg|eztv|rarbg|1337x)"
    r"[^\]\)\}]*[\]\)\}]",
    re.IGNORECASE,
)

# Extension de fichier parfois collée au titre par l'indexer.
_FILE_EXT_RE = re.compile(r"\.(torrent|mkv|mp4|avi)$", re.IGNORECASE)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Marqueurs qui interdisent toute fusion quand ils diffèrent : deux titres
# proches dont l'épisode, la saison, la résolution ou la piste audio ne
# concordent pas sont deux releases distinctes, taille identique ou non
# (une MULTi et une VOSTFR du même pack ne sont pas interchangeables).
_EPISODE_RE = re.compile(r"\bs(\d{1,3}) ?e(\d{1,4})\b")
_SEASON_RE = re.compile(r"\bs(\d{1,3})\b")
_RESOLUTION_RE = re.compile(r"\b(2160p|1080p|1080i|720p|576p|480p|4k|uhd)\b")
_LANGUAGE_RE = re.compile(
    r"\b(multi|vostfr|vost|subfrench|truefrench|french|vff|vfq|vf2|vfi|vf|english)\b"
)


def normalize_release_name(title: str) -> str:
    """Normalise un nom de release pour comparaison cross-tracker.

    Neutralise ce que chaque tracker ajoute de son côté (séparateurs,
    tags `[www.tracker.xx]`, extension de fichier, casse) sans toucher à ce
    qui identifie la release (titre, saison/épisode, résolution, source,
    codec, groupe).
    """
    t = (title or "").strip().lower()
    t = _TRACKER_BRACKET_RE.sub(" ", t)
    t = _FILE_EXT_RE.sub("", t)
    t = _NON_ALNUM_RE.sub(" ", t)
    tokens = [tok for tok in t.split() if tok and tok not in _NOISE_TOKENS]
    return " ".join(tokens)


def _tokens(normalized: str) -> Set[str]:
    return set(normalized.split())


def _markers(normalized: str) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """Extrait (épisodes, saisons, résolutions, langues) d'un nom normalisé."""
    episodes = {f"s{s_}e{e_}" for s_, e_ in _EPISODE_RE.findall(normalized)}
    seasons = set(_SEASON_RE.findall(normalized))
    resolutions = {r.replace("uhd", "4k") for r in _RESOLUTION_RE.findall(normalized)}
    languages = set(_LANGUAGE_RE.findall(normalized))
    return episodes, seasons, resolutions, languages


def _markers_conflict(a: Tuple[Set[str], ...], b: Tuple[Set[str], ...]) -> bool:
    """True si deux releases se contredisent sur un marqueur renseigné des deux côtés."""
    return any(m_a and m_b and m_a != m_b for m_a, m_b in zip(a, b))


def _sizes_compatible(a: int, b: int) -> bool:
    """True si deux tailles peuvent décrire la même release.

    Une taille inconnue (0) ne prouve rien : on la considère compatible,
    le nom normalisé identique fait alors seul la décision.
    """
    if a <= 0 or b <= 0:
        return True
    larger = max(a, b)
    return abs(a - b) / larger <= _SIZE_TOLERANCE


def _token_similarity(a: Set[str], b: Set[str]) -> float:
    """Indice de Jaccard entre deux ensembles de tokens."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class _Cluster:
    """Groupe de torrents considérés comme la même release."""

    __slots__ = ("best", "normalized", "tokens", "markers", "indexers", "count")

    def __init__(self, torrent: Torrent, normalized: str):
        self.best = torrent
        self.normalized = normalized
        self.tokens = _tokens(normalized)
        self.markers = _markers(normalized)
        self.indexers: List[str] = [torrent.indexer or ""]
        self.count = 1

    def matches(self, torrent: Torrent, normalized: str) -> bool:
        """Règles 2 et 3 : nom normalisé, ou taille exacte + titres proches."""
        if normalized and normalized == self.normalized:
            return _sizes_compatible(torrent.size_bytes, self.best.size_bytes)
        if torrent.size_bytes > 0 and torrent.size_bytes == self.best.size_bytes:
            if _markers_conflict(_markers(normalized), self.markers):
                return False
            return _token_similarity(_tokens(normalized), self.tokens) >= _TOKEN_SIMILARITY
        return False

    def absorb(self, torrent: Torrent) -> None:
        """Ajoute un doublon et garde la meilleure copie comme représentant."""
        self.count += 1
        if torrent.indexer:
            self.indexers.append(torrent.indexer)
        if _copy_rank(torrent) > _copy_rank(self.best):
            self.best = torrent

    def resolve(self) -> Torrent:
        """Représentant final, annoté du nombre de copies écartées."""
        if self.count == 1:
            return self.best
        others = tuple(
            ix for ix in self.indexers if ix and ix != self.best.indexer
        )
        extra: Dict[str, object] = dict(self.best.extra or {})
        extra["duplicate_count"] = self.count
        extra["duplicate_indexers"] = others
        return replace(self.best, extra=extra)


def _copy_rank(t: Torrent) -> Tuple[int, int, int, int]:
    """Ordre de préférence entre copies d'une même release.

    Seeders d'abord (c'est le swarm qu'on va réellement télécharger), puis
    la facilité de soumission à qBittorrent : bytes déjà en main > magnet >
    infohash connu.
    """
    return (
        max(t.seeders, 0),
        1 if t.torrent_bytes else 0,
        1 if t.magnet else 0,
        1 if t.infohash else 0,
    )


def dedup_torrents(torrents: Sequence[Torrent]) -> List[Torrent]:
    """Déduplique un pool cross-tracker en conservant la meilleure copie.

    L'ordre d'entrée (ranking server-side de Prowlarr) est préservé : chaque
    cluster garde la position de sa première occurrence.
    """
    clusters: List[_Cluster] = []
    by_hash: Dict[str, _Cluster] = {}
    by_name: Dict[str, List[_Cluster]] = {}
    by_size: Dict[int, List[_Cluster]] = {}

    for t in torrents:
        normalized = normalize_release_name(t.title)
        cluster = _find_cluster(t, normalized, by_hash, by_name, by_size)

        if cluster is not None:
            cluster.absorb(t)
        else:
            cluster = _Cluster(t, normalized)
            clusters.append(cluster)
            by_name.setdefault(normalized, []).append(cluster)

        # Un cluster peut apprendre un infohash ou une taille via une copie
        # ultérieure : on indexe chaque variante rencontrée.
        infohash = _hash_of(t)
        if infohash:
            by_hash.setdefault(infohash, cluster)
        if t.size_bytes > 0 and cluster not in by_size.setdefault(t.size_bytes, []):
            by_size[t.size_bytes].append(cluster)

    return [c.resolve() for c in clusters]


def _find_cluster(
    t: Torrent,
    normalized: str,
    by_hash: Mapping[str, _Cluster],
    by_name: Mapping[str, List[_Cluster]],
    by_size: Mapping[int, List[_Cluster]],
) -> Optional[_Cluster]:
    """Cherche le cluster auquel `t` appartient (hash, puis nom, puis taille)."""
    infohash = _hash_of(t)
    if infohash and infohash in by_hash:
        return by_hash[infohash]

    for cluster in by_name.get(normalized, ()):
        if cluster.matches(t, normalized):
            return cluster

    # Titres divergents mais taille identique à l'octet près : on retombe
    # sur la similarité de tokens (règle 3).
    if t.size_bytes > 0:
        for cluster in by_size.get(t.size_bytes, ()):
            if cluster.matches(t, normalized):
                return cluster
    return None


def _hash_of(t: Torrent) -> str:
    return (t.infohash or "").strip().lower()
