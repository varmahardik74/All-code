# Between Nations

Between Nations is a small Django application for comparing two countries through recent bilateral news coverage and the latest available national indicators. It is designed as an explainable university semester project: the application describes recent media coverage and country context; it does not predict geopolitical events.

## What the application does

The user selects two countries from the fixed active universe and receives four sections:

1. **Recent Developments** - the most recent relevant bilateral articles retrieved for the selected pair.
2. **News Activity** - daily counts, average articles per day, busiest day, and peak article count for relevant articles in the seven-day window.
3. **Narrative Shift** - a deterministic comparison of article categories in the first three days versus the last four days.
4. **Country Comparison** - the latest available World Bank observations for ten national indicators.

The news sections use one article dataset per analysis. In live mode, that dataset comes from one GDELT DOC 2.0 ArticleList request per uncached pair. In seeded/demo mode, it comes from verified local historical seed articles stored in MySQL. The same dataset powers Recent Developments, News Activity, and Narrative Shift. There is no TimelineVol request and no 30-day or 37-day geopolitical baseline.

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
- GDELT DOC 2.0 ArticleList API
- World Bank Indicators API
- Pandas for date grouping and narrative distributions
- NumPy for News Activity summary calculations
- Matplotlib for in-memory PNG charts

## Data sources and interpretation

### GDELT news

GDELT is queried in live mode with a seven-day rolling `timespan=7d`, `mode=ArtList`, `maxrecords=250`, and newest-first sorting. Country aliases and a small set of geopolitical relationship terms keep the query focused. Returned titles are checked again by a deterministic bilateral relevance filter before storage.

The dataset is recent media coverage, not an exhaustive census of every article. GDELT coverage, publisher indexing, query ranking, and API availability affect the result. The application does not claim that missing coverage means that no geopolitical activity occurred.

### Seeded historical news

The file `between_nations_seed_articles_v1.csv` contains verified sourced historical articles for the local demo window around 2026-09-28 through 2026-10-06. It intentionally covers only pairs where verified articles were found; the project does not create placeholder rows for uncovered pairs.

Import it with:

```powershell
.\.venv\Scripts\python.exe manage.py seed_articles
```

The importer validates active country codes, unordered pair membership, non-empty URL/title fields, publication dates, tone, and category values before inserting. It stores articles through the existing `Article` model and is idempotent by URL within the unordered country pair. Some CSV `pair_key` values are reversed relative to strict alphabetical order; the importer accepts them only when they contain the same unordered country pair, then stores the relationship using the project's canonical ISO ordering.

### News Activity

News Activity groups the publication timestamps of relevant stored articles by calendar day within the seven-day window. It reports:

- daily article count
- average articles per day
- busiest day
- peak article count

It describes relevant bilateral articles retrieved by this application. It is not a global media-volume measurement and is not a conflict or event predictor.

### Narrative Shift

Articles are classified deterministically into:

- Diplomacy
- Security / Military
- Trade / Economy
- Border / Conflict
- Sanctions / Restrictions
- Technology / Strategy

The first three days are compared with the last four days using Pandas category distributions and total variation distance. The result reports a score, dominant later category, largest positive shift, and a media-coverage interpretation. It does not establish geopolitical causality. If there are too few valid articles, the result is marked insufficient rather than shown as a fabricated zero.

### World Bank indicators

The country comparison uses the latest available non-empty observation for these ten centralized indicators:

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

World Bank values are country-level observations and are separate from the seven-day GDELT analysis. They are not necessarily real-time or from the same reporting year. Missing values remain `N/A`; they are not converted to zero.

## Architecture

The live comparison flow is:

`Django form -> comparison view -> analysis service -> one GDELT ArticleList dataset -> Article storage -> local activity/narrative calculations -> World Bank retrieval -> AnalysisRun -> template and charts`

The seeded/demo comparison flow is:

`Django form -> comparison view -> analysis service -> seeded Article rows from MySQL -> local activity/narrative calculations -> World Bank retrieval -> AnalysisRun -> template and charts`

Important files:

- `config/settings.py` - Django and MySQL configuration.
- `intelligence/models.py` - countries, articles, indicators, and analysis runs.
- `intelligence/forms.py` - active-country selection and duplicate validation.
- `intelligence/management/commands/seed_countries.py` - final ten-country seed and legacy deactivation.
- `intelligence/management/commands/seed_articles.py` - idempotent CSV importer for verified historical demo articles.
- `intelligence/services/gdelt.py` - ArticleList query, parsing, aliases, filtering, rate-limit errors, and article storage.
- `intelligence/services/analysis.py` - seven-day orchestration, cache selection, activity, and narrative calculations.
- `intelligence/services/pairs.py` - deterministic unordered pair-key and pair-query helpers.
- `intelligence/services/scoring.py` - NumPy News Activity summaries.
- `intelligence/services/narrative.py` - deterministic classification and shift calculation.
- `intelligence/services/worldbank.py` - centralized indicators and latest-value caching.
- `intelligence/services/charts.py` - Matplotlib chart generation.
- `intelligence/views.py` and `intelligence/templates/intelligence/` - existing page flow and presentation.
- `intelligence/tests.py` - mocked service, API-state, cache, country-universe, and rendering tests.

The main database models are:

- `Country` - country name, ISO code, and active-selection flag.
- `Article` - bilateral article title, URL, source domain, publication time, tone, category, and fetch time.
- `CountryIndicator` - World Bank observation, source year, and fetch time.
- `AnalysisRun` - selected pair and structured JSON result. Existing nullable analysis score fields remain for schema compatibility.

## GDELT request, cache, and failure behavior

For an uncached pair, the analysis makes one primary ArticleList request. Recent Developments, News Activity, and Narrative Shift reuse the resulting stored article rows; no separate request is made for any metric.

Relevant bilateral article cache entries are fresh for 15 minutes and must also have publication timestamps inside the current seven-day window. Fresh cache data is used without a live GDELT request. A stale cache triggers one controlled live ArticleList request. If that request fails, valid recent cached articles may be used as a fallback. If no valid cache exists, the result is unavailable.

The result distinguishes three states:

- `success_with_data` - GDELT or valid cache supplied relevant articles.
- `success_no_data` - GDELT responded successfully but no relevant articles were found; the UI says “No recent bilateral coverage found.”
- `unavailable` - timeout, connection failure, rate limit, malformed response, or another API failure; activity and narrative metrics are not fabricated.

The default live request does not immediately retry HTTP 429 responses. The lower-level request helper supports an explicitly bounded retry for mocked/service-level tests and honors numeric or HTTP-date `Retry-After` values with a maximum delay. This keeps normal country-pair analysis conservative and avoids request bursts. No Celery, Redis, worker, or external queue is used.

## News data modes

Set `NEWS_DATA_MODE` in `.env` or the shell:

- `NEWS_DATA_MODE=seeded` uses only locally stored seed CSV article URLs for the news portion and does not call GDELT. This is the safest local demo/offline mode.
- `NEWS_DATA_MODE=live` preserves the current GDELT cache, request, timeout, unavailable-state, and relevance-filtering behavior.

World Bank indicator retrieval remains unchanged in both modes.

## Setup

Create and activate a virtual environment, install dependencies, configure `.env`, migrate, and seed the active country universe:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_countries
python manage.py seed_articles
python manage.py check
```

Create the MySQL database before migrating:

```sql
CREATE DATABASE between_nations CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Configure `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and optionally `NEWS_DATA_MODE` in `.env`.

Start seeded/demo mode with:

```powershell
$env:NEWS_DATA_MODE="seeded"; .\.venv\Scripts\python.exe manage.py runserver
```

Start live mode with:

```powershell
$env:NEWS_DATA_MODE="live"; .\.venv\Scripts\python.exe manage.py runserver
```

Then open `http://127.0.0.1:8000/`.

## Testing and manual analysis

External APIs are mocked in unit tests. Run:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

For a manual pair analysis:

```powershell
$env:NEWS_DATA_MODE="seeded"; .\.venv\Scripts\python.exe manage.py analyze_pair IND CHN
```

Seeded manual runs use MySQL seed rows for articles and still use the existing World Bank logic for country indicators. Live manual runs use configured external endpoints, persist usable article and indicator data in MySQL, reuse fresh cache rows, and show explicit no-data or unavailable states when appropriate. Avoid repeatedly analyzing many uncached pairs in live mode because GDELT applies rate limits.

## Known limitations and future ideas

The fixed country universe is intentionally small. GDELT results depend on recent indexing and publisher coverage, and deterministic title keywords can misclassify ambiguous headlines or miss coverage whose titles omit country aliases. World Bank indicators can have different source years and are not real-time. External API outages and rate limits can make a pair temporarily unavailable.

Future ideas, not implemented features, include a larger carefully maintained country universe, richer article entity extraction, and more comprehensive source-quality diagnostics. The current project intentionally remains a local Django semester application without prediction, background workers, or additional product sections.
