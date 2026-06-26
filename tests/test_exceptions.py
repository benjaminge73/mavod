"""Tests de la hiérarchie d'exceptions maVOD."""

from __future__ import annotations

import pytest

from mavod.exceptions import (
    C411Error,
    ConfigError,
    DeepSeekError,
    DeepSeekMalformed,
    DeepSeekRateLimit,
    DeepSeekTimeout,
    DuplicateTorrent,
    IntentParseError,
    IntentValidationError,
    MavodError,
    NoCandidatesFound,
    ProwlarrError,
    QBittorrentError,
    RankingError,
    TorrentSourceError,
    WorkflowError,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "exc_class, parents",
    [
        (ConfigError, (MavodError,)),
        (DeepSeekError, (MavodError,)),
        (DeepSeekTimeout, (DeepSeekError, MavodError)),
        (DeepSeekRateLimit, (DeepSeekError, MavodError)),
        (DeepSeekMalformed, (DeepSeekError, MavodError)),
        (IntentParseError, (MavodError,)),
        (IntentValidationError, (IntentParseError, MavodError)),
        (TorrentSourceError, (MavodError,)),
        (ProwlarrError, (TorrentSourceError, MavodError)),
        (C411Error, (TorrentSourceError, MavodError)),
        (RankingError, (MavodError,)),
        (QBittorrentError, (MavodError,)),
        (DuplicateTorrent, (QBittorrentError, MavodError)),
        (WorkflowError, (MavodError,)),
        (NoCandidatesFound, (WorkflowError, MavodError)),
    ],
)
def test_hierarchy(exc_class, parents):
    """Chaque exception hérite bien des parents attendus."""
    for parent in parents:
        assert issubclass(exc_class, parent)


def test_root_is_exception():
    """MavodError descend bien d'Exception."""
    assert issubclass(MavodError, Exception)


def test_catch_subclass_via_root():
    """`except MavodError` doit attraper toutes les sous-classes."""
    with pytest.raises(MavodError):
        raise DuplicateTorrent("dup")
    with pytest.raises(MavodError):
        raise IntentValidationError("bad year")
