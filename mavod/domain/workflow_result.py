"""WorkflowResult : sortie typée du workflow torrent.

Schema v2 : structure stable, sérialisable JSON, consommée par l'UI Streamlit
et persistée dans `torrents/<search_id>/result.json`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence


SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class QbSubmitResult:
    """Résultat de l'ajout à qBittorrent."""

    infohash: str
    name: str
    submitted_at: float
    tags: Optional[str] = None


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Sortie complète du workflow pour une recherche."""

    schema_version: int
    search_id: str
    title: str
    media_type: str  # "movie" | "serie"
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    imdb_id: Optional[str] = None
    candidates: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    best_choice: Optional[Mapping[str, object]] = None
    llm_reasoning: Optional[str] = None
    llm_response: Optional[str] = None
    qb_submit: Optional[QbSubmitResult] = None
    error: Optional[str] = None
    created_at: float = 0.0

    def to_json(self) -> str:
        """Sérialise en JSON indenté (consommé par l'UI Streamlit)."""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)

    def write(self, path: Path) -> None:
        """Persiste sur disque (crée le dossier parent si besoin)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
