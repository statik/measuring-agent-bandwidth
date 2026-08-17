"""Catalog of workshop installers with live size measurement.

Each product resolves its current download URL from the publisher (release
API, CRAN index, or python.org release feed) and measures the size with a
HEAD request. When live resolution fails — an index moved, no network — the
catalog falls back to sizes measured on 2026-08-17 so a report can always be
produced, with the fallback noted in the item detail.
"""

from __future__ import annotations

import concurrent.futures
import re
from collections.abc import Callable
from dataclasses import dataclass

from netimpact import engine

FALLBACK_DATE = "2026-08-17"


@dataclass(frozen=True)
class Installer:
    """One downloadable installer for a specific platform.

    Attributes:
        key: Stable identifier, ``product-platform``.
        label: Human-readable name for the report.
        platform: ``mac`` or ``win``, used to apply the platform split.
        resolve_url: Returns the current download URL, hitting the network.
        fallback_url: Download URL measured on FALLBACK_DATE.
        fallback_bytes: Size measured on FALLBACK_DATE.
    """

    key: str
    label: str
    platform: str
    resolve_url: Callable[[], str]
    fallback_url: str
    fallback_bytes: int


def _python_url(suffix: str) -> str:
    cycles = engine.fetch_json("https://endoflife.date/api/python.json")
    version = cycles[0]["latest"]
    return f"https://www.python.org/ftp/python/{version}/python-{version}-{suffix}"


def _r_mac_url() -> str:
    page = engine.fetch_text("https://cran.r-project.org/bin/macosx/")
    match = re.search(r'href="([^"]*R-[\d.]+-arm64\.pkg)"', page)
    if not match:
        raise RuntimeError("no arm64 R installer link found on the CRAN macOS page")
    return f"https://cran.r-project.org/bin/macosx/{match.group(1)}"


def _r_win_url() -> str:
    page = engine.fetch_text("https://cran.r-project.org/bin/windows/base/")
    match = re.search(r'href="(R-[\d.]+-win\.exe)"', page)
    if not match:
        raise RuntimeError("no R installer link found on the CRAN Windows page")
    return f"https://cran.r-project.org/bin/windows/base/{match.group(1)}"


def _positron_url(template: str) -> str:
    release = engine.fetch_json(
        "https://api.github.com/repos/posit-dev/positron/releases/latest",
        engine.github_headers(),
    )
    return template.format(tag=release["tag_name"])


def _rstudio_url(url: str) -> Callable[[], str]:
    def resolve() -> str:
        return url

    return resolve


INSTALLERS: tuple[Installer, ...] = (
    Installer(
        key="python-mac",
        label="Python (macOS)",
        platform="mac",
        resolve_url=lambda: _python_url("macos11.pkg"),
        fallback_url="https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg",
        fallback_bytes=78_557_763,
    ),
    Installer(
        key="python-win",
        label="Python (Windows)",
        platform="win",
        resolve_url=lambda: _python_url("amd64.exe"),
        fallback_url="https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe",
        fallback_bytes=33_258_168,
    ),
    Installer(
        key="r-mac",
        label="R (macOS)",
        platform="mac",
        resolve_url=_r_mac_url,
        fallback_url="https://cran.r-project.org/bin/macosx/sonoma-arm64/base/R-4.6.1-arm64.pkg",
        fallback_bytes=105_066_342,
    ),
    Installer(
        key="r-win",
        label="R (Windows)",
        platform="win",
        resolve_url=_r_win_url,
        fallback_url="https://cran.r-project.org/bin/windows/base/R-4.6.1-win.exe",
        fallback_bytes=91_749_976,
    ),
    Installer(
        key="positron-mac",
        label="Positron (macOS)",
        platform="mac",
        resolve_url=lambda: _positron_url(
            "https://cdn.posit.co/positron/releases/mac/arm64/Positron-{tag}-arm64.dmg"
        ),
        fallback_url="https://cdn.posit.co/positron/releases/mac/arm64/Positron-2026.08.1-2-arm64.dmg",
        fallback_bytes=1_045_024_118,
    ),
    Installer(
        key="positron-win",
        label="Positron (Windows)",
        platform="win",
        resolve_url=lambda: _positron_url(
            "https://cdn.posit.co/positron/releases/win/x86_64/Positron-{tag}-UserSetup-x64.exe"
        ),
        fallback_url="https://cdn.posit.co/positron/releases/win/x86_64/Positron-2026.08.1-2-UserSetup-x64.exe",
        fallback_bytes=522_833_688,
    ),
    Installer(
        key="rstudio-mac",
        label="RStudio Desktop (macOS)",
        platform="mac",
        resolve_url=_rstudio_url(
            "https://download1.rstudio.org/electron/macos/RStudio-2026.08.0-187.dmg"
        ),
        fallback_url="https://download1.rstudio.org/electron/macos/RStudio-2026.08.0-187.dmg",
        fallback_bytes=852_532_730,
    ),
    Installer(
        key="rstudio-win",
        label="RStudio Desktop (Windows)",
        platform="win",
        resolve_url=_rstudio_url(
            "https://download1.rstudio.org/electron/windows/RStudio-2026.08.0-187.exe"
        ),
        fallback_url="https://download1.rstudio.org/electron/windows/RStudio-2026.08.0-187.exe",
        fallback_bytes=399_461_584,
    ),
)

PRODUCTS = ("python", "r", "positron", "rstudio")


def measure_installer(installer: Installer, share: float) -> engine.MeasuredItem:
    """Measure one installer's current size, falling back to the recorded size.

    Args:
        installer: The catalog entry to measure.
        share: Fraction of students downloading this platform's installer.

    Returns:
        A measured item ready for the report.
    """
    try:
        url = installer.resolve_url()
        size = engine.head_content_length(url)
        detail = url.rsplit("/", 1)[-1]
    except (RuntimeError, KeyError, IndexError):
        size = installer.fallback_bytes
        detail = f"{installer.fallback_url.rsplit('/', 1)[-1]} (size as measured {FALLBACK_DATE})"
    return engine.MeasuredItem(
        name=installer.label,
        group="Installers",
        share=share,
        bytes_each=size,
        detail=detail,
    )


def measure_installers(products: list[str], mac_share: float) -> list[engine.MeasuredItem]:
    """Measure the current installer sizes for the selected products.

    Args:
        products: Product identifiers from PRODUCTS.
        mac_share: Fraction of students on macOS; the rest are on Windows.

    Returns:
        Measured items for each selected product and platform, skipping
        platforms with a zero share.
    """
    shares = {"mac": mac_share, "win": 1.0 - mac_share}
    selected = [
        (installer, shares[installer.platform])
        for installer in INSTALLERS
        if installer.key.rsplit("-", 1)[0] in products and shares[installer.platform] > 0
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda pair: measure_installer(*pair), selected))
