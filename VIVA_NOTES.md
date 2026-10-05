# Between Nations — Viva Notes

## What is Between Nations?

It is a Django application that compares two countries using seven days of geopolitical news coverage and the latest World Bank indicators.

## Why this problem?

Country relationships generate large amounts of information. This project presents recent coverage and national context in a compact, explainable form.

## Why Django and MySQL?

Django provides forms, views, templates, validation, and database integration in Python. MySQL stores countries, articles, indicators, and saved analysis runs reliably.

## Why Pandas, NumPy, and Matplotlib?

Pandas organizes article and category data. NumPy calculates simple daily averages and peak values. Matplotlib creates the activity, narrative, and indicator charts in Python.

## Why GDELT and World Bank?

GDELT provides current international news coverage. World Bank provides official country-level economic and national indicators.

## Why only 20 countries?

A fixed universe keeps the academic project focused, validation simple, and comparisons consistent.

## Why only seven days?

Seven days keeps the geopolitical view recent and understandable. It measures current media coverage, not a long-term historical baseline.

## What is News Activity?

It is the number of bilateral articles per day during the last seven days, including the average, busiest day, and peak article count.

## What is Narrative Shift?

It measures how the categories emphasized by media coverage differ between the first three and last four days.

## How is Narrative Shift calculated?

Articles are classified into six categories. Pandas calculates each period's category shares, and the score uses total variation distance: half the sum of absolute share differences, expressed from 0 to 100.

## Why first three days versus last four days?

The split gives a short earlier period and a slightly larger later period while covering the full seven-day window.

## What is the database structure?

Country stores the fixed universe. Article stores bilateral news. CountryIndicator stores World Bank observations and years. AnalysisRun stores the structured result of a comparison.

## How are API failures handled?

Requests use timeouts and typed errors. Stored articles and indicators are reused when available; otherwise the application shows empty or unavailable states rather than tracebacks.

## Limitations

GDELT is a media source, article classification is rule-based, World Bank indicators may have different reporting years, and the fixed country list is not exhaustive.

## Why is this not an AI prediction system?

It uses deterministic keyword classification and descriptive statistics. It does not predict war, conflict, or geopolitical outcomes.
