# benchmarks/

Smoke tests comparatifs des modèles LLM du pipeline maVOD. But : décider quel
modèle DeepSeek garder (qualité vs coût) — typiquement `pro` vs `flash`.

## `deepseek_smoke.py`

Exerce les **deux** composants LLM sur une batterie de cas à réponse connue, pour
chaque modèle, et compare **qualité** (cas attendus), **latence**, **tokens** et
**coût estimé** :

- **intent** (9 cas) — parsing function-calling : film simple, titre ambigu
  (→ clarification), série multi-saisons (→ clarification saison), série `SxxEyy`,
  miniserie (→ saison 1), année déduite, **film étranger** (iranien), **série
  étrangère** (espagnole), et **série entière** (→ clarification saison, limitation
  connue documentée).
- **rank** (7 cas) — ranker, couvre les *hard rules* du prompt v2 : pénalité
  taille > 20 GB dominante, ban DTS du top 1, premium HDR/audio sous 15 GB,
  préférence season pack, **VO+sub FR pour film/série étrangers** (VOSTFR/MULTI
  > doublage FRENCH seul), **épisode précis** (préfère `SxxEyy` ou le pack le
  listant, jamais le mauvais épisode).

### Lancer en local

```bash
LLM_API_KEY=sk-... python benchmarks/deepseek_smoke.py
python benchmarks/deepseek_smoke.py --dry-run                 # valide le câblage, 0 appel API
python benchmarks/deepseek_smoke.py --models deepseek-v4-flash --suite rank
python benchmarks/deepseek_smoke.py --json out.json           # dump machine-lisible
```

Le script construit une `Settings` minimale (seul `LLM_API_KEY` est requis) et
appelle directement `IntentService` / `LLMRankingStrategy` — donc les **vrais**
prompts et schémas d'outils du pipeline.

### Lancer via GitHub Actions (utilise le secret `LLM_API_KEY`)

Onglet **Actions → "DeepSeek Benchmark (manual)" → Run workflow** : renseigne le(s)
modèle(s) en CSV et la suite. Le verdict s'affiche dans le *job summary* ; le détail
(`output.txt` + `result.json`) est uploadé en artefact.

## Tester un futur modèle

Aucune modif de code : passe son identifiant à `--models` (ou dans l'input du
workflow), p. ex. `--models deepseek-v5,deepseek-v4-flash`. Pour ajouter des cas,
édite `INTENT_CASES` / `RANK_CASES` dans `deepseek_smoke.py` (chaque cas porte son
prédicat de validation). Le verdict pro/flash est une heuristique (parité qualité
à ±1 cas) — à valider à l'œil sur le détail.

## Coût en $

Renseigne `PRICING` en tête de `deepseek_smoke.py` (tarifs $/1M tokens depuis la
page pricing DeepSeek). Tant que c'est `None`, seuls tokens et latence font foi.
