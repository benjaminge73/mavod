"""Tests unitaires de mavod.adapters.qbittorrent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mavod.adapters.qbittorrent import QBittorrentAdapter
from mavod.config import load_settings
from mavod.exceptions import DuplicateTorrent, QBittorrentError

pytestmark = pytest.mark.unit


_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "DEEPSEEK_API_KEY": "sk",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pk",
    "C411_URL_API": "http://c411",
    "C411_API_KEY": "ck",
    "C411_PASSKEY": "pk",
}


@pytest.fixture
def settings():
    return load_settings(env=_ENV)


@pytest.fixture
def adapter(settings):
    a = QBittorrentAdapter(settings)
    a._client = MagicMock()
    return a


class TestAdd:
    def test_returns_infohash(self, adapter):
        """add retourne l'infohash du torrent ajouté."""
        adapter._client.add_torrent.return_value = "deadbeef" * 5
        h = adapter.add("magnet:?xt=urn:btih:abc")
        assert h == "deadbeef" * 5

    def test_passes_optional_args(self, adapter):
        """Transmet les arguments optionnels (tags, category)."""
        adapter._client.add_torrent.return_value = "h"
        adapter.add("magnet:?xt=urn:btih:abc", download_dir="/d", tags="t", category="c")
        adapter._client.add_torrent.assert_called_once_with(
            "magnet:?xt=urn:btih:abc", download_dir="/d", tags="t", category="c"
        )

    def test_bytes_source_supported(self, adapter):
        """Supporte l'ajout depuis des bytes torrent."""
        adapter._client.add_torrent.return_value = "h"
        adapter.add(b"\x00\x01")
        args, _ = adapter._client.add_torrent.call_args
        assert args[0] == b"\x00\x01"

    def test_duplicate_french_message_raises_duplicate(self, adapter):
        """Le message Fails. en français lève DuplicateTorrent."""
        adapter._client.add_torrent.side_effect = RuntimeError("torrent déjà présent dans qB")
        with pytest.raises(DuplicateTorrent):
            adapter.add("magnet:?xt=urn:btih:abc")

    def test_duplicate_english_lowercase_raises_duplicate(self, adapter):
        """Le message duplicate anglais lève DuplicateTorrent."""
        adapter._client.add_torrent.side_effect = RuntimeError("Duplicate hash detected")
        with pytest.raises(DuplicateTorrent):
            adapter.add("magnet:?xt=urn:btih:abc")

    def test_other_runtime_error_raises_qbittorrent_error(self, adapter):
        """Une autre erreur runtime devient QBittorrentError."""
        adapter._client.add_torrent.side_effect = RuntimeError("login failed")
        with pytest.raises(QBittorrentError):
            adapter.add("magnet:?xt=urn:btih:abc")

    def test_duplicate_not_swallowed_as_generic(self, adapter):
        """Ordre du check : duplicate avant QBittorrentError."""
        adapter._client.add_torrent.side_effect = RuntimeError("déjà présent")
        with pytest.raises(DuplicateTorrent):
            adapter.add("magnet:?xt=urn:btih:abc")


class TestGetInfo:
    def test_returns_dict(self, adapter):
        """get_info retourne un dict d'informations."""
        adapter._client.get_torrent_info.return_value = {"hash": "abc", "state": "uploading"}
        info = adapter.get_info("abc")
        assert info["state"] == "uploading"

    def test_exception_wrapped(self, adapter):
        """Les exceptions de get_info sont enveloppées."""
        adapter._client.get_torrent_info.side_effect = Exception("net down")
        with pytest.raises(QBittorrentError, match="get_info KO"):
            adapter.get_info("abc")


class TestDelete:
    def test_passes_delete_files(self, adapter):
        """delete transmet l'option delete_files."""
        adapter.delete("abc", delete_files=True)
        adapter._client.delete_torrent.assert_called_once_with("abc", delete_files=True)

    def test_default_delete_files_false(self, adapter):
        """Par défaut delete_files vaut False."""
        adapter.delete("abc")
        adapter._client.delete_torrent.assert_called_once_with("abc", delete_files=False)

    def test_exception_wrapped(self, adapter):
        """Les exceptions de delete sont enveloppées."""
        adapter._client.delete_torrent.side_effect = Exception("auth fail")
        with pytest.raises(QBittorrentError, match="delete KO"):
            adapter.delete("abc")
