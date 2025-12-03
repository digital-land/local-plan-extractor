# Local Plan Extractor Script Documentation

The `local-plan-extractor.py` script extracts housing data from local plan PDFs using Claude AI. It supports processing single PDF files, directories of PDFs, and flexible output formats.

## Installation

Install dependencies:
```bash
pip install -r requirements.txt
```

Set your Anthropic API key:
```bash
export ANTHROPIC_API_KEY='your-key-here'
```

## Basic Usage

### Single PDF File

Process a single PDF and output JSON to stdout:

```bash
python bin/local-plan-extractor.py ./collection/document/my-plan.pdf
```

Redirect to file:
```bash
python bin/local-plan-extractor.py ./collection/document/my-plan.pdf > local-plan/my-plan.json
```

**Note:** Single PDF files always output JSON to stdout for Makefile compatibility. The `--output` and `--json-output` flags are ignored for single files.

### Directory Processing

#### Option 1: Save Individual JSON Files

Process all PDFs in a directory and save individual JSON files for each:

```bash
python bin/local-plan-extractor.py ./collection/document/ --json-output ./local-plan/
```

This creates:
```
local-plan/document1.json
local-plan/document2.json
local-plan/document3.json
...
```

#### Option 2: Save CSV File

Process all PDFs and save results to a single CSV file:

```bash
python bin/local-plan-extractor.py ./collection/document/ --output housing_data.csv
```

#### Option 3: Save Both JSON and CSV

Process all PDFs and save both individual JSON files and CSV:

```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --json-output ./local-plan/ \
  --output housing_data.csv
```

#### Option 4: Output JSON Array to Stdout

Process all PDFs and output JSON array to stdout (useful for piping):

```bash
python bin/local-plan-extractor.py ./collection/document/ | jq .
```

## Command-Line Options

```
usage: local-plan-extractor.py [-h] [--output OUTPUT] [--json-output JSON_OUTPUT]
                               [--api-delay API_DELAY] [--file-delay FILE_DELAY]
                               [--max-pages MAX_PAGES]
                               path

positional arguments:
  path                  Path to a PDF file or directory containing PDF files

optional arguments:
  -h, --help            Show help message
  --output, -o OUTPUT   Output CSV file path (directory processing only)
  --json-output, -j JSON_OUTPUT
                        Output directory for individual JSON files (directory processing only)
  --api-delay API_DELAY
                        Seconds to wait between API calls (default: 2)
  --file-delay FILE_DELAY
                        Seconds to wait between processing files (default: 3)
  --max-pages MAX_PAGES
                        Maximum pages to send to Claude (default: 32)
```

## Output Formats

### Individual JSON Files (--json-output)

Each JSON file contains extraction results for one PDF:

```json
{
  "name": "Plan Name",
  "organisation": "local-authority:XXX",
  "organisation-name": "Authority Name",
  "period-start-date": 2020,
  "period-end-date": 2035,
  "housing-numbers": [
    {
      "organisation-name": "Authority Name",
      "required-housing": 10000,
      "allocated-housing": 5000,
      "committed-housing": 3000,
      "windfall-housing": 2000,
      "broad-locations-housing": 5000,
      "annual-required-housing": 666,
      "pages": "45, 78",
      "notes": "Housing numbers extracted from plan..."
    }
  ],
  "confidence": "high",
  "pages_analysed": 32
}
```

### CSV Format (--output)

The CSV file contains one row per PDF with the following columns:

- `authority`: PDF filename or authority code
- `name`: Local plan name
- `organisation`: Local authority code
- `organisation-name`: Local authority name
- `period-start-date`: Plan start year
- `period-end-date`: Plan end year
- `housing-numbers`: JSON array of housing data (serialized)
- `pages_analysed`: Number of pages analysed
- `confidence`: Confidence level in extraction
- `error`: Error message (if processing failed)

## Usage Examples

### Makefile Integration

The script is designed to work with Make:

```makefile
local-plan/%.json: collection/document/%.pdf
	python bin/local-plan-extractor.py $< > $@
```

