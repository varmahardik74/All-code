"""Seven-day news activity calculations."""

from typing import Sequence
import numpy as np


def calculate_news_activity(daily_volumes: Sequence[float]) -> dict:
    """Summarize daily article counts within the seven-day window."""
    values = np.asarray(list(daily_volumes), dtype=float)
    if values.size == 0:
        values = np.zeros(7, dtype=float)
    peak_index = int(np.argmax(values))
    return {
        "daily_article_count": values.tolist(),
        "average_articles_per_day": round(float(np.mean(values)), 2),
        "peak_day": peak_index + 1,
        "peak_article_count": float(values[peak_index]),
    }
