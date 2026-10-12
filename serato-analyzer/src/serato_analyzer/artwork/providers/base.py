"""Artwork provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from ..models import ArtworkCandidate, TrackQuery


class ProviderStatus(str, Enum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"     # the endpoint is not available in this configuration
    OFFLINE = "offline"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class ProviderError(Exception):
    def __init__(self, message: str, status: ProviderStatus = ProviderStatus.ERROR):
        super().__init__(message)
        self.status = status


@dataclass
class ProviderResult:
    provider: str
    status: ProviderStatus
    candidates: list[ArtworkCandidate] = field(default_factory=list)
    message: str = ""


class ArtworkProvider(ABC):
    #: Stable identifier stored in the database.
    name: str = ""
    display_name: str = ""

    def configuration_problem(self) -> str | None:
        """``None`` when ready, otherwise a user-facing explanation."""
        return None

    @abstractmethod
    def search(self, track: TrackQuery) -> list[ArtworkCandidate]:
        """Return unscored candidates.  Raise :class:`ProviderError` on failure."""
