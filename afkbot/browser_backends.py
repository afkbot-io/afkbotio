"""Shared browser backend catalog for runtime, CLI, and settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, cast


BrowserBackendId = Literal["playwright_chromium", "lightpanda_cdp"]

PLAYWRIGHT_CHROMIUM: Final[BrowserBackendId] = "playwright_chromium"
LIGHTPANDA_CDP: Final[BrowserBackendId] = "lightpanda_cdp"
DEFAULT_BROWSER_BACKEND: Final[BrowserBackendId] = PLAYWRIGHT_CHROMIUM
LIGHTPANDA_DEFAULT_CDP_URL: Final[str] = "http://127.0.0.1:9222"


@dataclass(frozen=True, slots=True)
class BrowserBackendSpec:
    """One supported browser backend with CLI-facing metadata."""

    id: BrowserBackendId
    label: str
    summary: str
    install_summary: str
    requires_cdp_url: bool


_SPECS: Final[dict[BrowserBackendId, BrowserBackendSpec]] = {
    PLAYWRIGHT_CHROMIUM: BrowserBackendSpec(
        id=PLAYWRIGHT_CHROMIUM,
        label="Playwright Chromium",
        summary="Best compatibility. Installs and launches local Chromium runtime.",
        install_summary="Install Playwright and the local Chromium browser runtime.",
        requires_cdp_url=False,
    ),
    LIGHTPANDA_CDP: BrowserBackendSpec(
        id=LIGHTPANDA_CDP,
        label="Lightpanda (CDP)",
        summary="Best for headless servers. Connects Playwright to an external CDP browser.",
        install_summary="Install Playwright client support and use an external CDP browser such as Lightpanda.",
        requires_cdp_url=True,
    ),
}


def browser_backend_choices() -> tuple[BrowserBackendId, ...]:
    """Return supported browser backend identifiers in stable display order."""

    return tuple(_SPECS.keys())


def get_browser_backend_spec(backend: str) -> BrowserBackendSpec:
    """Return backend metadata or fall back to the default backend."""

    normalized = str(backend).strip().lower()
    if normalized in _SPECS:
        return _SPECS[cast(BrowserBackendId, normalized)]
    return _SPECS[DEFAULT_BROWSER_BACKEND]
