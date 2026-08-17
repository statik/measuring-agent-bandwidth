"""Measurement and estimation engine.

Download sizes are measured from the live package indexes and CDNs with HEAD
requests, so the numbers in a report are real transfer sizes, not guesses.
AI assistant traffic is modeled from token counts because chat APIs move so
little data that precision there does not change any venue decision.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from netimpact import __version__

USER_AGENT = f"netimpact/{__version__} (+https://github.com/statik/measuring-agent-bandwidth)"

BYTES_PER_REQUEST_TOKEN = 4.0
BYTES_PER_STREAMED_TOKEN = 30.0
PROTOCOL_OVERHEAD = 1.08
STREAM_TOKENS_PER_SECOND = 75
WIFI_GOODPUT_FACTOR = 0.7
LINK_SPEEDS_MBPS = (50, 100, 250, 500, 1000)

DEFAULT_CRAN_CONTRIB = "https://cran.r-project.org/bin/macosx/sonoma-arm64/contrib/4.6"

R_BUNDLED_PACKAGES = frozenset(
    {
        "base",
        "compiler",
        "datasets",
        "grDevices",
        "graphics",
        "grid",
        "methods",
        "parallel",
        "splines",
        "stats",
        "stats4",
        "tcltk",
        "tools",
        "translations",
        "utils",
        "boot",
        "class",
        "cluster",
        "codetools",
        "foreign",
        "KernSmooth",
        "lattice",
        "MASS",
        "Matrix",
        "mgcv",
        "nlme",
        "nnet",
        "rpart",
        "spatial",
        "survival",
    }
)


@dataclass(frozen=True)
class MeasuredItem:
    """One downloadable artifact with a measured size.

    Attributes:
        name: Human-readable label for the report.
        group: Report section this item belongs to.
        share: Fraction of students (0-1] expected to download it.
        bytes_each: Measured transfer size in bytes for one download.
        detail: Optional note, e.g. the resolved package list or source URL.
    """

    name: str
    group: str
    share: float
    bytes_each: int
    detail: str = ""


@dataclass(frozen=True)
class ChatItem:
    """A modeled block of AI assistant usage.

    Attributes:
        name: Human-readable label for the report.
        share: Fraction of students (0-1] doing this activity.
        conversations: Conversations per participating student.
        requests: API round trips per conversation (an agentic user turn is 2-5).
        context_tokens_start: Tokens sent with the first request (system prompt, tools).
        context_growth_tokens: Tokens added to the context by each round trip.
        response_tokens: Tokens streamed back per round trip.
    """

    name: str
    share: float
    conversations: int
    requests: int
    context_tokens_start: int
    context_growth_tokens: int
    response_tokens: int


def head_content_length(url: str) -> int:
    """Return the Content-Length reported by a HEAD request, following redirects.

    Args:
        url: The download URL to measure.

    Returns:
        The transfer size in bytes.

    Raises:
        RuntimeError: If the request fails or the server omits Content-Length.
    """
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"HEAD {url} failed ({error}); check the URL is still current"
        ) from error
    if length is None:
        raise RuntimeError(f"HEAD {url} returned no Content-Length; provide the size explicitly")
    return int(length)


def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    """Fetch a URL body as text.

    Args:
        url: The URL to fetch.
        headers: Extra request headers.

    Returns:
        The decoded response body.

    Raises:
        RuntimeError: If the request fails.
    """
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise RuntimeError(f"GET {url} failed ({error})") from error


def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict:
    """Fetch a URL and parse the body as JSON."""
    return json.loads(fetch_text(url, headers))


def github_headers() -> dict[str, str]:
    """Build GitHub API auth headers from GITHUB_TOKEN, when set."""
    token = os.environ.get("GITHUB_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def normalize_pypi_name(name: str) -> str:
    """Normalize a PyPI package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str | None:
    """Extract the normalized package name from a requires_dist entry.

    Entries guarded by an ``extra ==`` marker are optional and return None.

    Args:
        requirement: One entry from PyPI requires_dist metadata.

    Returns:
        The normalized package name, or None if the requirement is an extra.
    """
    if "extra ==" in requirement:
        return None
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return normalize_pypi_name(match.group(1)) if match else None


def pypi_distribution_size(payload: dict) -> int:
    """Pick the download size for one PyPI package.

    Prefers the universal wheel; otherwise uses the largest platform wheel so
    the estimate is conservative across attendee platforms. Falls back to the
    first available file.

    Args:
        payload: The JSON payload from pypi.org for one package.

    Returns:
        The download size in bytes.

    Raises:
        RuntimeError: If the package has no downloadable files.
    """
    files = payload.get("urls", [])
    wheels = [f for f in files if f["packagetype"] == "bdist_wheel"]
    universal = [f for f in wheels if f["filename"].endswith("py3-none-any.whl")]
    if universal:
        return int(universal[0]["size"])
    if wheels:
        return max(int(f["size"]) for f in wheels)
    if files:
        return int(files[0]["size"])
    raise RuntimeError(f"PyPI package {payload['info']['name']} has no downloadable files")


