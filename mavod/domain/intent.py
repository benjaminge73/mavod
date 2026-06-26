"""Intent : requête utilisateur structurée."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Union

from mavod.exceptions import IntentValidationError


_IMDB_RE = re.compile(r"^tt\d{7,8}$")
MediaType = Literal["movie", "serie"]


@dataclass(frozen=True, slots=True)
class Intent:
    """Requête utilisateur prête pour le workflow torrent."""

    title: str
    type: MediaType
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    imdb_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.title or not self.title.strip():
            raise IntentValidationError(f"title manquant: {self!r}")
        if self.type not in ("movie", "serie"):
            raise IntentValidationError(f"type invalide: {self.type!r}")
        if self.year is not None and not isinstance(self.year, int):
            raise IntentValidationError(f"year invalide: {self.year!r}")
        if self.season is not None and not isinstance(self.season, int):
            raise IntentValidationError(f"season invalide: {self.season!r}")
        if self.episode is not None and not isinstance(self.episode, int):
            raise IntentValidationError(f"episode invalide: {self.episode!r}")
        if self.type == "movie" and self.episode is not None:
            raise IntentValidationError(f"episode interdit pour type=movie: {self!r}")
        if self.imdb_id is not None:
            if not isinstance(self.imdb_id, str) or not _IMDB_RE.match(self.imdb_id):
                raise IntentValidationError(f"imdb_id invalide: {self.imdb_id!r}")

    # Sentinelles que le function-calling rend parfois en string pour un champ
    # nullable (ex. la chaîne "null" au lieu du null JSON).
    _NULL_SENTINELS = {"", "null", "none", "n/a", "nan", "undefined"}

    @staticmethod
    def _clean_optional(value: object) -> object:
        """Neutralise les sentinelles LLM ('null', 'none', ''…) → None."""
        if isinstance(value, str) and value.strip().lower() in Intent._NULL_SENTINELS:
            return None
        return value

    @staticmethod
    def _coerce_int(value: object) -> object:
        """Coerce un entier rendu en string ('2026' → 2026) ; sentinelle → None."""
        value = Intent._clean_optional(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        return value

    @classmethod
    def from_dict(cls, data: dict) -> "Intent":
        """Construit un Intent depuis un dict (sortie LLM par exemple).

        Frontière tolérante côté LLM : neutralise les sentinelles 'null'/'none'
        rendues en string, coerce les entiers en string, et drope un imdb_id mal
        formé (enrichissement optionnel) au lieu de rejeter toute la requête. Le
        constructeur `Intent(...)` direct, lui, reste strict.
        """
        title = data.get("title")
        if isinstance(title, str):
            title = title.strip()

        imdb_id = cls._clean_optional(data.get("imdb_id"))
        if isinstance(imdb_id, str):
            imdb_id = imdb_id.strip()
            # imdb_id halluciné/mal formé → droppé (non bloquant).
            if not _IMDB_RE.match(imdb_id):
                imdb_id = None

        return cls(
            title=title or "",
            type=data.get("type"),
            year=cls._coerce_int(data.get("year")),
            season=cls._coerce_int(data.get("season")),
            episode=cls._coerce_int(data.get("episode")),
            imdb_id=imdb_id,
        )


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    """Question de désambiguïsation à poser à l'utilisateur."""

    question: str
    options: Optional[Sequence[str]] = None
    missing_field: Optional[str] = None
    tool_call_id: str = ""

    def __post_init__(self) -> None:
        if not self.question or not self.question.strip():
            raise IntentValidationError("question vide")


IntentResult = Union[Intent, ClarificationRequest]
