"""Tests de mavod.services.ranking_service."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavod.config import load_settings
from mavod.domain import Intent, Torrent
from mavod.exceptions import RankingError
from mavod.services.ranking_service import (
    DeepSeekRankingStrategy,
    _enrich_files_from_bytes,
    _legacy_dict_to_torrent,
    _torrent_to_legacy_dict,
)

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


def _t(i: int, title: str = None, size_gb: float = 5.0) -> Torrent:
    return Torrent(
        title=title or f"Torrent.{i}.1080p.BluRay.x264-FOO",
        indexer="Prowlarr:T",
        size_bytes=int(size_gb * 1024 ** 3),
        seeders=20,
        infohash=f"{i:040x}",
    )


def _ranker_response(best: int, ranking: str = None):
    text = f"**Final ranking:** {ranking or f'Torrent {best}, Torrent 1'}\n**Best choice:** Torrent {best}"
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text, "reasoning_content": "thinking"}}],
            "usage": {"prompt_tokens": 200, "prompt_cache_hit_tokens": 150},
        },
    )


class TestDeepSeekRankingStrategy:
    @respx.mock
    def test_picks_best_choice(self, settings):
        """Sélectionne le meilleur torrent depuis la réponse DeepSeek."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_ranker_response(best=2)
        )
        strat = DeepSeekRankingStrategy(settings)
        candidates = [_t(1), _t(2), _t(3)]
        decision = strat.rank(
            Intent(title="Dune", type="movie", year=2021),
            candidates,
        )
        assert decision.best == candidates[1]  # Torrent 2 (1-indexé)
        assert decision.has_choice
        assert decision.reasoning == "thinking"
        assert decision.usage["prompt_cache_hit_tokens"] == 150

    @respx.mock
    def test_empty_candidates_returns_no_choice(self, settings):
        """Retourne aucun choix si la liste de candidats est vide."""
        strat = DeepSeekRankingStrategy(settings)
        decision = strat.rank(Intent(title="X", type="movie"), [])
        assert not decision.has_choice
        assert decision.ranked == ()

    @respx.mock
    def test_episode_appears_in_user_prompt(self, settings):
        """L'épisode demandé apparaît dans le prompt utilisateur."""
        captured = {}

        def cap(request):
            import json as _json
            captured["body"] = _json.loads(request.content)
            return _ranker_response(best=1)

        respx.post("https://api.deepseek.com/v1/chat/completions").mock(side_effect=cap)
        strat = DeepSeekRankingStrategy(settings)
        intent = Intent(title="The Bear", type="serie", season=3, episode=4, year=2024)
        strat.rank(intent, [_t(1)])
        user_msg = captured["body"]["messages"][1]["content"]
        assert "episode E04 specifically" in user_msg

    @respx.mock
    def test_full_season_appears_in_user_prompt(self, settings):
        """La demande de saison complète apparaît dans le prompt utilisateur."""
        captured = {}

        def cap(request):
            import json as _json
            captured["body"] = _json.loads(request.content)
            return _ranker_response(best=1)

        respx.post("https://api.deepseek.com/v1/chat/completions").mock(side_effect=cap)
        strat = DeepSeekRankingStrategy(settings)
        intent = Intent(title="Widows Bay", type="serie", season=1, year=2024)
        strat.rank(intent, [_t(1)])
        user_msg = captured["body"]["messages"][1]["content"]
        assert "full season S01" in user_msg
        assert "season packs" in user_msg
        assert "specifically" not in user_msg

    @respx.mock
    def test_unparseable_response_returns_no_best(self, settings):
        """Une réponse non parsable ne retourne pas de meilleur choix."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "no marker here"}}]},
            )
        )
        strat = DeepSeekRankingStrategy(settings)
        decision = strat.rank(Intent(title="X", type="movie"), [_t(1)])
        assert decision.best is None

    @respx.mock
    def test_deepseek_error_wrapped(self, settings):
        """Les erreurs DeepSeek sont enveloppées proprement."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(400, text="bad request")
        )
        strat = DeepSeekRankingStrategy(settings)
        with pytest.raises(RankingError):
            strat.rank(Intent(title="X", type="movie"), [_t(1)])

    @respx.mock
    def test_best_choice_out_of_range_returns_none(self, settings):
        """Si DeepSeek renvoie Torrent 99 mais on n'a que 3 candidats → pas de best."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_ranker_response(best=99)
        )
        strat = DeepSeekRankingStrategy(settings)
        decision = strat.rank(
            Intent(title="X", type="movie"),
            [_t(1), _t(2), _t(3)],
        )
        assert decision.best is None

    @respx.mock
    def test_best_choice_zero_returns_none(self, settings):
        """Index 0 invalide (1-indexé attendu)."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_ranker_response(best=0)
        )
        strat = DeepSeekRankingStrategy(settings)
        decision = strat.rank(Intent(title="X", type="movie"), [_t(1), _t(2)])
        assert decision.best is None

    @respx.mock
    def test_empty_content_returns_no_best(self, settings):
        """max_tokens dépassé → content vide → pas de best."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": ""}}]}
            )
        )
        strat = DeepSeekRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best is None
        # Fallback : si parsing échoue, ranked = candidats dans l'ordre d'entrée
        assert decision.ranked == tuple(candidates)

    @respx.mock
    def test_ranking_dedupes_duplicates(self, settings):
        """**Final ranking:** Torrent 1, Torrent 1, Torrent 2 → [1, 2]."""
        text = "**Final ranking:** Torrent 1, Torrent 1, Torrent 2\n**Best choice:** Torrent 1"
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": text}}]}
            )
        )
        strat = DeepSeekRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert len(decision.ranked) == 2
        assert decision.ranked[0] == candidates[0]
        assert decision.ranked[1] == candidates[1]

    @respx.mock
    def test_ranking_drops_out_of_range_indices(self, settings):
        """Indices 99 ignorés, mais 1 et 2 retenus."""
        text = "**Final ranking:** Torrent 1, Torrent 99, Torrent 2\n**Best choice:** Torrent 1"
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": text}}]}
            )
        )
        strat = DeepSeekRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert [c.title for c in decision.ranked] == [candidates[0].title, candidates[1].title]


class TestConversionHelpers:
    def test_torrent_to_dict_roundtrip(self):
        """Conversion Torrent vers dict puis retour conserve les champs."""
        t = Torrent(
            title="A",
            indexer="Prowlarr:Y",
            size_bytes=5 * 1024 ** 3,
            seeders=15,
            infohash="abc123",
            magnet="magnet:?xt=urn:btih:abc",
            extra={"guid": "g", "categories": [2000], "downloads": 5},
        )
        d = _torrent_to_legacy_dict(t)
        assert d["title"] == "A"
        assert d["size"] == 5 * 1024 ** 3
        assert d["is_magnet"] is True

        back = _legacy_dict_to_torrent(d)
        assert back.title == t.title
        assert back.size_bytes == t.size_bytes
        assert back.magnet == t.magnet

    def test_enrich_files_from_bytes_noop_if_no_bytes(self):
        """L'enrichissement est un noop sans bytes torrent."""
        t = Torrent(title="x", indexer="y", size_bytes=1, seeders=1)
        assert _enrich_files_from_bytes(t) is t

    def test_enrich_files_from_bytes_skips_if_already_has_files(self):
        """Skip l'enrichissement si les fichiers sont déjà présents."""
        from mavod.domain import TorrentFile
        t = Torrent(
            title="x", indexer="y", size_bytes=1, seeders=1,
            files=(TorrentFile(name="a", size_bytes=1),),
            torrent_bytes=b"would error if used",
        )
        assert _enrich_files_from_bytes(t) is t
