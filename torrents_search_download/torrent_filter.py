# -*- coding: utf-8 -*-
"""
torrent_filter.py
Shared logic for filtering, sorting, and deduplicating torrents before sending them to the LLM.

Pipeline :
  filter_candidates()          — filtre dur (type, langue, qualité, année/saison, titre)
  filter_top_torrents()        — scoring + sélection top N
  filter_and_select_torrents() — wrapper public (signature inchangée)
"""

import os
import re
from typing import List, Dict, Any, Optional, Tuple

# Nombre max de torrents envoyés au LLM
MAX_TORRENTS_FOR_LLM = 10


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — type et heuristiques
# ──────────────────────────────────────────────────────────────────────────────

def normalize_indexer_name(name: str) -> str:
    """Normalise un nom d'indexer pour comparaison (insensible à la casse et aux espaces)."""
    s = (name or "").strip().lower()
    return s


def is_tv_or_non_movie(title: str) -> bool:
    """Retourne True si le titre ressemble à un épisode TV, un ebook ou de l'audio."""
    t = (title or "").lower()
    if re.search(r"S\d{1,3}E\d{1,3}", t, re.IGNORECASE):
        return True
    if re.search(r"S\d{1,3}\.E\d{1,3}", t, re.IGNORECASE):
        return True
    if re.search(r"\.S\d{1,3}\.", t, re.IGNORECASE):
        return True
    if re.search(r"\bS\d{1,3}\b", t, re.IGNORECASE):
        return True
    if re.search(r"\bseason\s*\d+\b", t):
        return True
    if re.search(r"\bepisode\s*\d+\b", t):
        return True
    if re.search(r"\b(saison|complete|integral|episodes?)\b", t):
        return True
    if re.search(r"\b(epub|pdf|cbz|cbr|mobi|azw3|audiobook|mp3|flac|presse|magazine|comics|bds|livres)\b", t):
        return True
    return False


def is_movie_like(title: str) -> bool:
    """Heuristique basique de média vidéo/film."""
    t = (title or "").lower()
    return bool(re.search(r"(2160p|1080p|uhd|4k|bluray|webrip|web[- ]?dl|remux|bdrip|brrip|hdr)\b", t))


def is_movie_categories(categories: Optional[Any]) -> bool:
    """Retourne True si au moins une catégorie est dans la plage Torznab Movies (2000-2999)."""
    if categories is None:
        return False
    try:
        vals: List[int] = []
        if isinstance(categories, int):
            vals = [categories]
        elif isinstance(categories, str):
            parts = [p.strip() for p in categories.split(",") if p.strip()]
            for p in parts:
                try:
                    vals.append(int(p))
                except Exception:
                    continue
        elif isinstance(categories, (list, tuple)):
            for p in categories:
                try:
                    vals.append(int(p))
                except Exception:
                    continue
        else:
            return False
        for v in vals:
            if 2000 <= v < 3000:
                return True
    except Exception:
        return False
    return False


def is_tv_categories(categories: Optional[Any]) -> bool:
    """Retourne True si une catégorie est dans la plage Torznab TV (5000-5999) ou YggAPI Séries."""
    if categories is None:
        return False

    YGG_TV_CATEGORIES = {
        2184,    # Film/Vidéo : Série TV (YggAPI)
        2179,    # Film/Vidéo : Animation Série (YggAPI)
        2182,    # Film/Vidéo : Emission TV (YggAPI)
        2181,    # Film/Vidéo : Documentaire (YggAPI)
        102179,  # Film/Vidéo : Animation Série (ancien format)
        102182,  # Film/Vidéo : Emission TV (ancien format)
        102184,  # Film/Vidéo : Série TV (ancien format)
    }

    C411_TV_CATEGORIES = {
        7,  # Série TV (C411 subcategory)
        2,  # Animation Série (C411 subcategory)
    }

    try:
        vals: List[int] = []
        if isinstance(categories, int):
            vals = [categories]
        elif isinstance(categories, str):
            parts = [p.strip() for p in categories.split(",") if p.strip()]
            for p in parts:
                try:
                    vals.append(int(p))
                except Exception:
                    continue
        elif isinstance(categories, (list, tuple)):
            for p in categories:
                if isinstance(p, dict):
                    try:
                        if "id" in p:
                            vals.append(int(p.get("id")))
                            continue
                    except Exception:
                        pass
                try:
                    vals.append(int(p))
                except Exception:
                    continue
        else:
            return False
        for v in vals:
            if (5000 <= v < 6000) or (v in YGG_TV_CATEGORIES) or (v in C411_TV_CATEGORIES):
                return True
    except Exception:
        return False
    return False


