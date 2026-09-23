# DESIGN.md

How the pipeline works, stage by stage. For each stage: what it does, how it
does it, what is worth pointing at, and what broke while building it.

Brief is in `ASSIGNMENT.md`. Schema is in `firmable_ontology.yaml`.

## Terms used throughout

| term | what it is |
|---|---|
| **ABN** | Australian Business Number. Eleven digits with a checksum, issued by the Australian Business Register to any business that registers for tax. The strongest identifier in this data. |
| **ACN** | Australian Company Number. Nine digits with a checksum, issued by ASIC to companies only, so sole traders and partnerships have an ABN but no ACN. The last nine digits of an ABN are often the ACN, but not always. |
| **CKAN** | The open-source data portal software data.gov.au runs on. It gives every state portal the same REST API, so the same code works against data.nsw.gov.au and data.qld.gov.au. |
| **Solr** | The search index CKAN puts behind that API. `q` is the search, `fq` is a filter Solr applies before a response is built. |
| **observation** | One claim a source made about a business at a point in time. Five sources saying five different things produce five observations, not one curated value. |
| **link** | One row saying two specific source records refer to the same business, with the evidence for it. |

---

# The pipeline

```
  PART 1   DISCOVERY AND TRIAGE
  ──────────────────────────────────────────────────────────────────────

   find      12 package_search calls + 8 resource_search + 4 lookups,
     │       all format-filtered on CKAN's own server    ──>  519 records
     │
   flatten   one tidy row per dataset                    ──>  376 unique
     │
   score     keywords + publisher + format, no model
     │       54 yearly editions folded into their newest ──>  322 sources
     │
   triage    MODEL. what does one row of this file represent?
     │       a business / an event / a total / unknown
     │       120 datasets, 6 calls, 41s, $0.026
     │
   rank      top 50 by model confidence                  ──>  shortlist
     │
   check     MODEL. download the real file, read real rows
     │       20 sampled  ──>  86% of readable files correct
     ▼
   outputs/shortlist.csv        50 datasets, with licence and download url


  PART 2   SCHEMA INFERENCE AND CANONICAL MAPPING
  ──────────────────────────────────────────────────────────────────────

   select    deterministic. probe all 50, keep what downloads, pick six
     │       for spread of publisher, format and ontology fields
     ▼
  ┌────────  runs/<source_id>/state.json, saved after every step  ───────┐
  │                                                                      │
  │   1 probe      what this file really is, from its bytes        -     │
  │   2 profile    per column: nulls, distinct, examples,          -     │
  │                checksum pass rates                                   │
  │   3 propose    ontology + profiles  ──>  draft config        MODEL   │
  │   4 validate   run the draft on rows it never saw              -     │
  │   5 revise     failure report  ──>  corrected config         MODEL   │
  │                at most 2 rounds                                      │
  │   6 review     card: mapped, refused, failures, records      HUMAN   │
  │   7 freeze     configs/<source_id>.json                        -     │
  │                                                                      │
  └──────────────────────────────────────────────────────────────────────┘
     │
     ▼
   extract   ONE engine reads a config and emits canonical
     │       observations. Zero model calls.
     ▼
   outputs/observations/<source_id>.jsonl          6,000 records
   outputs/observations_deep/                    126,540 records, for part 3


  PART 3   ENTITY IDENTIFICATION
  ──────────────────────────────────────────────────────────────────────

   block     group records that share a validated ABN, a validated ACN,
     │       or a normalised name. No model.
     │
   score     compare every pair inside a group by method
     │       abn_exact 0.99 · acn_exact 0.99 · abn_acn_derived 0.80
     │       name_geo 0.75 · name_only 0.45
     │
     ├──  at or above 0.70  ──>  outputs/links.jsonl        1,609 links
     │
     ├──  conflicted or below  ──>  outputs/unlinked.jsonl  1,025 refusals
     │                              each with a stated reason
     │
     ├──  group links by key  ──>  outputs/entities.jsonl   1,434 entities
     │
     └──  a source names a different business
                              ──>  outputs/relationships.jsonl
                                   12,421 parent edges, never sameness
     │
     ▼
   measure   150 pairs, our verdict stripped out          OUTSIDE REVIEWER
             100% at 0.70 · 90% on the weak tier · 98% at 0.45


  PART 4   COMPANY PROFILES
  ──────────────────────────────────────────────────────────────────────

   gather    every observation behind one entity. No model.
     │
   resolve   per field: mutable facts by recency, registry facts by
     │       publisher reliability. Keep every losing value.
     ▼
   outputs/company_profiles.jsonl     50 businesses, per-field confidence,
                                      provenance, and visible conflicts
   outputs/profile_source_impact.json  what breaks if a source disappears
```

## The workflow, stage by stage

### Discovery

**In:** twelve search terms and eight file-name patterns from
`config/settings.yaml`. **Out:** `data/interim/datasets.jsonl`, 376 unique CKAN
dataset records, each with its licence, publisher and resource URLs.

Twelve `package_search` calls over dataset titles, descriptions and tags. Eight
`resource_search` calls over the names of files inside datasets, resolved back
to their datasets in four batched lookups. Both filtered on CKAN's own server
to formats we can parse. Deduplicated by dataset id.

### Triage

**In:** those 376 dataset records. **Out:** `outputs/shortlist.csv`, 50 ranked
datasets, each with a confidence, a one-line reason, a licence and a download
URL.

Keyword weights from `config/keywords.yaml` score every dataset, and 54 yearly
editions collapse into their newest. The top 120 go to `gemini-3.1-flash-lite`
in six batches of twenty, which classifies what one row of each file
represents. Totals and unknowns drop out; the top 50 by model confidence become
the shortlist. Twenty are sampled with a fixed seed and checked against their
downloaded files rather than their descriptions.

### Source selection

**In:** the 50 shortlisted datasets. **Out:** `outputs/selected_sources.json`,
six datasets with a verified live download URL.

95 candidate files are downloaded and inspected. 59 parse, across 30 datasets.
A written rule then picks six for spread: one per publisher, preferring a
format not yet held, with at least one that is not a plain CSV and at least one
whose rows are contracts rather than registrations.

### Schema inference

**In:** one source's download URL. **Out:** `configs/<source_id>.json`, a
mapping config, plus `runs/<source_id>/state.json` recording every step's
duration and cost.

Probe reads the file's first bytes to find its real format, unwraps zips,
locates the header row, and extracts 2,000 records — the first 400 as a sample,
the rest held back. Profile summarises each column into null rates, distinct
counts, example values and checksum pass rates. `gemini-3.7-flash` reads the
ontology, the 20 transforms and that profile, and returns a draft config. The
draft runs over the held-back rows and every failure is counted. Failures go
back to the model for up to two correction rounds, and a revision that deletes
a validating transform is rejected. A person then reads a review card and
approves before the config is written.

### Extraction

**In:** six approved configs. **Out:**
`outputs/observations/<source_id>.jsonl`, 6,000 canonical observations, each
with the full ontology envelope and three separate confidence numbers.

One engine opens each file from its config's `resource` block and applies the
transform chains. No model calls, so the per-record cost is zero.

### Entity identification

**In:** 126,540 observations from a deeper 25,000-per-source pull. **Out:**
`outputs/links.jsonl`, `unlinked.jsonl`, `entities.jsonl` and
`relationships.jsonl`.

Records are grouped by validated ABN, validated ACN, or normalised name. Each
candidate pair inside a group is scored by method. Pairs at or above 0.70
become links carrying their evidence; conflicted or low-confidence pairs become
refusals carrying their reason. 1,434 entities are derived by grouping links on
the entity key. WGEA's corporate group column becomes 12,421 parent edges,
joined on the same key and never treated as sameness.

### Profile assembly

**In:** entities and their observations. **Out:**
`outputs/company_profiles.jsonl`, 50 merged businesses.

Every value any source offered for each ontology field is collected with its
provenance. Mutable fields resolve by recency, registry fields by publisher
reliability, and every losing value is kept with its source and date. Each
field carries its own confidence. Fields no source supplied are absent.

---

# Decisions on system level

## Picking the model on today's price, not on the version number

`gemini-3.1-flash-lite` for triage, `gemini-3.7-flash` for mapping.

We read the live pricing page rather than working from memory, and two things
fell out of it:

- A newer model is not always the dearer one. On the day we checked,
  `gemini-3.5-flash` was $1.50 in and $9.00 out while `gemini-3.7-flash` was
  $0.75 and $3.75. Picking by version number would have cost twice as much for
  a worse model.
- Output bills four to five times higher than input, and thinking tokens count
  as output. Mapping configs are output, so we keep them compact and keep
  thinking off by default.

Prices live in `config/settings.yaml`, read on 2026-09-19, so every call is
priced from a table rather than estimated later.

**Neither price is the real cost story.** The extractor makes zero model calls,
so the per-record cost is zero whatever the token price does.

### The cheapest model on the price list does not exist

We first picked `gemini-2.5-flash-lite` at $0.10 in and $0.40 out. The first
real call returned 404: "no longer available to new users". `models.list()`
still advertises it. The list endpoint says yes and the generate endpoint says
no.

A published price list is not a statement of availability. We only found out
because we made three probe calls before spending at scale.

## Every model call is monitored

One function, `src/llm.py:ask()`, that every call goes through. It records
model, tokens in, tokens out, seconds and dollars to `runs/llm_calls.jsonl`.

Bolting this on at the end means the earlier numbers are gone and the answer
becomes an estimate, which the assignment says it marks down.

## The chosen contract between the agent and the engine

Written before any agent code, because changing it later kills every config
already generated.

