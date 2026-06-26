#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prowlarr_client.py
Client pour l'API Prowlarr (recherche de torrents via indexers agrégés).

Prowlarr est un gestionnaire d'indexers qui agrège les résultats de multiples
sources (1337x, RARBG, etc.) via une seule API REST.

Usage:
    from prowlarr_client import ProwlarrClient

    client = ProwlarrClient.from_env()
    results = client.search_movies("Dune", year=2021)
    # results[i]["downloadUrl"] est un magnet link ou une URL de téléchargement
"""

from __future__ import annotations
import os
import re
import sys
from typing import Any, Dict, List, Optional

import requests

DEFAULT_TIMEOUT = (5, 30)  # (connexion, lecture)


def _merge_dedup(
    primary: List[Dict[str, Any]],
    extra: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fusionne deux listes de résultats normalisés en dédupliquant.

    Clé = infoHash (insensible casse) sinon downloadUrl sinon titre. Préserve
    l'ordre (primary d'abord) pour ne pas perdre le ranking server-side.
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in list(primary) + list(extra):
        key = (r.get("infoHash") or "").lower() or r.get("downloadUrl") or r.get("title")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


class ProwlarrClient:
    """
    Client pour l'API REST Prowlarr v1.

    Recherche via GET /api/v1/search (authentification X-Api-Key).
    Retourne des résultats normalisés compatibles avec C411APIClient.
    """

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AgentTorrent/1.0",
            "X-Api-Key":  api_key,
        })

    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "ProwlarrClient":
        """
        Crée un client depuis les variables d'environnement.

        Lit PROWLARR_URL et PROWLARR_API_KEY depuis .env.
        """
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path) if env_path else load_dotenv()
        except ImportError:
            pass

        api_url = os.environ.get("PROWLARR_URL")
        api_key = os.environ.get("PROWLARR_API_KEY")

        # Fallback lecture manuelle si dotenv absent
        _env = env_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".env"
        )
        if (not api_url or not api_key) and os.path.exists(_env):
            try:
                with open(_env, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k == "PROWLARR_URL":
                                api_url = v
                            elif k == "PROWLARR_API_KEY":
                                api_key = v
            except Exception as e:
                print(f"[WARN] Erreur lecture .env: {e}", file=sys.stderr)

        if not api_url:
            raise RuntimeError("Variable PROWLARR_URL manquante dans .env")
        if not api_key:
            raise RuntimeError("Variable PROWLARR_API_KEY manquante dans .env")

        return cls(api_url=api_url, api_key=api_key)

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _matches_query(title: str, query: str) -> bool:
        """Vérifie que chaque mot significatif de la requête est présent dans le titre."""
        title_lower = re.sub(r"[._\-]", " ", title.lower())
        terms = re.split(r"[\s.]+", query.lower().strip())
        meaningful = [t for t in terms if t and re.search(r"[a-z0-9]", t)]
        return all(t in title_lower for t in meaningful)

    @staticmethod
    def _normalize_result(item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise un résultat Prowlarr (ReleaseResource) au format standard.

        Prowlarr retourne:
        - title, indexer, downloadUrl, magnetUrl, infoHash
        - size, seeders, leechers, grabs, publishDate
        - categories (list of dicts with "id" and "name")
        """
        # Préférer magnetUrl quand disponible
        magnet_url = item.get("magnetUrl") or ""
        download_url = item.get("downloadUrl") or ""
        final_url = magnet_url or download_url
        is_magnet = final_url.startswith("magnet:")

        # Extraire les catégories (Prowlarr retourne des dicts {id, name})
        raw_cats = item.get("categories") or []
        categories = []
        for cat in raw_cats:
            if isinstance(cat, dict):
                cat_id = cat.get("id")
                if cat_id is not None:
                    categories.append(int(cat_id))
            elif isinstance(cat, int):
                categories.append(cat)

        # Nom de l'indexer source (préfixé Prowlarr:)
        indexer_name = item.get("indexer") or "Unknown"

        return {
            "title":       item.get("title", ""),
            "indexer":     f"Prowlarr:{indexer_name}",
            "downloadUrl": final_url,
            "infoHash":    item.get("infoHash") or "",
            "guid":        item.get("guid") or "",
            "size":        item.get("size") or 0,
            "seeders":     int(item.get("seeders") or 0),
            "leechers":    int(item.get("leechers") or 0),
            "downloads":   int(item.get("grabs") or 0),
            "categories":  categories,
            "publishDate": item.get("publishDate") or "",
            "is_magnet":   is_magnet,
            "_prowlarr_indexer": indexer_name,
        }

    # ──────────────────────────────────────────────
    # Méthodes publiques
    # ──────────────────────────────────────────────

    def list_indexers(self) -> List[Dict[str, Any]]:
        """
        Liste les indexers configurés dans Prowlarr.

        Returns:
            Liste de dicts avec id, name, enable, protocol, privacy, etc.
        """
        try:
            url = f"{self.api_url}/api/v1/indexer"
            resp = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Erreur listing indexers Prowlarr: {e}", file=sys.stderr)
            return []

    def search(
        self,
        query: str,
        categories: Optional[List[int]] = None,
        search_type: str = "search",
        limit: int = 100,
        imdb_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche sur Prowlarr via GET /api/v1/search.

        Args:
            query: Terme de recherche
            categories: Liste d'IDs de catégorie Torznab (ex: [2000] pour films)
            search_type: Type de recherche ("search", "movie", "tvsearch")
            limit: Nombre max de résultats
            imdb_id: Identifiant IMDb optionnel (ex. "tt1049413") — propagé en
                     param `imdbid` Torznab. Si fourni, le filtre client-side
                     par titre est désactivé (l'indexer filtre déjà server-side).

        Returns:
            Liste de dicts normalisés au format standard.
        """
        params: Dict[str, Any] = {
            "query": query,
            "type": search_type,
            "limit": limit,
        }
        if categories:
            params["categories"] = categories
        if imdb_id:
            params["imdbid"] = imdb_id

        try:
            url = f"{self.api_url}/api/v1/search"
            resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"[WARN] Erreur requête Prowlarr: {e}", file=sys.stderr)
            return []
        except Exception as e:
            print(f"[ERROR] Erreur inattendue Prowlarr: {e}", file=sys.stderr)
            return []

        if not isinstance(data, list):
            data = data.get("data", []) if isinstance(data, dict) else []

        normalized = [self._normalize_result(item) for item in data]

        # Si imdb_id fourni → filtrage server-side, on retourne brut
        if imdb_id:
            return normalized

        # Sinon : filtrage client-side par titre (fallback historique)
        filtered = [r for r in normalized if self._matches_query(r["title"], query)]
        if not filtered and normalized:
            print(
                f"[INFO] Prowlarr: aucun résultat ne contient '{query}' "
                f"parmi {len(normalized)} résultats API",
                file=sys.stderr,
            )
        return filtered

    def search_movies(
        self,
        title: str,
        year: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche de films sur Prowlarr."""
        clean_title = re.sub(r"[^\w\s]", " ", title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # Avec imdb_id : strip l'année de la query (IMDb encode déjà l'année
        # et l'ajouter pollue le matching sur indexers fragiles).
        if imdb_id:
            query = clean_title
        else:
            query = f"{clean_title} {year}" if year else clean_title
        results = self.search(query, categories=[2000], imdb_id=imdb_id)

        # Si imdb_id fourni : pas de fallback fuzzy (zéro résultat préféré aux
        # faux positifs). L'appelant escaladera vers C411 si besoin.
        if imdb_id:
            return results

        # Fallback 1 : sans année
        if not results and year:
            print(f"[INFO] Prowlarr Fallback1: sans année pour '{clean_title}'", file=sys.stderr)
            results = self.search(clean_title, categories=[2000])

        # Fallback 2 : mot-clé principal + année
        main_keyword = clean_title.split()[0] if clean_title.split() else clean_title
        if not results and len(clean_title.split()) > 1:
            print(f"[INFO] Prowlarr Fallback2: mot-clé '{main_keyword}'", file=sys.stderr)
            results = self.search(
                f"{main_keyword} {year}" if year else main_keyword,
                categories=[2000],
            )

        return results

    def search_series(
        self,
        title: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche de séries TV sur Prowlarr."""
        clean_title = re.sub(r"[^\w\s]", " ", title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # Avec imdb_id : strip S{NN}/épisode de la query (indexers fragiles
        # peuvent mal aggregate IMDb + token saison). Filtre server-side.
        if imdb_id:
            return self.search(clean_title, categories=[5000], imdb_id=imdb_id)

        query = f"{clean_title} S{season:02d}" if season is not None else clean_title
        results = self.search(query, categories=[5000])

        # Épisode précis : compléter avec une requête S{ss}E{ee} pour remonter
        # les épisodes isolés que la recherche saison ne couvre pas toujours,
        # puis dédupliquer (infoHash sinon downloadUrl).
        if season is not None and episode is not None:
            ep_query = f"{clean_title} S{season:02d}E{episode:02d}"
            ep_results = self.search(ep_query, categories=[5000])
            results = _merge_dedup(results, ep_results)

        # Fallback : sans filtre de saison
        if not results and season is not None:
            print(f"[INFO] Prowlarr Series Fallback: '{clean_title}' sans saison", file=sys.stderr)
            results = self.search(clean_title, categories=[5000])

        return results


# ──────────────────────────────────────────────
# CLI de test
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test du client Prowlarr")
    parser.add_argument("--query", required=True, help="Terme de recherche")
    parser.add_argument("--type", choices=["movie", "serie"], default=None, help="Type de média")
    parser.add_argument("--year", type=int, default=None, help="Année (pour films)")
    parser.add_argument("--list-indexers", action="store_true", help="Lister les indexers configurés")
    args = parser.parse_args()

    try:
        client = ProwlarrClient.from_env()

        if args.list_indexers:
            indexers = client.list_indexers()
            print(f"📋 {len(indexers)} indexers configurés dans Prowlarr:")
            for idx in indexers:
                status = "✅" if idx.get("enable") else "❌"
                print(f"  {status} {idx.get('name')} ({idx.get('protocol', '?')})")
            sys.exit(0)

        if args.type == "movie":
            print(f"🔍 Recherche film: '{args.query}'", file=sys.stderr)
            results = client.search_movies(args.query, year=args.year)
        elif args.type == "serie":
            print(f"🔍 Recherche série: '{args.query}'", file=sys.stderr)
            results = client.search_series(args.query)
        else:
            print(f"🔍 Recherche: '{args.query}'", file=sys.stderr)
            results = client.search(args.query)

        print(f"✅ {len(results)} résultats pour '{args.query}'")
        for r in results[:10]:
            size_gb = round(r["size"] / (1024 ** 3), 2) if r["size"] else 0
            print(f"  - {r['title']}")
            print(f"    Indexer: {r['indexer']} | Seeds: {r['seeders']} | Taille: {size_gb} GB")
            if r["downloadUrl"]:
                print(f"    URL: {r['downloadUrl'][:80]}...")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