def resolve_pypi_sizes(roots: list[str], known: dict[str, int] | None = None) -> dict[str, int]:
    """Resolve a PyPI dependency tree and measure each package's download size.

    Walks requires_dist metadata breadth-first from the root packages, skipping
    optional extras. Uses the latest release of every package, matching what a
    fresh ``pip install`` at the workshop would fetch.

    Args:
        roots: Package names to install.
        known: Packages already sized (e.g. from a uv.lock); these and their
            metadata fetches are skipped.

    Returns:
        Mapping of normalized package name to download size in bytes.
    """
    sizes: dict[str, int] = {}
    already = set(known or {})
    queue = [n for n in (normalize_pypi_name(r) for r in roots) if n not in already]
    seen = set(queue) | already
    while queue:
        name = queue.pop(0)
        payload = fetch_json(f"https://pypi.org/pypi/{name}/json")
        sizes[name] = pypi_distribution_size(payload)
        for requirement in payload["info"].get("requires_dist") or []:
            dep = requirement_name(requirement)
            if dep and dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return sizes


def parse_cran_metadata(text: str) -> dict[str, dict[str, str]]:
    """Parse a CRAN PACKAGES file into a mapping of package name to fields.

    Args:
        text: The raw PACKAGES file contents.

    Returns:
        Mapping of package name to its metadata fields.
    """
    packages: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    last_key = ""
    for line in text.splitlines():
        if not line.strip():
            if "Package" in current:
                packages[current["Package"]] = current
            current, last_key = {}, ""
        elif line[0].isspace() and last_key:
            current[last_key] += " " + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
            last_key = key.strip()
    if "Package" in current:
        packages[current["Package"]] = current
    return packages


def cran_dependencies(fields: dict[str, str]) -> list[str]:
    """List the installable dependencies of one CRAN package.

    Args:
        fields: Metadata fields for one package from a PACKAGES file.

    Returns:
        Dependency names, excluding R itself and packages bundled with R.
    """
    names = []
    for key in ("Depends", "Imports", "LinkingTo"):
        for part in fields.get(key, "").split(","):
            name = part.split("(")[0].strip()
            if name and name != "R" and name not in R_BUNDLED_PACKAGES:
                names.append(name)
    return names


def _expand_cran_tree(roots: list[str], metadata: dict[str, dict[str, str]]) -> list[str]:
    queue = list(roots)
    seen = set(queue)
    ordered = []
    while queue:
        name = queue.pop(0)
        if name not in metadata:
            raise RuntimeError(f"CRAN package {name} not found in the binary repository")
        ordered.append(name)
        for dep in cran_dependencies(metadata[name]):
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return ordered


def resolve_cran_sizes(
    packages: list[str], contrib_url: str = DEFAULT_CRAN_CONTRIB, expand_deps: bool = False
) -> tuple[dict[str, int], list[str]]:
    """Measure CRAN binary download sizes for a set of packages.

    Args:
        packages: CRAN package names. Pass a lockfile's full transitive list
            with ``expand_deps=False``, or top-level names with ``True``.
        contrib_url: A CRAN binary contrib URL, e.g. the macOS arm64 tree.
        expand_deps: Walk Depends/Imports/LinkingTo from the given packages.

    Returns:
        A tuple of (name-to-bytes mapping, names missing from the repository).
        Missing packages (GitHub or Bioconductor sources, base packages) are
        reported rather than sized.
    """
    metadata = parse_cran_metadata(fetch_text(f"{contrib_url}/PACKAGES"))
    if expand_deps:
        wanted = _expand_cran_tree(packages, metadata)
    else:
        wanted = [p for p in packages if p not in R_BUNDLED_PACKAGES and p != "R"]
    available = [name for name in wanted if name in metadata]
    missing = [name for name in wanted if name not in metadata]
    urls = [f"{contrib_url}/{name}_{metadata[name]['Version']}.tgz" for name in available]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        sizes = dict(zip(available, pool.map(head_content_length, urls), strict=True))
    return sizes, missing


def conversation_bytes(chat: ChatItem) -> tuple[int, int]:
    """Model the bytes one conversation moves over the network.

    Chat APIs are stateless: every round trip re-sends the whole conversation,
    so uplink grows with each request while the streamed response stays small.
    Provider-side prompt caching reduces cost and latency, not transfer.

    Args:
        chat: The modeled usage block.

    Returns:
        A tuple of (uplink bytes, downlink bytes) for one conversation.
    """
    growth_total = chat.context_growth_tokens * chat.requests * (chat.requests - 1) // 2
    request_tokens = chat.requests * chat.context_tokens_start + growth_total
    uplink = request_tokens * BYTES_PER_REQUEST_TOKEN * PROTOCOL_OVERHEAD
    downlink = chat.requests * chat.response_tokens * BYTES_PER_STREAMED_TOKEN * PROTOCOL_OVERHEAD
    return int(uplink), int(downlink)