| file | what it is |
|---|---|
| `config/mapping_config.schema.json` | the shape of a mapping config |
| `config/transforms.yaml` | the closed list of 20 operations |
| `src/transforms.py` | the implementations |
| `configs/EXAMPLE_asic-company.yaml` | a worked example that validates |

### What a mapping config holds

Ten parts, eight of them required.

| part | what it is for |
|---|---|
| `config_version` | Which schema version this was written against, so an old config stays readable after the schema moves. |
| `source_id` | Stable id for the dataset. Goes onto every observation the engine emits. |
| `generated_by` | Which model wrote it, how many correction rounds it took, who approved it and when. This is what makes a bad mapping traceable to a model version six months later. |
| `resource` | How to open the file: real format, encoding, delimiter, header row, sheet, member inside a zip. Filled by the probe step from actual bytes, not from the catalogue's claim. |
| `record_id` | How to build a traceable id for each raw record. Where a source has no key, this records the strategy and the reason for it. |
| `observation` | The envelope every emitted record carries: licence, source reliability, and where `observed_at`, `valid_from` and `valid_to` come from. |
| `field_mappings` | One entry per canonical field, each with its source column, transform chain and confidence. The four things the brief requires per field. |
| `unmapped_source_fields` | Source columns we chose not to map, each with a reason. Listed, not guessed at. |
| `unfilled_canonical_fields` | Ontology fields this source cannot fill, each with a reason. |
| `validation` | Rows tested, rows emitted, and every failure with a count and real examples. Written by the test step, never by the model. This is what a reviewer reads. |

### The schema never reaches the model

Only `src/verify.py` reads `mapping_config.schema.json`, to check all six
configs are well-formed.

What goes into the propose prompt is three other things: the ontology rendered
from `firmable_ontology.yaml`, the 20 transforms rendered from
`config/transforms.yaml`, and the column profile.

The model's output is constrained by a smaller, separate response schema — the
`SCHEMA` constant in `src/agent/propose.py` — which covers only the parts the
model is allowed to decide. `source_id`, `licence`, the `resource` block and
the whole `validation` block are facts we already hold and fill in ourselves.
The model never gets to invent them.

**The model never writes code.** It picks transform names from a list. An
unknown name is rejected before anything runs:

```
>>> apply_chain('x', [{'op': 'exec_python'}])
ValueError: unknown transform 'exec_python'. Allowed: ['abn_normalize', ...]
```

Two reasons. Model-written code means the engine runs `eval()` on model output.
And a fixed vocabulary is what makes "one engine, six configs" true; if each
config can carry code, they are six scripts again.

| group | ops |
|---|---|
| text | strip, collapse_spaces, upper, lower, title_case, null_if |
| extract | regex_extract, split_take, digits_only |
| combine | concat, coalesce, constant |
| identifiers | abn_normalize, acn_normalize, nzbn_normalize |
| controlled values | map_values, state_normalize, postcode_extract |
| dates | parse_date, parse_datetime |

`abn_normalize` and `acn_normalize` run the published checksums, not a length
check, because the ontology says so in as many words. Both were tested against
real identifiers: the ATO's ABN 51824753556 and NAB's ACN 004085616 pass, and
the same numbers with one digit changed fail.

**An op returns nothing rather than a wrong answer.** A failed checksum gives
no ABN. `map_values` falls through to `unknown`. Fields we cannot fill end up
absent, not blank and not guessed.

**Confidences stay apart.** `source_reliability` is per source.
`field_confidence` is per field. `link_confidence` belongs on links in Part 3.
Blending them is on the assignment's count-against list.

---

# Part 1 — Discovery and triage

`src/catalogue.py`, `src/formats.py`, `src/score.py`, `src/triage.py`,
`src/shortlist.py`, `src/handcheck.py`

## Search on purpose

data.gov.au holds 141,297 datasets. Only 30,079 of them (21%) ship a file a
machine can read, so most of that catalogue is unusable to us whatever it is
about.

Every record we hold came from a deliberate search. An engineer who knows the
domain picks the terms, chosen against the ontology's own fields: `abn`,
`company register`, `licensed contractors`, `liquor licences`, `procurement
contracts`, and eight more.

The alternative was `q=*:*`, Solr for "match everything". That retrieves an
arbitrary slice and leaves a model to find the good ones, moving the judgement
downstream where it costs money per dataset and leaves no reviewable record.
Twelve terms in a config file are free, auditable and editable. We kept the
unaimed crawl only as a measurement, in `src/baseline_report.py`.

Two searches run, because they find different things.

## Channel 1: searching the dataset page

`package_search` runs free text over a dataset's title, description and tags,
which CKAN holds in a Solr index. One call per term:

```
GET /api/3/action/package_search
    ?q=liquor licences
    &fq=res_format:(CSV OR TSV OR XLSX OR XLS OR JSON OR GEOJSON OR XML OR ZIP)
    &rows=60&start=0
```

`q` is the search. `fq` is a filter Solr applies before building the response,
so a PDF-only dataset is never serialised, never sent, and never counted
against our page size. It is what takes 141,297 datasets down to the 30,079
(21%) worth looking at.

The reply carries complete dataset metadata, including the licence and every
resource URL, so one call gives us everything Part 2 will later need.

| query | records | query | records |
|---|---|---|---|
| abn | 60 | trade licences | 59 |
| business names | 60 | licensed contractors | 25 |
| company register | 60 | charities register | 22 |
| building approvals | 60 | food business register | 14 |
| procurement contracts | 60 | workplace safety prosecutions | 14 |
| | | government tenders awarded | 14 |
| | | liquor licences | 8 |

7/12 queries (58%) returned fewer than the 60 asked for, because the catalogue
ran out. Only 8 liquor licence datasets exist with a parseable file. The loop
stops on a short page, so we never page past the end.

## Channel 2: searching the file names

`package_search` does not index the names of files inside a dataset. A dataset
called "Annual Report 2023" that ships `licensed_contractors.csv` is invisible
to channel 1.

```
GET /api/3/action/resource_search?query=name:contractor&limit=40
```

Eight such queries returned 241 resource hits belonging to only 99 distinct
datasets — one dataset often ships a dozen similarly named files. Rather than
99 separate lookups we resolve them 30 at a time back through
`package_search`, which is 4 calls instead of 99. The format filter runs again
there, so 63/99 (64%) came back.

## Result

**24 HTTP calls, 43 seconds, no API key.** 519 records.

| found by | datasets | share |
|---|---|---|
| dataset text, channel 1 only | 313 | 83% |
| file name, channel 2 only | 45 | 12% |
| both channels | 18 | 5% |

Those 45 are what the second channel bought us. Without it, 12% of the pool
would not exist.

## Flatten (`catalogue flatten`)

One tidy row per dataset: id, title, description, organisation, tags, licence
id and title, plus every resource with its format, URL and size. This is the
only place raw CKAN shapes are read.

**The licence is captured here, at the first step.** The ontology requires one
on every observation, and backfilling it later means crawling twice.

519 records dedupe to **376 unique datasets** (72%).

## Rule scoring (`score`)

A score between 0 and 1 per dataset, from four inputs, with no model involved.

| input | weight | examples |
|---|---|---|
| strong words | +3 each | `abn`, `licensee`, `trading name`, `contractor` |
| medium words | +1 each | `permit`, `tender`, `prosecution` |
| negative words | −2 each | `rainfall`, `species`, `bathymetry` |
| known publisher | +2 | ASIC, the ABR, the ATO, the ACNC |
| readable format | +0.5 to +2 | CSV scores above ZIP, being less work to open |

Words match whole only, so `abn` never fires inside `abnormal`. Weights live in
`config/keywords.yaml` and are tunable without touching code.

**Every score keeps its evidence.** `rule_evidence` lists exactly which words
fired, so a reviewer can see why a dataset ranked where it did.

**No threshold anywhere.** The brief asks for 50 datasets, not for datasets
above a confidence. We rank and take the top k: `candidate_pool: 120`,
`shortlist_size: 50`.

### Folding yearly editions

Publishers republish the same dataset annually. The ACNC Annual Information
Statement appeared 12 times in the pool, MIWB Contract Disclosure 5 times.

We strip years, quarters and month names from the title, group by publisher
plus the stripped title, keep the newest, and record the rest as
`edition_siblings` on the survivor rather than deleting them — they are real
datasets and a reviewer should be able to disagree.

Without this we would pay the model to read one dataset twelve times, fill the
shortlist with one source repeated, and hand Part 2 six sources that are not
actually different.

**54 editions folded (14%). 376 datasets become 322 distinct sources.**

## Model triage (`triage`)

The keyword scorer ranks. The model judges. Different questions on purpose.

### The question worth paying for

Not "is this about business" — keywords answer that. It is **what one row of
this file represents**. The field is called `record_grain` in the code, after
the dimensional-modelling term, and takes four values:

| value | what one row is | example |
|---|---|---|
| `entity` | one business | ASIC Company Dataset |
| `event` | an approval, contract or prosecution naming a business | building approvals |
| `aggregate` | a count or an average across many businesses | business counts by state |
| `unknown` | not enough information to say | |

Our scorer cannot tell these apart. All four are full of business words.

### What the model is sent

Twenty datasets per call, one JSON line each:

```json
{"dataset_id": "7b8656f9-606d-4337-af29-66b89b2eeefb",
 "title": "ASIC - Company Dataset",
 "publisher": "Australian Securities and Investments Commission (ASIC)",
 "formats": ["CSV", "ZIP"],
 "resource_names": ["Company Dataset - Help File", "Company Dataset - Current"],
 "description": "###Update March 2025 ### From 11 March 2025, the dataset will
                 be updated to include 1 new field, Date of Deregistration…"}
```

**It never sees our rule score.** Tell a model what you already think and it
agrees with you, and two independent signals collapse into one.

