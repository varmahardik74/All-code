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

The snapshot has 19 calendar days, so the earlier and later periods are a nine-day first period and a ten-day second period. The UI labels them "FIRST 9 DAYS" and "LAST 10 DAYS".

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

---

# Technical implementation

## Technical workflow: how a request travels through the codebase

1. **Boot** — `python manage.py runserver` imports `config/settings.py`, which loads `.env` (python-dotenv) for the secret key, debug flag, and MySQL credentials, and registers the single local app `intelligence` in `INSTALLED_APPS`.
2. **URL routing** — every request hits `config/urls.py` (`ROOT_URLCONF`): `/admin/` goes to Django admin, everything else is delegated by `include("intelligence.urls")` to the two project routes: `""` → `views.home` and `"compare/<str:iso_a>/<str:iso_b>/"` → `views.comparison`.
3. **Home page** (`intelligence/views.py:20`) — GET renders `home.html` with a `ComparisonForm` whose two `ModelChoiceField`s run a SQL `SELECT` over active countries. POST runs Django form validation (including `clean()`, which rejects selecting the same country twice) and then `redirect()`s to the comparison URL — the PRG pattern, so refresh never re-submits.
4. **Comparison page** (`intelligence/views.py:32`) — `get_object_or_404(Country, iso_code=..., is_active=True)` issues a parameterised `SELECT ... WHERE iso_code = %s` for both countries (unknown ISO → clean 404; same country twice → 400 with form error).
5. **Retrieval + analysis** — `run_country_pair_analysis()` (`intelligence/analysis.py`) is the orchestrator: fetch curated articles from MySQL, split them into the earlier nine-day and later ten-day periods, compute News Activity and Narrative Shift with Pandas, read both countries' indicators from MySQL, assemble one result dictionary, and persist it as a new `AnalysisRun` row (`result_json` JSONField).
6. **Charts** — the view passes the result into `intelligence/charts.py`, which renders three Matplotlib figures (activity timeline, narrative comparison, indicator comparison) in memory with the non-interactive `Agg` backend into `BytesIO` PNG buffers. No image file is ever written to disk.
7. **Template render** — `render(request, "comparison.html", context)` runs Django's template engine: `{% if %}`/`{% for %}` tags decide which sections appear (e.g. Narrative Shift block only when `result.narrative_shift` exists), `{{ }}` variables inject values, and charts are embedded as base64 data-URI `<img>` tags.
8. **Response** — the browser receives plain HTML + one static CSS file (+ one small JS file on the home page). No client-side framework, no AJAX, no API endpoints.
9. **Failure path** — the whole comparison body is wrapped in `try/except`: any database or rendering error is logged with `logger.exception()` and the user sees a clean "analysis unavailable" section instead of a traceback.
10. **Data path (offline, one-time)** — `python manage.py migrate` creates the schema; `python manage.py seed_data` reads the two root CSVs (`seed_articles.csv`, `country_indicators.csv`) with the Python `csv` module, validates every row, and writes it into MySQL through the ORM. The web app itself never reads a CSV except the article URL allowlist.

## Important files and what the code does

| File | Role |
|---|---|
| `manage.py` | Django command-line entry point (`migrate`, `test`, `seed_data`, `runserver`, `analyze_pair`). |
| `config/settings.py` | All configuration: installed apps, middleware, template engine, static files, and the MySQL `DATABASES` block read from `.env`. |
| `config/urls.py` | Root URL configuration: admin + delegation to the app. |
| `config/__init__.py` | `pymysql.install_as_MySQLdb()` — registers PyMySQL as the MySQL driver for Django's mysql backend. |
| `intelligence/urls.py` | The two application routes: home and country comparison. |
| `intelligence/models.py` | The four database tables (Django ORM models): `Country`, `Article`, `CountryIndicator`, `AnalysisRun`. |
| `intelligence/migrations/0001_initial.py` | The generated migration that turns the models into SQL table/index/constraint definitions. |
| `intelligence/views.py` | Request handling: form flow, DB fetch, analysis orchestration call, chart generation + base64 encoding, indicator formatting, error state. |
| `intelligence/forms.py` | `ComparisonForm` — dropdown population from the DB and validation (two different active countries). |
| `intelligence/analysis.py` | The analysis engine: keyword classifier, News Activity, Narrative Shift (TVD), snapshot windowing, indicator reads, result assembly, `AnalysisRun` persistence. |
| `intelligence/seeds.py` | Fixed snapshot constants (dates, 19-day window, period split), canonical indicator list, CSV path helpers and loaders. |
| `intelligence/pairs.py` | Unordered-pair helpers, including `unordered_pair_q()` — the `Q` object that makes A–B and B–A identical. |
| `intelligence/charts.py` | Matplotlib (`Agg` backend) chart rendering to in-memory PNG bytes: activity timeline, narrative grouped bars, indicator comparison. |
| `intelligence/templates/intelligence/home.html` | Landing page template: CSRF-protected country selector form. |
| `intelligence/templates/intelligence/comparison.html` | Dashboard template: articles, metrics, charts, indicator table, honest empty/error states. |
| `intelligence/static/intelligence/…` | `css/styles.css` (full styling) and `js/main.js` (small home-page enhancement). |
| `intelligence/management/commands/seed_data.py` | Idempotent importer: 10-country universe, validated article rows, authoritative indicator snapshot with stale-row pruning. |
| `intelligence/management/commands/analyze_pair.py` | CLI reporter — runs the same analysis engine and prints it to the terminal. |
| `intelligence/admin.py` | Read-mostly Django admin registrations for all four models. |
| `intelligence/tests.py` | 39 automated tests: models, seed command, forms, scoring services, charts, snapshot honesty, local-only architecture. |
| `seed_articles.csv` | Canonical curated dataset: 109 sourced articles / 41 covered pairs; also the URL allowlist used at analysis time. |
| `country_indicators.csv` | Authoritative indicator snapshot: 10 indicators × 10 active countries = 100 rows exported from MySQL. |
| `.env` | Local secrets only (secret key, DB name/user/password/host/port); git-ignored, never committed. |

