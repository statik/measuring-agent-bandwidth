"""Command-line interface: turn a scenario TOML file into report files.

Usage:
    python -m netimpact scenarios/posit-workshop.toml --out-dir reports/

Writes ``<stem>.typ``, ``<stem>.pdf`` (when the typst package is installed),
and ``<stem>.json`` next to each other.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tomllib

from netimpact import engine, installers, lockfiles, presets, report


def load_scenario(path: str) -> dict:
    """Load and validate a scenario TOML file.

    Args:
        path: Filesystem path to the scenario.

    Returns:
        The parsed TOML data.

    Raises:
        SystemExit: If the file is missing, malformed, or incomplete.
    """
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        sys.exit(f"error: scenario file not found: {path}")
    except tomllib.TOMLDecodeError as error:
        sys.exit(f"error: {path} is not valid TOML: {error}")
    workshop = data.get("workshop", {})
    students = workshop.get("students")
    if not workshop.get("name") or not isinstance(students, int) or students < 1:
        sys.exit(f"error: {path} needs [workshop] with a name and a positive integer students")
    return data


def _share(entry: dict, label: str) -> float:
    share = float(entry.get("share", 1.0))
    if not 0.0 < share <= 1.0:
        sys.exit(f"error: {label} has share {share}; it must be in (0, 1]")
    return share


def build_items(scenario: dict) -> list[engine.MeasuredItem]:
    """Measure every download source described by a scenario.

    Args:
        scenario: The parsed scenario TOML.

    Returns:
        Measured items from installers, direct URLs, package roots, and
        scanned GitHub repositories.
    """
    items: list[engine.MeasuredItem] = []
    installer_config = scenario.get("installers")
    if installer_config:
        items += installers.measure_installers(
            installer_config.get("products", list(installers.PRODUCTS)),
            float(installer_config.get("mac_share", 0.5)),
        )
    for entry in scenario.get("downloads", []):
        size = int(entry["bytes"]) if "bytes" in entry else engine.head_content_length(entry["url"])
        items.append(
            engine.MeasuredItem(
                name=entry["name"],
                group=entry.get("group", "Downloads"),
                share=_share(entry, entry["name"]),
                bytes_each=size,
                detail=entry.get("url", "size provided in scenario"),
            )
        )
    for entry in scenario.get("pypi", []):
        sizes = engine.resolve_pypi_sizes(entry["packages"])
        items.append(_package_item(entry, sum(sizes.values()), len(sizes), "PyPI"))
    for entry in scenario.get("cran", []):
        contrib = entry.get("contrib_url", engine.DEFAULT_CRAN_CONTRIB)
        sizes, missing = engine.resolve_cran_sizes(entry["packages"], contrib, expand_deps=True)
        if missing:
            sys.exit(f"error: CRAN packages not found in {contrib}: {', '.join(missing)}")
        items.append(_package_item(entry, sum(sizes.values()), len(sizes), "CRAN"))
    for entry in scenario.get("repos", []):
        scan = lockfiles.scan_repo(entry["url"])
        found = ", ".join(scan.findings) or "no lockfiles"
        print(f"Scanned {scan.slug}@{scan.branch}: {found}", file=sys.stderr)
        items += lockfiles.measure_scan(scan, share=_share(entry, entry["url"]))
    return items


def _package_item(entry: dict, total: int, count: int, index: str) -> engine.MeasuredItem:
    roots = ", ".join(entry["packages"])
    return engine.MeasuredItem(
        name=entry.get("name", f"{index}: {roots}"),
        group=entry.get("group", f"{index} packages"),
        share=_share(entry, roots),
        bytes_each=total,
        detail=f"{count} packages resolved from {roots}",
    )


def build_chats(scenario: dict) -> list[engine.ChatItem]:
    """Build the assistant usage blocks described by a scenario.

    Args:
        scenario: The parsed scenario TOML.

    Returns:
        Chat items from the named preset plus any explicit [[chat]] blocks.
    """
    chats: list[engine.ChatItem] = []
    preset = scenario.get("assistant", {}).get("preset")
    if preset:
        if preset not in presets.PRESETS:
            sys.exit(
                f"error: unknown assistant preset {preset!r}; use one of {list(presets.PRESETS)}"
            )
        chats += presets.PRESETS[preset]
    for entry in scenario.get("chat", []):
        chats.append(
            engine.ChatItem(
                name=entry["name"],
                share=_share(entry, entry["name"]),
                conversations=int(entry.get("conversations", 1)),
                requests=int(entry.get("requests", 12)),
                context_tokens_start=int(entry.get("context_tokens_start", 4000)),
                context_growth_tokens=int(entry.get("context_growth_tokens", 2000)),
                response_tokens=int(entry.get("response_tokens", 800)),
            )
        )
    return chats


def main(argv: list[str] | None = None) -> None:
    """Run the CLI."""
    parser = argparse.ArgumentParser(
        prog="netimpact",
        description="Estimate workshop network load, including AI assistant traffic.",
    )
    parser.add_argument("scenario", help="path to a scenario TOML file")
    parser.add_argument("--out-dir", default=".", help="directory for the report files")
    args = parser.parse_args(argv)

    scenario = load_scenario(args.scenario)
    workshop = scenario["workshop"]
    print(f"Measuring scenario '{workshop['name']}'...", file=sys.stderr)
    try:
        items = build_items(scenario)
    except (RuntimeError, ValueError) as error:
        sys.exit(f"error: {error}")
    results = engine.summarize(
        name=workshop["name"],
        students=workshop["students"],
        duration_hours=float(workshop.get("duration_hours", 3.0)),
        items=items,
        chats=build_chats(scenario),
    )

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(args.scenario).stem
    typst_source = report.typst_report(results)
    (out_dir / f"{stem}.typ").write_text(typst_source, encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    written = [f"{stem}.typ", f"{stem}.json"]
    try:
        (out_dir / f"{stem}.pdf").write_bytes(report.render_pdf(typst_source))
        written.append(f"{stem}.pdf")
    except RuntimeError as error:
        print(f"warning: skipped PDF ({error})", file=sys.stderr)

    one_time = engine.format_bytes(results["one_time"]["aggregate_bytes"])
    assistant = engine.format_bytes(results["assistant"]["aggregate_bytes"])
    print(f"One-time downloads: {one_time}; assistant traffic: {assistant}", file=sys.stderr)
    print(f"Wrote {', '.join(written)} to {out_dir}/", file=sys.stderr)


if __name__ == "__main__":
    main()