**Resource names are included** because file names often say more than the
dataset page does. **Descriptions are cut at 400 characters**, since some run
to several thousand and the first 400 say what a dataset is.

### What came back

120 datasets, 6 calls, 41 seconds, **$0.026**.

| one row is | datasets | share |
|---|---|---|
| entity | 52 | 43% |
| event | 47 | 39% |
| aggregate | 14 | 12% |
| unknown | 7 | 6% |

The 14 aggregates are the win, because our keyword scorer had ranked several of
them highly. It dropped *Industry breakdown of PPSR registrations* as
"industry-level summaries" and the Geocoded National Address File as "a
database of addresses, not a list of businesses". Keywords cannot catch that.

### Guards on the call

- **JSON against a declared schema.** No markdown fences, no parse failures.
- **Matched by id, never by position.** A reply one item short would otherwise
  shift every judgement onto the wrong dataset. We warn and drop rather than
  guess.
- **Each batch cached to `runs/triage/`.** A failure on batch 4 does not
  re-spend batches 1 to 3.

### One honest weakness

Confidence bunches at the top: 52/120 datasets (43%) scored 0.90 or above, and
every shortlisted dataset is 0.90 or higher. The model is not using the range.

So `record_grain` is doing the real work and `confidence` is close to flat,
which means the rule score is breaking more ties than intended. Worth saying
rather than presenting the confidence column as if it discriminated.

## The shortlist (`shortlist`)

99/120 datasets (82%) survived as `entity` or `event`. Ranked by model
confidence, with the rule score only as a tie-break, the top 50 ship.

**We keep `event` datasets rather than dropping them.** A building approval
names a builder on every row, and the brief's own list of useful sources is
mostly event-shaped.

| | |
|---|---|
| Shortlist size | 50 |
| entity / event | 39 / 11 |
| Confidence range | 0.90 to 1.00 |
| Formats | CSV 45, XLSX 20, ZIP 7, TSV 7, JSON 7, XML 5, GEOJSON 4, XLS 3 |

Written to `outputs/shortlist.csv` and `.jsonl`, carrying dataset id, title,
organisation, formats, confidence, one-line reason, record grain, likely
ontology fields, rule score, licence, landing page and download URL.

## Checking against the real data (`handcheck`)

The brief asks for a check on 20 of the 50 and says plainly that it wants the
number honest rather than high.

Triage judged from metadata. This step does not. It downloads the actual file
and reads the real column names and first rows. The checker is a different
model from the one that made the original judgement and never sees it, and
every verdict is recorded alongside deterministic counts anyone can re-run:
cells containing "Pty Ltd", ABN-shaped values, entity-named columns.

It is still a model reading data, not a person, and the output says so on every
row.

### The numbers

| | |
|---|---|
| Sampled | 20 of 50, random, seed 20260919 |
| File could be read | 14 (70%) |
| Could not be read | 6 (30%) |
| Contained businesses | 12 |
| **Precision on readable files** | **12/14 = 86%** |
| Precision counting unreadable as wrong | 12/20 = 60% |

**We report both.** 86% is what the shortlist gets right about datasets we
could open. 60% is what you get end to end, and it is the number that matters
if you have to onboard a source tomorrow.

### Why six could not be read

| reason | count |
|---|---|
| HTTP 202, portal still building the file | 2 |
| HTTP 403 | 1 |
| HTTP 404, dead link in the catalogue | 1 |
| connection refused | 1 |
| zip over the 8 MB sample cap | 1 |

Roughly 30% of catalogue links do not serve a file on demand. That is a finding
about the catalogue, not a failure of ours, and source selection has to work
around it.

### The one genuine false positive

*ASIC - Banned and Disqualified Persons Dataset*. Its rows are individual
people, not companies. Triage saw "ASIC" and "disqualified" in the metadata and
scored it 1.00.

ASIC publishes a near-identical *Organisations* dataset, also on our shortlist,
which is correct. Metadata alone cannot separate those two. Data can, which is
the argument for this whole step.

## Bugs found in discovery, and what they taught

- **The crawler cached pages by page number.** Raising the target per query
  from 25 to 60 would have read the old 25-record page, seen it was short, and
  stopped — returning 25 and reporting success. Fixed by naming cached pages
  after their start offset and size, so a page is reused only when it is
  exactly the slice being asked for. *Caching that quietly returns a wrong
  answer is worse than no caching.*

- **Publishers type format labels by hand.** The catalogue holds `XLSX`,
  `EXCEL (.XLSX)`, `.XLSX`, `EXCEL (XLSX)` and `XSLX` for one thing, plus
  `ZIP (CSV)` and `ESRI SHAPEFILE - ZIPPED`. Comparing raw strings would have
  handed source selection a fake spread. `src/formats.py` folds 66 raw labels
  into 22 tokens and keeps the raw string beside the clean one. *The clean
  token is for logic. The raw string is evidence, so we never destroy it.*

- **We filtered in our own code instead of on the server.** The first version
  downloaded 600 records' metadata to keep 190 (32%). CKAN will apply
  `fq=res_format:(…)` itself. *Push work to the server where it will take it.*

- **The shortlist picked "the first resource that is not HTML".** ASIC's
  Company Dataset ships `company-dataset-help-file.pdf` first and
  `company_202609.csv` second, so we were checking the help file. The first
  check run reported 25% precision, which was our bug and not the shortlist's.
  *A default that is never exercised looks like a decision.*

- **Format ranking alone was not enough.** The ABN Bulk Extract ships a `.xsd`
  schema and an index CSV listing the other files, both of which beat the real
  zip on format. Anything named schema, readme, help, dictionary, resource list
  or codeset now sorts last whatever its format.

- **The download cache was keyed by dataset id.** When a fix changed which file
  we wanted, the old download was served from cache and the fix looked broken.
  *A cache key must contain everything that decides the content.*

- **A habit, not a bug.** A number that looks wrong usually is. Treat a
  surprising metric as a hypothesis to test before writing it down.

# Part 2a — Source selection

`src/select.py`, `src/fetchfile.py`

No model calls. This is the programmed check the agent depends on.

## Probe

Download the head of every file on the shortlist and record what it really is:
format from the bytes, header row, column names, column count, and whether the
values look like businesses.

Not one file per dataset but several — the easiest one plus an alternative per
other format — because many publishers ship the same records as CSV and XLSX,
and Part 2 needs at least one source that is not a clean CSV.

**95 files across 50 datasets. 59 files (62%) parsed, covering 30 datasets
(60%).** The other 20 datasets (40%) cannot be processed at all today.

## The selection rule

Written down, not chosen by taste, because the brief asks whether the solution
generalises.

1. Walk the shortlist in rank order, one dataset at a time.
2. Skip a dataset whose publisher is already chosen. Six registers from one
   publisher would look diverse and would not be.
3. From that dataset's usable files, take the one in a format we do not have
   yet. If they are all formats we already have, take the best parse.
4. Afterwards, make sure at least one pick is not a plain CSV, and at least one
   is event-shaped rather than a register. Swap the lowest-ranked pick if not.

Rule 4 fired once: *Fair Jobs Code Registers* was swapped for *Historical
Australian Government Contract Notice Data* so the six are not all registers.

## The six

| # | format | grain | cols | publisher | dataset |
|---|---|---|---|---|---|
| 1 | TSV | entity | 15 | ASIC | Company Dataset |
| 2 | ZIP | entity | 20 | WGEA | WGEA Dataset |
| 3 | CSV | entity | 69 | ACNC | Registered Charities |
| 4 | XLSX | entity | 21 | Liquor Control Victoria | Liquor licences by location |
| 5 | XLSX | entity | 5 | Vic Dept of Education | Government Schools ABNs |
| 6 | XLSX | event | 43 | Dept of Finance | Historical Contract Notices |

Six publishers, four formats, both grains, 5/6 (83%) not a plain CSV, column
counts from 5 to 69.

**2/6 (33%) are mislabelled in the catalogue.** Source 1 is declared CSV and is
tab separated. Source 6 is declared CSV and is a spreadsheet. That is what the
agent has to survive.

## Bugs found in source selection, and what they taught

- **A zip must be whole before it opens.** Its index sits at the end of the
  file, so our 3 MB sample cap broke every zip — and every xlsx, since an xlsx
  *is* a zip. Fixed by sniffing the first bytes and refetching in full when
  they say zip. *A sample is not always a smaller version of the thing.*

- **openpyxl judged our files by their names.** Cache files end in `.bin`, so
  it raised `InvalidFileException` on 16 perfectly good spreadsheets. Fixed by
  handing it an open file object instead of a path. *A library's error can be
  about your filename rather than your data.*

- **A code list is not data.** The Finance contract dataset ships `AusTender
  Customised UNSPSC Codeset.xlsx` alongside the contract records, and we picked
  the code list. Now anything named codeset, lookup or reference data sorts
  last. *Publishers put dictionaries next to records and name them similarly.*

- **Spreadsheets do not start at row one.** That same file opens with a title
  banner and puts the real headers on row 2, which our parser read as 16,374
  columns. Now we take the row with the most non-empty cells among the first
  six, and record which row we chose so a reviewer can disagree. The mapping
  config carries `header_row`.

- **A file that parses to one or two columns has not parsed.** Added as a gate:
  usable means at least three columns. It catches wrong delimiters and title
  rows the parser swallowed without complaint. *Silence is not success.*

# Part 2b — Schema inference agent

`src/agent/`, one module per concern, orchestrated by `src/agent/run.py`

## The state file

One per source at `runs/<source_id>/state.json`, written after every step.

