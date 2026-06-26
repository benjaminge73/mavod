"""Smoke test comparatif DeepSeek pro vs flash (intent + ranker).

Exerce les DEUX composants LLM du pipeline (parsing d'intent function-calling
et ranking de torrents) sur une batterie de cas représentatifs, pour chaque
modèle, et compare qualité / latence / tokens / coût. But : décider si `flash`
tient la parité avec `pro` et permet de réduire les coûts.

Usage :
    LLM_API_KEY=sk-... python benchmarks/deepseek_smoke.py
    python benchmarks/deepseek_smoke.py --dry-run            # valide le câblage, 0 appel
    python benchmarks/deepseek_smoke.py --models deepseek-v4-flash --suite rank
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Permet `python benchmarks/deepseek_smoke.py` depuis la racine du repo.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mavod.config import Settings  # noqa: E402
from mavod.domain import Intent, Torrent, TorrentFile  # noqa: E402
from mavod.exceptions import MavodError  # noqa: E402
from mavod.services.intent_service import IntentService, IntentTurnResult  # noqa: E402
from mavod.services.ranking_service import LLMRankingStrategy  # noqa: E402


# ─── Tarifs (À REMPLIR) ──────────────────────────────────────────────────────
# $ / 1M tokens. Laisser None tant que les vrais tarifs ne sont pas renseignés
# depuis la page pricing DeepSeek → le coût est alors affiché "n/a" et seule la
# comparaison tokens/latence fait foi.
PRICING: Dict[str, Dict[str, Optional[float]]] = {
    "deepseek-v4-pro":   {"cache_hit": None, "input": None, "output": None},
    "deepseek-v4-flash": {"cache_hit": None, "input": None, "output": None},
}

DEFAULT_MODELS = ("deepseek-v4-pro", "deepseek-v4-flash")


# ─── Cas de test ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntentCase:
    """Un cas de parsing d'intent : historique → prédicat sur le résultat."""

    id: str
    history: List[Dict[str, str]]
    expected: str
    check: Callable[[IntentTurnResult], Tuple[bool, str]]


@dataclass(frozen=True)
class RankCase:
    """Un cas de ranking : intent + candidats → index(s) acceptable(s) en best."""

    id: str
    intent: Intent
    candidates: List[Torrent]
    acceptable_best: frozenset
    expected: str


def _u(text: str) -> Dict[str, str]:
    return {"role": "user", "content": text}


