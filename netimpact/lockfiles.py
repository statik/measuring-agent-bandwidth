"""Scan a GitHub project for dependency lockfiles and size the packages they name.

Recognized files anywhere in the repository tree:

- ``uv.lock`` — exact wheel sizes are recorded in the lock, no index queries needed.
- ``renv.lock`` — full transitive R package list, sized against CRAN binaries.
- ``manifest.json`` — Posit Connect manifests; the ``packages`` table lists R packages.
- ``requirements.txt`` — top-level Python names, resolved through PyPI metadata.
"""

from __future__ import annotations

import json
import re
import statistics
import tomllib
from dataclasses import dataclass, field

from netimpact import engine

LOCKFILE_NAMES = ("renv.lock", "uv.lock", "manifest.json", "requirements.txt")


@dataclass
class RepoScan:
    """Everything learned from scanning one GitHub repository.

    Attributes:
        slug: The ``owner/repo`` identifier.
        branch: The default branch that was scanned.
        findings: Repo-relative paths of recognized lockfiles.
        python_sizes: Exact package sizes from uv.lock files, in bytes.
        python_roots: Top-level package names from requirements.txt files.
        r_packages: R package names from renv.lock and manifest.json files.
    """

    slug: str
    branch: str
    findings: list[str] = field(default_factory=list)
    python_sizes: dict[str, int] = field(default_factory=dict)
    python_roots: set[str] = field(default_factory=set)
    r_packages: set[str] = field(default_factory=set)


def parse_github_repo(reference: str) -> str:
    """Normalize a GitHub URL or ``owner/repo`` reference to a slug.

    Args:
        reference: A GitHub URL or bare ``owner/repo`` string.

    Returns:
        The ``owner/repo`` slug.

    Raises:
        ValueError: If the reference is not recognizably a GitHub repository.
    """
    cleaned = reference.strip().removesuffix(".git").rstrip("/")
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+)", cleaned)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", cleaned):
        return cleaned
    raise ValueError(f"could not parse a GitHub repository from {reference!r}")


def parse_uv_lock(text: str) -> dict[str, int]:
    """Extract exact package download sizes from a uv.lock file.

    Uses the largest recorded wheel per package (conservative across attendee
    platforms) and falls back to the sdist size. Local and virtual packages
    (the project itself) are skipped.

    Args:
        text: The uv.lock file contents.

    Returns:
        Mapping of normalized package name to download size in bytes.
    """
    data = tomllib.loads(text)
    sizes: dict[str, int] = {}
    for package in data.get("package", []):
        source = package.get("source", {})
        if "registry" not in source:
            continue
        wheel_sizes = [w["size"] for w in package.get("wheels", []) if w.get("size")]
        size = max(wheel_sizes) if wheel_sizes else package.get("sdist", {}).get("size")
        if size:
            sizes[engine.normalize_pypi_name(package["name"])] = int(size)
    return sizes


def parse_renv_lock(text: str) -> list[str]:
    """Extract the R package names recorded in an renv.lock file.

    Args:
        text: The renv.lock file contents.

    Returns:
        Package names from repository sources (CRAN and CRAN-like).
    """
    data = json.loads(text)
    names = []
    for name, fields in data.get("Packages", {}).items():
        if fields.get("Source", "Repository") in ("Repository", "CRAN"):
            names.append(name)
    return names


def parse_manifest_json(text: str) -> list[str]:
    """Extract R package names from a Posit Connect manifest.json.

    Python manifests reference a requirements file instead of listing packages,
    so they contribute nothing here; the requirements file is picked up by the
    tree scan on its own.

    Args:
        text: The manifest.json file contents.

    Returns:
        R package names, or an empty list for Python content manifests.
    """
    data = json.loads(text)
    if "python" in data:
        return []
    return list(data.get("packages", {}))


def parse_requirements_txt(text: str) -> list[str]:
    """Extract top-level package names from a requirements.txt file.

    Args:
        text: The requirements.txt file contents.

    Returns:
        Normalized package names. Options, includes, and URL requirements
        are skipped.
    """
    names = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name_part = line.split("@")[0].strip()
        if "://" in name_part:
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", name_part)
        if match:
            names.append(engine.normalize_pypi_name(match.group(1)))
    return names


def scan_repo(reference: str) -> RepoScan:
    """Scan a GitHub repository for lockfiles and collect the packages they name.

    Args:
        reference: A GitHub URL or ``owner/repo`` string.

    Returns:
        The scan results, with Python and R package sets merged across files.

    Raises:
        RuntimeError: If the repository or its tree cannot be fetched.
        ValueError: If the reference is not a GitHub repository.
    """
    slug = parse_github_repo(reference)
    headers = engine.github_headers()
    repo = engine.fetch_json(f"https://api.github.com/repos/{slug}", headers)
    branch = repo["default_branch"]
    tree = engine.fetch_json(
        f"https://api.github.com/repos/{slug}/git/trees/{branch}?recursive=1", headers
    )
    scan = RepoScan(slug=slug, branch=branch)
    for entry in tree.get("tree", []):
        path = entry["path"]
        if entry["type"] == "blob" and path.rsplit("/", 1)[-1] in LOCKFILE_NAMES:
            scan.findings.append(path)
    for path in scan.findings:
        text = engine.fetch_text(f"https://raw.githubusercontent.com/{slug}/{branch}/{path}")
        _merge_lockfile(scan, path, text)
    return scan


def _merge_lockfile(scan: RepoScan, path: str, text: str) -> None:
    basename = path.rsplit("/", 1)[-1]
    if basename == "uv.lock":
        for name, size in parse_uv_lock(text).items():
            scan.python_sizes[name] = max(size, scan.python_sizes.get(name, 0))
    elif basename == "requirements.txt":
        scan.python_roots.update(parse_requirements_txt(text))
    elif basename == "renv.lock":
        scan.r_packages.update(parse_renv_lock(text))
    elif basename == "manifest.json":
        scan.r_packages.update(parse_manifest_json(text))


def measure_scan(scan: RepoScan, share: float = 1.0) -> list[engine.MeasuredItem]:
    """Turn a repository scan into measured download items.

    Args:
        scan: The result of scan_repo().
        share: Fraction of students who set up this project.

    Returns:
        Up to two items — one for Python packages, one for R packages — with
        the resolved package counts and any unsized packages noted.
    """
    items = []
    python_sizes = dict(scan.python_sizes)
    if scan.python_roots:
        python_sizes |= engine.resolve_pypi_sizes(sorted(scan.python_roots), known=python_sizes)
    if python_sizes:
        items.append(
            engine.MeasuredItem(
                name=f"Python packages for {scan.slug}",
                group="Workshop project packages",
                share=share,
                bytes_each=sum(python_sizes.values()),
                detail=f"{len(python_sizes)} packages from {scan.slug}",
            )
        )
    if scan.r_packages:
        sizes, missing = engine.resolve_cran_sizes(sorted(scan.r_packages))
        total = sum(sizes.values())
        detail = f"{len(sizes)} CRAN binary packages from {scan.slug}"
        if missing and sizes:
            typical = int(statistics.median(sizes.values()))
            total += typical * len(missing)
            detail += (
                f"; {len(missing)} non-CRAN packages estimated at "
                f"{engine.format_bytes(typical)} each"
            )
        items.append(
            engine.MeasuredItem(
                name=f"R packages for {scan.slug}",
                group="Workshop project packages",
                share=share,
                bytes_each=total,
                detail=detail,
            )
        )
    return items