```json
{ "source_id": "asic-company-dataset-7b8656f9",
  "source":    { "title": "ASIC - Company Dataset", "download_format": "CSV" },
  "steps": {
    "c1_probe":    { "status": "done", "seconds": 1.84 },
    "c2_profile":  { "status": "done", "seconds": 0.02 },
    "c3_propose":  { "status": "done", "seconds": 10.78,
                     "result": { "model": "gemini-3.7-flash",
                                 "input_tokens": 2696, "output_tokens": 3318,
                                 "usd": 0.0145 } },
    "c4_validate": { "status": "done", "seconds": 0.03 },
    "c5_revise":   { "status": "done", "seconds": 11.01,
                     "result": { "rounds": 1, "still_failing": false } },
    "c6_review":   { "status": "done", "seconds": 0.00 },
    "c7_freeze":   { "status": "done", "seconds": 0 } } }
```

It does three jobs at once.

- **It carries state between steps.** Propose needs what probe and profile
  found, so each step reads the file rather than taking ten arguments.
- **It makes a crash cheap.** A step marked `done` is skipped on a rerun, so a
  failure in validate costs validate and not the download. A failed step
  records the exception and traceback and leaves everything before it intact.
- **It is the cost evidence.** Every `seconds` and `usd` in Part 5 is summed
  from these files, not estimated afterwards.

**Where a run can fail without losing the whole job:**

| step | failure | cost of retry |
|---|---|---|
| Probe | download dies, format unreadable | the download only |
| Profile | a column will not parse | profiled as unknown, run continues |
| Propose | model returns unusable JSON | one call |
| Validate | engine raises on a row | counted as a failure, run continues |
| Revise | no convergence in 2 rounds | flagged for a human, config kept as draft |
| Review | a person rejects | note kept, propose through review rerun with it |
| Freeze | only runs on approval | nothing else can write to `configs/` |

## Step 1 — Probe (`c1_probe`)

Download the file, work out what it really is from its bytes, and pull up to
2,000 records into one flat shape. The output is the `resource` block of a
mapping config: real format, encoding, delimiter, header row, sheet name,
member inside a zip.

3/6 sources are spreadsheets, 1 is tab separated, 1 a CSV, 1 a zip. The probe
turns all of them into the same list of dictionaries, so nothing downstream has
to care which.

The records are then split, and the split is the point:

```
runs/<source_id>/records.jsonl        2,000 records
├── rows    0 – 399     sample     ──>  profile  ──>  what the model sees
└── rows  400 – 1,999   holdout    ──>  validate ──>  what the model is tested on
```

The model never reads either half. It reads the summary the profile builds from
the sample. A mapping that only works on the rows the sample came from is not a
mapping, so validate runs on 800 rows the profile never touched.

## Step 2 — Profile (`c2_profile`)

Per column, over the 400 sample rows:

- **null rate** — how often the column is empty
- **distinct count** — capped at 500
- **max length**
- **examples** — up to 8 real values, never invented
- **all_values** — every distinct value when there are 25 or fewer, so the
  model can write a complete `map_values` rather than guess at an enum
- **looks_like** — the share of values passing the ABN checksum, the ACN
  checksum, matching a postcode, a state, a number, a URL, an email
- **date_formats** — which `strptime` patterns the values match

ASIC's Company Dataset, first 6 columns:

```
Company Name   null=0.00  distinct=400  e.g. LOVINI HOLDINGS PTY LTD
ACN            null=0.00  distinct=167  acn_valid=1.000
Type           null=0.00  distinct=3    all values: APTY, APUB, FNOS
Class          null=0.01  distinct=3    all values: LMGT, LMSG, LMSH
Sub Class      null=0.01  distinct=6    all values: LISN, LIST, PROP, PSTC, ULSN, ULST
Status         null=0.00  distinct=3    all values: DRGD, EXAD, REGD
```

`acn_valid=1.000` is the published checksum run over 400 real values, not a
guess from the column header. That matters three ways: a column name is a claim
and a checksum is proof; it catches the reverse case, where the Victorian
schools file has 9-digit values in a column called `ABN`; and it sets the
baseline validate measures against, so a field filling 60% from a column that
passes 99.8% means the transforms are eating values.

That schools column scores 0.998, not 1.000, and the failing rows stay in the
profile. A real register has bad rows, and a mapping claiming 100% is what the
brief says it marks down.

### Why the profile exists

| source | columns | profile | 400 raw rows | ratio |
|---|---|---|---|---|
| Victorian schools ABNs | 5 | 600 tokens | 12,000 | 20x |
| ASIC Company Dataset | 15 | 1,600 | 46,000 | 29x |
| WGEA | 20 | 2,400 | 93,000 | 39x |
| Victorian liquor licences | 21 | 2,100 | 60,000 | 29x |
| Finance contract notices | 43 | 5,100 | 145,000 | 28x |
| ACNC Registered Charities | 69 | 6,700 | 205,000 | 31x |

**The cost, at $0.75 per million input tokens.** The ACNC source is $0.005 per
attempt as a profile against $0.15 as raw rows — 30x. Across 6 sources at up to
3 attempts each, that is $0.09 against $2.50: 4% of the cost, and the
difference between a $10 budget holding and not.

**And it is better input, not just cheaper.** 400 raw rows show the model 400
examples of the same thing. The profile carries null rates, distinct counts,
the complete enum where one exists, and checksum pass rates. None of that is
visible from staring at rows.

## Step 3 — Propose (`c3_propose`)

The model receives 4 things and never the file: the ontology rendered as field
names with notes and allowed enum values, the closed list of 20 transforms, the
`resource` block from probe, and the column profile. The ASIC prompt is 7,875
characters, about 2,000 tokens.

### In

```
# THE ONTOLOGY you are mapping onto
  entity.legal_name (string) - As registered. Not the trading name.
  entity.abn (string) pattern ^\d{11}$ - Has a checksum — validate it.
  entity.status (enum) one of ['active','deregistered','in_liquidation',…]
  …

# THE TRANSFORMS you may use
  strip(): Remove whitespace from both ends.
  map_values(mapping, default): Look the value up. Use for enums.
  abn_normalize(): Strip to digits, require 11, verify the checksum.
  …

# THE FILE
  { "real_format": "TSV", "delimiter": "\t", "header_row": 0 }

# THE COLUMNS
  - "Company Name"  null_rate=0.0  distinct=400  max_len=59
      examples=["LOVINI HOLDINGS PTY LTD", "MONAKA PTY LTD", …]
  - "ACN"  null_rate=0.0  distinct=167  looks_like={"acn_valid": 1.0}
      examples=["000003958", "000000779", …]
  - "Status"  null_rate=0.0  distinct=3
      all_values=["DRGD", "EXAD", "REGD"]
```

### Out

```json
{ "record_id": { "strategy": "hash_of_fields",
                 "fields": ["ACN", "Company Name"],
                 "note": "ACN alone is not unique because the dataset includes
                          historical name records per company." },
  "source_reliability": 0.98,
  "field_mappings": [
    { "canonical_field": "entity.legal_name", "source_field": "Company Name",
      "transforms": [{ "op": "strip" }, { "op": "collapse_spaces" }],
      "field_confidence": 0.95 },
    { "canonical_field": "entity.acn", "source_field": "ACN",
      "transforms": [{ "op": "acn_normalize" }],
      "field_confidence": 1.0 } ],
  "unfilled_canonical_fields": [
    { "canonical_field": "entity.trading_name",
      "reason": "Source only contains registered corporate legal names, not
                 trading names." } ] }
```

Transform arguments arrive as a JSON string in `args_json`, because a response
schema cannot describe a free-form object and `map_values` takes a dictionary.
We parse it on the way in and drop anything that will not parse. Across all 6
sources, 0 arguments failed.

### What came back

| source | mapped | left alone | cannot fill | cost |
|---|---|---|---|---|
| ASIC Company Dataset | 6 | 9 | 10 | $0.0168 |
| WGEA | 4 | 17 | 12 | $0.0181 |
| ACNC Registered Charities | 10 | 59 | 6 | $0.0226 |
| Victorian liquor licences | 5 | 16 | 11 | $0.0170 |
| Victorian schools ABNs | 2 | 3 | 14 | $0.0084 |
| Finance contract notices | 6 | 35 | 10 | $0.0156 |

Three things in the ASIC draft are worth pointing at.

- **It covered the whole enum.** The profile sent all 3 distinct values of
  `Status`, so the model wrote a complete `map_values` for `REGD`, `DRGD` and
  `EXAD` with a default of `unknown`, rather than guessing at categories it had
  not seen.
- **It used the detected date format**, `%d/%m/%Y`, not a guess.
- **It refused to invent a record id.** It chose `hash_of_fields` over ACN plus
  Company Name and said why: the file holds historical company names, so one
  ACN appears on several rows. It read that out of the distinct counts.

### Refusing is the behaviour we wanted

10/16 ontology fields (63%) are declared unfillable for ASIC, each with a
reason. For `entity.trading_name`: "Dataset contains only registered corporate
legal names, not trading or business names."

The Victorian schools file mapped 2/16 (13%), which is right. It has 5 columns
and 3 of them are a school number, a financial period and a load timestamp.

## Step 4 — Validate (`c4_validate`)

Run the draft config over rows 400–1,199 — 800 rows the profile was not built
from. No model call, which is what makes the next step a correction rather than
a second opinion.

Two things come out.

**Fill rates.** Each mapped field's fill rate beside its source column's. A
field tracking its column is correct; a column at 99% producing a field at 13%
means the transforms are eating values. Comparing against the column rather
than against the model's claimed confidence matters, because a sparse column
can be mapped perfectly.

**Failures**, in 6 classes:

| class | what it means |
|---|---|
| unknown transform | the op is not in the vocabulary |
| transform raised | the chain threw on a real value |
| source column missing | the config names a column the file does not have |
| dropped a value the source had | usually a failed checksum or an unparsed date |
| value not in the enum | `map_values` fell through to its default |
| two mappings write one field | ambiguous, raised before any row is read |