def is_tv_like(title: str) -> bool:
    """Heuristique pour détecter si un titre ressemble à une série TV."""
    t = (title or "").lower()
    if re.search(r"\bS\d{1,3}E\d{1,3}\b", t):
        return True
    if re.search(r"\bS\d{1,3}\b", t):
        return True
    if re.search(r"\bseason\s*\d+\b", t):
        return True
    if re.search(r"(2160p|1080p|uhd|4k|webrip|web[- ]?dl|bluray|hdr)\b", t):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — filtres qualité et langue (NOUVEAUX)
# ──────────────────────────────────────────────────────────────────────────────

def is_below_1080p(title: str) -> bool:
    """
    Retourne True si le titre mentionne explicitement une résolution sous 1080p.

    Règle conservative : on exclut uniquement ce qui est tagué 720p ou moins.
    Pas de résolution dans le titre → False (bénéfice du doute).
    S'applique aux films ET aux séries.
    """
    t = (title or "").lower()
    return bool(re.search(r"\b(720p|480p|480i|576p|576i|360p|240p)\b", t))


def is_banned_language(title: str) -> bool:
    """
    Retourne True si le titre est une release indésirable côté langue.

    Bans : TRUEFRENCH, VFF, VF2 **uniquement quand isolés** (pas de tag
    MULTI/MULTi ni VOSTFR à côté). Un release "MULTI.VFF" garde la piste
    originale + un dub français spécifique : c'est légitime et souvent la
    SEULE option pour les films/animations distribués en France (ex. Pixar
    Up → "Là.Haut.MULTI.VFF.2160p.BluRay"). Le ranker LLM pénalise
    ensuite VFF (-5) face à MULTI seul (+2) → net -3, déprioritisé mais
    non exclu.

    FRENCH seul n'est PAS banni au niveau du filtre : c'est la piste audio
    légitime pour les films non-anglophones (italiens, espagnols, asiatiques…)
    où aucune release MULTI n'existe. Le ranker applique -2 sur FRENCH seul.
    """
    t = (title or "").upper()
    has_banned = bool(re.search(r'\b(TRUEFRENCH|VFF|VF2)\b', t))
    if not has_banned:
        return False
    # Accepter quand un tag MULTI ou VOSTFR coexiste (piste FR au sein d'un release multi-audio)
    has_multi  = bool(re.search(r'\bMULTI\b', t))
    has_vostfr = bool(re.search(r'\bVOSTFR\b', t))
    return not (has_multi or has_vostfr)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — année, saison, titre
# ──────────────────────────────────────────────────────────────────────────────

def matches_year(title_str: str, target_year: Optional[int]) -> bool:
    """Vérifie si le torrent correspond à l'année recherchée."""
    if target_year is None:
        return True
    year_pattern = r"\b(19\d{2}|20\d{2})\b"
    years_found = re.findall(year_pattern, title_str)
    if not years_found:
        return True  # Aucune année trouvée → bénéfice du doute
    return str(target_year) in years_found


def matches_season(title_str: str, season: Optional[int], season_re: Optional[re.Pattern]) -> bool:
    """Vérifie si le torrent correspond à la saison recherchée."""
    if season is None or season_re is None:
        return True
    return bool(season_re.search(title_str))


def is_multi_season(title_str: str, multi_season_re: Optional[re.Pattern]) -> bool:
    """Vérifie si le torrent est un pack multi-saisons."""
    if multi_season_re is None:
        return False
    return bool(multi_season_re.search(title_str))