## Technical implementation of the important files

- **`config/settings.py`** — `DATABASES["default"]` uses `ENGINE: django.db.backends.mysql` with `NAME`/`USER`/`PASSWORD`/`HOST`/`PORT` taken from `.env` (`DB_NAME=between_nations`, `DB_HOST=127.0.0.1`, `DB_PORT=3306`) and `charset: utf8mb4`. `INSTALLED_APPS` adds the one local app, `intelligence`. `TIME_ZONE = "UTC"`, `USE_TZ = True` so every `published_at` is stored as UTC. `APP_DIRS: True` tells the template engine to look inside `intelligence/templates/`.
- **`intelligence/models.py`** — the entire schema:
  - `Country` — unique `name` and `iso_code`, `is_active` flag (the fixed 10-country universe).
  - `Article` — two `ForeignKey`s to `Country` (`country_a`/`country_b`), title/url/source/publish time, optional `tone` and `category`; DB indexes on `url`, `published_at`, and the composite `(country_a, country_b)` pair.
  - `CountryIndicator` — `FloatField` `value` + `PositiveIntegerField` `year` with a `UniqueConstraint` on `(country, indicator_code, year)`, so re-importing updates instead of duplicating.
  - `AnalysisRun` — stores every produced analysis in a `JSONField result_json` plus indexable `activity_score`/`narrative_drift_score` columns.
- **`intelligence/analysis.py`** — the engine. Fixed window constants (`SNAPSHOT_START/END`, half-open `WINDOW_END_DT`, `EARLIER_END_DT`, `LATER_START_DT`). `classify_article()` maps titles to six categories with pre-compiled regex keyword patterns (`_COMPILED_PATTERNS`). `_curated_pair_articles()` fetches rows filtered by the unordered-pair `Q`, the seed-CSV URL allowlist, and the snapshot datetime bounds. `_window_periods()` splits articles into the earlier nine-day and later ten-day periods. `calculate_news_activity()` averages over the full 19-day denominator (zero days included). `calculate_narrative_shift()` builds Pandas `Series`, computes per-category shares and total variation distance, and returns `None` whenever either period is empty. `_country_indicators()` reads the latest year per canonical code. `run_country_pair_analysis()` composes everything into one plain dict and writes an `AnalysisRun` row — so the same structure is used by the web view, the CLI command, and the tests.
- **`intelligence/views.py`** — `home()` handles the form/PRG cycle; `comparison()` does `get_object_or_404`, calls the engine, builds a 19-day Pandas timeline for the chart, encodes each PNG with `base64.b64encode` (`_chart_data()`), formats indicator display strings (`_format_indicator()`: trillions/billions/millions, `None` → `N/A`), and assembles the context dict. Chart keys are `None` when their data is absent (no activity chart for no-data pairs, no narrative chart when Narrative Shift is insufficient), which the template mirrors with `{% if %}`.
- **`intelligence/management/commands/seed_data.py`** — one idempotent command, three phases: `_seed_countries()` (`update_or_create` per ISO, deactivates anything outside the 10), `_seed_articles()` (row-by-row validation: active ISO codes, matching pair key, valid tone/category, publication date inside the snapshot window; duplicates by URL+pair are skipped; invalid rows are reported and reject the command), `_seed_indicators()` (CSV rows with a value are `update_or_create`d; then `_prune_stale_indicators()` deletes any active-country row not covered with a value by the CSV, so stale observations cannot display). Every phase prints exact counts.
- **`intelligence/charts.py`** — sets `matplotlib.use("Agg")` before pyplot import so it works headless; each `generate_*_chart()` builds a figure, and `_render_figure_to_png_bytes()` does `savefig(BytesIO, format="png", dpi=120)` then `plt.close(fig)` — charts live only for the request.
- **Templates** — `comparison.html` is a server-rendered dashboard: `{% for article in result.articles|slice:':8' %}` for cards, metric blocks for News Activity, the Narrative Shift summary guarded by `{% if result.narrative_shift %}` (with an explicit "not enough valid coverage data" fallback), an indicator table driven by the view-built `indicator_rows`, and base64 `<img>` tags for the three charts. `{% url %}` and `{% static %}` keep links/asset paths decoupled from hard-coded paths.