A drop is *not* counted as a failure when a `null_if` in the chain names that
exact value, because then it is a recorded decision rather than an accident.

### What the model is handed

`runs/<source_id>/failure_report.txt`, verbatim:

```
Your config was run over 800 rows that the profile was NOT built from.

FILL RATES. 'column' is how full the source column is, 'field' is how often
your mapping produced a value.
  entity.legal_name       from "Company Name"  column 100% -> field 100%
  entity.legal_name       from "Current Name"  column  58% -> field 100%
  entity.acn              from "ACN"           column 100% -> field 100%
  entity.abn              from "ABN"           column 100% -> field  96%
  entity.status           from "Status"        column 100% -> field 100%

FAILURES:
  entity.legal_name   two mappings write this one canonical field, 100% of rows
      real values that did this: ["Company Name", "Current Name"]
  entity.status       value not in the enum, fell back to default, 2% of rows
      real values that did this: ["SOFF"]
```

Every line of that is measured. `SOFF` is a real ASIC status the 400-row sample
never contained.

## Step 5 — Revise (`c5_revise`)

The model reads that report and returns a corrected config. At most 2 rounds.

### What it fixed

The report above is ASIC's. Both failures in it were fixed in round 1.

**The duplicate.** Propose had written two mappings for `entity.legal_name`,
one from `Company Name` and one from `Current Name`. Validate raised it before
reading a row. Revise merged them into one mapping with the preferred column
first:

```
was:  [Company Name] strip -> collapse_spaces
      [Current Name] strip -> collapse_spaces        two mappings, one field
now:  coalesce["Current Name", "Company Name"] -> strip -> collapse_spaces
```

**The missing enum value.** `SOFF` — strike-off action in progress — appeared
on 2% of the held-out rows and on none of the 400 the profile was built from.
Revise added it:

```
was:  map_values{REGD: active, DRGD: deregistered, EXAD: in_liquidation}
now:  map_values{REGD: active, DRGD: deregistered, EXAD: in_liquidation,
                 SOFF: in_liquidation}
```

7 mappings became 6, 2 failures became 0.

**Worth noting what propose got right without being told.** ASIC writes `0`
where a company has no ABN. The draft already carried
`null_if{values:["0"]} -> abn_normalize`, inferred from the example values in
the profile. Without it, that placeholder would have been dropped by a checksum
quietly failing rather than by a decision anyone could see.

### Where it tried to cheat

On the Finance contract register, `address.postcode` dropped `801`, `97219` and
`OX2 6DP`. The model's fix was to delete `postcode_extract` and keep only
`strip`. Failures went to 0 and the mapping got worse: a US zip code now passed
as an Australian postcode.

**A model optimising for a number will pass the test by deleting the test.**

So "better" is no longer "fewer failures". Ops that *check* a value rather than
reshape it — `abn_normalize`, `acn_normalize`, `postcode_extract`,
`state_normalize`, `parse_date`, `map_values` — are tracked per field, and a
revision that drops one is rejected whatever its numbers say:

```
c5_revise: round 1, failures 1 -> 0, rejected, made it worse
           removed validating transforms: ['address.postcode:postcode_extract']
```

That source went to a human. `97219` is a real value in an Australian
government contract register and somebody has to decide what it means.

### Where it ran out

The Victorian schools file has 9-digit values in a column called `ABN`, such as
`142 547 710`. That is ACN length. 2 rounds did not resolve it, so the source
was flagged rather than forced through.

### The result

| source | failures before | after | rounds | outcome |
|---|---|---|---|---|
| ASIC Company Dataset | 2 | 0 | 1 | fixed |
| WGEA | 0 | 0 | 0 | clean first time |
| ACNC Registered Charities | 0 | 0 | 0 | clean first time |
| Victorian liquor licences | 1 | 0 | 1 | fixed |
| Victorian schools ABNs | 1 | 1 | 2 | flagged for a human |
| Finance contract notices | 1 | 1 | 1 | revision rejected, flagged |

**4/6 sources (67%) onboarded without a person. 2 stopped and said why.**

## Step 6 — Human review (`c6_review`)

The agent stops and writes one card per source to
`runs/<source_id>/review_card.md`. It covers four things in a deliberate order:
what the agent decided and why, every field it mapped with the source column's
fill rate beside the field's and the transform chain below it, everything it
refused with a reason, and what broke on the 800 rows the model never saw. Then
5 real records, raw beside canonical. Refusals sit above failures because that
is where a reviewer is most likely to disagree — a failure is a fact, a refusal
is a judgement. Nothing on the card is the model describing its own work; every
number came from running the config. A reviewer approves or rejects from the
command line, and a rejection carries their note back into the next propose
prompt.

## Step 7 — Freeze (`c7_freeze`)

A config reaches `configs/` because a person said yes, never because a model
said it was fine. Approving stamps the decision into the config itself:

```json
"generated_by": { "agent_version": "0.1",
                  "model": "gemini-3.7-flash",
                  "revisions": 1,
                  "approved_by": "dao.lq",
                  "approved_at": "2026-09-20T09:14:02" },
"validation":   { "rows_tested": 800, "rows_emitted": 800, "failures": [] }
```

So "approved anyway" stays visible: the 2 configs with an unresolved failure
carry it here, with the reviewer's note.

That `generated_by` block is the answer to the brief's hardest question. When
the matching model improves 6 months from now, you can select every config a
given model version produced.

## Bugs found in the agent, and what they taught

- **A deliberate `null_if` was counted as a failure.** Once the model added
  one, the engine still reported "dropped a value the source had", because the
  source did have a value — so the fix looked like it had not worked. The
  engine now checks whether a `null_if` names that exact value and treats the
  drop as a recorded decision. *A rule that cannot tell a decision from an
  accident sends a model round in circles.*

- **Rejecting did not undo approving.** A refused config stayed in `configs/`
  and kept shipping. A rejection now deletes the frozen file. *An approval you
  can withdraw only in theory is not a gate.*

- **A reviewer's note went nowhere.** It was recorded and then ignored. It is
  now carried into the next propose prompt under a heading saying a human
  rejected the earlier attempt and their notes outrank the model's judgement,
  and notes accumulate across rejections. *Asking for a reason and discarding
  it is worse than not asking.*

- **Two mappings can write one canonical field.** Asked to also map `Current
  Name`, the model mapped both it and `Company Name` to `entity.legal_name`.
  The engine applies mappings in order, so the later one won when it had a
  value — producing the right answer by accident. The review card's fill-rate
  table, keyed by canonical field, showed nonsense as a result. A duplicate is
  now a failure validate raises before reading a single row, and revise is told
  the answer is `coalesce`. Round 1 took 3 failures to 0:

  ```
  entity.legal_name   coalesce["Current Name", "Company Name"] -> strip -> collapse_spaces
  ```

  *The right answer for the wrong reason is worse than a wrong answer, because
  nothing tells you it happened.*

- **The human gate found that one, not us.** The reviewer rejected ASIC with
  "also map Current Name to entity.legal_name", and everything above followed.
  It is the clearest evidence in the project that the review step does
  something.

---

# Part 2c — Canonical extraction

`src/engine.py`, `src/extract.py`

6 configs, 1 engine. The engine knows nothing about any source: it reads a
config, opens the file that config describes, applies the transform chains, and
writes canonical observations. That is what makes 6 configs different from 6
scripts.

**The same code runs twice.** Inside the agent's validate step, over held-out
rows, to find what a draft gets wrong. Here, over a full sample, to produce the
deliverable. So what the agent was checked against is exactly what ships.

**The file is opened from the config's `resource` block**, never from anything
the agent left behind, which proves a config is sufficient on its own.

## The run

| source | observations | with ABN or ACN | share |
|---|---|---|---|
| ASIC Company Dataset | 1,000 | 1,000 | 100% |
| WGEA | 1,000 | 1,000 | 100% |
| Victorian schools ABNs | 1,000 | 993 | 99% |
| Finance contract notices | 1,000 | 992 | 99% |
| ACNC Registered Charities | 1,000 | 982 | 98% |
| Victorian liquor licences | 1,000 | 0 | 0% |
| **total** | **6,000** | **4,967** | **83%** |

**6,000 observations in 1.3 seconds, 0 model calls.** Per-record cost is $0.00
and that is measured rather than claimed: `outputs/extraction_report.json`
records the call count for the whole stage.

The liquor register contributes 1,000 businesses with no identifier at all,
known only by name and address. That is the hard case for Part 3, and it is
useful to have one.

## What an observation looks like

```json
{ "source_id":         "asic-company-dataset-7b8656f9",
  "source_record_id":  "0eaa924bae48e834",
  "observed_at":       "2026-09-14T15:25:32",
  "ingested_at":       "2026-09-19T16:35:53+00:00",
  "valid_from":        "1990-01-08",
  "licence":           "Creative Commons Attribution 3.0 Australia",
  "extractor_version": "engine0.1/config1.0",
  "confidence": {
    "source_reliability": 0.98,
    "field_confidence": { "entity.legal_name": 0.95, "entity.acn": 1.0, … } },
  "entity": { "legal_name": "MONAKA PTY LTD", "acn": "000000019",
              "abn": "89000000019", "entity_type": "company",
              "status": "active", "date_registered": "1990-01-08" } }
```

Three things that envelope is doing.

- **`observed_at` and `ingested_at` are different dates.** When the source said
  it, and when we read it. The ontology insists on both.
- **The two confidences stay apart.** `source_reliability` is one number for
  the publisher; `field_confidence` is one per field. They are never blended.