def declares_other_episode(title_str: str, season: Optional[int], episode: Optional[int]) -> bool:
    """True si le titre déclare explicitement un épisode UNIQUE de la saison ciblée
    qui n'est PAS celui demandé (ex. on cherche S01E05, le titre dit S01E04).

    Garde (renvoie False) : l'épisode exact, les packs de saison sans marqueur
    d'épisode, et les plages `SxxEaa-Ebb` / `SxxEaaEbb` qui couvrent l'épisode.
    Sert de filtre dur : un mauvais épisode unique ne doit jamais être candidat,
    sinon le top-N qualité l'emporte sur le bon épisode avant le ranker.
    """
    if season is None or episode is None:
        return False
    # Plages `SxxEaa-(E)bb` (tiret obligatoire pour ne pas confondre avec 2160p) :
    # si la plage couvre l'épisode demandé, on garde.
    for m in re.finditer(rf"(?i)\bS0*{season}E0*(\d+)\s*-\s*E?0*(\d+)\b", title_str):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= episode <= max(a, b):
            return False
    # Épisodes uniques explicites de la saison (gère les clusters `SxxEaaEbb`).
    eps: list[int] = []
    for m in re.finditer(rf"(?i)\bS0*{season}((?:E0*\d+)+)", title_str):
        eps += [int(n) for n in re.findall(r"(?i)E0*(\d+)", m.group(1))]
    if not eps:
        return False  # pas de marqueur d'épisode → pack/saison, peut contenir l'épisode
    return episode not in eps


def normalize_title_for_comparison(title: str) -> str:
    """Normalise un titre pour comparaison (enlève points, tirets, etc.)."""
    return re.sub(r'[.\-_]', ' ', title).lower().strip()


def get_existing_torrents(download_dir: str) -> Tuple[set, int]:
    """
    Récupère les titres normalisés des fichiers torrent existants dans le dossier.

    Returns:
        Tuple de (set des titres normalisés, nombre de fichiers .torrent)
    """
    existing_files = set()
    count = 0

    if not os.path.exists(download_dir):
        return existing_files, count

    for f in os.listdir(download_dir):
        if f.endswith('.torrent'):
            count += 1
            file_title = f.replace('.torrent', '')
            file_title = re.sub(r'\s*\[[^\]]+\](\s*\[[^\]]+\])*\s*$', '', file_title).strip()
            file_title_normalized = normalize_title_for_comparison(file_title)
            existing_files.add(file_title_normalized)

    return existing_files, count