def assess(one_time_bytes: int, assistant_bytes: int, assistant_mbps: float) -> dict[str, str]:
    """Judge the overall venue impact in plain language.

    Args:
        one_time_bytes: Aggregate one-time download volume for the room.
        assistant_bytes: Aggregate assistant traffic for the whole session.
        assistant_mbps: Room-wide average assistant bandwidth.

    Returns:
        A dict with a verdict ``label``, an accent ``color``, and a one-line
        ``impact`` sentence for the top of the report.
    """
    assistant = (
        f"AI assistant traffic is negligible: {format_bytes(assistant_bytes)} for the whole "
        f"room over the full session, averaging {assistant_mbps:g} Mbps — less than one video call."
    )
    if one_time_bytes < 10e9:
        return {
            "label": "LIGHT",
            "color": "#235a68",
            "impact": f"{assistant} Any network that handles normal browsing "
            "will handle this workshop.",
        }
    if one_time_bytes < 40e9:
        return {
            "label": "MODERATE",
            "color": "#c2762c",
            "impact": (
                f"{assistant} Plan around the one-time {format_bytes(one_time_bytes)} download "
                "burst: stagger the install step or ask attendees to install before arriving."
            ),
        }
    return {
        "label": "PLAN DOWNLOADS",
        "color": "#a33d2f",
        "impact": (
            f"{assistant} The one-time {format_bytes(one_time_bytes)} download burst is the real "
            "load: pre-install before arrival, hand out offline installers, or schedule downloads."
        ),
    }


def summarize(
    name: str,
    students: int,
    duration_hours: float,
    items: list[MeasuredItem],
    chats: list[ChatItem],
) -> dict:
    """Aggregate measurements into the report data structure.

    Args:
        name: Workshop name.
        students: Number of attendees.
        duration_hours: Session length in hours.
        items: Measured one-time downloads.
        chats: Modeled assistant usage blocks.

    Returns:
        A JSON-serializable dict with every number the report needs.
    """
    groups: dict[str, list[dict]] = {}
    group_totals: dict[str, int] = {}
    for item in items:
        downloads = students * item.share
        aggregate = int(item.bytes_each * downloads)
        group_totals[item.group] = group_totals.get(item.group, 0) + aggregate
        groups.setdefault(item.group, []).append(
            {
                "name": item.name,
                "bytes_each": item.bytes_each,
                "share": item.share,
                "downloads": round(downloads, 1),
                "aggregate_bytes": aggregate,
                "detail": item.detail,
            }
        )
    group_list = [
        {"group": g, "items": rows, "aggregate_bytes": group_totals[g]}
        for g, rows in groups.items()
    ]
    one_time_total = sum(group_totals.values())

    chat_rows = []
    chat_total = 0
    for chat in chats:
        uplink, downlink = conversation_bytes(chat)
        per_student = (uplink + downlink) * chat.conversations
        aggregate = int(per_student * students * chat.share)
        chat_total += aggregate
        chat_rows.append(
            {
                "name": chat.name,
                "conversations_per_student": chat.conversations,
                "requests_per_conversation": chat.requests,
                "uplink_bytes_per_conversation": uplink,
                "downlink_bytes_per_conversation": downlink,
                "bytes_per_student": per_student,
                "share": chat.share,
                "aggregate_bytes": aggregate,
            }
        )
    duration_seconds = max(duration_hours, 0.1) * 3600
    average_mbps = round(chat_total * 8 / duration_seconds / 1e6, 3)
    stream_bps = STREAM_TOKENS_PER_SECOND * BYTES_PER_STREAMED_TOKEN * PROTOCOL_OVERHEAD * 8

    return {
        "tool": f"netimpact {__version__}",
        "generated": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "workshop": {"name": name, "students": students, "duration_hours": duration_hours},
        "verdict": assess(one_time_total, chat_total, average_mbps),
        "one_time": {
            "groups": group_list,
            "aggregate_bytes": one_time_total,
            "average_bytes_per_student": one_time_total // students,
        },
        "assistant": {
            "items": chat_rows,
            "aggregate_bytes": chat_total,
            "average_bytes_per_student": chat_total // students,
            "average_mbps": average_mbps,
            "all_streaming_at_once_mbps": round(students * stream_bps / 1e6, 2),
        },
        "download_burst": [
            {
                "link_mbps": speed,
                "minutes": round(one_time_total * 8 / (speed * 1e6 * WIFI_GOODPUT_FACTOR) / 60, 1),
            }
            for speed in LINK_SPEEDS_MBPS
        ],
        "assumptions": {
            "bytes_per_request_token": BYTES_PER_REQUEST_TOKEN,
            "bytes_per_streamed_token": BYTES_PER_STREAMED_TOKEN,
            "protocol_overhead": PROTOCOL_OVERHEAD,
            "stream_tokens_per_second": STREAM_TOKENS_PER_SECOND,
            "wifi_goodput_factor": WIFI_GOODPUT_FACTOR,
        },
    }


def format_bytes(count: float) -> str:
    """Format a byte count with decimal units, the way network staff read them."""
    for unit, factor in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if count >= factor:
            return f"{count / factor:.2f} {unit}".replace(".00 ", " ")
    return f"{int(count)} B"
