# netimpact

Estimate the network load of a hands-on workshop — including the AI assistant
traffic — and hand venue staff a one-page PDF they can plan against.

Instructors describe a workshop (students, installers, a GitHub project, how
much attendees will use an AI assistant such as Posit Assistant or Claude).
netimpact measures the real download sizes from the live release CDNs and
package indexes, models the assistant chat traffic, and renders a report with
a clear verdict.

**[Example report (PDF)](docs/example/posit-workshop.pdf)** — a 20-student
Posit Assistant workshop. The punchline: 33.66 GB of one-time installer and
package downloads, versus **144.89 MB** of AI assistant traffic for the whole
room across the entire 6-hour session — an average of 0.054 Mbps, less than
one video call.

## Run the Shiny app

```bash
uv sync
uv run shiny run app.py
```

Point it at a GitHub project and it scans the tree for `renv.lock`, `uv.lock`,
`manifest.json` (Posit Connect), and `requirements.txt` files to size the
packages students will install, adds the installers you select, and models
assistant usage from a preset. Download the result as PDF, Typst source, or
JSON.

### Deploy to Connect Cloud

The app deploys to [Connect Cloud](https://connect.posit.cloud) as-is:

1. Publish this repository (or your fork) as a **Shiny for Python** app with
   entrypoint `app.py`. Dependencies come from `requirements.txt`.
2. Optionally set a `GITHUB_TOKEN` environment variable so repository scans
   are not limited to 60 GitHub API requests per hour.

## Run the CLI

Scenarios are TOML files; see [`scenarios/posit-workshop.toml`](scenarios/posit-workshop.toml).

```bash
uv run python -m netimpact scenarios/posit-workshop.toml --out-dir reports
```

This writes `<scenario>.typ`, `<scenario>.pdf`, and `<scenario>.json`.
A scenario can combine:

| Table | What it does |
|---|---|
| `[installers]` | `products` (python, r, positron, rstudio) and `mac_share`; sizes resolved from the publishers live |
| `[[downloads]]` | any direct URL, measured with a HEAD request (or a fixed `bytes`) |
| `[[pypi]]` / `[[cran]]` | package names, expanded through their dependency trees and sized |
| `[[repos]]` | a GitHub repository, scanned for lockfiles |
| `[assistant]` / `[[chat]]` | a usage preset, or explicit token-level conversation models |

## How the numbers are produced

**Downloads are measured, not guessed.** Installer sizes come from HEAD
requests against the publishers' CDNs (with sizes recorded on 2026-08-17 as a
fallback if an index moves). Python packages use PyPI metadata — the largest
platform wheel per package, so estimates are conservative — or exact sizes
recorded in `uv.lock`. R packages use CRAN's binary repository.

**Assistant traffic is modeled.** Chat APIs are stateless: every round trip
re-sends the conversation so far, so uplink grows quadratically with
conversation length while responses stream back as a trickle of text. The
model uses 4 bytes per request token, 30 bytes per streamed token (including
SSE framing), 1.08× protocol overhead, and ~75 tokens/second of streaming.
Provider-side prompt caching reduces cost and latency, not network transfer.
Even generous assumptions leave a full day of agentic use per student in the
tens of megabytes — the modeling error is irrelevant next to a single
installer.

**Scale math.** Per-item sizes × students × share give room aggregates; the
report shows how long the worst-case "everyone downloads everything at once"
burst takes at common venue link speeds, assuming 70% of link speed as
real-world Wi-Fi goodput.

### Validating against real traffic

To check the model at your own desk before an event:

- Proxy the IDE through [mitmproxy](https://mitmproxy.org) (`mitmproxy
  --mode local`) and read per-host byte counts from the flow list while you
  run through the workshop script once.
- On macOS, `nettop -p <pid> -J bytes_in,bytes_out` gives per-process totals
  without a proxy; on Windows, Resource Monitor's Network tab does the same.
- At the venue, most access points report per-client byte counters — compare
  a pilot student's counter against the per-student column in the report.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff format --check . && uv run ruff check .
uv run ty check
```