def prepare_torrent_data(raw_torrent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prépare les données d'un torrent brut pour le filtrage.
    Calcule size_gb et num_files.
    """
    title = raw_torrent.get("title") or ""
    size_bytes = raw_torrent.get("size", 0)

    try:
        size_gb = float(size_bytes) / (1024**3)
    except (ValueError, TypeError):
        size_gb = 0.0

    num_files = 1
    if "files" in raw_torrent:
        try:
            num_files = int(raw_torrent["files"])
        except Exception:
            pass

    return {
        "title":           title,
        "name":            title,
        "indexer":         raw_torrent.get("indexer") or "",
        "downloadUrl":     raw_torrent.get("downloadUrl") or raw_torrent.get("guid"),
        "infoHash":        raw_torrent.get("infoHash") or "",
        "seeders":         int(raw_torrent.get("seeders", 0)),
        "leechers":        int(raw_torrent.get("leechers") or raw_torrent.get("peers", 0)),
        "seeders_unknown": bool(raw_torrent.get("seeders_unknown", False)),
        "size":            size_bytes,
        "size_gb":         size_gb,
        "num_files":       num_files,
        "categories":      raw_torrent.get("categories") or raw_torrent.get("category"),
        "is_magnet":       bool(raw_torrent.get("is_magnet", False)),
        "publishDate":     raw_torrent.get("publishDate") or raw_torrent.get("date") or "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# filter_candidates() — NOUVEAU : filtre dur sans scoring ni troncature
# ──────────────────────────────────────────────────────────────────────────────

def filter_candidates(
    raw_results: List[Dict[str, Any]],
    media_type: str,               # "movie" ou "serie"
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    search_title: Optional[str] = None,
    verbose: bool = True,
    imdb_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Filtre les candidats valides sans scoring ni troncature.

    Utilisé pour le comptage inter-sources (seuil de 30) et en interne
    par filter_and_select_torrents().

    Ordre des filtres :
      1. Type (catégorie Torznab ou heuristique selon l'indexer)
      2. Langue — ban TRUEFRENCH / VFF / VF2 / FRENCH-sans-MULTI
      3. Qualité ≥ 1080p — exclut 720p et moins
      4. Année (films) / Saison (séries)
      5. Titre fuzzy
      6. URL non vide

    `episode` (séries) : un mauvais épisode UNIQUE (ex. S01E04 quand on veut
    S01E05) est exclu durement ; l'épisode exact, les packs et les plages qui le
    couvrent sont conservés. Historiquement non bloquant — un torrent qui
    matche la saison est retenu qu'il s'agisse de pack ou d'épisode unique.
    Le boost/penalty au scoring (filter_top_torrents) discrimine au moment
    du ranking.

    `year` (séries) : ignoré ici. Pour les séries, c'est la saison (et non
    l'année) qui discrimine ; le titre d'une release season pack ne porte
    d'ailleurs pas toujours d'année fiable. `year` reste utilisé pour les films.

    Returns:
        (candidats: List[Dict], stats: Dict[str, int])
    """
    stats = {
        "raw_count":             len(raw_results),
        "filtered_by_type":      0,
        "filtered_by_language":  0,
        "filtered_by_quality":   0,
        "filtered_by_year_season": 0,
        "filtered_by_title":     0,
        "filtered_magnets":      0,
    }

    if not raw_results:
        return [], stats

    # Préparer les regex pour les séries
    season_re: Optional[re.Pattern] = None
    multi_season_re: Optional[re.Pattern] = None
    if media_type == "serie" and season is not None:
        season_re = re.compile(
            rf"(?i)(\bS0*{season}(?:[.\-]|\b)|\bS0*{season}E\d+\b"
            rf"|\bseason\s*0*{season}\b|\bsaison\s*0*{season}\b)"
        )
        # Multi-saisons = à exclure quand l'utilisateur cible UNE saison.
        # Les plages exigent un vrai tiret (`\s*-\s*`) : un simple espace ne
        # doit PAS faire passer "Season 1 1080p" pour une plage "1→1080".
        # On ne flague pas "S01E01-08" (plage d'épisodes d'une seule saison).
        multi_season_re = re.compile(
            r"(?i)(S\d+\s*-\s*S\d+|(?:season|saison)s?\s*\d+\s*-\s*\d+"
            r"|\bint[ée]grale?\b|\bseries?\s+complete\b|\bcomplete\s+series\b)",
            re.IGNORECASE,
        )

    # Normaliser le titre recherché — split AVANT de retirer la ponctuation
    # pour que les stop words filtrent réellement et que chaque mot soit
    # cherché individuellement dans le titre du torrent normalisé.
    # Seuil len >= 2 pour conserver "up", "ed" et autres titres courts.
    #
    # Quand imdb_id est fourni, on désactive le filtre titre client : les
    # indexers honorant IMDb peuvent retourner le titre en langue locale
    # (ex. "La.Haut" pour Up, ou versions JP/ES) qui ne matchera pas le
    # titre anglais. On fait confiance au filtre server-side IMDb.
    search_words: List[str] = []
    short_title_strict_re: Optional[re.Pattern] = None
    if search_title and media_type in ("serie", "movie") and not imdb_id:
        stop_words = {"the", "le", "la", "les", "un", "une", "de", "du", "des"}
        raw_words = re.split(r"[\s\-._]+", search_title.lower().strip())
        search_words = [w for w in raw_words if len(w) >= 2 and w not in stop_words]
        # Titres courts (≤4 chars total) → ancrage strict en début de release.
        # Le titre doit apparaître comme premier mot du release name
        # (ex. "Up.2009.1080p…" OK ; "Wake.Up.Sid…" rejeté).
        # On accepte optionnellement les chars d'ouverture style "[", "(", "[YTS]".
        if len(search_title.strip()) <= 4:
            short_title_strict_re = re.compile(
                rf"^\W*{re.escape(search_title.strip())}\b", re.IGNORECASE
            )

    filtered_results: List[Dict[str, Any]] = []

    for it in raw_results:
        title = it.get("title") or ""
        idx_norm = normalize_indexer_name(it.get("indexer", ""))
        cats = it.get("categories") or it.get("category")

        # ── Étape 1 : filtre type ─────────────────────────────────────────────
        if media_type == "movie":
            if idx_norm in ["yggapi", "c411"]:
                if is_tv_or_non_movie(title) or not is_movie_like(title):
                    stats["filtered_by_type"] += 1
                    continue
            else:
                if cats is not None:
                    if not is_movie_categories(cats):
                        stats["filtered_by_type"] += 1
                        continue
                else:
                    if is_tv_or_non_movie(title) or not is_movie_like(title):
                        stats["filtered_by_type"] += 1
                        continue

        elif media_type == "serie":
            if cats is not None:
                if not is_tv_categories(cats):
                    stats["filtered_by_type"] += 1
                    continue
            else:
                if not is_tv_like(title):
                    stats["filtered_by_type"] += 1
                    continue

        # ── Étape 2 : filtre langue ───────────────────────────────────────────
        if is_banned_language(title):
            stats["filtered_by_language"] += 1
            continue

        # ── Étape 3 : filtre qualité ≥ 1080p ─────────────────────────────────
        if is_below_1080p(title):
            stats["filtered_by_quality"] += 1
            continue

        # ── Étape 4 : filtre année / saison ──────────────────────────────────
        if media_type == "movie":
            if not matches_year(title, year):
                stats["filtered_by_year_season"] += 1
                continue

        elif media_type == "serie":
            if not matches_season(title, season, season_re):
                stats["filtered_by_year_season"] += 1
                continue
            if is_multi_season(title, multi_season_re):
                stats["filtered_by_year_season"] += 1
                continue
            # Épisode demandé : exclure durement un mauvais épisode unique
            # (garde l'épisode exact, les packs et les plages qui le couvrent).
            if episode is not None and declares_other_episode(title, season, episode):
                stats["filtered_by_year_season"] += 1
                continue

        # ── Étape 5 : filtre titre fuzzy ──────────────────────────────────────
        if short_title_strict_re is not None:
            # Titre court : match strict word-boundary sur titre original
            if not short_title_strict_re.search(title):
                stats["filtered_by_title"] += 1
                continue
        elif search_words:
            title_normalized = (
                title.lower()
                .replace(" ", "").replace("-", "").replace(".", "").replace("_", "")
            )
            matched = sum(1 for w in search_words if w in title_normalized)
            # Au moins 50% des mots significatifs doivent matcher
            if matched * 2 < len(search_words):
                stats["filtered_by_title"] += 1
                continue

        # ── Étape 6 : URL non vide ────────────────────────────────────────────
        dl = it.get("downloadUrl") or it.get("guid")
        if not dl or not isinstance(dl, str):
            stats["filtered_magnets"] += 1
            continue

        rec = prepare_torrent_data(it)
        filtered_results.append(rec)

    if verbose:
        label = f"année={year or 'ANY'}" if media_type == "movie" else f"saison={season or 'ANY'}"
        print(
            f"[INFO] filter_candidates ({label}): "
            f"{len(filtered_results)} candidats "
            f"(type={stats['filtered_by_type']} excl, "
            f"langue={stats['filtered_by_language']} excl, "
            f"qualité={stats['filtered_by_quality']} excl, "
            f"année/saison={stats['filtered_by_year_season']} excl, "
            f"titre={stats['filtered_by_title']} excl)"
        )

    return filtered_results, stats


# ──────────────────────────────────────────────────────────────────────────────
# filter_top_torrents() — scoring mis à jour (audio + langue + regex Radarr)
# ──────────────────────────────────────────────────────────────────────────────

def filter_top_torrents(
    torrents: List[Dict[str, Any]],
    limit: int = MAX_TORRENTS_FOR_LLM,
) -> List[Dict[str, Any]]:
    """
    Sélectionne les N meilleurs torrents par score de priorité.

    Barème (issue des custom formats Radarr/Sonarr) :

    Résolution :
      4K/UHD/2160p          +4.0
      1080p/1080i            +1.0

    HDR/DV :
      Dolby Vision           +3.0
      HDR/HDR10/HDR10+       +1.0  (pas de cumul avec DV)

    Codec vidéo :
      x265/HEVC/H.265        +1.0

    Audio (hiérarchie Radarr — tiers exclusifs) :
      Tier 1 — Atmos / TrueHD          +2.5
      Tier 2 — EAC3 / DD+ / DDP        +1.5
      Tier 3 — AC3 / DD5.1             +0.5
      DTS (tous variants)              -1.5  (pénalité)
      FLAC                             +0.5  (cumulable)

    Langue :
      MULTI ou VOSTFR                  +1.5

    Seeders :
      < 3                              -4.0
      5–9                              +0.5
      10–19                            +1.0
      ≥ 20                             +1.5
      (inconnu = pas de pénalité)

    Taille effective (Go) :
      ≤ 25                             +1.0
      25–40                            -1.0
      > 40                             -2.0
    """
    if not torrents:
        return []

    def priority_score(t: Dict[str, Any]) -> float:
        title = t.get('name', t.get('title', '')).upper()
        score = 0.0

        # ── 1) Résolution ────────────────────────────────────────────────────
        is_4k = bool(re.search(r'\b(2160P|4K|UHD|ULTRA[\s\.]?HD)\b', title))
        if is_4k:
            score += 4.0
        elif re.search(r'\b(1080P|1080I)\b', title):
            score += 1.0

        # ── 2) HDR / Dolby Vision — regex issues Radarr ──────────────────────
        has_dv = bool(re.search(r'\b(DV|DOVI|DOLBY[\W]?VISION)\b', title))
        if has_dv:
            score += 3.0

        has_hdr = bool(re.search(r'\b(HDR|HDR10|HDR10PLUS|HDR10\+)\b', title))
        if has_hdr and not has_dv:   # pas de double bonus DV+HDR
            score += 1.0

        # ── 3) Codec vidéo ───────────────────────────────────────────────────
        if re.search(r'\b(X265|HEVC|H[\.\-]?265)\b', title):
            score += 1.0

        # ── 4) Audio — hiérarchie Radarr/Sonarr (tiers exclusifs) ───────────
        # Tier 1 : Atmos / TrueHD   → \b(Atmos|TrueHD)\b
        if re.search(r'\b(ATMOS|TRUEHD)\b', title):
            score += 2.5
        # Tier 2 : EAC3 / DD+ / DDP → \b(EAC3|[DE]AC3|DD\.?P|DDP|DD\+)\b
        elif re.search(r'\b(EAC3|DAC3|DD[\.\s]?P|DDP|DD\+)\b', title):
            score += 1.5
        # Tier 3 : AC3 / DD5.1      → \b(AC3|DD5\.?1|5\.1)\b
        elif re.search(r'\b(AC3|DD5[\.\s]?1)\b', title) or '5.1' in title:
            score += 0.5

        # Pénalité DTS (tous variants) → \bDTS(\W?HD|MA|X)?\b
        if re.search(r'\bDTS(\W?(HD|MA|X))?\b', title):
            score -= 1.5

        # FLAC (lossless, souvent REMUX — cumulable avec les tiers)
        if 'FLAC' in title:
            score += 0.5

        # ── 5) Langue ────────────────────────────────────────────────────────
        # MULTI ou VOSTFR → \b(MULTI|VOSTFR)\b
        if re.search(r'\b(MULTI|VOSTFR)\b', title):
            score += 1.5
        # (TRUEFRENCH / VFF / VF2 / FRENCH déjà éliminés par filter_candidates)

        # ── 6) Seeders ───────────────────────────────────────────────────────
        seeders = t.get('seeders', 0)
        if t.get('seeders_unknown', False):
            pass  # Pas de pénalité (YGG Gratis / NIP-35)
        elif seeders < 3:
            score -= 4.0
        elif 5 <= seeders < 10:
            score += 0.5
        elif 10 <= seeders < 20:
            score += 1.0
        elif seeders >= 20:
            score += 1.5

        # ── 7) Taille ────────────────────────────────────────────────────────
        size_gb = t.get('size_gb', 0)
        num_files = t.get('num_files', 1)
        effective_size = size_gb / num_files if num_files > 1 else size_gb

        if effective_size <= 25:
            score += 1.0
        elif effective_size <= 40:
            score -= 1.0
        else:
            score -= 2.0

        return score

    scored = [(priority_score(t), t) for t in torrents]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:limit]]


