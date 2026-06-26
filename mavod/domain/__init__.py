"""Types métier purs maVOD (zéro I/O).

Tous les services et adapters échangent des instances de ces dataclasses
plutôt que des dict bruts. Permet de typer fortement le pipeline et de
détecter les invalidités au plus tôt.
"""

from mavod.domain.intent import (
    ClarificationRequest,
    Intent,
    IntentResult,
)
from mavod.domain.torrent import (
    RankingDecision,
    Torrent,
    TorrentFile,
)
from mavod.domain.workflow_result import (
    QbSubmitResult,
    WorkflowResult,
)

__all__ = [
    "ClarificationRequest",
    "Intent",
    "IntentResult",
    "QbSubmitResult",
    "RankingDecision",
    "Torrent",
    "TorrentFile",
    "WorkflowResult",
]
