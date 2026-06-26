#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
c411_api_client.py
Client pour l'API JSON de C411 (c411.org).

Approche active : API JSON (/api/torrents) + magnet links.
Les magnet links fonctionnent parfaitement avec qBittorrent (100% en <60s, testé).

Usage:
    from c411_api_client import C411APIClient

    client = C411APIClient.from_env()
    results = client.search_movies("Dune", year=2021)
    # results[i]["downloadUrl"] est un magnet link → passer directement à qBittorrent
"""

from __future__ import annotations
import os
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import requests

# ──────────────────────────────────────────────
# Catégories C411 (API JSON /api/torrents)
# ──────────────────────────────────────────────
C411_CATEGORIES = {
    "FILMS_VIDEOS": 1,
    "EBOOK":        2,
    "AUDIO":        3,
    "APPLICATIONS": 4,
    "JEUX_VIDEO":   5,
}

C411_SUBCATEGORIES = {
    "ANIMATION":      1,
    "ANIMATION_SERIE": 2,
    "CONCERT":        3,
    "DOCUMENTAIRE":   4,
    "EMISSION_TV":    5,
    "FILM":           6,
    "SERIE_TV":       7,
    "SPECTACLE":      8,
    "SPORT":          9,
    "VIDEO_CLIPS":    12,
}

DEFAULT_TIMEOUT = (5, 30)  # (connexion, lecture)


class C411APIClient:
    """
    Client pour l'API JSON de C411.

    Recherche via GET /api/torrents (Bearer token).
    Retourne des magnet links construits depuis l'infoHash + announce C411.
    Compatible qBittorrent qui gère parfaitement les magnets de tracker privé.
    """

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://c411.org/api",
        passkey: Optional[str] = None,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.passkey = passkey

        # URL d'announce pour le tracker privé C411
        base = self.api_url
        if base.endswith("/api"):
            base = base[:-4]
        self.tracker_url = f"{base}/announce/{passkey}" if passkey else None

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent":    "AgentTorrent/1.0",
            "Authorization": f"Bearer {self.api_key}",
        })

    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "C411APIClient":
        """
        Crée un client depuis les variables d'environnement.

        Lit C411_API_KEY, C411_URL_API et C411_PASSKEY depuis .env.
        Le passkey est nécessaire pour construire l'announce URL dans les magnet links.
        """
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path) if env_path else load_dotenv()
        except ImportError:
            pass

        api_key = os.environ.get("C411_API_KEY")
        api_url = os.environ.get("C411_URL_API", "https://c411.org/api")
        passkey = os.environ.get("C411_PASSKEY")

        # Fallback lecture manuelle si dotenv absent
        _env = env_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".env"
        )
        if (not api_key or not passkey) and os.path.exists(_env):
            try:
                with open(_env, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k == "C411_API_KEY":
                                api_key = v
                            elif k == "C411_URL_API":
                                api_url = v
                            elif k == "C411_PASSKEY":
                                passkey = v
            except Exception as e:
                print(f"[WARN] Erreur lecture .env: {e}", file=sys.stderr)

        if not api_key:
            raise RuntimeError("Variable C411_API_KEY manquante dans .env")
        if not passkey:
            print(
                "[WARN] C411_PASSKEY absent — les magnet links n'auront pas d'announce "
                "C411 et ne fonctionneront pas sur ce tracker privé.",
                file=sys.stderr,
            )

        return cls(api_key=api_key, api_url=api_url, passkey=passkey)

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _format_query(query: str) -> str:
        """Formate la requête en style dotted (convention noms de torrents)."""
        return query.strip().replace(" ", ".")

    @staticmethod
    def _matches_query(title: str, query: str) -> bool:
        """Vérifie que chaque mot significatif de la requête est présent dans le titre."""
        title_lower = title.lower()
        terms = re.split(r"[\s.]+", query.lower().strip())
        # Ignorer les tokens vides ou purement symboliques (":", "'", "-"…)
        meaningful = [t for t in terms if t and re.search(r"[a-z0-9]", t)]
        return all(t in title_lower for t in meaningful)

    def _build_magnet(self, info_hash: str, torrent_name: str) -> str:
        """Construit un magnet link avec l'announce du tracker privé C411."""
        tr_param = f"&tr={quote(self.tracker_url, safe='')}" if self.tracker_url else ""
        return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(torrent_name)}{tr_param}"

    # ──────────────────────────────────────────────
    # Méthodes publiques
    # ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        category_id: Optional[int] = None,
        subcategory_id: Optional[int] = None,
        options: Optional[Dict] = None,
        per_page: int = 100,
        page: int = 1,
        # Paramètres Torznab hérités — ignorés, pour rétro-compatibilité
        categories: Optional[List[int]] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche sur C411 via l'API JSON (/api/torrents).

        Args:
            query: Terme de recherche
            category_id: ID de catégorie C411 (C411_CATEGORIES)
            subcategory_id: ID de sous-catégorie (C411_SUBCATEGORIES)
            per_page: Nombre de résultats (max 100)
            page: Page de résultats

        Returns:
            Liste de dicts normalisés. Le champ downloadUrl est un magnet link.
        """
        formatted_query = self._format_query(query)

        params: Dict[str, Any] = {
            "name":    formatted_query,
            "perPage": per_page,
            "page":    page,
        }
        if category_id:
            params["categoryId"] = category_id
        if subcategory_id:
            params["subcategoryId"] = subcategory_id
        if options:
            for opt_id, opt_val in options.items():
                params[f"options[{opt_id}]"] = opt_val

        try:
            url = f"{self.api_url}/torrents"
            resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)

            # Fallback si Bearer échoue
            if resp.status_code == 401:
                self.session.headers.update({"Authorization": self.api_key})
                resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)

            resp.raise_for_status()
            data = resp.json()

        except requests.exceptions.RequestException as e:
            print(f"[WARN] Erreur requête C411: {e}", file=sys.stderr)
            return []
        except Exception as e:
            print(f"[ERROR] Erreur inattendue C411: {e}", file=sys.stderr)
            return []

        torrents = data.get("data", data) if isinstance(data, dict) else data

        normalized = []
        for item in torrents:
            def get_id(val):
                return val.get("id") if isinstance(val, dict) else val

            cat_id    = get_id(item.get("category"))
            subcat_id = get_id(item.get("subcategory"))

            info_hash    = item.get("infoHash", "")
            torrent_name = item.get("name", "")

            # Magnet link avec announce C411 (tracker privé)
            magnet = self._build_magnet(info_hash, torrent_name) if info_hash else ""

            normalized.append({
                "title":       torrent_name,
                "indexer":     "C411",
                "downloadUrl": magnet,
                "infoHash":    info_hash,
                "guid":        item.get("link", f"https://c411.org/torrents/{item.get('id')}"),
                "size":        item.get("size", 0),
                "seeders":     int(item.get("seeders", 0) or 0),
                "leechers":    int(item.get("leechers", 0) or 0),
                "downloads":   int(item.get("completions", 0) or 0),
                "categories":  [c for c in [cat_id, subcat_id] if c],
                "category":    cat_id,
                "publishDate": item.get("createdAt", ""),
                "_c411_id":    item.get("id"),
                "is_magnet":   True,
            })

        filtered = [r for r in normalized if self._matches_query(r["title"], query)]
        if not filtered and normalized:
            print(
                f"[INFO] Aucun résultat ne contient exactement '{query}' "
                f"parmi {len(normalized)} résultats API",
                file=sys.stderr,
            )
        return filtered

    def search_movies(
        self,
        title: str,
        year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche de films sur C411."""
        # Nettoyer le titre : retirer la ponctuation spéciale (: ; ' " ! ?)
        # qui perturbe l'API et _matches_query (ex: "Avatar : La Voie de l'Eau")
        clean_title = re.sub(r"[^\w\s]", " ", title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        query = f"{clean_title} {year}" if year else clean_title
        results = self.search(
            query,
            category_id=C411_CATEGORIES["FILMS_VIDEOS"],
            subcategory_id=C411_SUBCATEGORIES["FILM"],
        )

        # Fallback 1 : 0 résultats avec titre complet + année → réessayer sans année
        # (C411 peut ne pas trouver quand l'année est collée à un long titre)
        if not results and year:
            print(f"[INFO] Fallback1: sans année pour '{clean_title}'", file=sys.stderr)
            results = self.search(
                clean_title,
                category_id=C411_CATEGORIES["FILMS_VIDEOS"],
                subcategory_id=C411_SUBCATEGORIES["FILM"],
            )

        # Fallback 2 : 0 résultats → chercher avec seulement le 1er mot + année
        # Gère les films dont C411 indexe le titre en français
        # ex: "Avatar Fire and Ash" → "Avatar" (trouve "Avatar.Feu.et.Cendre.2025")
        main_keyword = clean_title.split()[0] if clean_title.split() else clean_title
        if not results and len(clean_title.split()) > 1:
            print(
                f"[INFO] Fallback2: mot-clé principal '{main_keyword}' + année {year}",
                file=sys.stderr,
            )
            results = self.search(
                f"{main_keyword} {year}" if year else main_keyword,
                category_id=C411_CATEGORIES["FILMS_VIDEOS"],
                subcategory_id=C411_SUBCATEGORIES["FILM"],
            )

        # Fallback 3 : toujours 0 → mot-clé principal seul (sans année)
        if not results and year and len(clean_title.split()) > 1:
            print(f"[INFO] Fallback3: mot-clé '{main_keyword}' sans année", file=sys.stderr)
            results = self.search(
                main_keyword,
                category_id=C411_CATEGORIES["FILMS_VIDEOS"],
                subcategory_id=C411_SUBCATEGORIES["FILM"],
            )

        return results

    def search_series(
        self,
        title: str,
        season: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche de séries TV sur C411.

        Si `season` est fourni, on tente d'abord une requête `Title S{NN}`
        (narrowing) ; à défaut de résultat on retombe sur le titre seul, le
        filtre saison downstream (`torrent_filter`) restant le garde-fou.
        """
        cat = C411_CATEGORIES["FILMS_VIDEOS"]
        subcat = C411_SUBCATEGORIES["SERIE_TV"]

        if season is not None:
            results = self.search(
                f"{title} S{season:02d}",
                category_id=cat,
                subcategory_id=subcat,
            )
            if results:
                return results
            print(f"[INFO] C411 Series Fallback: '{title}' sans saison", file=sys.stderr)

        return self.search(title, category_id=cat, subcategory_id=subcat)

    def download_torrent(self, download_url: str) -> bytes:
        """
        Non utilisé avec l'approche magnet (download_url est un magnet link).

        Pour télécharger un fichier .torrent binaire depuis C411,
        voir la section BACKUP Torznab ci-dessous.
        """
        raise NotImplementedError(
            "download_torrent() n'est pas utilisé avec l'approche magnet. "
            "Passer directement le magnet link (downloadUrl) à qBittorrent. "
            "Pour télécharger un .torrent binaire, réactiver l'approche Torznab (voir BACKUP ci-dessous)."
        )


# ══════════════════════════════════════════════════════════════════════════════
# ██████████████████████  BACKUP — API TORZNAB  ██████████████████████████████
# ══════════════════════════════════════════════════════════════════════════════
#
# DÉCOMMISSIONNÉ le 2026-03-09 — conservé comme backup.
#
# CONTEXTE :
#   L'API Torznab native de C411 (/api/torznab) a été découverte via la
#   définition Jackett (https://github.com/Jackett/Jackett/blob/master/
#   src/Jackett.Common/Definitions/c411.yml) et permet de télécharger de
#   vrais fichiers .torrent (private:1, announce C411 inclus) via :
#     GET /api?t=get&id={infohash}&apikey={key}
#
# POURQUOI DÉCOMMISSIONNÉ :
#   Les tests ont montré que qBittorrent gère parfaitement les magnet links
#   de tracker privé (100% en <60s, 173 Mo/s). L'approche magnet est plus
#   simple et ne nécessite pas de télécharger un fichier .torrent intermédiaire.
#   Le bug de "boucle infinie" (225 Go téléchargés pour 7.69 Go) était lié à
#   Transmission, pas à l'approche magnet.
#
# POUR RÉACTIVER :
#   Remplacer la méthode search() ci-dessus par _search_torznab() ci-dessous,
#   et remplacer download_torrent() par _download_torrent_torznab().
#
# ══════════════════════════════════════════════════════════════════════════════

# import xml.etree.ElementTree as ET
# _TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
# _TORZNAB_CATEGORIES = {
#     "FILMS": 2000, "SERIE_TV": 5000, "SERIE_ANIME": 5070,
#     "AUDIO": 3000, "JEUX_VIDEO": 1000, "APPLICATIONS": 4000, "EBOOK": 7000,
# }
#
# def _search_torznab(self, query, categories=None, season=None, episode=None,
#                     imdb_id=None, limit=100, **kwargs):
#     """Recherche via API Torznab native C411 (/api/torznab)."""
#     t_type = "movie" if imdb_id else ("tvsearch" if season or episode else "search")
#     formatted_q = re.sub(r"\W+", "%", query.strip())
#     params = {"apikey": self.api_key, "t": t_type, "q": formatted_q, "limit": limit}
#     if categories: params["cat"] = ",".join(str(c) for c in categories)
#     if season:     params["season"] = season
#     if episode:    params["ep"] = episode
#     if imdb_id:    params["imdbid"] = imdb_id
#     torznab_url = self.api_url.rstrip("/api") if self.api_url.endswith("/api") \
#                   else self.api_url.rsplit("/", 1)[0]
#     torznab_url = torznab_url.rstrip("/") + "/api/torznab"
#     resp = self.session.get(torznab_url, params=params, timeout=DEFAULT_TIMEOUT)
#     resp.raise_for_status()
#     # Parser le XML Torznab — chaque item a <enclosure url="..."> avec l'URL de DL
#     root = ET.fromstring(resp.text)
#     results = []
#     for item in root.findall(".//item"):
#         title_el = item.find("title")
#         enc = item.find("enclosure")
#         if not title_el or enc is None: continue
#         title = title_el.text.strip()
#         download_url = enc.get("url", "")
#         infohash = next((a.get("value","") for a in item.findall(
#             f"{{{_TORZNAB_NS}}}attr") if a.get("name")=="infohash"), "")
#         seeders = int(next((a.get("value",0) for a in item.findall(
#             f"{{{_TORZNAB_NS}}}attr") if a.get("name")=="seeders"), 0))
#         size_el = item.find("size")
#         size = int(size_el.text or 0) if size_el is not None else 0
#         results.append({"title": title, "indexer": "C411",
#             "downloadUrl": download_url, "infoHash": infohash,
#             "seeders": seeders, "size": size, "is_magnet": False})
#     filtered = [r for r in results if self._matches_query(r["title"], query)]
#     return filtered
#
# def _download_torrent_torznab(self, download_url):
#     """
#     Télécharge un fichier .torrent binaire depuis l'endpoint Torznab C411.
#     URL format: https://c411.org/api?t=get&id={infohash}&apikey={key}
#     Retourne bytes (bencode, private:1, announce C411 avec passkey intégré).
#     """
#     if "apikey" not in download_url and self.api_key:
#         sep = "&" if "?" in download_url else "?"
#         download_url = f"{download_url}{sep}apikey={self.api_key}"
#     resp = self.session.get(download_url, timeout=DEFAULT_TIMEOUT)
#     resp.raise_for_status()
#     if resp.content[:1] != b"d":
#         raise RuntimeError(f"Pas un .torrent bencode: {resp.content[:50]}")
#     return resp.content

# ══════════════════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────
# CLI de test
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test du client C411 (API JSON + magnet)")
    parser.add_argument("--query", required=True, help="Terme de recherche")
    args = parser.parse_args()

    try:
        client = C411APIClient.from_env()
        print(f"🔍 Recherche: '{args.query}'", file=sys.stderr)
        results = client.search(args.query)
        print(f"✅ {len(results)} résultats pour '{args.query}'")
        for r in results[:5]:
            print(f"  - {r['title']}")
            print(f"    Seeds: {r['seeders']} | Taille: {r['size']:,} bytes")
            print(f"    Magnet: {r['downloadUrl'][:80]}...")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        sys.exit(1)
