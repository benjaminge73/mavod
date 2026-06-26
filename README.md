# maVOD

**A Telegram bot that turns a free-text request into a ready-to-watch download.**

Send a title → maVOD identifies the movie/series, searches several torrent
indexers, ranks the candidates with an LLM, hands the best one to a remote
qBittorrent instance, and notifies you when it's ready.

> Personal project, open-sourced as a portfolio piece. It showcases a layered
> Python architecture, LLM function-calling, deterministic + LLM-based ranking,
> async Telegram handling, and a fully-mocked test suite.

---

## How it works

```
Telegram message
   │
   ▼
IntentService      DeepSeek function-calling — extracts title / type / year /
                   season / episode, or asks a clarifying question when the
                   request is ambiguous.
   │
   ▼
SearchService      Prowlarr first, C411 as a fallback when Prowlarr returns
                   nothing after filtering.
   │
   ▼
RankingService     Hard filters (language, quality ≥ 1080p, year/season, title)
                   → local scoring (codec, HDR, audio, seeders, size)
                   → DeepSeek ranker (prompt v2 + per-file .torrent breakdown).
   │
   ▼
WorkflowService    Sends the winner to qBittorrent and writes result.json for
                   the read-only viewer UI.
   │
   ▼
DownloadWatcher    Polls qBittorrent every 30s and notifies the user on
                   completion.
```

The bot uses Telegram **long-polling**, so no public port is exposed. DeepSeek
is a cloud API; everything else runs in two Docker containers (bot + UI).

### Why an LLM in the loop?

Two places where a model earns its keep:

1. **Intent parsing.** Users type things like *"the new dune"*, *"breaking bad
   season 3 episode 4"*, or *"that movie with the dreams inside dreams"*. A
   function-calling model maps this to a structured `Intent`, infers the year
   when the work is identifiable, and asks a clarifying question otherwise —
   instead of brittle regex parsing.
2. **Ranking.** After deterministic hard filters and a numeric score, a model
   reads the actual file breakdown of each `.torrent` (resolution, codec, HDR,
   audio tracks, language tags, size) and picks the best release — including
   the right episode inside a season pack.

The deterministic layer does the heavy lifting and stays cheap; the model only
arbitrates the shortlist.

---

## Layered architecture

The code is organized into four layers, lowest to highest. The separation makes
each piece testable in isolation (and is why the suite can be fully mocked).

| Layer | Folder | Responsibility |
|---|---|---|
| **Domain** | `mavod/domain/` | Pure types, zero I/O: `Intent`, `Torrent`, `WorkflowResult`, `RankingDecision`. Validated at construction. |
| **Adapters** | `mavod/adapters/` | All network I/O (DeepSeek, Prowlarr, C411, qBittorrent) plus local bencode parsing. Consume `Settings`, return Domain types. HTTP retry is centralized in `_retry.py`. |
| **Services** | `mavod/services/` | Orchestrated business logic: `IntentService`, `SearchService`, `RankingService`, `WorkflowService`. No direct network calls — everything goes through adapters. |
| **Telegram** | `mavod/telegram/` | python-telegram-bot handlers (`bot.py`), thread-safe per-user session state (`state.py`), download watching (`jobs.py`). |

Dependencies only ever point downward: `telegram → services → adapters → domain`.

---

## Project layout

```
mavod/
├── __main__.py              # python -m mavod → starts the bot
├── config.py                # Settings (single source of truth for env vars)
├── exceptions.py            # MavodError + subclasses
├── logging_setup.py         # structured logger (JSON via MAVOD_LOG_JSON=1)
├── qbittorrent_client.py    # low-level qBittorrent client (used via an adapter)
├── domain/                  # pure types (Intent, Torrent, WorkflowResult…)
├── adapters/                # typed I/O (DeepSeek, Prowlarr, C411, qBittorrent, bencode)
│   └── deepseek/
│       └── prompts/         # externalized system prompts (intent, ranker)
├── services/                # IntentService, SearchService, RankingService, WorkflowService
└── telegram/                # bot.py (handlers), state.py (sessions), jobs.py (watcher)

torrents_search_download/    # low-level HTTP clients (wrapped by mavod/adapters)
├── prowlarr_client.py
├── c411_api_client.py
└── torrent_filter.py        # hard filters + local scoring (language/quality/seeders/size)

ui/                          # read-only Streamlit viewer
├── main.py
├── data.py                  # reads torrents/<search_id>/result.json
└── style.py                 # dark, mobile-first CSS

docker_configs/
├── Dockerfile.base          # shared system layer
├── Dockerfile.bot
├── Dockerfile.ui
└── docker-compose.prod.yml  # services: mavod-bot, mavod-ui

requirements/
├── base.txt                 # bot production deps
├── ui.txt                   # -r base + streamlit
└── dev.txt                  # -r base + pytest + respx (tests)

tests/                       # see "Tests" below
benchmarks/                  # DeepSeek smoke test (model comparison)
```

---

## Quick start

### Local

```bash
git clone https://github.com/benjaminge73/mavod.git && cd mavod
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # = requirements/dev.txt

cp .env.example .env && $EDITOR .env      # fill in the variables below

python -m mavod                           # start the bot
cd ui && ./run.sh                         # start the viewer UI (optional)
```

