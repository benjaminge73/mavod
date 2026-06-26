"""mavod — Telegram bot V2 + in-process workflow pour recherche/téléchargement torrents.

Architecture :
- config / exceptions / logging_setup    — primitives transverses
- domain/                                — types métier (Intent, Torrent, …)
- adapters/                              — I/O isolé (DeepSeek, Prowlarr, C411, qB, bencode)
- services/                              — orchestration (intent, search, ranking, workflow)
- telegram/                              — bot + lifecycle (state, jobs)

Entry point : `python -m mavod`.
"""

__version__ = "2.0.0"
