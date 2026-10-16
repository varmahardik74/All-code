# Between Nations — Viva Notes

## What is Between Nations?

It is a fully local Django application that compares two countries using a curated 19-day historical snapshot of geopolitical news coverage and locally stored World Bank indicator values.

## Why this problem?

Country relationships generate large amounts of information. This project presents curated historical coverage and national context in a compact, explainable form.

## Why Django and MySQL?

Django provides forms, views, templates, validation, and database integration in Python. MySQL stores countries, articles, indicators, and saved analysis runs reliably.

## Why Pandas, NumPy, and Matplotlib?

Pandas organizes article and category data. NumPy calculates simple daily averages and peak values. Matplotlib creates the activity, narrative, and indicator charts in Python.

## Why is the application fully local?

The dashboard analyzes a fixed curated dataset rather than live feeds. Curated articles come from `seed_articles.csv` (109 sourced articles, 41 covered pairs) and country indicators from `country_indicators.csv` (10 indicators per active country), both loaded into MySQL by one `seed_data` command. No external API is ever called, so results are reproducible and the dashboard works offline as long as MySQL runs. `requests` is not a dependency.

## Why only 10 countries?

A fixed universe keeps the academic project focused, validation simple, and comparisons consistent. Indicators in `country_indicators.csv` cover all 10 active countries 10/10.

## Why a fixed 19-day snapshot instead of seven days?

The dashboard analyzes one fixed historical window: 28 September through 16 October 2026 (19 days inclusive). Fixing the window makes every analysis reproducible and lets News Activity report all 19 calendar days with a consistent denominator of 19. It measures curated historical coverage, not rolling live data.

## What is News Activity?

It is the number of bilateral articles per day across the 19 snapshot days, including the average (total / 19, zero days included), busiest day, and peak article count.

## What is Narrative Shift?

It measures how the categories emphasized by media coverage differ between the first nine days (28 September - 6 October) and the last ten days (7 October - 16 October) of the snapshot.

## How is Narrative Shift calculated?

Articles are classified into six categories. Pandas calculates each period's category shares, and the score uses total variation distance: half the sum of absolute share differences, expressed from 0 to 100. If either period has no valid articles, Narrative Shift is unavailable instead of assigning an artificial distribution.

## Why a nine-day/ten-day split?

The snapshot has 19 calendar days, so the earlier and later periods are a nine-day first period and a ten-day second period. The UI labels them "FIRST 6 DAYS" and "LAST 6 DAYS".

## What is the database structure?

Country stores the fixed universe. Article stores bilateral news. CountryIndicator stores locally imported indicator observations and years. AnalysisRun stores the structured result of a comparison.

## How does the analysis stay honest about coverage?

Only stored articles whose URLs appear in `seed_articles.csv` (the canonical allowlist) and whose pair matches are analyzed. Pairs with no curated articles (CHN-ISR, IND-IRN, ISR-JPN, ISR-RUS) are reported as honest no-data states, never filled with placeholder rows. An active country/indicator code absent from the authoritative CSV or containing only blank values has its stale observations pruned. All years for still-covered codes and records for inactive countries are preserved. Missing indicator values render as `N/A`, never as zero.

## How are failures handled?

There are no external requests to fail. Database errors are caught and shown as a clean "analysis unavailable" state rather than a traceback.

## Limitations

The curated snapshot covers 41 of the 45 possible unordered pairs, article classification is rule-based, indicator values may have different reporting years, and the fixed country list is not exhaustive.

## Why is this not an AI prediction system?

It uses deterministic keyword classification and descriptive statistics over a fixed historical snapshot. It does not predict war, conflict, or geopolitical outcomes.