This processes each PDF and saves the JSON output to the `local-plan/` directory.

### Batch Processing with Progress

Process multiple PDFs with custom delays:

```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --json-output ./local-plan/ \
  --api-delay 3 \
  --file-delay 5
```

This adds 3-second delays between API calls and 5-second delays between processing files.

### Combination with Data Processing Pipeline

Extract housing data and generate CSVs for analysis:

```bash
# Step 1: Extract individual JSONs
python bin/local-plan-extractor.py ./collection/document/ \
  --json-output ./local-plan/

# Step 2: Generate CSVs from source JSON files
python bin/generate-csvs.py --source-dir ./source/

# Step 3: Combine with extracted housing data
python bin/generate-csvs.py \
  --housing housing_data.csv \
  --output-dir ./dataset/
```

## Performance Tuning

### Rate Limiting

The script includes built-in rate limiting to respect API quotas:

- `--api-delay`: Seconds to wait between Claude API calls (default: 2)
- `--file-delay`: Seconds to wait between processing different PDF files (default: 3)

Increase these values if hitting rate limits:

```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --api-delay 5 \
  --file-delay 10 \
  --json-output ./local-plan/
```

### Maximum Pages

Limit the number of pages sent to Claude:

```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --max-pages 16 \
  --json-output ./local-plan/
```

Fewer pages = faster processing but potentially less complete data extraction.

## Error Handling

The script gracefully handles errors during processing:

- **Network errors**: Automatically retries with exponential backoff
- **Rate limit errors**: Reports the error and continues with next file
- **Parsing errors**: Logs the error and marks the result with an `error` field

Failed PDFs still get an entry in the output with an `error` message:

```json
{
  "authority": "problematic-plan.pdf",
  "pdf_file": "collection/document/problematic-plan.pdf",
  "error": "Failed to extract data: connection timeout"
}
```

## Output Examples

### Summary After Directory Processing

When using `--json-output` or `--output`:

```
============================================================
SUMMARY STATISTICS
============================================================
Successfully processed: 45/50
Failed: 5/50
Individual JSON files saved to: ./local-plan/
Results saved to CSV: housing_data.csv

Total housing requirements found: 42
Average requirement: 8,500 homes
Total homes across all plans: 357,000
```

## Workflow: From PDF to Static Website

Complete workflow using all tools:

```bash
# 1. Download PDFs (existing process)
python bin/download-documents.py

# 2. Extract housing data to JSON files
python bin/local-plan-extractor.py ./collection/document/ \
  --json-output ./local-plan/ \
  --api-delay 3

# 3. Generate static HTML website
make all

# 4. (Optional) Also generate CSVs for analysis
python bin/local-plan-extractor.py ./collection/document/ \
  --output housing_data.csv
```

The `render.py` script reads the JSON files from `local-plan/` and generates static HTML pages.

## Troubleshooting

### "ANTHROPIC_API_KEY environment variable not set"

Set your API key:
```bash
export ANTHROPIC_API_KEY='sk-...'
```

### "Found 0 PDF files to process"

Verify the directory contains PDF files:
```bash
ls -la collection/document/*.pdf | head
```

### Rate limiting errors

Increase delays:
```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --api-delay 5 \
  --file-delay 10 \
  --json-output ./local-plan/
```

### Memory issues with large PDFs

Reduce max pages:
```bash
python bin/local-plan-extractor.py ./collection/document/ \
  --max-pages 16 \
  --json-output ./local-plan/
```

## File Naming

When saving individual JSON files with `--json-output`:

- Input: `collection/document/my-plan-2024.pdf`
- Output: `local-plan/my-plan-2024.json`

The filename (without extension) is preserved.

## See Also

- [README.md](README.md) - Project overview
- [JOINT_LOCAL_PLANS.md](JOINT_LOCAL_PLANS.md) - Joint local plan configuration
- [generate-csvs.py](bin/generate-csvs.py) - Generate structured CSVs from extracted data