- **Absent, not blank.** Fields no source supplied do not appear, and the
  `entity` and `address` blocks only exist when they hold something.

## The bug this stage exposed

On 4/6 sources (67%), `observed_at` equalled `ingested_at` on every row. The
ontology is explicit that these are different and both matter.

The cause was ours, not the agent's. Those 4 sources carry no date column, so
the config falls back to a constant — and we never populated that constant. The
dataset's `metadata_modified` was sitting in the crawl output and was never
carried through.

We fixed it by carrying it through, then re-running from validate onwards and
asking the reviewer to approve again. The propose drafts were reused, so the
mappings came out byte-identical — the same 6, 4, 10 and 5 fields, still 0 open
failures. Only the envelope date moved.

```
ASIC        2026-09-14    WGEA        2026-01-09
ACNC        2026-09-13    Vic liquor  2026-09-14
```

WGEA's is 8 months older than the rest, which is real signal: when two sources
disagree about a company in Part 4, we know which one spoke more recently.

**The process point.** Those configs were already approved. Rather than editing
approved files in place, we regenerated and went back through the gate. *An
approval that can be silently amended afterwards is not an approval.*

---

# Part 3 — Entity identification

`src/match.py`, `src/names.py`, `src/relationships.py`, `src/precision.py`

No model calls anywhere in the matcher. Every decision is a rule a person can
read and argue with.

## The data model: a pairwise link table

The assignment leaves this open and says the choice tells them more than the
implementation. Ours is a **pairwise link table as the source of truth, with
clusters derived from it**.

| | pairwise links | canonical key only | cluster / component | graph database |
|---|---|---|---|---|
| Records why two records matched | yes | no | no | yes, on edges |
| Undo one bad decision | delete one row | recompute everything | recompute the cluster | delete one edge |
| Re-score after a model upgrade | per link, with a diff | all or nothing | all or nothing | per edge |
| Place to record a refusal | yes | none | none | yes |
| "Who is this business?" | needs a derivation step | instant | instant | traversal |
| Cost at 15M companies | O(pairs) | O(records) | O(records) | O(pairs) |

**Why pairwise wins here.** Part 5 asks what you do when the matching model
improves and millions of links already exist. A link row carrying its method,
matcher version and evidence can be re-scored on its own: you diff old against
new, promote what improved, quarantine what flipped. A bare canonical key
throws away *why*, so the only option left is recomputing everything.

**And a wrong link is worse than no link.** Pairwise gives a refusal somewhere
to live. A key assignment has nowhere to say "I nearly merged these and chose
not to".

**Why not blind clustering.** Transitive closure across weak edges causes
catastrophic merges: A matches B, B matches C, and now A and C are one company
on no evidence. Components are built only from links at or above threshold, and
`entities.jsonl` is a view — rebuildable from the links, which are the record.

**The honest cost.** Links grow with pairs, not records, so at 15M companies
you cannot compare everything to everything. Blocking is what makes it
tractable and it is the component that breaks first at scale.

### What a row looks like

One row of `outputs/links.jsonl`, unedited. Natural Areas Pty Ltd appears in
both the charity register and the ASIC company register:

```json
{ "source_a_record": { "source_id": "acnc-registered-charities-b050b242",
                       "source_record_id": "323bbe0105d4b4f6" },
  "source_b_record": { "source_id": "asic-company-dataset-7b8656f9",
                       "source_record_id": "cc13ba86dd3c5455" },
  "canonical_entity_key": "abn:13000864077",
  "confidence": 0.99,
  "evidence": { "method": "abn_exact",
                "matched_on": { "abn": "13000864077" },
                "agreement": { "legal_name_a": "NATURAL AREAS PTY LTD",
                               "legal_name_b": "NATURAL AREAS PTY LTD" },
                "conflicts": [],
                "source_reliability_a": 0.95,
                "source_reliability_b": 0.98 },
  "matcher_version": "matcher0.1",
  "threshold_used": 0.7,
  "human_verdict": null }
```

Read as a table, 1,609 rows of it:

| record A | record B | entity key | conf | method |
|---|---|---|---|---|
| acnc / 323bbe01… | asic / cc13ba86… | `abn:13000864077` | 0.99 | abn_exact |
| finance / CN3444680-A5 | wgea / 6f56ddc1… | `abn:90127406295` | 0.99 | abn_exact |
| acnc / 047bf993… | liquor / 31100099 | `nk:4a2f8c…` | 0.75 | name_geo |

**The two records are pointers, never copies.** A link says nothing about the
businesses themselves, so re-extracting a source cannot break it. Copying the
names in would freeze a snapshot that quietly goes stale.

**The alternative it replaces** is writing `entity_id: 4471` onto each record
and discarding the rest. That stores the conclusion and loses the reason, which
is what turns a matcher upgrade into a rebuild instead of a diff.

**`entities.jsonl` is built by grouping these rows** on `canonical_entity_key`.
Delete it and it rebuilds in a second. Delete the links and nothing can.

## The canonical key carries its own provenance

```
abn:51824753556     government identifier, checksum passed
acn:004085616       government identifier, checksum passed
nk:<sha1>           normalised name plus geography. provisional.
```

The prefix is part of the key on purpose. A consumer can see at a glance
whether an entity was identified by a registered number or by a name that
looked the same. Those are different claims and should not be
indistinguishable downstream.

## Matching methods

| method | what it means | link_confidence |
|---|---|---|
| `abn_exact` | Both records carry an ABN, both pass the checksum, and they are the same eleven digits. A government identifier for one legal entity. | 0.99 |
| `acn_exact` | The same, for a nine-digit ACN. | 0.99 |
| `abn_acn_derived` | One record has an ABN, the other an ACN, and the ABN's last nine digits are that ACN. A bridge, not an identifier match. | 0.80 |
| `name_geo` | Legal or trading names match after normalising, **and** the postcode or state agrees. Two signals, neither of them an identifier. | 0.75 |
| `name_only` | Names match and nothing else confirms it. Below threshold, so these become refusals. | 0.45 |

Normalising a name means uppercasing it, stripping punctuation and removing the
legal form, so `Wilson Security Pty Ltd` and `WILSON SECURITY PTY. LTD.` are
the same string. The legal form is kept separately, because an incorporated
association and a company with the same stem are different bodies.

`abn_acn_derived` exists because the ontology warns about it: "The last 9
digits of an ABN are often the ACN, but not always." **It produced zero links
on these six sources**, because ASIC is the only source carrying an ACN and it
carries an ABN too, so `abn_exact` always wins first. Built and tested,
currently unused. Saying so is better than leaving it looking like a working
feature.

`link_confidence` is the only number in the score. Source reliability and the
per-field confidences are recorded inside the evidence as inputs and never
folded in.

## Sampling was the real problem

The first run over the Part 2 deliverable found **17** cross-source ABN matches
in total. Not enough to check 50 links.

The cause was sampling, not matching. Each 1,000-row slice is the head of a
different file: ASIC's is sorted by ACN, so it is the oldest companies in
Australia, while WGEA's is a different population entirely.

The engine costs nothing to run, so Part 3 runs on a deeper pull of 25,000
records per source — **126,540 observations in 6.5 seconds, $0.00** — written
to `outputs/observations_deep/` so the Part 2 deliverable stays at the 1,000
per source the assignment specifies.

**Cross-source ABN matches went from 17 to 1,541.**

## The result

| | |
|---|---|
| Links proposed at threshold 0.70 | 1,609 |
| Refused | 1,025 |
| Entities in more than one source | 1,434 |
| Runtime | 1.1s, zero model calls |

In four sources: 3 businesses. In three: 83. In two: 1,348.

The Victorian liquor register, which carries no identifier at all, links 63
times to the ACNC and 6 times to the contract register purely on name and
postcode. That is the tier that earns the 0.75 confidence.

1,541 of the links are `abn_exact` and 68 are `name_geo`.

## Where we drew the line

The refusal queue is a deliverable, not a list of failures.

| reason | count |
|---|---|
| confidence below the threshold | 762 |
| names agree but the ABNs differ | 217 |
| different legal forms | 28 |
| one side reads as a person's name | 11 |
| one side is a related body, the other is not | 5 |
| names agree but the states differ | 2 |

```
KINROSS WOLAROI SCHOOL | KINROSS WOLAROI SCHOOL
  refused: names agree but abn: 54645079607 vs 87938495176
```

Same name, two registered entities. A name-only matcher merges these.

Every refusal keeps `would_have_been`, so a looser threshold can be evaluated
without rerunning anything.

## Relationships between businesses

We scanned every column of all six sources for parent, group, holding, owner,
subsidiary and trustee. **Only WGEA asserts a relationship between two
businesses**, via `corporate_group_name`, and on 36% of rows it differs from
the employer name.

| outcome | count |
|---|---|
| parent assertions found | 12,421 |
| both ends resolved to a canonical key | 4,193 |
| parent not found in our six sources | 6,880 |
| parent name too generic to resolve | 1,243 |
| **parent name ambiguous, two businesses share it** | **105** |

```
24 children   SONIC HEALTHCARE
21 children   COMFORTDELGRO CORPORATION AUSTRALIA
19 children   WESFARMERS
```

476 groups have more than one known child. 353 of those children and 101 of
the parents are themselves multi-source entities, so `relationships.jsonl` and
`entities.jsonl` join on `canonical_entity_key`.

**Three rules govern that file.**

A relationship is never a sameness link. A subsidiary is not its parent, and
merging them is the failure the assignment warns about precisely because the
names look alike.

We record only what a source asserted. `SALTER BROTHERS (CLOVELLY) PTY LTD`
looks like a child of `SALTER BROTHERS HOSPITALITY`, and probably is. But
`NICHOLAS FAMILY TRUST` as parent of `STEEKIM NICHOLAS FAMILY TRUST` is the
same shape and far less certain. We infer nothing from name similarity.