# ──────────────────────────────────────────────────────────────────────────────
# filter_and_select_torrents() — wrapper public (signature identique)
# ──────────────────────────────────────────────────────────────────────────────

def filter_and_select_torrents(
    raw_results: List[Dict[str, Any]],
    media_type: str,
    download_dir: str,
    max_torrents: int = MAX_TORRENTS_FOR_LLM,
    year: Optional[int] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    search_title: Optional[str] = None,
    verbose: bool = True,
    imdb_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Filtre, score et sélectionne les meilleurs torrents pour le LLM.

    `episode` (séries) : annoté sur chaque candidat retourné via
    `_episode_match` (bool) pour que le ranker downstream sache si le titre
    matche explicitement S{season}E{episode}.

    Étapes :
      1. filter_candidates()    — filtres durs (type, langue, qualité, année, titre, URL)
      2. get_existing_torrents() — vérification des fichiers déjà téléchargés
      3. filter_top_torrents()  — scoring et sélection top N
      4. Dédup existants + troncature finale
    """
    # Étape 1 : filtrage via filter_candidates
    candidates, stats = filter_candidates(
        raw_results, media_type, year, season, episode, search_title, verbose,
        imdb_id=imdb_id,
    )

    # Annoter chaque candidat avec _episode_match si episode demandé
    if media_type == "serie" and episode is not None and season is not None:
        ep_re = re.compile(rf"(?i)\bS0*{season}E0*{episode}\b")
        for c in candidates:
            c["_episode_match"] = bool(ep_re.search(c.get("title") or ""))

    # Agréger les stats manquantes pour rétrocompatibilité
    stats.setdefault("selected", 0)
    stats.setdefault("existing", 0)
    stats.setdefault("final", 0)

    if not candidates:
        return [], stats

    magnet_count = sum(1 for r in candidates if (r.get("downloadUrl") or "").startswith("magnet:"))
    if verbose and magnet_count > 0:
        print(f"[INFO] {magnet_count} magnet(s), {len(candidates) - magnet_count} torrent(s) fichier(s)")

    # Étape 2 : fichiers existants
    existing_files, existing_count = get_existing_torrents(download_dir)
    stats["existing"] = existing_count

    limit_for_selection = max_torrents * 2 if existing_count > 0 else max_torrents

    # Étape 3 : scoring
    scored_torrents = filter_top_torrents(candidates, limit=limit_for_selection)
    stats["selected"] = len(scored_torrents)

    if verbose:
        print(f"[INFO] Torrents sélectionnés: {len(scored_torrents)} (max: {max_torrents})")

    # Étape 4 : exclure les doublons de fichiers existants + troncature
    releases_to_download = []
    for r in scored_torrents:
        title = r.get("title") or (
            f"{search_title} {year or ''}" if media_type == "movie" else search_title or ""
        )
        title_normalized = normalize_title_for_comparison(title)
        if title_normalized not in existing_files:
            releases_to_download.append(r)

    max_to_download = max(0, max_torrents - existing_count)
    releases_to_download = releases_to_download[:max_to_download]
    stats["final"] = len(releases_to_download)

    if verbose and existing_count > 0:
        print(
            f"[INFO] {existing_count} torrent(s) déjà téléchargé(s), "
            f"{len(releases_to_download)} nouveau(x) à télécharger (max: {max_to_download})"
        )

    return releases_to_download, stats
