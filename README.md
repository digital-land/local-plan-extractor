# Local Plan Extractor

Extract information from local planning authority websites and local plan documents to seed data for https://planning.data.gov.uk

The data extracted is in the format defined by the following data specifications:

* [Local plan](https://digital-land.github.io/specification/specification/local-plan/) — headline information including housing numbers found in local plan documents
* [Local plan timetable](https://digital-land.github.io/specification/specification/local-plan-timetable/) — estimated and actual dates for milestones found in Local Development Scheme documents

Local planning authorities are encouraged to review and improve this data using the [Check and Provide](https://provide.planning.data.gov.uk) service.

## Set-up

Requires Python 3.12 or newer and Make v4.0 or above. We recommend working in a [virtual environment](http://docs.python-guide.org/en/latest/dev/virtualenvs/).

Install dependencies:

    $ make init

Set your [Anthropic API key](https://docs.anthropic.com/en/docs/get-started) — required for scraping and extraction steps:

    $ export ANTHROPIC_API_KEY='your-key-here'

## Workflow

The full pipeline runs in three broad phases. The first two are slow and should be run once (or when you need to refresh data). The third is fast and is what `make` runs automatically.

### Phase 1 — Find plan URLs (Claude + web scraping)

Visits each local planning authority website and uses Claude to identify local plan documents. Writes results to `source/`.

    $ make scrape

To process a single authority:

    $ python3 bin/find-local-plan.py local-authority:DAC

### Phase 2 — Download documents

Download the main adopted local plan PDF per authority into `collection/`:

    $ make download

To download every document URL across all plans instead:

    $ make download-all

Individual PDFs for extraction should be placed in `document/`.

### Phase 3 — Extract, generate and render (fast)

Once PDFs are in `document/` and reference data is in `source/`, run the full build:

    $ make

This will:

1. Download reference data from planning.data.gov.uk into `var/cache/` (if not already cached)
2. Run `local-plan-extractor.py` on any PDF in `document/` that doesn't yet have a corresponding `local-plan/*.json`
3. Generate `dataset/local-plan.csv` and `dataset/local-plan-document.csv`
4. Render the static site into `docs/`

### Other commands

| Command | Description |
| --- | --- |
| `make extract-dates` | Extract adoption dates from plans using Claude (slow, hits API) |
| `make generate-config` | Generate config files for local plans |
| `make test` | Run the test suite |
| `make server` | Serve the generated site locally at [http://localhost:8000](http://localhost:8000) |
| `make black` | Format all scripts in `bin/` with Black |
| `make clobber` | Delete generated `local-plan/*.json` and dataset CSVs so `make` rebuilds them |
| `make clean` | Delete `var/` (cached reference data) so it is re-downloaded on next `make` |

## Project structure

    bin/                    Python scripts
      find-local-plan.py    Find plan URLs for an authority (Claude + web scraping)
      download-documents.py Download plan PDFs
      local-plan-extractor.py Extract housing data from a PDF (Claude)
      extract-adoption-dates.py Extract adoption dates from plans (Claude)
      generate-csvs.py      Generate CSV datasets from extracted data
      render.py             Render the static HTML site
      organisation_matcher.py Match authority names to official codes
      utils.py              Shared utilities (hashing, file type detection, slugify)
    source/                 Plan metadata per authority (output of find-local-plan.py)
    document/               PDFs to extract data from
    local-plan/             Extracted JSON per plan (output of local-plan-extractor.py)
    dataset/                Generated CSV datasets
    collection/             Downloaded documents and logs
    var/cache/              Cached reference data from planning.data.gov.uk
    docs/                   Generated static site
    templates/              Jinja2 HTML templates
    tests/                  Unit tests

## Licence

The software in this project is open source and covered by the [LICENSE](LICENSE) file.

Individual datasets copied into this repository may have specific copyright and licensing, otherwise all content and data in this repository is
[© Crown copyright](http://www.nationalarchives.gov.uk/information-management/re-using-public-sector-information/copyright-and-re-use/crown-copyright/)
and available under the terms of the [Open Government 3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) licence.
