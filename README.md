# Firmable take-home: schema inference and entity identification

An agent reads an Australian government dataset it has never seen, works out how
its columns map onto a canonical business schema, and writes a config. One
engine reads those configs and emits records. Nothing after that calls a model.

Six sources onboarded, 126,540 observations, 1,609 cross-source links, 50
company profiles.

- `WRITEUP.md` — the Part 5 answers
- `DESIGN.md` — how each stage works, and every bug found building it

---

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then paste a Gemini API key into it
```

A key is free from https://aistudio.google.com/apikey. The whole pipeline costs
about $0.20 to run.

---

## Run it

Two paths. The short one reproduces this submission. The long one is the system
doing its actual job.

### Short: the six sources this submission used (~4 minutes)

```bash
.venv/bin/python -m src.select pick --pinned
.venv/bin/python -m src.agent.run
```

The agent stops and asks you to approve each config. Read one, then approve it:

```bash
.venv/bin/python -m src.approve                          # what is waiting
.venv/bin/python -m src.approve <source_id>              # read the card
.venv/bin/python -m src.approve <source_id> --yes --by you
```

Then:

```bash
.venv/bin/python -m src.extract
.venv/bin/python -m src.extract --limit 25000 --out observations_deep
.venv/bin/python -m src.match
.venv/bin/python -m src.relationships
.venv/bin/python -m src.profile
.venv/bin/python -m src.report
```

### Long: discover sources from the live catalogue (~13 minutes)

| step | what it does | time |
|---|---|---|
| crawl, score, triage, shortlist | 366 datasets down to 50 | 83s |
| check against real data | 20 files downloaded and read | 110s |
| probe | ~100 candidate files inspected | 387s |
| pick six | deterministic | instant |
| agent, six sources | six configs produced | 137s |
| approve x6 | you read the cards | — |
| extract, match, relationships, profiles | 126,540 observations | 16s |


Everything above, but starting from a crawl:

```bash
.venv/bin/python -m src.catalogue fetch      # 24 HTTP calls, no API key
.venv/bin/python -m src.catalogue flatten
.venv/bin/python -m src.score                # keyword scoring, no model
.venv/bin/python -m src.triage               # model reads 120 datasets
.venv/bin/python -m src.shortlist            # top 50
.venv/bin/python -m src.handcheck            # check 20 against their real data
.venv/bin/python -m src.select probe         # 6.5 min, downloads ~100 files
.venv/bin/python -m src.select pick
```

then `src.agent.run` and the rest as above.

### Check the deliverables

```bash
.venv/bin/python -m src.verify
```

Reads the assignment's requirements and checks the files on disk against them.

---

## What you get

| file | what it is |
|---|---|
| `outputs/shortlist.csv` | 50 datasets, ranked, with licence and download link |
| `configs/*.json` | six mapping configs, each written by the agent and approved by a person |
| `outputs/observations/` | 6,000 canonical records, 1,000 per source |
| `outputs/links.jsonl` | 1,609 proposed links with their evidence |
| `outputs/unlinked.jsonl` | 1,025 pairs we refused to link, and why |
| `outputs/entities.jsonl` | businesses appearing in more than one source |
| `outputs/relationships.jsonl` | parent and sibling edges, kept apart from sameness |
| `outputs/company_profiles.jsonl` | 50 merged profiles, per-field confidence and provenance |
| `outputs/onboarding_report.json` | what it cost, in dollars, tokens and seconds |

All of these are already in the repo, so you can read the results without
running anything. Two intermediates are left out to keep the repo small: the
per-source record dumps under `runs/`, and the 25,000-per-source pull that
Part 3 matches on. Both regenerate in seconds, and `.gitignore` says which
command rebuilds each.

---

## How it works

```
  crawl ──> score ──> triage ──> shortlist ──> pick six
                                                  │
                                                  ▼
                            probe ─> profile ─> propose ─> validate ─> revise
                                                              │
                                                              ▼
                                                    a person approves
                                                              │
                                                              ▼
                              one engine reads the config and emits records
                                                              │
                                                              ▼
                                              match ─> profiles
```

Model calls happen in four places: triaging the catalogue, checking the
shortlist against real data, proposing a mapping, and correcting one. Everything
else is ordinary code, which is why the per-record cost is zero.

`DESIGN.md` has the detail.

---

## Things worth knowing before you run it

**The catalogue is live.** A crawl today will not return what a crawl last week
returned, so the long path can legitimately pick six different sources. It did
exactly that on a clean-checkout test, and the agent mapped all six without a
line of code from us. Use `--pinned` to get the six this submission used.

**Pinned fixes the sources, not the mappings.** Rerun the pinned path and the
agent reproduces this submission closely — field counts match on five of six,
and both problem sources fail the same way — but the model's proposal varies
run to run. The validate step is what makes that safe rather than alarming.

**It takes about 13 minutes, not the ten the brief asks for.** The probe step is
six and a half of those, downloading roughly a hundred files one after another.
Lower `shortlist_size` in `config/settings.yaml`, or use the pinned path.

**A person has to approve six configs.** That is the design, not an oversight.
Nothing reaches `configs/` because a model said it was fine.

**Two of the six sources ship with a known failure**, recorded in the config and
approved deliberately. The Victorian schools file has nine-digit values in a
column called ABN, and the federal contract register has overseas postcodes our
transforms cannot condition on country. Both are dropped rather than guessed.

**Precision was measured by a reviewer outside this pipeline.**
`src/precision.py prepare` writes 150 sampled pairs with our own verdict
stripped out; a reviewer answers into `outputs/precision_verdicts.json`, and
`merge` reports. The answers from our run are in the repo.

---

## Layout

```
config/       settings, keyword lists, the transform vocabulary, the config schema
src/          the pipeline; src/agent/ is the seven-step onboarding agent
configs/      the six approved mapping configs
outputs/      every deliverable
runs/         per-source state, review cards, and a line per model call
data/         downloads and intermediate files (gitignored)
```
