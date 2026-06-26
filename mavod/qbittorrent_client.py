#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qbittorrent_client.py
Client pour qBittorrent via l'API WebUI v2.

Interface identique à TransmissionClient pour pouvoir switcher facilement.

Usage:
    from qbittorrent_client import QBittorrentClient

    client = QBittorrentClient.from_env()
    torrent_hash = client.add_torrent(torrent_bytes)
    info = client.get_torrent_info(torrent_hash)
"""

from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

DEFAULT_TIMEOUT = (5, 30)


class QBittorrentClient:
    """
    Client pour qBittorrent via l'API WebUI v2.

    Authentification : POST /api/v2/auth/login → cookie SID
    Add torrent      : POST /api/v2/torrents/add (multipart ou form)
    Get info         : GET  /api/v2/torrents/info?hashes={hash}
    Delete           : POST /api/v2/torrents/delete
    """

    def __init__(self, url: str, username: str, password: str) -> None:
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "AgentTorrent/1.0"})
        self._logged_in = False

    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "QBittorrentClient":
        """
        Crée un client depuis les variables d'environnement.

        Lit QB_URL, QB_USER, QB_PASS depuis .env
        """
        if env_path is None:
            base_dir = Path(__file__).parent.parent
            env_path = str(base_dir / ".env")

        # Lecture manuelle du .env
        env_vars: Dict[str, str] = {}
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")

        url = env_vars.get("QB_URL") or os.environ.get("QB_URL")
        username = env_vars.get("QB_USER") or os.environ.get("QB_USER")
        password = env_vars.get("QB_PASS") or os.environ.get("QB_PASS")

        if not url:
            raise RuntimeError("QB_URL manquant dans .env")
        if not username or not password:
            raise RuntimeError("QB_USER / QB_PASS manquants dans .env")

        return cls(url=url, username=username, password=password)

    # ──────────────────────────────────────────────
    # Authentification
    # ──────────────────────────────────────────────

    def _login(self) -> None:
        """Authentifie et stocke le cookie SID dans la session."""
        url = f"{self.base_url}/api/v2/auth/login"
        resp = self.session.post(
            url,
            data={"username": self.username, "password": self.password},
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200 or "fails" in resp.text.lower():
            raise RuntimeError(
                f"Échec login qBittorrent ({resp.status_code}): {resp.text[:200]}"
            )
        self._logged_in = True
        print(f"[QB] Connecté à {self.base_url}", file=sys.stderr)

    def _ensure_logged_in(self) -> None:
        if not self._logged_in:
            self._login()

    def _api(self, method: str, path: str, **kwargs) -> requests.Response:
        """Appel générique à l'API qBittorrent avec auto-login."""
        self._ensure_logged_in()
        url = f"{self.base_url}/api/v2/{path}"
        resp = self.session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
        # Si session expirée → re-login
        if resp.status_code == 403:
            self._logged_in = False
            self._login()
            resp = self.session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
        return resp

    # ──────────────────────────────────────────────
    # Méthodes publiques
    # ──────────────────────────────────────────────

    def add_torrent(
        self,
        torrent_source,
        download_dir: Optional[str] = None,
        tags: Optional[str] = None,
        category: Optional[str] = None,
    ) -> str:
        """
        Ajoute un torrent à qBittorrent.

        Args:
            torrent_source:
                - str commençant par "magnet:" → magnet link
                - bytes → contenu brut d'un fichier .torrent
                - str (chemin) → chemin vers un .torrent sur disque
            download_dir: Dossier de téléchargement (optionnel)
            tags: Tags à associer (optionnel)
            category: Catégorie (optionnel)

        Returns:
            infohash du torrent (str, en minuscules)
        """
        is_magnet = isinstance(torrent_source, str) and torrent_source.startswith("magnet:")
        is_url = isinstance(torrent_source, str) and torrent_source.startswith("http")
        is_bytes = isinstance(torrent_source, bytes)

        # Paramètres communs
        data: Dict[str, Any] = {}
        if download_dir:
            data["savepath"] = download_dir
        if tags:
            data["tags"] = tags
        if category:
            data["category"] = category

        if is_magnet or is_url:
            # Magnet link ou URL HTTP → champ `urls`
            data["urls"] = torrent_source
            resp = self._api("POST", "torrents/add", data=data)
        elif is_bytes:
            # Bytes .torrent → multipart
            files = {"torrents": ("torrent.torrent", torrent_source, "application/x-bittorrent")}
            resp = self._api("POST", "torrents/add", data=data, files=files)
        else:
            # Chemin fichier
            filepath = Path(torrent_source)
            if not filepath.exists():
                raise FileNotFoundError(f"Fichier .torrent introuvable: {torrent_source}")
            with open(filepath, "rb") as f:
                torrent_bytes = f.read()
            files = {"torrents": (filepath.name, torrent_bytes, "application/x-bittorrent")}
            resp = self._api("POST", "torrents/add", data=data, files=files)
            # Convertir le filepath en bytes pour la résolution du hash
            # (On a déjà lu le fichier, donc on le passe maintenant)
            torrent_source = torrent_bytes
            is_bytes = True

        if resp.status_code != 200 or "fails" in resp.text.lower():
            # qBittorrent retourne "Fails." pour les doublons (torrent déjà présent)
            # → vérifier si le torrent existe déjà avant de lever une exception
            if "fails" in resp.text.lower():
                import re as _re
                existing_hash = None

                if is_magnet:
                    # Magnet : extraire le hash directement depuis l'URI
                    m = _re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", torrent_source, _re.IGNORECASE)
                    if m:
                        existing_hash = m.group(1).lower()
                    else:
                        import base64 as _b64
                        m32 = _re.search(r"xt=urn:btih:([A-Z2-7]{32})", torrent_source, _re.IGNORECASE)
                        if m32:
                            try:
                                existing_hash = _b64.b32decode(m32.group(1).upper()).hex().lower()
                            except Exception:
                                pass

                elif is_bytes:
                    # .torrent bytes : calculer le hash depuis le bencode
                    try:
                        import bencodepy as _bc, hashlib as _hl
                        _data = _bc.decode(torrent_source)
                        _info = _data.get(b"info", {})
                        if _info:
                            existing_hash = _hl.sha1(_bc.encode(_info)).hexdigest().lower()
                    except Exception as _e:
                        print(f"[QB][WARN] Impossible d'extraire hash pour vérification doublon: {_e}", file=sys.stderr)

                if existing_hash:
                    existing = self.get_torrent_info(existing_hash)
                    if existing:
                        print(
                            f"[QB] Torrent déjà présent dans qBittorrent ({existing_hash[:8]}…) "
                            f"— utilisation de l'entrée existante",
                            file=sys.stderr,
                        )
                        return existing_hash

            raise RuntimeError(
                f"Échec ajout torrent qBittorrent ({resp.status_code}): {resp.text[:300]}"
            )

        print(f"[QB] Torrent ajouté ✅ (réponse: {resp.text.strip()})", file=sys.stderr)

        # qBittorrent ne retourne pas directement le hash → on le récupère
        # Extraire d'abord le nom attendu (pour le fallback par nom si nécessaire)
        _torrent_name: Optional[str] = None
        if is_bytes:
            try:
                import bencodepy as _bc
                _d = _bc.decode(torrent_source)
                _i = _d.get(b"info", {})
                if _i and b"name" in _i:
                    _torrent_name = _i[b"name"].decode("utf-8", errors="replace")
            except Exception:
                pass

        infohash = self._resolve_infohash(torrent_source, is_magnet, is_bytes, torrent_name=_torrent_name)
        return infohash

    def _resolve_infohash(self, torrent_source, is_magnet: bool, is_bytes: bool, torrent_name: Optional[str] = None) -> str:
        """
        Récupère l'infohash après ajout du torrent.

        Args:
            torrent_source: Magnet string ou bytes .torrent
            is_magnet: True si magnet link
            is_bytes: True si bytes .torrent
            torrent_name: Nom attendu du torrent (pour le fallback par nom)
        """
        if is_magnet:
            import re as _re, base64
            # Hex (40 chars) — format standard C411
            match = _re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", torrent_source, _re.IGNORECASE)
            if match:
                return match.group(1).lower()
            # Base32 (32 chars A-Z2-7) — format BitTorrent alternatif
            match = _re.search(r"xt=urn:btih:([A-Z2-7]{32})", torrent_source, _re.IGNORECASE)
            if match:
                try:
                    raw = base64.b32decode(match.group(1).upper())
                    return raw.hex().lower()
                except Exception as e:
                    print(f"[QB][WARN] Échec décodage Base32 du magnet: {e}", file=sys.stderr)
            # Extraire le nom depuis le paramètre dn= du magnet (pour le fallback)
            if not torrent_name:
                from urllib.parse import unquote_plus
                dn_match = _re.search(r"[?&]dn=([^&]+)", torrent_source)
                if dn_match:
                    torrent_name = unquote_plus(dn_match.group(1))

        if is_bytes:
            # Extraire le hash ET le nom depuis le bencode .torrent
            try:
                import bencodepy
                import hashlib
                data = bencodepy.decode(torrent_source)
                info = data.get(b"info", {})
                if info:
                    raw = bencodepy.encode(info)
                    return hashlib.sha1(raw).hexdigest().lower()
            except Exception as e:
                print(f"[QB][WARN] Impossible d'extraire hash depuis bytes: {e}", file=sys.stderr)
                # Essayer d'extraire au moins le nom pour le fallback
                if not torrent_name:
                    try:
                        import bencodepy as _bc
                        _data = _bc.decode(torrent_source)
                        _info = _data.get(b"info", {})
                        if _info and b"name" in _info:
                            torrent_name = _info[b"name"].decode("utf-8", errors="replace")
                    except Exception:
                        pass

        # Fallback : recherche par nom dans les torrents récemment ajoutés
        # ⚠️ On n'utilise JAMAIS "dernier torrent ajouté" sans vérification du nom
        print(
            f"[QB][WARN] Hash non extractible — recherche par nom dans qBittorrent "
            f"(nom attendu: '{torrent_name or 'inconnu'}')",
            file=sys.stderr
        )
        time.sleep(3)  # Laisser le temps à qBittorrent d'indexer le torrent
        resp = self._api("GET", "torrents/info", params={"sort": "added_on", "reverse": "true", "limit": 10})
        if resp.status_code == 200:
            torrents = resp.json()
            if torrents:
                if torrent_name:
                    # Recherche par nom : le nom du torrent doit contenir des mots du nom attendu
                    name_lower = torrent_name.lower().replace(".", " ")
                    name_words = [w for w in name_lower.split() if len(w) > 2]
                    for t in torrents:
                        t_name_lower = t.get("name", "").lower()
                        if name_words and any(word in t_name_lower for word in name_words):
                            print(
                                f"[QB] Torrent retrouvé par nom : '{t['name']}' ({t['hash'][:8]}…)",
                                file=sys.stderr
                            )
                            return t["hash"].lower()
                    raise RuntimeError(
                        f"[QB] Impossible de retrouver le torrent '{torrent_name}' dans qBittorrent. "
                        f"Torrents récents : {[t.get('name','?') for t in torrents[:3]]}. "
                        f"Le torrent a peut-être été ajouté mais son hash est inconnu — vérifier qBittorrent manuellement."
                    )
                else:
                    # Aucun nom disponible : refuser d'utiliser le fallback aveugle
                    raise RuntimeError(
                        "[QB] Hash non extractible et nom inconnu — fallback désactivé pour éviter "
                        "de monitorer le mauvais torrent. Vérifier qBittorrent manuellement."
                    )

        raise RuntimeError("Impossible de déterminer l'infohash du torrent ajouté")

    def get_torrent_info(self, torrent_hash: str) -> Optional[Dict[str, Any]]:
        """
        Retourne les informations d'un torrent.

        Returns:
            dict avec : hash, name, state, progress (0.0-1.0), dlspeed (bytes/s),
                        num_seeds, num_leechs, size, downloaded, eta
            None si le torrent n'existe pas
        """
        if not torrent_hash:
            return None
        resp = self._api("GET", "torrents/info", params={"hashes": torrent_hash.lower()})
        if resp.status_code != 200:
            return None
        torrents = resp.json()
        if not torrents:
            return None
        t = torrents[0]
        return {
            "hash":        t.get("hash", ""),
            "name":        t.get("name", ""),
            "state":       t.get("state", ""),       # metaDL, stalledDL, downloading, seeding...
            "progress":    t.get("progress", 0.0),   # 0.0 à 1.0
            "dlspeed":     t.get("dlspeed", 0),      # bytes/s
            "upspeed":     t.get("upspeed", 0),
            "num_seeds":   t.get("num_seeds", 0),
            "num_leechs":  t.get("num_leechs", 0),
            "size":        t.get("size", 0),
            "downloaded":  t.get("downloaded", 0),
            "eta":         t.get("eta", -1),         # secondes, -1 si inconnu
        }

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> None:
        """Supprime un torrent (et optionnellement ses fichiers)."""
        resp = self._api(
            "POST",
            "torrents/delete",
            data={
                "hashes":      torrent_hash.lower(),
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        if resp.status_code != 200:
            print(
                f"[QB][WARN] Suppression torrent {torrent_hash}: {resp.status_code} {resp.text[:100]}",
                file=sys.stderr,
            )
        else:
            print(f"[QB] Torrent {torrent_hash[:8]}... supprimé", file=sys.stderr)

    # ──────────────────────────────────────────────
    # Metadata probe : list files sans télécharger
    # ──────────────────────────────────────────────

    METADATA_PROBE_CATEGORY = "mavod_metadata_probe"
    METADATA_PROBE_TAG = "mavod_probe"

    @staticmethod
    def parse_torrent_files(torrent_bytes: bytes) -> Dict[str, Any]:
        """
        Parse un fichier .torrent localement (bencode) et retourne la liste des fichiers.
        Aucun aller-retour qBittorrent, fonctionne offline, instantané.

        Retourne le MÊME schéma que `fetch_files_metadata()` :
            {
                "infohash": str,
                "name": str,
                "total_size": int,
                "total_size_gb": float,
                "num_files": int,
                "files": [{"name": str, "size": int, "size_gb": float, "index": int}, ...],
            }

        Raises:
            ValueError: bencode invalide ou structure inattendue.
        """
        import bencodepy
        import hashlib

        try:
            data = bencodepy.decode(torrent_bytes)
        except Exception as e:
            raise ValueError(f"bencode decode KO: {e}") from e

        info = data.get(b"info")
        if not isinstance(info, dict):
            raise ValueError("torrent.info absent ou malformé")

        infohash = hashlib.sha1(bencodepy.encode(info)).hexdigest().lower()
        name = info.get(b"name", b"").decode("utf-8", errors="replace")

        files: list = []
        if b"files" in info:
            # Multi-fichier : info["files"] = [{length, path: [parts]}]
            for i, f in enumerate(info[b"files"]):
                parts = f.get(b"path") or []
                fname = "/".join(p.decode("utf-8", errors="replace") for p in parts)
                size = int(f.get(b"length", 0))
                files.append({
                    "name":    f"{name}/{fname}" if name else fname,
                    "size":    size,
                    "size_gb": round(size / (1024 ** 3), 3),
                    "index":   i,
                })
        else:
            # Single-file : info["length"]
            size = int(info.get(b"length", 0))
            files.append({
                "name":    name,
                "size":    size,
                "size_gb": round(size / (1024 ** 3), 3),
                "index":   0,
            })

        total_size = sum(f["size"] for f in files)
        return {
            "infohash":      infohash,
            "name":          name,
            "total_size":    total_size,
            "total_size_gb": round(total_size / (1024 ** 3), 3),
            "num_files":     len(files),
            "files":         files,
        }

    def extract_files_metadata(
        self,
        torrent_source,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Helper de plus haut niveau : tente d'abord le parse local (bencode) si
        `torrent_source` est des bytes .torrent ; sinon (magnet/URL) tombe sur
        `fetch_files_metadata()` qui interroge qBittorrent.

        Args:
            torrent_source: bytes .torrent, magnet str, URL http(s), ou chemin
                fichier.
            timeout: timeout pour le path qBittorrent uniquement.

        Returns:
            Même schéma que `fetch_files_metadata()`.
        """
        if isinstance(torrent_source, bytes):
            return self.parse_torrent_files(torrent_source)

        if isinstance(torrent_source, str):
            if torrent_source.startswith("magnet:"):
                return self.fetch_files_metadata(torrent_source, timeout=timeout)
            if torrent_source.startswith(("http://", "https://")):
                import requests as _req
                resp = _req.get(torrent_source, timeout=30, headers={"User-Agent": "AgentTorrent/1.0"})
                resp.raise_for_status()
                return self.parse_torrent_files(resp.content)
            # Chemin fichier
            filepath = Path(torrent_source)
            if filepath.exists():
                with open(filepath, "rb") as f:
                    return self.parse_torrent_files(f.read())

        raise ValueError(f"torrent_source non supporté: {type(torrent_source).__name__}")

    def fetch_files_metadata(
        self,
        torrent_source,
        timeout: int = 30,
        poll_interval: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Ajoute un torrent en mode `paused` + `skip_checking`, attend la résolution
        des métadonnées via DHT/trackers, lit la liste des fichiers, puis supprime
        le torrent. Aucun contenu n'est téléchargé.

        Args:
            torrent_source: magnet (str), bytes .torrent ou chemin fichier.
            timeout: secondes max à attendre la métadonnée.
            poll_interval: délai entre deux polls de l'état.

        Returns:
            {
                "infohash": str,
                "name": str,
                "total_size": int,            # bytes
                "total_size_gb": float,
                "num_files": int,
                "files": [
                    {"name": str, "size": int, "size_gb": float, "index": int},
                    ...
                ],
            }

        Raises:
            TimeoutError: si l'état reste sur metaDL/checkingResumeData après `timeout`s.
            RuntimeError: échec d'ajout ou de lecture côté qBittorrent.
        """
        is_magnet = isinstance(torrent_source, str) and torrent_source.startswith("magnet:")
        is_url = isinstance(torrent_source, str) and torrent_source.startswith("http")
        is_bytes = isinstance(torrent_source, bytes)

        # NOTE: ne PAS passer `paused=true` ni `stopped=true` — sur qBittorrent ≥ 5,
        # cela bloque la résolution de métadonnées (le torrent reste indéfiniment en
        # `stoppedDL`/`pausedDL` avec size=0). On laisse démarrer normalement, puis on
        # neutralise les fichiers (filePriority=0) dès que la métadonnée est résolue
        # pour éviter tout download avant le delete final.
        data: Dict[str, Any] = {
            "skip_checking": "true",
            "category":      self.METADATA_PROBE_CATEGORY,
            "tags":          self.METADATA_PROBE_TAG,
            "autoTMM":       "false",
            "upLimit":       "1",   # cap upload bandwidth à ~1 B/s
            "dlLimit":       "1",   # cap download bandwidth à ~1 B/s
        }

        if is_magnet or is_url:
            data["urls"] = torrent_source
            resp = self._api("POST", "torrents/add", data=data)
        elif is_bytes:
            files = {"torrents": ("probe.torrent", torrent_source, "application/x-bittorrent")}
            resp = self._api("POST", "torrents/add", data=data, files=files)
        else:
            filepath = Path(torrent_source)
            if not filepath.exists():
                raise FileNotFoundError(f"Fichier .torrent introuvable: {torrent_source}")
            with open(filepath, "rb") as f:
                torrent_bytes = f.read()
            files = {"torrents": (filepath.name, torrent_bytes, "application/x-bittorrent")}
            resp = self._api("POST", "torrents/add", data=data, files=files)
            torrent_source = torrent_bytes
            is_bytes = True

        if resp.status_code != 200:
            raise RuntimeError(
                f"[QB probe] add KO ({resp.status_code}): {resp.text[:200]}"
            )

        # Résolution du hash (réutilise la logique existante mais sans le fallback par nom)
        infohash: Optional[str] = None
        try:
            if is_magnet:
                import re as _re, base64 as _b64
                m = _re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", torrent_source, _re.IGNORECASE)
                if m:
                    infohash = m.group(1).lower()
                else:
                    m32 = _re.search(r"xt=urn:btih:([A-Z2-7]{32})", torrent_source, _re.IGNORECASE)
                    if m32:
                        infohash = _b64.b32decode(m32.group(1).upper()).hex().lower()
            elif is_bytes:
                import bencodepy as _bc, hashlib as _hl
                _data = _bc.decode(torrent_source)
                _info = _data.get(b"info", {})
                if _info:
                    infohash = _hl.sha1(_bc.encode(_info)).hexdigest().lower()
        except Exception as e:
            print(f"[QB probe][WARN] Hash extraction failed: {e}", file=sys.stderr)

        if not infohash:
            # Fallback : on liste les torrents de la catégorie probe et on prend le plus récent.
            time.sleep(2)
            list_resp = self._api(
                "GET", "torrents/info",
                params={"category": self.METADATA_PROBE_CATEGORY, "sort": "added_on", "reverse": "true", "limit": 1},
            )
            if list_resp.status_code == 200:
                items = list_resp.json()
                if items:
                    infohash = items[0].get("hash", "").lower() or None
            if not infohash:
                raise RuntimeError("[QB probe] Impossible de résoudre l'infohash")

        # Poll jusqu'à acquisition des métadonnées
        deadline = time.monotonic() + timeout
        metadata_acquired = False
        last_state = "unknown"
        try:
            while time.monotonic() < deadline:
                info_resp = self._api("GET", "torrents/info", params={"hashes": infohash})
                if info_resp.status_code != 200:
                    time.sleep(poll_interval)
                    continue
                items = info_resp.json()
                if items:
                    last_state = items[0].get("state", "")
                    has_size = items[0].get("size", 0) > 0
                    # metaDL = phase DHT/trackers ; quand size > 0 et state out of metaDL → métadonnée prête
                    if last_state not in ("metaDL", "checkingResumeData", "moving") and has_size:
                        metadata_acquired = True
                        break
                time.sleep(poll_interval)

            if not metadata_acquired:
                raise TimeoutError(
                    f"[QB probe] Métadonnées non résolues après {timeout}s (state={last_state})"
                )

            # Métadonnée OK → neutralise immédiatement tous les fichiers (priority=0)
            # puis pause le torrent pour stopper tout trafic résiduel.
            files_resp = self._api("GET", "torrents/files", params={"hash": infohash})
            if files_resp.status_code != 200:
                raise RuntimeError(
                    f"[QB probe] /torrents/files KO ({files_resp.status_code}): {files_resp.text[:200]}"
                )
            raw_files = files_resp.json() or []

            if raw_files:
                file_ids = "|".join(str(i) for i in range(len(raw_files)))
                self._api(
                    "POST", "torrents/filePrio",
                    data={"hash": infohash, "id": file_ids, "priority": "0"},
                )
            # Pause définitive (couvre both v4.x `pause` et v5.x `stop`)
            for endpoint in ("torrents/pause", "torrents/stop"):
                try:
                    self._api("POST", endpoint, data={"hashes": infohash})
                except Exception:
                    pass

            info_items = self._api("GET", "torrents/info", params={"hashes": infohash}).json() or []
            name = info_items[0].get("name", "") if info_items else ""
            total_size = info_items[0].get("size", 0) if info_items else sum(f.get("size", 0) for f in raw_files)

            files = [
                {
                    "name":    f.get("name", ""),
                    "size":    int(f.get("size", 0)),
                    "size_gb": round(int(f.get("size", 0)) / (1024 ** 3), 3),
                    "index":   i,
                }
                for i, f in enumerate(raw_files)
            ]

            return {
                "infohash":      infohash,
                "name":          name,
                "total_size":    int(total_size),
                "total_size_gb": round(int(total_size) / (1024 ** 3), 3),
                "num_files":     len(files),
                "files":         files,
            }
        finally:
            # Cleanup : toujours supprimer le torrent probe + ses fichiers éventuels.
            try:
                self.delete_torrent(infohash, delete_files=True)
            except Exception as e:
                print(f"[QB probe][WARN] Cleanup KO pour {infohash[:8]}: {e}", file=sys.stderr)