Then send `Dune 2021` or `The Bear S03E04` to your bot on Telegram.

### Docker

```bash
cp .env.example .env && $EDITOR .env
docker compose -f docker_configs/docker-compose.prod.yml up -d --build
docker compose -f docker_configs/docker-compose.prod.yml logs -f mavod-bot
```

> The reference deployment runs these two containers on a small VPS behind an
> nginx reverse proxy, redeployed automatically on every push to `main`. That
> deployment pipeline is environment-specific and intentionally not shipped in
> this public repository.

---

## Configuration

All configuration is read once at startup into an immutable `Settings`
dataclass (`mavod/config.py`) — no scattered `os.environ` lookups. Copy
`.env.example` to `.env` and fill it in:

```dotenv
# Telegram
TELEGRAM_BOT_TOKEN=                   # required
TELEGRAM_ALLOWED_USERS=               # CSV of allowed user ids; empty = nobody

# DeepSeek API
DEEPSEEK_API_KEY=                     # required
DEEPSEEK_MODEL=deepseek-v4-flash      # default model
DEEPSEEK_BASE_URL=https://api.deepseek.com

# qBittorrent (remote WebUI)
QB_URL=
QB_USER=
QB_PASS=

# Indexers
PROWLARR_URL=
PROWLARR_API_KEY=
C411_URL_API=
C411_API_KEY=
C411_PASSKEY=

# Optional
MAVOD_UI_URL=http://localhost:8501
MAVOD_STATE_PATH=/app/state/persistence.pkl
MAVOD_LOG_JSON=1                      # 1 = JSON logs, otherwise human-readable
```

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot authentication |
| `TELEGRAM_ALLOWED_USERS` | recommended | CSV of allowed user ids; empty/absent ⇒ nobody is allowed |
| `DEEPSEEK_API_KEY` | ✅ | Intent parsing + ranking |
| `DEEPSEEK_MODEL` | ❌ | Defaults to `deepseek-v4-flash` |
| `DEEPSEEK_BASE_URL` | ❌ | Defaults to `https://api.deepseek.com` |
| `QB_URL`, `QB_USER`, `QB_PASS` | ✅ | Remote qBittorrent |
| `PROWLARR_URL`, `PROWLARR_API_KEY` | ✅ | Primary indexer |
| `C411_URL_API`, `C411_API_KEY`, `C411_PASSKEY` | ✅ | Fallback indexer |
| `MAVOD_UI_URL` | ❌ | Link to the viewer UI shown in bot messages |
| `MAVOD_STATE_PATH` | ❌ | Telegram persistence file |

> Secrets live only in `.env` (git-ignored) or in your CI/host secret store.
> No credentials are committed to this repository.

---

## Torrent sources & filtering

| Source | Role | When |
|--------|------|------|
| Prowlarr | primary | always |
| C411 | fallback | when Prowlarr returns 0 candidates after filtering |

Hard filters applied **before** scoring:

- **Language**: bans isolated `TRUEFRENCH` / `VFF` / `VF2` tags (accepted inside
  a `MULTI.VFF` release).
- **Quality**: `≥ 1080p` (720p and below excluded).
- **Year (movies) / season (series)**: strict match, with a ±1-year tolerance
  when a year is provided by the intent.
- **Title**: fuzzy match on ≥ 50% of significant words, or a strict
  start-of-release match for very short titles (≤ 4 characters).

Surviving candidates are then scored locally (resolution, codec, HDR, audio,
seeders, file size) and the top shortlist is handed to the LLM ranker, which
reads the per-file breakdown extracted directly from the `.torrent` metadata
(bencode parsing — no tracker round-trip needed for most releases).

---

## Tests

Three markers are declared in `pytest.ini`:

| Marker | When | Command |
|---|---|---|
| `unit` | every push / PR (CI) | `pytest -m unit` |
| `integration` | on demand, hits real APIs | `RUN_INTEGRATION=1 pytest -m integration` |
| `e2e` | on demand, ~30–60s per test | `RUN_E2E=1 pytest -m e2e` |

The unit tests are **fully mocked** (HTTP via [`respx`](https://lundberg.github.io/respx/))
and run in a couple of seconds — no API keys required:

```bash
pytest -m unit -q
```

`tests/conftest.py` provides shared fixtures (a minimal valid `Settings`,
factories for DeepSeek function-calling / ranker responses, and normalized
Prowlarr/C411 search payloads) so new tests avoid duplicating setup.

Convention: one `tests/test_<module>.py` per production module.

---

## Tech stack

- **Python 3.11**, fully type-annotated, dataclass-based domain.
- **python-telegram-bot v21** (async, long-polling).
- **httpx** for all HTTP, with centralized retry/backoff.
- **DeepSeek** API for intent parsing and ranking (function calling).
- **Prowlarr** (Torznab) + **C411** as torrent indexers.
- **qBittorrent** WebUI API as the download backend.
- **Streamlit** for the read-only viewer UI.
- **pytest** + **respx** for a fully-mocked test suite.
- **Docker** / docker-compose for packaging.

## License

Released under the MIT License. See [`LICENSE`](./LICENSE).
