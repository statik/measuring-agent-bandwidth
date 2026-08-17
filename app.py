"""netimpact Shiny app: estimate a workshop's network impact for venue staff.

Point it at a GitHub project and it finds renv.lock, uv.lock, manifest.json,
and requirements.txt files to size the packages students will install, adds
the installers they download, models the AI assistant traffic, and produces
a one-page PDF report for the venue.

Deployable to Connect Cloud as-is; set GITHUB_TOKEN in the environment to
raise the GitHub API rate limit for busy deployments.
"""

from __future__ import annotations

import json

from shiny import App, reactive, render, req, ui

from netimpact import engine, installers, lockfiles, presets, report

PRODUCT_CHOICES = {
    "python": "Python",
    "r": "R",
    "positron": "Positron",
    "rstudio": "RStudio Desktop",
}

app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.input_text("name", "Workshop name", value="Hands-on workshop"),
        ui.input_text(
            "repo",
            "GitHub project (optional)",
            placeholder="owner/repo or full URL",
        ),
        ui.help_text(
            "Scanned for renv.lock, uv.lock, manifest.json, and requirements.txt "
            "to size the packages students install."
        ),
        ui.input_numeric("students", "Students", value=20, min=1),
        ui.input_numeric("duration", "Session length (hours)", value=6, min=0.5, step=0.5),
        ui.input_checkbox_group(
            "products",
            "Installers downloaded at the venue",
            choices=PRODUCT_CHOICES,
            selected=list(PRODUCT_CHOICES),
        ),
        ui.input_slider("mac_share", "Students on macOS", min=0, max=100, value=50, post="%"),
        ui.input_select(
            "preset",
            "AI assistant usage",
            choices=presets.PRESET_LABELS,
            selected="half-day-workshop",
        ),
        ui.input_task_button("estimate", "Estimate network impact"),
        width=340,
    ),
    ui.output_ui("scan_status"),
    ui.output_ui("report_view"),
    ui.output_ui("downloads"),
    title="netimpact — workshop network impact",
    fillable=False,
)


def _measure(inputs: dict) -> tuple[dict, str]:
    """Run all measurements for the current inputs.

    Args:
        inputs: Snapshot of the UI inputs.

    Returns:
        A tuple of (report results, scan status message).
    """
    items = installers.measure_installers(inputs["products"], inputs["mac_share"])
    status = ""
    if inputs["repo"]:
        scan = lockfiles.scan_repo(inputs["repo"])
        found = ", ".join(scan.findings) if scan.findings else "no lockfiles found"
        status = f"Scanned {scan.slug}@{scan.branch}: {found}."
        items += lockfiles.measure_scan(scan)
    results = engine.summarize(
        name=inputs["name"] or "Hands-on workshop",
        students=inputs["students"],
        duration_hours=inputs["duration"],
        items=items,
        chats=presets.PRESETS[inputs["preset"]],
    )
    return results, status


def server(input, output, session):
    """Wire the UI to the measurement engine."""
    results_value: reactive.Value[dict] = reactive.value({})
    status_value = reactive.value("")

    @reactive.effect
    @reactive.event(input.estimate)
    def _run_estimate():
        inputs = {
            "name": input.name().strip(),
            "repo": input.repo().strip(),
            "students": int(input.students() or 1),
            "duration": float(input.duration() or 1.0),
            "products": list(input.products()),
            "mac_share": input.mac_share() / 100,
            "preset": input.preset(),
        }
        with ui.Progress(min=0, max=1) as progress:
            progress.set(0.3, message="Measuring live download sizes...")
            try:
                results, status = _measure(inputs)
            except (RuntimeError, ValueError) as error:
                ui.notification_show(str(error), type="error", duration=10)
                return
            results_value.set(results)
            status_value.set(status)

    @render.ui
    def scan_status():
        message = status_value()
        return ui.p(message, class_="text-muted") if message else None

    @render.ui
    def report_view():
        results = results_value()
        if not results:
            return ui.p(
                "Describe the workshop in the sidebar, then press "
                "“Estimate network impact”. Download sizes are measured live, "
                "so the estimate takes a few seconds.",
                class_="text-muted",
            )
        return ui.HTML(report.html_report(results))

    @render.ui
    def downloads():
        req(results_value())
        return ui.div(
            ui.download_button("download_pdf", "Download PDF"),
            ui.download_button("download_typ", "Download Typst source"),
            ui.download_button("download_json", "Download JSON"),
            class_="my-3 d-flex gap-2 justify-content-center",
        )

    @render.download(filename="network-impact-report.pdf")
    def download_pdf():
        req(results_value())
        yield report.render_pdf(report.typst_report(results_value()))

    @render.download(filename="network-impact-report.typ")
    def download_typ():
        req(results_value())
        yield report.typst_report(results_value()).encode()

    @render.download(filename="network-impact-report.json")
    def download_json():
        req(results_value())
        yield json.dumps(results_value(), indent=2).encode()


app = App(app_ui, server)
