"""Services : logique métier orchestrée.

Chaque service consomme des `adapters/` et renvoie/manipule des
`domain/` dataclasses. Pas d'I/O direct, pas d'os.environ.
"""

from mavod.services.dedup import dedup_torrents, normalize_release_name
from mavod.services.intent_service import IntentService
from mavod.services.ranking_service import (
    LLMRankingStrategy,
    RankingService,
)
from mavod.services.search_service import SearchService
from mavod.services.workflow_service import (
    WorkflowService,
    build_search_id,
    sanitize_filename,
)

__all__ = [
    "LLMRankingStrategy",
    "dedup_torrents",
    "normalize_release_name",
    "IntentService",
    "RankingService",
    "SearchService",
    "WorkflowService",
    "build_search_id",
    "sanitize_filename",
]