## How do we use Django and SQL together?

We never write raw SQL. Django's ORM is our SQL layer: the Python model classes in `intelligence/models.py` map to MySQL tables, and every read/write is expressed as Python querysets (`.filter()`, `.exclude()`, `.get_object_or_404()`, `update_or_create()`, `.create()`) that Django's `django.db.backends.mysql` backend compiles into parameterised `SELECT`/`INSERT`/`UPDATE`/`DELETE` statements (PyMySQL is the driver, registered in `config/__init__.py`). SQL structure itself is produced by migrations: `manage.py makemigrations` generated `intelligence/migrations/0001_initial.py`, and `manage.py migrate` executes it as `CREATE TABLE`, `CREATE INDEX`, and `CREATE UNIQUE CONSTRAINT` statements. The only non-relational column is `AnalysisRun.result_json`, a MySQL `JSON` field (via Django `JSONField`) that stores the full analysis output.

## Where exactly is the database created and stored?

There is no SQLite file and nothing database-related inside the project folder. `config/settings.py` points at a local **MySQL Server 8.0** instance: `DB_NAME=between_nations`, `DB_USER=root`, `DB_HOST=127.0.0.1`, `DB_PORT=3306` (credentials come from the git-ignored `.env`). The database is **created** the first time you run `python manage.py migrate`, which connects to MySQL and creates the schema `between_nations` with the four app tables (`country`, `article`, `countryindicator`, `analysisrun`) plus Django's own auth/session/admin tables. Physically it is **stored** by the MySQL Windows service (`MySQL80`) in its data directory `C:\ProgramData\MySQL\MySQL Server 8.0\Data` (confirmed from `my.ini`: `datadir=C:/ProgramData/MySQL/MySQL Server 8.0/Data`), where each table appears as InnoDB `.ibd` files under `between_nations\` — MySQL owns those files; the application never reads them directly. Two more databases exist only transiently: Django's test runner creates `test_between_nations` for `manage.py test` and drops it afterwards. The MySQL service must be running for the site to work.

## How is data retrieved from the database?

All retrieval happens through the ORM at request time — a handful of parameterised queries per page, no raw SQL:

1. **Countries** — `get_object_or_404(Country, iso_code=iso_a, is_active=True)`; the home form uses `Country.objects.filter(is_active=True)` to build its dropdowns.
2. **Articles** — `Article.objects.filter(unordered_pair_q(country_a, country_b))` becomes `WHERE (country_a_id=%s AND country_b_id=%s) OR (country_a_id=%s AND country_b_id=%s)` so A–B and B–A return the same rows, then `.filter(url__in=seed_urls)` (the canonical allowlist from `seed_articles.csv`) and `.filter(published_at__gte=window_start, published_at__lt=window_end)` restrict to the fixed snapshot, ordered by `-published_at`. Indexes on `url`, `published_at`, and `(country_a, country_b)` keep these lookups cheap.
3. **Indicators** — `CountryIndicator.objects.filter(country=country).order_by("indicator_code", "-year")`; Python then keeps the first (newest) row per canonical code, and a code with no row yields `None` → displayed as `N/A`.
4. **Writes** — each analysis is saved with `AnalysisRun.objects.create(...)`, and `seed_data` writes rows with `update_or_create`/`create`/`delete`.

## How and what do we use to display data to the UI?

Django's **server-side template engine (DTL)** renders the HTML — there is no JavaScript framework and no JSON API. The view puts a context dict together (`result`, `charts`, `indicator_rows`, countries), and `templates/intelligence/comparison.html` renders it with `{% if %}`/`{% for %}` tags and `{{ }}` expressions. Charts are produced by **Matplotlib** in memory (`Agg` backend → PNG bytes → `base64`), embedded directly in the HTML as `<img src="data:image/png;base64,...">`, so no chart files or charting library are needed. Textual data comes from plain context variables: article cards from `result.articles`, metric numbers from `result.news_activity`, the indicator table from `indicator_rows` (values pre-formatted in `views.py` — `$1.25 T`, `1.44 B`, `N/A`), and missing sections are handled honestly (`{% if result.narrative_shift %}` … explicit "unavailable" message). Styling is one static CSS file (`intelligence/static/intelligence/css/styles.css`) loaded with `{% static %}`, plus a small `main.js` on the home page.