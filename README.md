# Between Nations

Between Nations is a small, fully local Django application for comparing two countries through curated historical bilateral news coverage and locally stored national indicators. It is designed as an explainable university semester project: the application analyzes a fixed 12-day historical snapshot and describes media coverage and country context; it does not predict geopolitical events.

The application never calls an external API. Curated articles and country indicators are loaded from CSV files at the project root into MySQL, and every page reads only from the local database.

## What the application does

The user selects two countries from the fixed active universe and receives four sections:

1. **Recent Developments** - the curated bilateral articles for the selected pair within the historical snapshot.
2. **News Activity** - daily counts, average articles per day, busiest day, and peak article count across the 12 calendar days of the snapshot.
3. **Narrative Shift** - a deterministic comparison of article categories in the first six days versus the last six days of the snapshot.
4. **Country Comparison** - the locally exported World Bank observation values for ten national indicators.

One article dataset drives Recent Developments, News Activity, and Narrative Shift. The dataset is the curated historical snapshot, not live coverage.

## Historical snapshot window

The dashboard analyzes one fixed snapshot:

- Entire snapshot: **28 September 2026 through 9 October 2026 (12 days, inclusive)**.
- Earlier period: 28 September - 3 October (first 6 days).
- Later period: 4 October - 9 October (last 6 days).

Because the window is fixed, News Activity always covers the same 12 calendar days and its average uses a denominator of 12 (zero-volume days stay in the average). The UI shows the label "Historical snapshot: 28 September–9 October 2026 (12 days)" wherever the old rolling seven-day window was described. The dashboard is a historical snapshot, never live or rolling data.

## Supported countries

The active selectable universe contains exactly these ten countries:

| Country | ISO code |
| --- | --- |
| China | CHN |
| India | IND |
| Iran | IRN |
| Russia | RUS |
| Germany | DEU |
| Japan | JPN |
| United Kingdom | GBR |
| United States | USA |
| Pakistan | PAK |
| Israel | ISR |

The seed command deactivates legacy country rows instead of deleting them, so historical article and analysis records are preserved while removed countries no longer appear in the form.

## Technology stack

- Python and Django
- MySQL with Django ORM
- Pandas for date grouping and narrative distributions
- NumPy for News Activity summary calculations
- Matplotlib for in-memory PNG charts

There are no external API dependencies (`requests` is not used anywhere in the project).

## Data sources and interpretation

### Curated historical dataset

The file `seed_articles.csv` contains 93 verified, sourced historical articles referenced by URL. It covers the fixed snapshot window (28 September through 9 October 2026) and forms the canonical URL allowlist for the analysis: only stored articles whose URL is in this file and whose pair is the selected one are ever analyzed. Nothing else in the `Article` table is used.

The dataset intentionally covers only pairs where verified articles were found (41 of the 45 possible unordered pairs). The project does not create placeholder rows for uncovered pairs. Full article URLs are kept in the dataset and displayed on the dashboard.

Import it with:

```powershell
.\.venv\Scripts\python.exe manage.py seed_data
```

### Locally exported indicators

The file `country_indicators.csv` contains the country indicators exported from the local database (100 rows: 10 indicators per active country). Importing it with `seed_data` uses `update_or_create` and never fabricates values: genuinely missing observations stay missing and are rendered as `N/A`, never as zero.

### News Activity

News Activity groups the publication timestamps of the curated articles by calendar day across the 12-day snapshot. It reports:

- daily article count (all 12 days, including zero days)
- average articles per day (total divided by 12)
- busiest day
- peak article count

It describes curated bilateral coverage in this dataset. It is not a global media-volume measurement and is not a conflict or event predictor.

### Narrative Shift

Articles are classified deterministically into:

- Diplomacy
- Security / Military
- Trade / Economy
- Border / Conflict
- Sanctions / Restrictions
- Technology / Strategy

The first six days (28 September - 3 October) are compared with the last six days (4 October - 9 October) using Pandas category distributions and total variation distance. The result reports a score, dominant later category, largest positive shift, and a media-coverage interpretation. It does not establish geopolitical causality. If there are too few valid articles, the result is marked insufficient rather than shown as a fabricated zero.

### Country indicators

The country comparison shows the locally stored observations for these ten canonical indicators:

1. GDP
2. GDP Growth Rate
3. GDP per Capita
4. Population
5. Population Growth Rate
6. Inflation Rate
7. Unemployment Rate
8. Trade (% of GDP)
9. Military Expenditure
10. Exports of Goods and Services

Indicator values are country-level observations, separate from the media snapshot. They are not necessarily real-time or from the same reporting year. Missing values remain `N/A`; they are not converted to zero.

## Architecture

The comparison flow is entirely local:

`Django form -> comparison view -> analysis (local Article rows via seed URL allowlist within the 12-day window) -> local activity/narrative calculations -> local CountryIndicator rows -> AnalysisRun -> template and charts`

Important files:

- `config/settings.py` - Django and MySQL configuration.
- `intelligence/models.py` - countries, articles, indicators, and analysis runs.
- `intelligence/forms.py` - active-country selection and duplicate validation.
- `intelligence/management/commands/seed_data.py` - one idempotent command that seeds the 10 country universe, imports `seed_articles.csv`, and imports `country_indicators.csv`. Running it twice creates no duplicates and deletes nothing.
- `intelligence/management/commands/analyze_pair.py` - manual pair analysis from the local snapshot.
- `intelligence/analysis.py` - the complete local analysis pipeline in one file: article classification, NumPy News Activity summaries with Pandas date grouping, the earlier/later 6-6 day split, Narrative Shift via total variation distance, and the snapshot orchestration function.
- `intelligence/pairs.py` - deterministic unordered pair-key and pair-query helpers.
- `intelligence/seeds.py` - single source of truth for the fixed snapshot window, the article-URL allowlist, the canonical indicator codes, and both CSV loaders.
- `intelligence/charts.py` - Matplotlib chart generation.
- `intelligence/views.py` and `intelligence/templates/intelligence/` - existing page flow and presentation.
- `intelligence/tests.py` - local data-driven tests, snapshot-behavior tests, and rendering tests.

The main database models are:

- `Country` - country name, ISO code, and active-selection flag.
- `Article` - bilateral article title, URL, source domain, publication time, tone, category, and fetch time.
- `CountryIndicator` - locally stored indicator observation and source year.
- `AnalysisRun` - selected pair and structured JSON result. Existing nullable analysis score fields remain for schema compatibility.

## Local-only operation

Because there is no live mode and no API cache, every comparison reads the curated snapshot from MySQL. Article coverage depends on the curated dataset: stored rows whose URLs are not in `seed_articles.csv` are ignored so older experimental rows cannot contaminate results. If a pair is not covered by the snapshot, the analysis returns an explicit honest no-data state:

- `success_with_data` - curated articles exist for the pair.
- `success_no_data` - the snapshot has no curated articles for the pair; the UI shows "No curated bilateral coverage for this pair in the snapshot."

No external request is made at any point, so the dashboard works with the network unavailable as long as MySQL is running.

## Setup

Create and activate a virtual environment, install dependencies, configure `.env`, migrate, and seed the local dataset:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_data
python manage.py check
```

Create the MySQL database before migrating:

```sql
CREATE DATABASE between_nations CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Configure `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` in `.env`. No news-data mode or API key is needed.

Start the server with:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Then open `http://127.0.0.1:8000/`.

## Testing and manual analysis

No external APIs are called, so tests are fully local and data-driven from the same CSVs the dashboard uses. Run:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

For a manual pair analysis:

```powershell
.\.venv\Scripts\python.exe manage.py analyze_pair IND CHN
```

Manual runs read the curated snapshot and local indicator rows from MySQL. Pairs with no verified articles (for example CHN-ISR) are reported honestly as no-data rather than filled in.

## Known limitations and future ideas

The fixed country universe is intentionally small. The curated dataset covers 41 of the 45 possible unordered pairs; the 4 uncovered pairs are reported as no-data. Deterministic title keywords can misclassify ambiguous headlines or miss coverage whose titles omit country aliases (the curated dataset predates this via verified categories). Indicator values can have different source years and are not real-time.

Future ideas, not implemented features, include a larger carefully maintained country universe, richer article entity extraction, and more comprehensive source-quality diagnostics. The current project intentionally remains a local Django semester application without prediction, background workers, external APIs, or additional product sections.