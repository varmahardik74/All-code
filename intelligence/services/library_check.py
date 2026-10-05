"""Tiny real operations that prove NumPy, Pandas, and Matplotlib are usable."""

from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def check_numpy():
    values = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    return {
        "values": values.tolist(),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
    }


def check_pandas():
    frame = pd.DataFrame(
        {
            "name": ["United States", "China", "India"],
            "iso_code": ["USA", "CHN", "IND"],
            "score": [3.2, 4.1, 2.7],
        }
    )
    selected = frame[["name", "score"]]
    sorted_frame = selected.sort_values("score", ascending=False).reset_index(drop=True)
    return {
        "columns": list(sorted_frame.columns),
        "sorted_names": sorted_frame["name"].tolist(),
        "mean_score": float(sorted_frame["score"].mean()),
        "row_count": int(len(sorted_frame)),
    }


def check_matplotlib():
    buffer = BytesIO()
    figure, axes = plt.subplots()
    axes.bar(["A", "B", "C"], [1.5, 3.0, 2.0])
    axes.set_title("Library check")
    figure.savefig(buffer, format="png")
    plt.close(figure)
    buffer.seek(0)
    png_bytes = buffer.getvalue()
    return {
        "format": "png",
        "size_bytes": len(png_bytes),
        "buffer": buffer,
    }