INTENT_CASES: List[IntentCase] = [
    IntentCase(
        id="movie_simple",
        history=[_u("Télécharge le film Inception")],
        expected="submit_intent movie, year=2010",
        check=lambda r: (
            (r.is_intent and r.intent.type == "movie" and r.intent.year == 2010),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="movie_ambiguous",
        history=[_u("Je veux voir Dune")],
        expected="ask_clarification (Lynch 1984 vs Villeneuve 2021)",
        check=lambda r: (r.is_clarification, _intent_detail(r)),
    ),
    IntentCase(
        id="serie_multiseason",
        history=[_u("Télécharge Breaking Bad")],
        expected="ask_clarification missing_field=season",
        check=lambda r: (r.is_clarification, _intent_detail(r)),
    ),
    IntentCase(
        id="serie_episode",
        history=[_u("Breaking Bad saison 2 épisode 5")],
        expected="submit_intent serie S02E05",
        check=lambda r: (
            (r.is_intent and r.intent.type == "serie"
             and r.intent.season == 2 and r.intent.episode == 5),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="miniseries_no_season",
        history=[_u("Télécharge la série Chernobyl")],
        expected="submit_intent serie season=1 (miniserie)",
        check=lambda r: (
            (r.is_intent and r.intent.type == "serie" and r.intent.season == 1),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="movie_year_from_knowledge",
        history=[_u("Le Fabuleux Destin d'Amélie Poulain")],
        expected="submit_intent movie, year=2001",
        check=lambda r: (
            (r.is_intent and r.intent.type == "movie" and r.intent.year == 2001),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="movie_foreign_iranian",
        history=[_u("Télécharge le film Une Séparation")],
        expected="submit_intent movie, year=2011 (film iranien)",
        check=lambda r: (
            (r.is_intent and r.intent.type == "movie" and r.intent.year == 2011),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="serie_foreign_spanish",
        history=[_u("Télécharge La Casa de Papel saison 1")],
        expected="submit_intent serie season=1 (série espagnole)",
        check=lambda r: (
            (r.is_intent and r.intent.type == "serie" and r.intent.season == 1),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="serie_unknown_recent_title",
        history=[_u("Télécharge moi S01E03 de widows bay sortie en 2026")],
        # Titre inconnu (postérieur au cutoff) + S/E explicite → on cherche, pas de clarification.
        expected="submit_intent serie S01E03 (titre inconnu, ne pas refuser)",
        check=lambda r: (
            (r.is_intent and r.intent.type == "serie"
             and r.intent.season == 1 and r.intent.episode == 3),
            _intent_detail(r),
        ),
    ),
    IntentCase(
        id="full_series_unsupported",
        history=[_u("Télécharge toute la série Breaking Bad")],
        # Limitation connue : pas de notion "toutes saisons" → le bot demande la saison.
        expected="ask_clarification (série entière non supportée → demande la saison)",
        check=lambda r: (r.is_clarification, _intent_detail(r)),
    ),
]


def _intent_detail(r: IntentTurnResult) -> str:
    """Résumé lisible d'un résultat d'intent pour le rapport."""
    if r.is_intent:
        i = r.intent
        return (f"intent(title={i.title!r}, type={i.type}, year={i.year}, "
                f"S={i.season}, E={i.episode}, imdb={i.imdb_id})")
    c = r.clarification
    return f"clarification(missing={c.missing_field}, options={c.options})"


def _mk(title: str, size_gb: float, seeders: int = 25,
        files: Optional[Sequence[TorrentFile]] = None) -> Torrent:
    """Fabrique un candidat torrent ; la qualité est encodée dans le titre."""
    return Torrent(
        title=title,
        indexer="Prowlarr:Bench",
        size_bytes=int(size_gb * 1024 ** 3),
        seeders=seeders,
        files=tuple(files or ()),
    )


def _season_pack_files(season: int, episodes: int, ep_gb: float) -> List[TorrentFile]:
    """Breakdown d'un season pack : N épisodes de taille ~égale."""
    return [
        TorrentFile(name=f"The.Bear.S{season:02d}E{e:02d}.1080p.WEB-DL.mkv",
                    size_bytes=int(ep_gb * 1024 ** 3))
        for e in range(1, episodes + 1)
    ]


RANK_CASES: List[RankCase] = [
    RankCase(
        id="size_penalty_dominates",
        intent=Intent(title="Inception", type="movie", year=2010),
        candidates=[
            _mk("Inception.2010.1080p.BluRay.REMUX.AVC.DTS-HD.MA-FGT", 28.0),   # >20 GB + DTS
            _mk("Inception.2010.1080p.BluRay.x264.AC3-GROUP", 8.0),             # défaut fort
            _mk("Inception.2010.720p.HDTV.x264-LOL", 2.0),                      # 720p + HDTV
        ],
        acceptable_best=frozenset({2}),
        expected="Torrent 2 (≤15 GB BluRay AC3 bat le REMUX 28 GB et le HDTV 720p)",
    ),
    RankCase(
        id="dts_banned_from_top1",
        intent=Intent(title="Dune", type="movie", year=2021),
        candidates=[
            _mk("Dune.2021.1080p.BluRay.x264.DTS-HD.MA.5.1-GRP", 12.0),         # DTS → banni top1
            _mk("Dune.2021.1080p.BluRay.x264.EAC3-GRP", 11.0),                  # EAC3 non-DTS
            _mk("Dune.2021.1080p.WEBRip.x264.AC3-GRP", 9.0),                    # WEBRip AC3
        ],
        acceptable_best=frozenset({2}),
        expected="Torrent 2 (EAC3 non-DTS, BluRay ; le DTS est banni du top 1)",
    ),
    RankCase(
        id="hdr_audio_premium",
        intent=Intent(title="Blade Runner 2049", type="movie", year=2017),
        candidates=[
            _mk("Blade.Runner.2049.2160p.BluRay.x265.DV.TrueHD.Atmos-TERMINAL", 14.0),  # premium ≤15
            _mk("Blade.Runner.2049.1080p.WEB-DL.EAC3-GRP", 6.0),
            _mk("Blade.Runner.2049.1080p.HDTV.x264-GRP", 4.0),                          # HDTV
        ],
        acceptable_best=frozenset({1}),
        expected="Torrent 1 (DV + TrueHD/Atmos + 2160p BluRay, et tient sous 15 GB)",
    ),
    RankCase(
        id="season_pack_preferred",
        intent=Intent(title="The Bear", type="serie", year=2024, season=3),
        candidates=[
            _mk("The.Bear.S03E04.1080p.WEB-DL.EAC3-GRP", 2.0),                  # épisode unique
            _mk("The.Bear.S03.1080p.WEB-DL.x264-GRP", 18.0,
                files=_season_pack_files(3, 10, 1.7)),                          # pack, plus gros mkv ~1.7 GB
            _mk("The.Bear.S03.COMPLETE.720p.HDTV-GRP", 6.0),                    # pack 720p HDTV
        ],
        acceptable_best=frozenset({2}),
        expected="Torrent 2 (season pack préféré ; plus gros .mkv ~1.7 GB, meilleure source que le 720p)",
    ),
    RankCase(
        id="foreign_film_vo_subfr",
        intent=Intent(title="Le Labyrinthe de Pan", type="movie", year=2006),
        # Tout égal par ailleurs (8 GB, BluRay, EAC3, 1080p) → la langue décide.
        candidates=[
            _mk("Le.Labyrinthe.de.Pan.2006.FRENCH.1080p.BluRay.x264.EAC3-GRP", 8.0),   # doublage FR seul
            _mk("Le.Labyrinthe.de.Pan.2006.VOSTFR.1080p.BluRay.x264.EAC3-GRP", 8.0),   # VO + sous-titres FR
            _mk("El.Laberinto.del.Fauno.2006.MULTi.1080p.BluRay.x264.EAC3-GRP", 8.5),  # MULTI (VO + FR)
        ],
        acceptable_best=frozenset({2, 3}),
        expected="Torrent 2 ou 3 (VOSTFR/MULTI = VO+sub FR ; jamais le doublage FRENCH seul)",
    ),
    RankCase(
        id="foreign_serie_vo_subfr",
        intent=Intent(title="La Casa de Papel", type="serie", year=2017, season=1),
        candidates=[
            _mk("La.Casa.de.Papel.S01.FRENCH.1080p.WEB-DL.x264-GRP", 12.0),
            _mk("La.Casa.de.Papel.S01.MULTi.1080p.WEB-DL.x264-GRP", 12.0),
            _mk("La.Casa.de.Papel.S01.VOSTFR.1080p.WEB-DL.x264-GRP", 12.5),
        ],
        acceptable_best=frozenset({2, 3}),
        expected="Torrent 2 ou 3 (MULTI/VOSTFR ; pas le doublage FRENCH seul)",
    ),
    RankCase(
        id="specific_episode_preferred",
        intent=Intent(title="The Bear", type="serie", year=2024, season=3, episode=4),
        candidates=[
            _mk("The.Bear.S03E04.1080p.WEB-DL.EAC3-GRP", 2.0),                  # bon épisode (attendu)
            _mk("The.Bear.S03.1080p.WEB-DL.x264-GRP", 18.0,
                files=_season_pack_files(3, 10, 1.7)),                          # pack listant S03E04.mkv
            _mk("The.Bear.S03E07.1080p.WEB-DL.EAC3-GRP", 2.0),                  # MAUVAIS épisode
        ],
        acceptable_best=frozenset({1, 2}),
        expected="Torrent 1 (S03E04) ou 2 (pack listant S03E04) ; jamais le S03E07",
    ),
]


# ─── Exécution ───────────────────────────────────────────────────────────────


@dataclass
class CaseResult:
    """Résultat d'un cas pour un modèle donné."""

    case_id: str
    suite: str
    ok: bool
    latency_s: float
    detail: str
    usage: Dict[str, int]
    error: Optional[str] = None


def _make_settings(api_key: str, model: str, base_url: str, timeout: float) -> Settings:
    """Settings minimal pour le benchmark (champs requis non-LLM = factices)."""
    return Settings(
        telegram_bot_token="x", llm_api_key=api_key,
        qb_url="x", qb_user="x", qb_pass="x",
        prowlarr_url="x", prowlarr_api_key="x",
        llm_model=model, llm_base_url=base_url, llm_timeout=timeout,
    )


def _usage_fields(u: Dict[str, Any]) -> Dict[str, int]:
    return {
        "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(u.get("completion_tokens", 0) or 0),
        "cache_hit_tokens": int(u.get("prompt_cache_hit_tokens", 0) or 0),
        "cache_miss_tokens": int(u.get("prompt_cache_miss_tokens", 0) or 0),
        "total_tokens": int(u.get("total_tokens", 0) or 0),
    }


def run_intent_suite(settings: Settings, cases: List[IntentCase]) -> List[CaseResult]:
    service = IntentService(settings)
    out: List[CaseResult] = []
    for c in cases:
        t0 = time.perf_counter()
        try:
            turn = service.parse(list(c.history))
            dt = time.perf_counter() - t0
            ok, detail = c.check(turn)
            out.append(CaseResult(c.id, "intent", ok, dt, detail, _usage_fields(turn.usage)))
        except MavodError as e:
            dt = time.perf_counter() - t0
            out.append(CaseResult(c.id, "intent", False, dt, "", {}, error=f"{type(e).__name__}: {e}"))
    return out


def run_rank_suite(settings: Settings, cases: List[RankCase]) -> List[CaseResult]:
    strat = LLMRankingStrategy(settings)
    out: List[CaseResult] = []
    for c in cases:
        t0 = time.perf_counter()
        try:
            decision = strat.rank(c.intent, c.candidates)
            dt = time.perf_counter() - t0
            best_idx = (c.candidates.index(decision.best) + 1) if decision.best else None
            ok = best_idx in c.acceptable_best
            detail = f"best=Torrent {best_idx} (attendu {sorted(c.acceptable_best)})"
            out.append(CaseResult(c.id, "rank", ok, dt, detail, _usage_fields(decision.usage)))
        except MavodError as e:
            dt = time.perf_counter() - t0
            out.append(CaseResult(c.id, "rank", False, dt, "", {}, error=f"{type(e).__name__}: {e}"))
    return out


# ─── Agrégation & rapport ────────────────────────────────────────────────────


def _model_cost(model: str, results: List[CaseResult]) -> Optional[float]:
    """Coût estimé ($) si les tarifs du modèle sont renseignés, sinon None."""
    price = PRICING.get(model)
    if not price or any(price.get(k) is None for k in ("cache_hit", "input", "output")):
        return None
    cost = 0.0
    for r in results:
        u = r.usage
        miss = u.get("cache_miss_tokens") or max(0, u.get("prompt_tokens", 0) - u.get("cache_hit_tokens", 0))
        cost += (u.get("cache_hit_tokens", 0) * price["cache_hit"]
                 + miss * price["input"]
                 + u.get("completion_tokens", 0) * price["output"]) / 1_000_000
    return cost


def _print_model_report(model: str, results: List[CaseResult]) -> Dict[str, Any]:
    passed = sum(1 for r in results if r.ok)
    total = len(results)
    lat = [r.latency_s for r in results]
    tok_prompt = sum(r.usage.get("prompt_tokens", 0) for r in results)
    tok_completion = sum(r.usage.get("completion_tokens", 0) for r in results)
    tok_cache = sum(r.usage.get("cache_hit_tokens", 0) for r in results)
    cost = _model_cost(model, results)

    print(f"\n=== {model} ===")
    print(f"  qualité   : {passed}/{total} cas OK ({100*passed/total:.0f}%)")
    print(f"  latence   : moy {mean(lat):.2f}s | médiane {median(lat):.2f}s | max {max(lat):.2f}s")
    print(f"  tokens    : prompt {tok_prompt} (cache hit {tok_cache}) | completion {tok_completion}")
    print(f"  coût est. : {('$%.4f' % cost) if cost is not None else 'n/a (remplir PRICING)'}")
    print("  détail des cas :")
    for r in results:
        flag = "✅" if r.ok else "❌"
        line = f"    {flag} [{r.suite}] {r.case_id}: "
        line += r.error if r.error else r.detail
        line += f"  ({r.latency_s:.2f}s)"
        print(line)

    return {
        "model": model, "passed": passed, "total": total,
        "lat_mean": mean(lat), "lat_median": median(lat),
        "tok_prompt": tok_prompt, "tok_completion": tok_completion, "tok_cache": tok_cache,
        "cost": cost,
        "cases": [vars(r) for r in results],
    }


def _print_comparison(summaries: List[Dict[str, Any]]) -> None:
    if len(summaries) < 2:
        return
    pro = next((s for s in summaries if "pro" in s["model"]), summaries[0])
    flash = next((s for s in summaries if "flash" in s["model"]), summaries[-1])
    if pro is flash:
        return

    print("\n" + "=" * 60)
    print("COMPARAISON pro vs flash")
    print("=" * 60)
    print(f"  qualité  : pro {pro['passed']}/{pro['total']}  vs  flash {flash['passed']}/{flash['total']}")
    if flash["lat_mean"]:
        print(f"  latence  : flash {pro['lat_mean']/flash['lat_mean']:.2f}× la vitesse de pro "
              f"(pro {pro['lat_mean']:.2f}s / flash {flash['lat_mean']:.2f}s)")
    pro_tok = pro["tok_prompt"] + pro["tok_completion"]
    flash_tok = flash["tok_prompt"] + flash["tok_completion"]
    if flash_tok:
        print(f"  tokens   : pro {pro_tok} vs flash {flash_tok} (ratio {pro_tok/flash_tok:.2f}×)")
    if pro["cost"] and flash["cost"]:
        print(f"  coût     : pro ${pro['cost']:.4f} vs flash ${flash['cost']:.4f} "
              f"(flash {pro['cost']/flash['cost']:.1f}× moins cher)")

    quality_ok = flash["passed"] >= pro["passed"] - 1   # tolérance 1 cas
    print("\n  VERDICT (heuristique — à valider à l'œil) :")
    if quality_ok:
        print("  → flash tient la parité qualité (≤1 cas d'écart) : bascule recommandée pour le coût.")
    else:
        print(f"  → flash régresse de {pro['passed']-flash['passed']} cas : rester sur pro (qualité > coût).")


def _dry_run() -> None:
    """Valide le câblage sans appel réseau : affiche prompts et payloads."""
    from mavod.adapters.llm.prompts import (load_intent_prompt, load_ranker_prompt, prompt_hash)
    intent_p, ranker_p = load_intent_prompt(), load_ranker_prompt()
    print("DRY-RUN — aucun appel API.\n")
    print(f"intent prompt  : hash={prompt_hash(intent_p)} ({len(intent_p)} chars)")
    print(f"ranker prompt  : hash={prompt_hash(ranker_p)} ({len(ranker_p)} chars)\n")

    print(f"INTENT — {len(INTENT_CASES)} cas :")
    for c in INTENT_CASES:
        print(f"  - {c.id}: {c.history[-1]['content']!r}  → attendu: {c.expected}")

    # Reconstruit le message utilisateur exact que le ranker enverrait.
    settings = _make_settings("dry", "deepseek-v4-flash", "https://api.deepseek.com", 60.0)
    strat = LLMRankingStrategy.__new__(LLMRankingStrategy)
    strat._settings = settings
    print(f"\nRANK — {len(RANK_CASES)} cas :")
    for c in RANK_CASES:
        print(f"\n  - {c.id}  → attendu: {c.expected}")
        msg = strat._format_candidates(c.intent, c.candidates)
        for line in msg.splitlines():
            print(f"      {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test comparatif DeepSeek pro vs flash.")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="CSV de modèles (défaut: pro,flash)")
    ap.add_argument("--suite", choices=("intent", "rank", "all"), default="all")
    ap.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--dry-run", action="store_true", help="valide le câblage, 0 appel API")
    ap.add_argument("--json", dest="json_out", default=None, help="dump JSON des résultats")
    args = ap.parse_args()

    if args.dry_run:
        _dry_run()
        return 0

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        print("ERREUR: LLM_API_KEY absent de l'environnement.", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    summaries: List[Dict[str, Any]] = []
    for model in models:
        settings = _make_settings(api_key, model, args.base_url.rstrip("/"), args.timeout)
        results: List[CaseResult] = []
        if args.suite in ("intent", "all"):
            results += run_intent_suite(settings, INTENT_CASES)
        if args.suite in ("rank", "all"):
            results += run_rank_suite(settings, RANK_CASES)
        summaries.append(_print_model_report(model, results))

    _print_comparison(summaries)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
        print(f"\nRésultats écrits dans {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
