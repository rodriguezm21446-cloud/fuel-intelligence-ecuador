from __future__ import annotations

"""Interactive companion charts for notebooks and analyst workspaces.

Telegram still receives deterministic PNG files from ``chart_renderer``.  bqplot
is used here for inspection and exploration; it is not treated as a data source.
"""

from pathlib import Path


def build_bqplot_figure(payload: dict):
    """Build a bqplot figure from the same validated payload used for PNGs."""
    import bqplot as bq

    labels = list(payload.get("labels", []))
    kind = payload.get("chartKind", "line")
    colors = [item.get("color", "#8A8A8A") for item in payload.get("datasets", [])]
    x_scale = bq.OrdinalScale()
    y_scale = bq.LinearScale()
    marks = []
    if kind in {"bar", "stacked-bar"}:
        values = [item.get("values", []) for item in payload.get("datasets", [])]
        marks.append(
            bq.Bars(
                x=labels,
                y=values,
                scales={"x": x_scale, "y": y_scale},
                colors=colors,
                type="stacked" if kind == "stacked-bar" else "grouped",
                labels=[item.get("label", "") for item in payload.get("datasets", [])],
                display_legend=True,
            )
        )
    else:
        for item in payload.get("datasets", []):
            marks.append(
                bq.Lines(
                    x=labels,
                    y=item.get("values", []),
                    scales={"x": x_scale, "y": y_scale},
                    colors=[item.get("color", "#8A8A8A")],
                    labels=[item.get("label", "")],
                    display_legend=True,
                    interpolation="step-after" if kind == "step" else "linear",
                )
            )
    return bq.Figure(
        title=payload.get("title", ""),
        subtitle=payload.get("subtitle", ""),
        marks=marks,
        axes=[
            bq.Axis(scale=x_scale, label="Fecha", tick_rotate=-35),
            bq.Axis(scale=y_scale, orientation="vertical", side="right"),
        ],
        background_style={"fill": "#0B0C0E"},
        title_style={"fill": "#E6E3DC"},
        legend_location="top-left",
    )


def export_bqplot_html(payload: dict, destination: Path) -> Path:
    """Write a self-contained interactive HTML file for a trusted analyst."""
    from ipywidgets.embed import embed_minimal_html

    destination.parent.mkdir(parents=True, exist_ok=True)
    embed_minimal_html(str(destination), views=[build_bqplot_figure(payload)], title=payload.get("title", "Gráfico"))
    return destination
