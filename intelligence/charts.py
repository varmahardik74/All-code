"""In-memory Matplotlib visualization service for Between Nations.

Generates clean, academic-quality charts directly into BytesIO buffers.
No chart files are saved permanently to the filesystem.
"""

from io import BytesIO
import matplotlib

# Set non-interactive backend for headless web server rendering
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _render_figure_to_png_bytes(fig) -> bytes:
    """Helper to export a Matplotlib figure to in-memory PNG bytes and close the figure."""
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def generate_activity_timeline_chart(
    timeline_df: pd.DataFrame,
    country_a_name: str = "Country A",
    country_b_name: str = "Country B",
) -> bytes:
    """Generate a daily activity timeline chart showing bilateral article volume over time."""
    fig, ax = plt.subplots(figsize=(8, 3.5))

    if timeline_df.empty or "date" not in timeline_df.columns:
        ax.text(0.5, 0.5, "No activity timeline data available", ha="center", va="center")
        ax.axis("off")
        return _render_figure_to_png_bytes(fig)

    df = timeline_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    ax.plot(df["date"], df["volume"], marker="o", markersize=3, color="#1a5276", linewidth=1.8, label="Relevant Articles / Day")
    
    ax.set_title(f"Relevant Bilateral Articles per Day: {country_a_name} × {country_b_name}", fontsize=11, fontweight="bold", pad=10)
    ax.set_xlabel("Date", fontsize=9)
    ax.set_ylabel("Articles / Day", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(loc="upper left", fontsize=8)

    return _render_figure_to_png_bytes(fig)


def generate_narrative_chart(
    narrative_result: dict,
    country_a_name: str = "Country A",
    country_b_name: str = "Country B",
) -> bytes:
    """Generate a horizontal comparison bar chart of earlier vs later distribution."""
    fig, ax = plt.subplots(figsize=(8, 4))

    categories_data = narrative_result.get("categories", [])
    if not categories_data:
        ax.text(0.5, 0.5, "No narrative classification data available", ha="center", va="center")
        ax.axis("off")
        return _render_figure_to_png_bytes(fig)

    categories = [item["category"] for item in categories_data]
    earlier_shares = [item["earlier_share"] * 100 for item in categories_data]
    later_shares = [item["later_share"] * 100 for item in categories_data]

    y = np.arange(len(categories))
    height = 0.35

    ax.barh(y - height / 2, earlier_shares, height, label="Earlier (6d) Share %", color="#7f8c8d", alpha=0.85)
    ax.barh(y + height / 2, later_shares, height, label="Later (6d) Share %", color="#2980b9", alpha=0.9)

    ax.set_yticks(y)
    ax.set_yticklabels(categories, fontsize=9)
    ax.invert_yaxis()  # Top-down order
    ax.set_xlabel("Share of Articles (%)", fontsize=9)
    ax.set_title(
        f"Narrative Shift (Score: {narrative_result.get('shift_score', 0):.1f}/100)",
        fontsize=11,
        fontweight="bold",
        pad=10,
    )
    ax.grid(True, axis="x", linestyle=":", alpha=0.6)
    ax.legend(loc="lower right", fontsize=8)

    return _render_figure_to_png_bytes(fig)


def generate_indicator_comparison_chart(
    country_a_indicators: dict[str, dict],
    country_b_indicators: dict[str, dict],
    country_a_name: str = "Country A",
    country_b_name: str = "Country B",
) -> bytes:
    """Generate a grouped bar chart for selected comparable World Bank indicators."""
    fig, ax = plt.subplots(figsize=(8, 4))

    # Pick 4 universally comparable percentage indicators for a neat, readable chart
    comparable_codes = [
        ("NY.GDP.MKTP.KD.ZG", "GDP Growth (%)"),
        ("FP.CPI.TOTL.ZG", "Inflation (%)"),
        ("SL.UEM.TOTL.ZS", "Unemployment (%)"),
        ("NE.TRD.GNFS.ZS", "Trade (% GDP)"),
    ]

    labels = []
    val_a = []
    val_b = []

    for code, label in comparable_codes:
        obs_a = country_a_indicators.get(code)
        obs_b = country_b_indicators.get(code)
        v_a = obs_a.get("value") if obs_a else None
        v_b = obs_b.get("value") if obs_b else None

        labels.append(label)
        val_a.append(float(v_a) if v_a is not None else np.nan)
        val_b.append(float(v_b) if v_b is not None else np.nan)

    x = np.arange(len(labels))
    width = 0.35

    ax.bar(x - width / 2, val_a, width, label=country_a_name, color="#1b4f72", alpha=0.9)
    ax.bar(x + width / 2, val_b, width, label=country_b_name, color="#d35400", alpha=0.9)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Percentage (%)", fontsize=9)
    ax.set_title(f"National Indicators: {country_a_name} vs {country_b_name}", fontsize=11, fontweight="bold", pad=10)
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)
    ax.legend(loc="upper right", fontsize=8)

    return _render_figure_to_png_bytes(fig)
