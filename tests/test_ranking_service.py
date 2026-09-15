"""Tests de mavod.services.ranking_service."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavod.config import load_settings
from mavod.domain import Intent, Torrent
from mavod.exceptions import RankingError
from mavod.services.ranking_service import (
    LLMRankingStrategy,
    _enrich_files_from_bytes,
    _legacy_dict_to_torrent,
    _torrent_to_legacy_dict,
)

pytestmark = pytest.mark.unit


_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "LLM_API_KEY": "sk",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pk",
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


class TestLLMRankingStrategy:
    @respx.mock
    def test_picks_best_choice(self, settings):
        """Sélectionne le meilleur torrent depuis la réponse LLM."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_ranker_response(best=2)
        )
        strat = LLMRankingStrategy(settings)
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
        strat = LLMRankingStrategy(settings)
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
        strat = LLMRankingStrategy(settings)
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
        strat = LLMRankingStrategy(settings)
        intent = Intent(title="Widows Bay", type="serie", season=1, year=2024)
        strat.rank(intent, [_t(1)])
        user_msg = captured["body"]["messages"][1]["content"]
        assert "full season S01" in user_msg
        assert "season packs" in user_msg
        assert "specifically" not in user_msg

    @respx.mock
    def test_unparseable_response_falls_back_to_local_top(self, settings):
        """Réponse non parsable → fallback sur le meilleur score local, pas d'abandon."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "no marker here"}}]},
            )
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best == candidates[0]
        assert decision.fallback_reason == "llm_unparsable"
        assert decision.is_fallback

    @respx.mock
    def test_llm_error_wrapped(self, settings):
        """Les erreurs LLM sont enveloppées proprement."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(400, text="bad request")
        )
        strat = LLMRankingStrategy(settings)
        with pytest.raises(RankingError):
            strat.rank(Intent(title="X", type="movie"), [_t(1)])

    @respx.mock
    def test_best_choice_out_of_range_falls_back(self, settings):
        """Si LLM renvoie Torrent 99 mais on n'a que 3 candidats → fallback local."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_ranker_response(best=99, ranking="Torrent 3, Torrent 1")
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2), _t(3)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        # `Final ranking` reste exploitable → son premier élément fait foi.
        assert decision.best == candidates[2]
        assert decision.fallback_reason == "llm_unparsable"

    @respx.mock
    def test_best_choice_zero_falls_back(self, settings):
        """Index 0 invalide (1-indexé attendu) → fallback sur le top local."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_ranker_response(best=0, ranking="rien d'exploitable")
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best == candidates[0]
        assert decision.fallback_reason == "llm_unparsable"

    @respx.mock
    def test_empty_content_falls_back_to_local_top(self, settings):
        """max_tokens dépassé → content vide → fallback sur le meilleur score local."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"finish_reason": "length",
                                   "message": {"content": ""}}]},
            )
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best == candidates[0]
        assert decision.fallback_reason == "llm_empty"
        # Fallback : si parsing échoue, ranked = candidats dans l'ordre d'entrée
        assert decision.ranked == tuple(candidates)

    # ─── Robustesse de formatage (cf. incident « aucun retour exploitable ») ──

    @pytest.mark.parametrize(
        "content",
        [
            "**Final ranking:** Torrent 2, Torrent 1\n**Best choice:** Torrent 2",
            "**Final ranking**: Torrent 2, Torrent 1\n**Best choice**: Torrent 2",
            "Final ranking: Torrent 2, Torrent 1\nBest choice: Torrent 2",
            "**Final ranking:** 2, 1\n**Best choice:** 2",
            "final ranking: torrent 2, torrent 1\nbest choice: #2",
            "**Best choice:** Torrent 2 — meilleure source et audio EAC3.",
        ],
        ids=["bold_canonical", "bold_outside_colon", "no_bold",
             "bare_numbers", "lowercase_hash", "trailing_prose"],
    )
    @respx.mock
    def test_best_choice_format_variants(self, settings, content):
        """Le verdict est lu quelle que soit la variante de formatage du modèle."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": content}}]}
            )
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2), _t(3)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best == candidates[1]
        assert not decision.is_fallback

    @respx.mock
    def test_best_choice_read_from_reasoning_content(self, settings):
        """Reasoner tronqué : le verdict est dans `reasoning_content`, on le lit."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": "Le 3 est trop gros.\n**Best choice:** Torrent 2",
                    },
                }]},
            )
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2), _t(3)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best == candidates[1]
        assert decision.fallback_reason is None

    @respx.mock
    def test_last_best_choice_wins(self, settings):
        """Un modèle qui se reprend : la dernière occurrence valide fait foi."""
        text = "**Best choice:** Torrent 1\nCorrection : **Best choice:** Torrent 3"
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": text}}]}
            )
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2), _t(3)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.best == candidates[2]

    @respx.mock
    def test_ranking_ignores_numbers_inside_titles(self, settings):
        """`Torrent N` prime sur les nombres nus (1080p ne doit pas être un index)."""
        text = ("**Final ranking:** Torrent 2 (1080p), Torrent 1 (720p)\n"
                "**Best choice:** Torrent 2")
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": text}}]}
            )
        )
        strat = LLMRankingStrategy(settings)
        candidates = [_t(1), _t(2)]
        decision = strat.rank(Intent(title="X", type="movie"), candidates)
        assert decision.ranked == (candidates[1], candidates[0])

    @respx.mock
    def test_ranking_dedupes_duplicates(self, settings):
        """**Final ranking:** Torrent 1, Torrent 1, Torrent 2 → [1, 2]."""
        text = "**Final ranking:** Torrent 1, Torrent 1, Torrent 2\n**Best choice:** Torrent 1"
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": text}}]}
            )
        )
        strat = LLMRankingStrategy(settings)
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
        strat = LLMRankingStrategy(settings)
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
