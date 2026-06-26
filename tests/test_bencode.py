"""Tests de mavod.adapters.bencode."""

from __future__ import annotations

import hashlib

import bencodepy
import pytest

from mavod.adapters.bencode import (
    BencodeError,
    extract_infohash,
    extract_name,
    parse_torrent_bytes,
)

pytestmark = pytest.mark.unit


def _build_single_file_torrent(name: str = "movie.mkv", size: int = 1024 * 1024) -> bytes:
    info = {b"name": name.encode("utf-8"), b"length": size, b"piece length": 16384, b"pieces": b""}
    data = {b"info": info, b"announce": b"http://example.com/announce"}
    return bencodepy.encode(data)


def _build_multi_file_torrent(name: str = "Series.S01") -> bytes:
    info = {
        b"name": name.encode("utf-8"),
        b"piece length": 16384,
        b"pieces": b"",
        b"files": [
            {b"length": 2 * 1024 ** 3, b"path": [b"S01E01.mkv"]},
            {b"length": 3 * 1024 ** 3, b"path": [b"S01E02.mkv"]},
            {b"length": 1 * 1024 ** 3, b"path": [b"extras", b"behind_scenes.mkv"]},
        ],
    }
    data = {b"info": info, b"announce": b"http://example.com/announce"}
    return bencodepy.encode(data)


class TestParseTorrentBytes:
    def test_single_file(self):
        """Parse un torrent mono-fichier."""
        torrent_bytes = _build_single_file_torrent("dune.2021.mkv", 1024)
        result = parse_torrent_bytes(torrent_bytes)
        assert result["name"] == "dune.2021.mkv"
        assert result["total_size"] == 1024
        assert len(result["files"]) == 1
        assert result["files"][0].name == "dune.2021.mkv"
        assert result["files"][0].size_bytes == 1024

    def test_multi_file_aggregates_size(self):
        """Agrège correctement la taille d'un torrent multi-fichiers."""
        torrent_bytes = _build_multi_file_torrent()
        result = parse_torrent_bytes(torrent_bytes)
        expected_total = (2 + 3 + 1) * 1024 ** 3
        assert result["total_size"] == expected_total
        assert len(result["files"]) == 3
        # Vérifie que les paths multi-segment sont joints
        names = [f.name for f in result["files"]]
        assert "Series.S01/S01E01.mkv" in names
        assert "Series.S01/extras/behind_scenes.mkv" in names

    def test_infohash_sha1(self):
        """Calcule l'infohash en SHA1 du dictionnaire info."""
        torrent_bytes = _build_single_file_torrent()
        result = parse_torrent_bytes(torrent_bytes)
        # Vérifie que infohash = sha1(info)
        data = bencodepy.decode(torrent_bytes)
        expected = hashlib.sha1(bencodepy.encode(data[b"info"])).hexdigest().lower()
        assert result["infohash"] == expected

    def test_invalid_bencode_raises(self):
        """Lève BencodeError sur bytes invalides."""
        with pytest.raises(BencodeError):
            parse_torrent_bytes(b"not a bencode")

    def test_missing_info_raises(self):
        """Lève BencodeError quand la clé info manque."""
        bad = bencodepy.encode({b"announce": b"http://x"})
        with pytest.raises(BencodeError):
            parse_torrent_bytes(bad)


class TestExtractInfohash:
    def test_magnet_hex(self):
        """Extrait l'infohash hexadécimal d'un lien magnet."""
        magnet = "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&dn=foo"
        assert extract_infohash(magnet) == "0123456789abcdef0123456789abcdef01234567"

    def test_magnet_base32(self):
        """Extrait l'infohash base32 d'un lien magnet."""
        # 32-char base32 = 20 raw bytes = 40 hex chars
        magnet = "magnet:?xt=urn:btih:AEBAGBA7AEBAGBA7AEBAGBA7AEBAGBA7&dn=foo"
        result = extract_infohash(magnet)
        assert result is not None
        assert len(result) == 40

    def test_magnet_invalid(self):
        """Retourne None pour un magnet mal formé."""
        assert extract_infohash("magnet:?xt=urn:btih:xxx") is None

    def test_bytes(self):
        """Extrait l'infohash depuis des bytes torrent."""
        torrent_bytes = _build_single_file_torrent()
        result = extract_infohash(torrent_bytes)
        assert result is not None
        assert len(result) == 40

    def test_unknown_type(self):
        """Retourne None pour un type d'entrée inconnu."""
        assert extract_infohash(12345) is None


class TestExtractName:
    def test_magnet_with_dn(self):
        """Extrait le nom depuis le paramètre dn d'un magnet."""
        magnet = "magnet:?xt=urn:btih:abc&dn=Dune.2021.1080p"
        assert extract_name(magnet) == "Dune.2021.1080p"

    def test_magnet_without_dn(self):
        """Retourne None quand le magnet n'a pas de dn."""
        assert extract_name("magnet:?xt=urn:btih:abc") is None

    def test_bytes(self):
        """Extrait le nom depuis des bytes torrent."""
        torrent_bytes = _build_single_file_torrent("some.movie.mkv")
        assert extract_name(torrent_bytes) == "some.movie.mkv"
