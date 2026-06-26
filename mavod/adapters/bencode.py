"""Parse bencode .torrent → métadonnées fichiers typées.

Extrait depuis `mavod/qbittorrent_client.py::parse_torrent_files`. Pas de
dépendance qBittorrent : purement offline. Couvre 90 % du pipeline car
Prowlarr livre des `.torrent` bytes (le probe qBittorrent reste un
fallback pour les magnets purs C411 quand DHT est dispo).
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

import bencodepy

from mavod.domain import TorrentFile


class BencodeError(Exception):
    """Bencode invalide ou structure inattendue."""


def parse_torrent_bytes(torrent_bytes: bytes) -> dict:
    """Parse un fichier .torrent en dict structuré.

    Returns:
        {
            "infohash": str,                  # SHA1 lower-case
            "name": str,                      # nom du torrent
            "total_size": int,                # bytes
            "files": list[TorrentFile],
        }

    Raises:
        BencodeError si le bencode est invalide ou structure inattendue.
    """
    try:
        data = bencodepy.decode(torrent_bytes)
    except Exception as e:  # bencodepy lève divers types
        raise BencodeError(f"bencode decode KO: {e}") from e

    info = data.get(b"info")
    if not isinstance(info, dict):
        raise BencodeError("torrent.info absent ou malformé")

    infohash = hashlib.sha1(bencodepy.encode(info)).hexdigest().lower()
    name_bytes = info.get(b"name", b"")
    name = name_bytes.decode("utf-8", errors="replace") if isinstance(name_bytes, bytes) else ""

    files: List[TorrentFile] = []
    if b"files" in info:
        for i, f in enumerate(info[b"files"]):
            parts = f.get(b"path") or []
            fname = "/".join(
                p.decode("utf-8", errors="replace") if isinstance(p, bytes) else str(p)
                for p in parts
            )
            size = int(f.get(b"length", 0))
            full = f"{name}/{fname}" if name else fname
            files.append(TorrentFile(name=full, size_bytes=size, index=i))
    else:
        size = int(info.get(b"length", 0))
        files.append(TorrentFile(name=name, size_bytes=size, index=0))

    total_size = sum(f.size_bytes for f in files)
    return {
        "infohash":   infohash,
        "name":       name,
        "total_size": total_size,
        "files":      files,
    }


def extract_infohash(magnet_or_bytes) -> Optional[str]:
    """Extrait l'infohash depuis un magnet URI OU des bytes .torrent.

    Retourne None si non extractible.
    """
    import base64
    import re

    if isinstance(magnet_or_bytes, str) and magnet_or_bytes.startswith("magnet:"):
        m = re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", magnet_or_bytes, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        m32 = re.search(r"xt=urn:btih:([A-Z2-7]{32})", magnet_or_bytes, re.IGNORECASE)
        if m32:
            try:
                return base64.b32decode(m32.group(1).upper()).hex().lower()
            except Exception:
                return None
        return None

    if isinstance(magnet_or_bytes, bytes):
        try:
            return parse_torrent_bytes(magnet_or_bytes)["infohash"]
        except BencodeError:
            return None

    return None


def extract_name(magnet_or_bytes) -> Optional[str]:
    """Extrait le nom (dn= pour magnet, info.name pour bytes)."""
    import re
    from urllib.parse import unquote_plus

    if isinstance(magnet_or_bytes, str) and magnet_or_bytes.startswith("magnet:"):
        m = re.search(r"[?&]dn=([^&]+)", magnet_or_bytes)
        if m:
            return unquote_plus(m.group(1))
        return None

    if isinstance(magnet_or_bytes, bytes):
        try:
            return parse_torrent_bytes(magnet_or_bytes)["name"] or None
        except BencodeError:
            return None

    return None
