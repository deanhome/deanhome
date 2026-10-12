"""Artwork providers."""

from __future__ import annotations

from ...config import Settings
from ..http import HttpClient
from .apple_music import AppleMusicProvider
from .base import ArtworkProvider, ProviderError, ProviderResult, ProviderStatus
from .musicbrainz import MusicBrainzProvider


def build_providers(settings: Settings, http: HttpClient) -> list[ArtworkProvider]:
    providers: list[ArtworkProvider] = []
    if settings.provider_musicbrainz:
        providers.append(MusicBrainzProvider(http))
    if settings.provider_apple_music:
        providers.append(AppleMusicProvider(http, settings.apple_music_token,
                                            settings.apple_music_storefront))
    return providers


__all__ = [
    "AppleMusicProvider", "ArtworkProvider", "MusicBrainzProvider", "ProviderError",
    "ProviderResult", "ProviderStatus", "build_providers",
]
