# Between Nations

Between Nations is a Django application for comparing two countries from a fixed 20-country universe using recent geopolitical coverage and latest World Bank indicators.

## Problem and objective

International news is difficult to scan alongside national statistics. The objective is to provide a small, explainable comparison tool that summarizes recent bilateral coverage and places it beside comparable country indicators. It is descriptive analysis, not prediction.

## Features

- Two-country selection with duplicate-country validation.
- Recent Developments from the last seven days.
- News Activity counts relevant bilateral articles by calendar day and displays a Matplotlib timeline.
- Narrative Shift using six deterministic coverage categories and a first-3/last-4 comparison.
- Ten World Bank indicators with observation years and `N/A` for unavailable values.
- MySQL persistence, caching, mocked unit tests, and responsive Django templates.

## Final analytical pipeline

GDELT → 7-day geopolitical news → News Activity → Narrative Shift → World Bank country comparison → Matplotlib visualizations

The comparison page contains four areas:

1. **Recent Developments** — recent articles from the last seven days.
2. **News Activity** — daily article counts, average daily activity, busiest day, peak count, and a seven-day Matplotlib chart.
3. **Narrative Shift** — Pandas category distributions comparing the first three days with the last four days.
4. **Country Comparison** — the latest available values for ten World Bank indicators.

News Activity describes media volume only. Narrative Shift describes changes in media coverage emphasis and does not claim geopolitical causality.

## Architecture and database design

The request flow is: Django form → comparison view → analysis service → one GDELT ArticleList request for the seven-day article dataset → World Bank services → MySQL models → Django template and Python-generated charts.

The database contains `Country`, `Article`, `CountryIndicator`, and `AnalysisRun`. Articles are indexed by bilateral pair and publication time; country indicators preserve their source year. Existing nullable analysis fields are retained for schema compatibility and are not required by the final pipeline.

## Data methodology

GDELT is queried once for bilateral articles using a seven-day timespan and an ArticleList request. Stored relevant articles are filtered to the current seven-day window, grouped by publication calendar day, and reused for Recent Developments, News Activity, and Narrative Shift. News Activity describes relevant bilateral articles retrieved per day, not global media volume. Narrative Shift classifies titles into Diplomacy, Security / Military, Trade / Economy, Border / Conflict, Sanctions / Restrictions, or Technology / Strategy. World Bank retrieval selects the latest non-empty observation for each configured indicator and caches it in the database.

## Technology

- Django 5.2
- MySQL 8.0
- NumPy and Pandas for numerical and tabular analysis
- Matplotlib for in-memory charts
- GDELT DOC 2.0 for seven-day article retrieval
- World Bank Indicators API for country data

## World Bank indicators

GDP, GDP Growth Rate, GDP per Capita, Population, Population Growth Rate, Inflation Rate, Unemployment Rate, Trade (% of GDP), Military Expenditure, and Exports of Goods and Services.

## Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_countries
python manage.py check
python manage.py test
python manage.py runserver
```

Open `http://127.0.0.1:8000/` and select two different countries.

## Environment and MySQL

Copy `.env.example` to `.env` and configure `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`. Create the database before migrating:

```sql
CREATE DATABASE between_nations CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

## Testing and manual analysis

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py analyze_pair IND CHN
```

External APIs are mocked in unit tests. Manual runs use the configured GDELT and World Bank endpoints and fall back to stored data when those services are unavailable.

## Limitations and future scope

Coverage depends on GDELT availability and source reporting. Keyword classification can misclassify ambiguous headlines, and World Bank observations are not always from the same year. Future work could improve classification and broaden the country universe, but those are outside the current scope.

External requests are cached or handled with clean empty/error states when unavailable. The project does not forecast conflict or geopolitical outcomes.