Both ends resolve where we can and stay raw where we cannot. Those 105
ambiguous parents stay unresolved: picking one of two registered businesses
would be a guess, and a wrong parent is worse than no parent.

**`same_group_sibling` is derived and deliberately unused.** 4,674 sibling
pairs are recorded with `"used_in_matching": false` on every row. Knowing that
six hospitals share a parent is a positive signal that they are *different*
companies, so it could lower link confidence. We chose to record it and not
wire it in, and the flag on each row is what a future change would have to
flip.

**What we left alone, and why.** The Finance register's `Parent Contract ID`
relates two contracts, not two businesses. ASIC's former-versus-current name
and the liquor register's licensee-versus-trading-as are one business under
two names, not two businesses; both already feed matching through `coalesce`
and the trading-name comparison.

## Measured precision

Three samples, because one random 50 would not tell you much.

| sample | threshold | checked | correct | wrong | unsure | precision |
|---|---|---|---|---|---|---|
| headline, random | 0.70 | 50 | 50 | 0 | 0 | **100%** |
| weak tier only | 0.75 | 50 | 45 | 2 | 3 | **90%** |
| below threshold | 0.45 | 50 | 49 | 1 | 0 | **98%** |

| method | correct |
|---|---|
| `abn_exact` | 49/49 = 100% |
| `name_geo` | 46/51 = 90% |
| `name_only` | 49/50 = 98% |

**A random 50 at threshold 0.70 is almost all `abn_exact`**, which is two
checksum-valid identical numbers. 100% is expected and says nothing about the
matcher's judgement. The weak tier is where errors live, and a random sample
would have contained about two of them.

**The reviewer sits outside the pipeline.** `prepare` writes the 150 pairs,
a reviewer answers into `precision_verdicts.json`, `merge` reports. The
reviewer never sees our method, our confidence or our key: those fields are
prefixed with an underscore and the question says to ignore them. `reviewed_by`
is recorded in the output, so the number always carries who produced it.

The matcher is rules and the extraction was Gemini, so the reviewer is a
different model family again. Letting one model family grade its own work is
not a check.

## The pattern that caused most of the errors

The first measurement put the weak tier at 88%, and four of the five errors
were the same shape:

```
Mannix College Foundation           | MANNIX COLLEGE
William Angliss Institute Found...  | WILLIAM ANGLISS COLLEGE
Venus Bay Community Centre Inc      | VENUS BAY COMMUNITY CENTRE COMMITTEE
Burwood Rsl Sub-Branch              | 11 HYSLOP STREET LTD
```

**A body attached to an organisation is not that organisation.** A foundation
is not its college. A committee of management is not the association it
manages. They share an address, a postcode and most of a name, which is exactly
what a name-based matcher falls for.

It survived the legal-form check because "Foundation" is part of the name, not
a suffix like PTY LTD.

The fix is a marker list — FOUNDATION, COMMITTEE, AUXILIARY, FRIENDS, ALUMNI,
SUB BRANCH, BRANCH, GUILD, TRUSTEE and a few more — **applied
asymmetrically**: refuse only when one side carries a marker and the other does
not. Two foundations with the same name are still plausibly the same
foundation. It is checked on trading names as well, because the marker can sit
there.

Three of the five errors became refusals. The weak tier went from 88% to 90%,
and more usefully the remaining errors are now different in kind.

## What the reviewer refused to decide

Three "cannot_tell" verdicts, all the same shape: a venue or hotel brand on one
side and its corporate owner on the other.

```
Pullman Melbourne Albert Park | Ascendas Hotel Investment Company Pty Ltd
Aitken Hill                   | Zhong Ao Zhi Hong Investment Holding Pty Ltd
```

Only the trading name links them and there is no ABN to confirm it. Refusing to
guess is the right answer, and it is a reminder that a trading-name match
between a brand and a holding company is a weaker claim than it looks.

## On loosening the threshold

98% at 0.45 on this sample, which looks like free recall. **We are not acting
on it**, and the reason belongs in the write-up: those pairs are clean because
the rule-based refusals had already removed the obvious contradictions. A
production loosening would need the 217 ABN-conflict refusals re-examined
first, which is a different experiment.


---

# Part 4 — Company profiles

`src/profile.py`

One record per business, merging what all six sources say. No model calls.
This part is arithmetic over data we already have.

## Which 50

Ranked by how many sources an entity appears in, then by how many observations
back it. A profile assembled from four disagreeing sources is worth more than
one built from two identical rows.

That gave 3 entities in four sources and 47 in three.

**The consequence is worth stating.** The rule favours the big national
sources, so neither Victorian file appears in the 50 at all. A selection
spread across all six sources would produce a more flattering
source-impact table and a less representative set of profiles. We kept the
rule that picks the best-evidenced entities and reported what it costs. The
alternative is noted for the write-up.

## Collecting candidates

Every observation whose record resolves to the entity's key, including the
within-source duplicates the matcher collapsed to a representative. A company
with 40 contract rows has 40 chances to state its address.

Values are compared on a normalised form and kept raw. `Wilson Security Pty
Ltd` and `WILSON SECURITY PTY. LTD.` are agreement, not conflict. Counting
them as a conflict would manufacture disagreement that is not there.

## Two policies, because the right answer differs by field

| policy | fields | why |
|---|---|---|
| `recency_wins` | legal_name, trading_name, status, website, entity_type, all address fields | facts that change. A company that moved has a new address, so the newest assertion is right |
| `authority_wins` | abn, acn, nzbn, date_registered, industry_code | registry facts that do not change. Disagreement means one source is wrong, so the more reliable publisher wins |

Each policy is a ladder and the profile records which rung decided it.
`recency_wins` asks in order: is one claim still current while the other has
expired, then which was stated more recently, then which source is more
reliable, then how many sources agreed. `authority_wins` starts with
reliability instead.

## A different kind of confidence, named as such

The ontology's `field_confidence` means "we parsed this right". A profile needs
"this is the right value for this business", which is a different question. We
call it `value_confidence` and keep its inputs visible beside it — sources
agreeing, sources disagreeing, the best contributing parse confidence, and the
winning source's reliability — rather than blending them into one opaque
number.

Unanimous agreement across three sources is not the same claim as one source
speaking alone, and the profile has to show which it was.

## What it produced

| | |
|---|---|
| Profiles | 50 |
| Mean fields filled | 11.6 of 15 |
| Fields with a conflict | 147 |

| field | filled | conflicts |
|---|---|---|
| entity.legal_name | 50 | 31 |
| entity.abn | 50 | 0 |
| entity.industry_code | 50 | 0 |
| address.locality | 50 | 31 |
| address.postcode | 50 | 29 |
| address.state | 50 | 20 |
| address.full | 44 | 28 |
| entity.acn | 39 | 0 |
| entity.status | 39 | 0 |
| entity.website | 18 | 0 |

Identifiers never conflict, which is the point of using them as the key. Names
and addresses conflict constantly.

## Showing the working

Fujitsu Australia, ABN 19001011427, assembled from 74 observations across three
sources:

```
entity.legal_name = "FUJITSU AUSTRALIA LIMITED"    value_confidence 0.931
  3 sources agreed, 2 distinct values
  decided_by: the losing claim had expired
  lost: "FUJITSU"  (contract CN3619643, 2019-08-13)
```

**"The losing claim had expired" is `valid_to` earning its place.** That field
was nearly shipped empty, and here it is deciding which of two names is
current.

## The address finding, and a known limitation

`address.full` for Fujitsu has **eight distinct values** from a single source:
Barton ACT, Macquarie Park NSW, Cheltenham VIC, North Ryde NSW, a GPO box in
Canberra, and more.

That is not a merge error. It is what a contract register is: each row records
the supplier address for that contract, so a national company has many.

Its confidence reflects it — **0.765, against 0.931 for the name**. One source,
eight candidates. This is exactly the case per-field confidence exists for.

**The limitation is the schema, not the data.** The ontology carries one
address per entity. A real company has a registered office, a principal place
of business and a site per contract, and the assignment's ontology subset does
not model that. We pick the most recent and list the rest rather than pretend
there is one answer. Carried to the write-up as a future improvement.

## If we deleted one source

Every profile rebuilt six times, once with each source removed.

| source removed | profiles gone | lose a field | a value moves |
|---|---|---|---|
| WGEA | 1 | **49** | 0 |
| Finance contract notices | 1 | **43** | 0 |
| ASIC Company Dataset | 0 | 39 | **8** |
| ACNC Registered Charities | 0 | 18 | **7** |
| Victorian schools ABNs | 0 | 0 | 0 |
| Victorian liquor licences | 0 | 0 | 0 |

**WGEA is load-bearing for coverage.** Removing it costs a field on 49 of 50
profiles, because it is the only source of `industry_code`.

**ASIC is the one that changes answers, not just coverage.** Removing it moves
a winning value on 8 profiles: it is the sole source of `entity_type`,
`status` and `acn`.

**The two Victorian sources carry no weight in these 50.** They are real,
correctly mapped, and contribute nothing here. Saying so is better than
implying six sources all pull equally.

Written to `outputs/company_profiles.jsonl` and
`outputs/profile_source_impact.json`.


---

# Running it: reproducible or live, and the honest limit

The assessment list asks for engineering that "runs, is legible, and is
reproducible". Those pull against each other here, so there are two paths.

## Two ways to start Part 2

```
python -m src.select pick --pinned     the exact six this submission used
python -m src.select pick              discover six from the live catalogue
```

**Pinned** reads `config/pinned_sources.json`, skips discovery entirely, and
goes straight to the agent in about ten seconds. It checks every pinned link
serves a file before anything spends money, and names any that has rotted
rather than quietly dropping a source. All six answered HTTP 200 when this was
written.

