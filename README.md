# Lead Nationality Classifier

A standalone Python application that classifies LinkedIn profiles exported via **Phantombuster Sales Navigator Search Export** into native-language speakers vs. foreigners. Built to separate Polish native speakers from non-Polish contacts so you can send outreach messages in the right language.

## How It Works

Every profile is evaluated across **8 independent signals**, each contributing a weighted score:

| # | Signal | Weight | What it checks |
|---|--------|--------|----------------|
| 1 | Surname suffix | +3 | Polish endings: -ski, -wicz, -czyk, -czak … |
| 2 | Surname database | +2 | 150+ common Polish surnames without classic suffixes (Nowak, Mazur, Kowal …) |
| 3 | Last-name diacritics | +2 | Polish characters: ą ć ę ł ń ó ś ź ż |
| 4 | First-name diacritics | +2 | Same characters in the first name |
| 5 | First name (strong/regular) | +3 / +2 | 400+ Polish first names in two tiers |
| 6 | Foreign first name | −3 | 200+ clearly non-Polish names (Ukrainian, Arabic, East Asian …) |
| 7 | Job-title language | +3 | Polish keywords + `langdetect` fallback |
| 8 | Summary language | +2 | `langdetect` on LinkedIn bio text |
| 9 | Company keywords | +1 | Polish company-name indicators (sp. z o.o., polska …) |

**Thresholds** (configurable):
- Score **≥ 3** → **Polish (PL)**
- Score **≤ 0** → **Foreign (EN)**
- Score **1–2** → **Uncertain** (manual review recommended)

## Quick Start

```bash
# 1. Clone / copy the project
cd lead-classifier

# 2. Install dependencies
pip install -r requirements.txt

# 3. Classify a CSV file
python classifier.py path/to/your/leads.csv

# 4. Find outputs in the same directory as the input file
```

## Usage

### CLI (classifier.py)

```bash
# Basic — outputs land next to the input CSV
python classifier.py data/leads.csv

# Custom output directory and filename prefix
python classifier.py data/leads.csv -o results/ -p MyProject

# Adjust thresholds (stricter native classification)
python classifier.py data/leads.csv --native-threshold 4 --foreign-threshold -1

# Classify from a public Google Sheet
python classifier.py "https://docs.google.com/spreadsheets/d/SHEET_ID/edit?gid=0#gid=0"

# CSV + JSON only (skip Excel)
python classifier.py data/leads.csv --no-excel

# Verbose logging
python classifier.py data/leads.csv -v
```

### Web UI (app.py) — Phase 2

```bash
pip install flask
python app.py                # → http://127.0.0.1:5000
python app.py --port 8080    # custom port
```

The web UI supports drag-and-drop CSV upload, Google Sheets URL input, and one-click download of all output files.

## Output Files

Every run produces:

| File | Description |
|------|-------------|
| `*_Polish_Speakers.csv` | Profiles classified as Polish native speakers |
| `*_Foreign_EN.csv` | Profiles classified as foreign / English speakers |
| `*_Uncertain.csv` | Ambiguous profiles for manual review |
| `*_Classification_Audit.csv` | All profiles with score + reasoning columns |
| `*_Classified.xlsx` | Excel workbook with all sheets (PL, EN, Uncertain, Summary, Audit) |
| `*_Report.json` | Machine-readable JSON summary with statistics |

## Customization

### Adjusting Thresholds

Edit `config.py` or use CLI flags:

```python
# config.py
POLISH_PROFILE = LanguageProfile(
    native_threshold=4,    # stricter: needs more evidence
    foreign_threshold=-1,  # more lenient: only strong negatives
)
```

### Editing Name Databases

All name lists are external files in `name_databases/`:

- **`polish_first_names.txt`** — Add/remove names; use `## STRONG` and `## REGULAR` section headers to control scoring weight.
- **`foreign_names.txt`** — Add names that indicate a non-Polish person.
- **`polish_surname_patterns.json`** — Surname suffixes, common surnames, title keywords, and company keywords.

### Adding a New Language (e.g. DE/EN)

1. Create name database files for the new language.
2. Add a new `LanguageProfile` in `config.py` with appropriate weights.
3. Register it in the `PROFILES` dict.
4. Run with `python classifier.py data/leads.csv -l de`.

## Project Structure

```
lead-classifier/
├── classifier.py          # Main CLI entry point
├── scoring.py             # 8-signal scoring engine
├── language_detect.py     # langdetect wrapper (graceful fallback)
├── config.py              # Thresholds, weights, language profiles
├── utils.py               # CSV/Excel/JSON I/O helpers
├── app.py                 # Flask web UI (Phase 2)
├── name_databases/
│   ├── polish_first_names.txt
│   ├── polish_surname_patterns.json
│   └── foreign_names.txt
├── requirements.txt
└── README.md
```

## Requirements

- Python 3.9+
- pandas, openpyxl, langdetect (see `requirements.txt`)
- Flask (optional, for the web UI)

## Performance

Classifies ~1,900 profiles in approximately 60–90 seconds (language detection is the bottleneck). Without `langdetect` installed, classification runs in under 2 seconds but loses two scoring signals.

## Accuracy

Tested on a 1,882-profile Phantombuster export from Poland:

- **85.9%** classified as Polish (1,616 profiles)
- **12.4%** classified as Foreign (234 profiles)
- **1.7%** flagged as Uncertain (32 profiles)

The Uncertain bucket contains genuinely ambiguous cases (Ukrainian first name + Polish married surname, Serbian names, company pages) that benefit from human review.