**Discovered** is the system doing its actual job: crawl, triage, shortlist,
probe, select. It takes about nine minutes, most of it downloading 104 files
in the probe step.

## Why both exist

data.gov.au is live. A clean-checkout run on a different day crawled 366
datasets instead of 376 and picked a **completely different six**:

```
Liquor licence premises list       Liquor & Gaming NSW        CSV
Bar, tavern, pub patron capacity   City of Melbourne          JSON, 3 columns
Fair Jobs Code Registers           Vic Dept of Jobs           CSV
Premises list as of 1 Nov 2020     Data.NSW                   CSV
```

**The agent mapped all six without a line of code from anyone.** Five needed at
most one revision round. The sixth hit the same postcode problem and was
flagged for a human. Three of the six had the wrong format in the catalogue: a
declared CSV that is TSV, another that is JSON, another that is a spreadsheet.

That is the assignment's own test — "if we handed your submission a seventh
dataset tomorrow, could it produce a mapping config without you writing
anything?" — answered on six at once, by accident, because the catalogue moved.

But it is useless for reproducing a result, which is why the pinned file
exists.

## What the pinned path does and does not fix

Run pinned in a directory that has never seen these six, and the agent
reproduces the submission closely: field counts match on five of six, and both
problem sources fail in exactly the same way. Schools still cannot resolve its
9-digit ABNs after two rounds. Finance still has its revision rejected for
deleting `postcode_extract`. The guards are stable, not lucky.

The sixth differs. ACNC came out with 10 mapped fields instead of 9, because
the model found `address.full` that time and not the other.

**The sources are pinned. The mapping the model proposes is not.** Same prompt,
same profile, different answer. That is a property of using a model, not a bug,
and it is exactly why the validate step exists: whatever is proposed gets run
against held-out rows and either survives or gets corrected.

Where we control randomness we pin it. The hand-check sample and the precision
sample both use fixed seeds, so those numbers reproduce exactly.

## End to end from a clean checkout

| step | result | time |
|---|---|---|
| crawl, score, triage, shortlist | 366 datasets to 50 | 83s |
| check against real data | 14/15 readable correct | 110s |
| probe 104 files | 61 usable across 31 datasets | 387s |
| pick six | 5 publishers, 4 formats | instant |
| agent on six unseen sources | six configs produced | 137s |
| approve x6 | human gate | — |
| extract, match, relationships, profiles | 100,902 observations | 16s |

**About 13 minutes, not the "under 10" the assignment asks for.** The probe
dominates. Lowering `shortlist_size` in `config/settings.yaml` shortens it, and
the pinned path skips it altogether.

**The run has two halves with a person in the middle.** Six configs need
approving and the precision check needs an outside reviewer. Both gates were
asked for, so the README says so rather than implying one command does
everything.

## A bug the clean run found

`src/relationships.py` hard-coded the WGEA source id. The moment WGEA was not
among the six it raised `FileNotFoundError`, and an entire Part 3 deliverable
would have been missing.

It now discovers its own group column. It scans every approved config for a
column whose name suggests a link to a different business — group, parent,
holding, ultimate, controlling, subsidiary, owner — and requires that column to
actually disagree with the entity's own name on at least 5% of rows. A column
that always equals the employer name is not a parent.

The filter is deliberately narrow. `contract`, `invoice`, `order`, `ethnic`,
`anzsic` and anything ending in `id` are excluded, so `Parent Contract ID`
cannot be mistaken for a corporate parent.

On our six it still finds WGEA's `corporate_group_name` and the same 12,421
assertions. On the clean-room six it finds nothing, writes an empty file and
exits cleanly. **An absent relationship is a finding about those sources, not
a failure of the code.**


# Numbers

## Discovery

| measure | value |
|---|---|
| Datasets in the catalogue | 141,297 |
| With a machine-readable file | 30,079 |
| HTTP calls in the full crawl | 24 |
| Records crawled | 519, 376 unique |
| Found by dataset text only | 313 |
| Found by file name only | 45 |
| Found by both channels | 18 |
| Yearly editions folded | 54 |
| Distinct sources | 322 |
| Judged by the model | 120 |
| Shortlist | 50 |
| Shortlist precision, readable files | 12/14 = 86% |
| Shortlist precision, end to end | 12/20 = 60% |
| Wall clock, crawl and score | about 43 seconds |

## Onboarding one source

**Interim. These figures are regenerated with `python -m src.report` once
everything has run, and the write-up quotes that run, not this table.**

Measured from `runs/*/state.json` and `runs/llm_calls.jsonl`, both written as
the pipeline ran.

| source | steps | model calls | tokens in+out | seconds | usd | revisions |
|---|---|---|---|---|---|---|
| ACNC Registered Charities | 7 | 1 | 5,253 + 4,986 | 16.0 | 0.0226 | 0 |
| ASIC Company Dataset | 7 | 8 | 22,186 + 23,282 | 20.9 | 0.1039 | 1 |
| Finance contract notices | 7 | 3 | 12,296 + 9,915 | 28.5 | 0.0464 | 1 |
| Victorian schools ABNs | 7 | 5 | 10,141 + 8,992 | 21.6 | 0.0413 | 2 |
| Victorian liquor licences | 7 | 5 | 14,026 + 12,872 | 27.1 | 0.0588 | 1 |
| WGEA | 7 | 1 | 2,808 + 4,256 | 24.3 | 0.0181 | 0 |

**Mean cost to onboard one source: $0.0485. Median wall clock: 24.3 seconds.**

ASIC cost twice the mean because the reviewer rejected it and the whole
propose-to-review cycle ran again. That is the real cost of onboarding including rework, and
it is the number worth quoting.

## Linking and profiles

| measure | value |
|---|---|
| Observations for Part 3, deeper pull | 126,540 |
| With a validated ABN or ACN | 100,024 |
| Cross-source ABN matches | 1,541 |
| Links proposed at threshold 0.70 | 1,609 |
| By `abn_exact` / `name_geo` | 1,541 / 68 |
| Refused | 1,025 |
| Entities in more than one source | 1,434 |
| In four sources / three / two | 3 / 83 / 1,348 |
| Link precision, random 50 at 0.70 | 50/50 = 100% |
| Link precision, weak tier only | 45/50 = 90% |
| Link precision, below threshold at 0.45 | 49/50 = 98% |
| Parent-child assertions | 12,421 |
| Both ends resolved to a key | 4,193 |
| Sibling pairs recorded, unused | 4,674 |
| Company profiles | 50 |
| Mean fields filled per profile | 11.6 of 15 |
| Fields with a recorded conflict | 147 |

Matching, relationships and profiles all run in about three seconds combined
and make **zero model calls**.

## Cost, split the way Part 5 asks

| | |
|---|---|
| Part 1, discovery and triage | $0.151 |
| Part 2, onboarding six sources | $0.291 |
| Extraction, per record | $0.00 |
| **Everything so far** | **interim, see `python -m src.report`** |

The per-record cost is zero because the engine makes no model calls. All spend
is one-time, at config-generation time. That holds whatever the token price
does.

---

# Known gaps and deviations

Listed because the assignment marks down claims of full coverage.

**The ontology carries one address per entity, and a real company has
several.** A registered office, a principal place of business, and a site per
contract. We pick the most recent and list every losing value with its date
rather than pretend there is one answer. Modelling addresses as a typed list is
a schema change, not a bug fix.

**The 50 profiles were chosen by source count**, which favours the big national
sources and leaves both Victorian files contributing nothing. A selection
spread across all six would read better and represent the data worse.

**A rerun is reproducible in its sources, not in its mappings.** The pinned
file fixes which six datasets are used. The model's proposal still varies run
to run, so a field count can move by one. The validate step is what makes that
safe rather than alarming.

**A full discovered run takes about 13 minutes, not under 10.** The probe step
downloads 104 files. The pinned path avoids it.

**`derivation_level` is in the config schema but not emitted on observations.**
The ontology marks it optional.

**Triage confidence does not discriminate.** 52 of 120 datasets scored 0.90 or
above. `record_grain` is doing the real work.

**The hand-check is a model, not a person.** It reads real data rather than
descriptions, it is a different model from the one that made the judgment, and
every verdict has deterministic evidence beside it. It is still not a human.

**Twenty of the fifty shortlisted datasets cannot be downloaded today.** HTTP
202, 403, 404 and dead links. We report precision both ways rather than quietly
excluding them.

**Two of the six configs were approved with an open failure**, each with a note
recorded in the config: 0.9% of the Victorian schools ABN column holds 9-digit
values we drop rather than guess, and the Finance register contains
international suppliers whose postcodes we drop because the transform list
cannot condition on country.

---

# Four things to say out loud

**The crawler knows what it is looking for, and we measured what that is
worth.** Twelve search terms chosen against the ontology's fields, on two
channels. Run `python -m src.catalogue baseline` then
`python -m src.baseline_report`: the best dataset an unaimed `q=*:*` crawl
returns scores 0.15 and is a fibre-optic declaration for Mays Hill NSW. Ours
returns the national business register.

**We push work to the server where it will take it.** CKAN filters by file
format before sending anything. That turned a crawl of 600 records to keep 190
into a crawl where every record is a candidate.

**A model optimising for a number will pass the test by deleting the test.**
Given a failing postcode check, it removed the check. The guard against that is
now part of the loop, and it is the reason the revision step can be trusted at
all.

**The human gate found the thing we missed.** One rejection, "also map Current
Name to entity.legal_name", exposed a duplicate-mapping bug in the engine, a
rejection that did not unfreeze, and a reviewer note that went nowhere. None of
those would have surfaced from the agent running cleanly.
