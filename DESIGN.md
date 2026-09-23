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

- `gemini-3.5-flash` costs $1.50 in and $9.00 out. `gemini-3.7-flash` costs
  $0.75 and $3.75, under a promotion ending 31 December 2026. The newer model
  is currently the cheaper one. Picking by version number would have cost
  twice as much for a worse model.
- Output bills four to five times higher than input, and thinking tokens count
  as output. Mapping configs are output, so we keep them compact and keep
  thinking off by default.

Prices live in `config/settings.yaml`, so every call is priced from a table
rather than estimated later. The promotion end date is recorded so the cost
figure still reads correctly next year.

**The promotion is not the real cost story.** The extractor makes zero model
calls, so the per-record cost is zero whatever the token price does.

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

## Search on purpose (`catalogue fetch`)

data.gov.au holds 141,297 datasets. Fewer than one in four has a file a machine
can parse.

**Every record comes from a deliberate search.** An engineer who knows the
domain picks the terms, chosen against the ontology's own fields: `abn`,
`company register`, `licensed contractors`, `liquor licences`, `procurement
contracts`, and eight more.

### Two channels, because they find different things

**Channel 1, `package_search`.** Free text over a dataset's title, description
and tags. CKAN keeps those in a Solr full-text index. One call per term:

```
GET /api/3/action/package_search
    ?q=liquor licences
    &fq=res_format:(CSV OR TSV OR XLSX OR XLS OR JSON OR GEOJSON OR XML OR ZIP)
    &rows=60&start=0
```

`q` is the search. `fq` is a filter CKAN hands to Solr, which applies it before
building the response, so a PDF-only dataset is never serialised and never
counted against our page size.

The filter itself lives in `config/settings.yaml`:

```yaml
format_filter: "res_format:(CSV OR TSV OR XLSX OR XLS OR JSON OR GEOJSON OR XML OR ZIP)"
```

and is passed on every call in `src/catalogue.py`:

```python
total += _collect(
    base_url, folder, label,
    lambda start, rows, q=query: {"q": q, "fq": fmt, "rows": rows, "start": start},
    settings["records_per_query"], settings)
```

Of 141,297 datasets, 30,079 survive that filter. The reply is complete dataset metadata,
including licence and every resource URL, so one call gives us everything Part
2 will need.

| query | records | query | records |
|---|---|---|---|
| abn | 60 | trade licences | 59 |
| business names | 60 | licensed contractors | 25 |
| company register | 60 | charities register | 22 |
| building approvals | 60 | food business register | 14 |
| procurement contracts | 60 | workplace safety prosecutions | 14 |
| | | government tenders awarded | 14 |
| | | liquor licences | 8 |

Five queries returned fewer than 60 because the catalogue ran out. Only eight
liquor licence datasets exist with a parseable file. The loop stops on a short
page, so we never page past the end.

**Channel 2, `resource_search`.** `package_search` does not index the names of
files inside a dataset. A dataset called "Annual Report 2023" shipping
`licensed_contractors.csv` is invisible to channel 1.

```
GET /api/3/action/resource_search?query=name:contractor&limit=40
```

Eight such queries returned 241 resource hits belonging to only 99 distinct
datasets. Rather than 99 lookups we batch them 30 at a time back through
`package_search` with `fq=id:(...)`. Four calls instead of 99. The format
filter runs again, which is why 63 of the 99 came back.

### Result

**24 HTTP calls, 43 seconds, no API key.** 519 records.

| found by | datasets |
|---|---|
| dataset text, channel 1 only | 313 |
| file name, channel 2 only | 45 |
| both channels | 18 |

Those 45 are what the second channel bought us.

## Flatten (`catalogue flatten`)

One tidy row per dataset: id, title, description, organisation, tags, licence
id and title, plus every resource with its format, URL and size. This is the
only place raw CKAN shapes are read.

**The licence is captured here, at the first step.** The ontology requires one
on every observation, and backfilling it later means crawling twice.

519 records dedupe to **376 unique datasets**.

## Rule scoring, with plain code (`score`)

Word weights from `config/keywords.yaml`. Strong words worth 3 points each
(`abn`, `licensee`, `trading name`, `contractor`), medium worth 1 (`permit`,
`tender`, `prosecution`), negative worth −2 (`rainfall`, `species`,
`bathymetry`). Whole-word matching, so `abn` never fires on `abnormal`. A
publisher bonus for ASIC, the ABR, the ATO and the ACNC. A format bonus,
because CSV is less work than ZIP.

**Every score keeps its evidence.** `rule_evidence` lists exactly which words
fired, so a human can see why a dataset ranked where it did.

**No threshold anywhere.** The assignment asks for 50 datasets, never for
datasets above a confidence. We rank and take the top k:
`candidate_pool: 120`, `shortlist_size: 50`, both in `config/settings.yaml`.

### Folding yearly editions

The ACNC Annual Information Statement appeared twelve times in the pool, one
file per year. MIWB Contract Disclosure five times.

Strip years, quarters and month names from the title, group by publisher plus
the stripped title, keep the newest, and record the rest as `edition_siblings`
on the survivor.

Three reasons: we would pay the model to read the same dataset twelve times;
the shortlist would hold one source repeated twelve ways; and Part 2 needs six
sources that differ, which twelve editions of one register plainly are not.

We record the siblings rather than delete them. They are real datasets, and a
reviewer should be able to disagree.

**54 editions folded. 376 datasets become 322 distinct sources.**

## Model triage, the first model call (`triage`)

The keyword scorer ranks. The model judges. Different questions on purpose.

### What the model sees

One JSON line per dataset, twenty per call: dataset id, title, publisher,
readable formats, the names of up to six files inside it, and the first 400
characters of the description.

**It never sees our rule score.** Tell a model what you already think and it
agrees with you, and two signals collapse into one.

**Resource names are included** because file names often say more than the
dataset page. **Descriptions are cut at 400 characters** because some run to
several thousand and the first 400 say what a dataset is.

### The question worth paying for

Not "is this about business". Keywords answer that. It is **what one row of this file
represents**. The field is called `record_grain` in the code, after the
dimensional-modelling term, and it takes four values:

| value | what one row is | example |
|---|---|---|
| `entity` | one business | ASIC Company Dataset |
| `event` | an approval, contract or prosecution that names a business | building approvals |
| `aggregate` | a count or an average across many businesses | business counts by state |
| `unknown` | not enough information to say | |

Our scorer cannot tell these apart. Every one of them is full of business words.

### What came back

120 datasets, 6 calls, 41 seconds, **$0.026**.

| one row is | datasets |
|---|---|
| entity | 52 |
| event | 47 |
| aggregate | 14 |
| unknown | 7 |

The 14 aggregates are the win, because our keyword scorer had ranked several of
them highly. It dropped *Industry breakdown of PPSR registrations* as
"industry-level summaries" and the Geocoded National Address File as "a
database of addresses, not a list of businesses". Keywords cannot catch that.

### Guards on the call

- **JSON against a declared schema.** No markdown fences, no parse failures.
- **Matched by id, never by position.** A reply one item short would otherwise
  shift every judgment onto the wrong dataset. We warn and drop rather than
  guess.
- **Each batch cached to `runs/triage/`.** A failure on batch 4 does not
  re-spend batches 1 to 3.

### One honest weakness

Confidence bunches at the top. Of 120 datasets, 52 scored 0.90 or above, and
every shortlisted dataset is 0.90 or higher. The model is not using the range.

So `record_grain` is doing the real work and `confidence` is close to flat,
which means the rule score is breaking more ties than intended. Worth saying
rather than presenting the confidence column as if it discriminated.

## The shortlist (`shortlist`)

99 of the 120 survived as entity or event. Ranked by model confidence with the
rule score only as a tie-break, top 50 shipped.

**We keep `event` datasets.** A building approval names a builder per row, and
the assignment's own list of useful sources is mostly event-shaped.

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

The assignment asks for a hand-check of 20 and says plainly that it wants the
number honest rather than high.

**What we built, and what it is.** A second model reads the **actual file**:
real column names, real first rows, downloaded from the real URL. It is a
different model from the one that made the original judgment and is never shown
that judgment. Beside every verdict we record deterministic counts anyone can
check: cells containing "Pty Ltd", ABN-shaped values, entity-named columns.

**Say the limitation plainly: this is a model checking a model.** What makes it
worth more than the triage step is that it reads records where triage read
descriptions. What makes it auditable is that the raw evidence sits next to
every verdict in `outputs/shortlist_handcheck_results.csv`.

### The numbers

| | |
|---|---|
| Sampled | 20 of 50, random, seed 20260919 |
| File could be read | 14 |
| Could not be read | 6 |
| Contained businesses | 12 |
| **Precision on readable files** | **12/14 = 86%** |
| Precision counting unreadable as wrong | 12/20 = 60% |

**We report both.** 86% is what the shortlist gets right about datasets we
could open. 60% is what you get end to end, and it is the number that matters
if you have to onboard a source tomorrow.

### Why seven could not be read

| reason | count |
|---|---|
| HTTP 202, portal still building the file | 2 |
| HTTP 403 | 1 |
| HTTP 404, dead link in the catalogue | 1 |
| connection refused | 1 |
| zip over the 8 MB sample cap | 1 |
| spreadsheet would not parse | 1 |

**This is a finding, not a failure.** Roughly a third of catalogue links do not
serve a file on demand, and Source selection has to work around that.

### The one genuine false positive

*ASIC - Banned and Disqualified Persons Dataset*. The rows are individual
people, not companies. Triage saw "ASIC" and "disqualified" in the metadata and
scored it 1.00.

ASIC publishes a near-identical *Organisations* dataset, also on our shortlist,
which is correct. Metadata alone cannot separate those two. Data can. That is
the argument for this whole step.

## Bugs found in discovery, and what they taught

**The crawler cached pages by page number.** Raising the target per query from
25 to 60 would have read the old 25-record page, seen it was short, and
stopped. It would have returned 25 and reported success. Fixed by naming cached
pages after their start offset and size, so a cached page is reused only when
it is exactly the slice being asked for. *Caching that quietly returns a wrong
answer is worse than no caching.*

**Publishers type format labels by hand.** The catalogue holds `XLSX`,
`EXCEL (.XLSX)`, `.XLSX`, `EXCEL (XLSX)` and `XSLX` for one thing, plus
`ZIP (CSV)` and `ESRI SHAPEFILE - ZIPPED`. Comparing raw strings would have
given source selection a fake spread. `src/formats.py` folds 66 raw labels into 22
tokens and keeps the raw string beside the clean one. *The clean token is for
logic. The raw string is evidence, so we never destroy it.*

**We filtered in our own code instead of on the server.** The first version
downloaded 600 records' metadata to keep 190. CKAN will apply
`fq=res_format:(...)` itself. *Push work to the server where it will take it.*

**The shortlist picked "the first resource that is not HTML".** ASIC's Company
Dataset ships `company-dataset-help-file.pdf` first and `company_202609.csv`
second, so we were checking the help file. The first hand-check run reported
25% precision, which was our bug, not the shortlist's. *A default that is never
exercised looks like a decision.*

**Format ranking alone was not enough.** The ABN Bulk Extract ships a `.xsd`
schema and an index CSV listing the other files, both of which beat the real
zip on format. Anything named schema, readme, help, dictionary, resource list
or codeset now sorts last whatever its format.

**The download cache was keyed by dataset id.** When a fix changed which file
we wanted, the old download was served from cache and the fix looked broken.
*A cache key must contain everything that decides the content.*

**And a habit, not a bug.** A number that looks wrong usually is. Treat a
surprising metric as a hypothesis to test before writing it down.

---

# Part 2a — Source selection

`src/select.py`, `src/fetchfile.py`

No model calls. This is the programmed check the agent depends on.

## Probe

We download the head of every file on the shortlist. Not one file per dataset,
but several: the easiest one plus one alternative per other format. Many
publishers ship the same records as CSV and XLSX, and Part 2 needs sources that
are not clean CSVs.

**95 files across 50 datasets. 59 usable, on 30 datasets.**

That is the measured version of "about a third of catalogue links do not serve
a file". Twenty of the fifty shortlisted datasets cannot be processed at all
today.

What a probe records: the real format from the file's bytes, the header row,
the column names, how many columns, and deterministic business signals.

## The selection rule

Written down, not chosen by taste, because the assignment asks whether the
solution generalises.

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

Six publishers, four formats, both grains, five of six not a plain CSV, column
counts from 5 to 69.

**Source 1 is declared CSV in the catalogue and is actually tab separated.**
Source 6 is declared CSV and is a spreadsheet. That is the kind of thing the
agent has to survive.

## Bugs found in source selection, and what they taught

**A zip must be whole to be opened.** Its index sits at the end, so the 3 MB
sample cap broke every zip and every xlsx, because an xlsx is a zip. Fixed by
sniffing the first bytes and going back for the whole file when it says zip.

**openpyxl refuses a file whose name it does not recognise.** Our cache files
end in `.bin`, so it raised `InvalidFileException` on 16 perfectly good
spreadsheets. Fixed by handing it an open file object instead of a path. *A
library's error message can be about your filename rather than your data.*

**A code list is not data.** The Finance contract dataset ships `AusTender
Customised UNSPSC Codeset.xlsx` next to the contract records, and we picked the
code list.

**Spreadsheets do not start at row one.** That same file has a title banner in
row 1 and the real headers in row 2, which our parser read as 16,374 columns.
Now we take the row with the most non-empty cells among the first six and
record which row we chose, so a human can disagree. The mapping config carries
`header_row`.

**A file that parses to one or two columns has not parsed.** Added as a gate:
usable means at least three columns. It catches wrong delimiters and title rows
the parser swallowed silently.

---

# Part 2b — Schema inference agent

`src/agent/`, one module per concern, orchestrated by `src/agent/run.py`

## The state file

One per source at `runs/<source_id>/state.json`, saved after every step. A step
already marked done is skipped on a rerun. A failed step records the exception
and traceback and leaves everything before it intact.

It does three jobs at once: carries state between steps, makes a crash cost one
step instead of the whole run, and holds the seconds and dollars Part 5 asks
for.

**Where a run can fail without losing the whole job:**

| step | failure | cost of retry |
|---|---|---|
| Probe | download dies, format unreadable | the download only |
| Profile | a column will not parse | profiled as unknown, run continues |
| Propose | model returns unusable JSON | one call |
| Validate | engine raises on a row | counted as a failure, run continues |
| Revise | no convergence in two rounds | flagged for a human, config kept as draft |
| Review | a person rejects | note kept, propose through review rerun with it |
| Freeze | only runs on approval | nothing else can write to `configs/` |

## Step 1 — Probe (`c1_probe`)

Download the file, work out what it really is from its bytes, and pull up to
2,000 records into one flat shape at `runs/<source_id>/records.jsonl`.

The output is the `resource` section of a mapping config: real format,
encoding, delimiter, header row, sheet name, member inside a zip.

**The records are split in two.** The first 400 rows are the sample. The rest
are held back for the validate step. A mapping that only works on rows the sample came from is
not a mapping.

**Who reads what.** Our code reads all 400 rows. The model never sees them. It
sees the summary the profile step builds from them.

Whether a "row" is a spreadsheet row or a line of text depends on the source.
Three of the six are spreadsheets, one is tab separated, one is a CSV, one is a
zip. The probe step turns all of them into the same list of dictionaries, so nothing
downstream has to care.

## Step 2 — Profile (`c2_profile`)

Per column, from the sample rows only:

- how full it is, how many distinct values, longest value
- up to eight real example values, never invented ones
- **all** distinct values when there are 25 or fewer, so the model can write a
  complete `map_values` rather than guess at an enum
- what the values look like: the share that pass the ABN checksum, the ACN
  checksum, look like a postcode, a state, a number, a URL, an email, and which
  date formats match

ASIC's Company Dataset, first six columns:

```
Company Name   null=0.00  distinct=400  e.g. LOVINI HOLDINGS PTY LTD
ACN            null=0.00  distinct=167  acn_valid=1.000
Type           null=0.00  distinct=3    all values: APTY, APUB, FNOS
Class          null=0.01  distinct=3    all values: LMGT, LMSG, LMSH
Sub Class      null=0.01  distinct=6    all values: LISN, LIST, PROP, PSTC, ULSN, ULST
Status         null=0.00  distinct=3    all values: DRGD, EXAD, REGD
```

**`acn_valid=1.000` is not a guess from a column name.** It is the published
checksum run over 400 real values. The model is told which column holds a valid
identifier before it is asked to map anything.

Three reasons that number is worth computing. A column name is a claim and a
checksum is proof, so `entity.abn` becomes a near-certain mapping rather than a
hopeful one. It catches the opposite case too: the Victorian schools file has
nine-digit values sitting in a column called `ABN`, which the name alone would
never reveal. And it sets the expectation the validate step measures against —
if the profile says 99.8% pass and the extracted field fills 60%, the transform
chain is eating values. It costs nothing, being arithmetic over 400 values
already in memory.

The Victorian schools ABN column scores 0.998, not 1.000. Those failing rows
stay in the profile. A real register has bad rows, and a mapping that claims
100% is the kind of claim the assignment says it marks down.

### Why the profile exists

| source | columns | profile | 400 raw rows |
|---|---|---|---|
| Victorian schools ABNs | 5 | 600 tokens | 12,000 |
| ASIC Company Dataset | 15 | 1,600 | 46,000 |
| WGEA | 20 | 2,400 | 93,000 |
| Victorian liquor licences | 21 | 2,100 | 60,000 |
| Finance contract notices | 43 | 5,100 | 145,000 |
| ACNC Registered Charities | 69 | 6,700 | 205,000 |

**Thirty times smaller, and better.** Raw rows would show 400 examples of the
same thing and no statistics. The profile carries null rates, distinct counts,
the complete enum where there is one, and checksum pass rates. None of that is
visible from staring at rows.

At $0.75 per million input tokens, the ACNC source alone would cost $0.15 per
attempt in raw rows against $0.005 as a profile. Across six sources and up to
three attempts each, that is about $2.50 against $0.09.

## Step 3 — Propose (`c3_propose`)

The model gets four things and never the file: the ontology as field names with
notes and allowed enum values, the closed list of 20 transforms, what the
probe found about the file, and what the profile found about every column. The ASIC prompt is 7,875
characters, about 2,000 tokens.

### One thing that needed working around

A response schema cannot describe a free-form object, and transform arguments
are free-form: `parse_date` takes a list, `map_values` takes a dictionary. So
the model returns arguments as a JSON string in `args_json`, parsed on the way
in. An argument that will not parse is dropped and recorded rather than reaching
the engine. Across all six sources, zero arguments failed to parse.

### What came back

| source | fields mapped | columns left alone | cannot fill | cost |
|---|---|---|---|---|
| ASIC Company Dataset | 6 | 9 | 10 | $0.0168 |
| WGEA | 4 | 17 | 12 | $0.0181 |
| ACNC Registered Charities | 10 | 59 | 6 | $0.0226 |
| Victorian liquor licences | 5 | 16 | 11 | $0.0170 |
| Victorian schools ABNs | 2 | 3 | 14 | $0.0084 |
| Finance contract notices | 6 | 35 | 10 | $0.0156 |

Three things in the ASIC draft are worth pointing at.

**It covered the whole enum.** The profile sent every distinct value of `Status`, so the
model wrote a complete `map_values` for `REGD`, `DRGD` and `EXAD` with a default
of `unknown`, rather than guessing at categories it had not seen.

**It used the date format the profile detected**, `%d/%m/%Y`, not a guess.

**It refused to invent a record id.** It chose `hash_of_fields` over ACN plus
Company Name and explained why: the file holds historical company names, so one
ACN appears on several rows. It read that out of the distinct counts.

### Refusing is the behaviour we wanted

Ten of sixteen ontology fields are declared unfillable for ASIC, each with a
reason. For `entity.trading_name`: "Dataset contains only registered corporate
legal names, not trading or business names."

The Victorian schools file mapped 2 fields of 16, which is right. It has five
columns and three are a school number, a financial period and a load timestamp.

## Step 4 — Validate (`c4_validate`)

Run the config over rows 400 to 1200, which the profile was not built from. No
model call. Two things come out: what failed, and how often each mapped field
produced a value.

### The check I got wrong first

The first version flagged a field when its fill rate was far below the
confidence the model claimed. It flagged the ACNC charity register's
`entity.trading_name`: claimed 0.85, filled 13%.

A false alarm, and the reason matters. `field_confidence` means "we are sure
this mapping and parse are right". Fill rate means "this column has data". The
ACNC's other-names column is empty 87% of the time, and a mapping of it can be
perfectly correct.

**The right comparison is the field's fill rate against the source column's own
fill rate.** Now the report reads:

```
entity.trading_name  from "Other_Organisation_Names"  column 10% -> field 13%
entity.abn           from "ABN"                       column 98% -> field 99%
address.state        from "State"                     column 88% -> field 86%
```

A field tracking its column is correct. A column at 99% producing a field at
13% means the transforms are eating values. That is the only thing worth
calling a problem.

### What the six drafts did

Two had no failures at all. The two real findings were both in ASIC's Company
Dataset: `entity.abn` dropped a value on 4% of rows, and the value was `0`,
which ASIC uses as a null placeholder; `entity.status` fell back to `unknown`
on 1.5%, and the value was `SOFF`, struck off, which the 400-row sample did not
contain.

Neither is visible from metadata or from a profile. Both needed the config run.

## Step 5 — Revise (`c5_revise`)

The model is shown the failure report and asked for a corrected config. At most
two rounds. What makes it a correction and not a second opinion is that the
report came from running its config over real rows. A failed ABN checksum is a
fact.

### What it fixed

**ASIC, round 1, two failures to zero.** It added `SOFF: deregistered` to the
status map and put a `null_if` in front of the ABN chain:

```
was:  abn_normalize
now:  strip, null_if{values:["0"]}, abn_normalize
```

That is the better answer, and not only because the count went down. Before, a
placeholder was dropped by a checksum failing. After, it is dropped because the
config says so. The difference is whether a reader can tell it was deliberate.

**Victorian liquor licences, round 1, one failure to zero.**

### Where it tried to cheat

On the Finance contract notices, `address.postcode` dropped `801`, `97219` and
`OX2 6DP`. The model's fix was to delete `postcode_extract` and keep only
`strip` and `collapse_spaces`. The failure count went to zero and the mapping
got worse: a US zip code now passes as an Australian postcode.

**A model optimising for a number will pass the test by deleting the test.**

So "better" is no longer just "fewer failures". Ops that check a value rather
than reshape it are tracked per field, and a revision that drops one is
rejected whatever its numbers say:

```
c5_revise: round 1, failures 1 -> 0, rejected, made it worse
           removed validating transforms: ['address.postcode:postcode_extract']
```

That source went to a human, which is the honest outcome. `97219` is a real
value in an Australian government contract register and somebody has to decide
what it means.

### Where it ran out

The Victorian schools file has 9-digit values in a column called ABN, such as
`142 547 710`. That is ACN length. Two rounds did not fix it, so the source was
flagged rather than forced through.

### The result

| source | failures before | after | rounds | outcome |
|---|---|---|---|---|
| ASIC Company Dataset | 2 | 0 | 1 | fixed |
| WGEA | 0 | 0 | 0 | clean first time |
| ACNC Registered Charities | 0 | 0 | 0 | clean first time |
| Victorian liquor licences | 1 | 0 | 1 | fixed |
| Victorian schools ABNs | 1 | 1 | 2 | flagged for a human |
| Finance contract notices | 1 | 1 | 1 | revision rejected, flagged |

**Four of six onboarded without a person. Two stopped and said why.**

## Step 6 — Human review (`c6_review`)

One per source at `runs/<source_id>/review_card.md`. It shows what the agent
decided and why, what it mapped with the source column's fill rate next to the
field's, what it refused and why, what broke on rows the model never saw, and
five real records raw beside canonical.

**Nothing on the card is the model describing its own work.** Every number came
from running the config.

## Step 7 — Freeze (`c7_freeze`)

A config reaches `configs/` because a person said yes, never because a model
said it was fine. Approving stamps who, when, how many correction rounds it
took, and any unresolved failures into the config itself, so "approved anyway"
stays visible.

That `generated_by` block is the answer to the assignment's hardest question.
Six months from now, when the matching model improves, you can find every
config a given model version produced.

## Bugs found in the agent, and what they taught

**A deliberate `null_if` was counted as a failure.** Once the model added one,
the engine still reported "dropped a value the source had", because the source
did have a value. The fix looked like it had not worked. Now the engine checks
whether a `null_if` names that exact value, and treats the drop as a recorded
decision. *A rule that cannot tell a decision from an accident sends a model
round in circles.*

**Rejecting did not undo approving.** The refused config stayed in `configs/`
and kept shipping. Now a rejection deletes the frozen file.

**A reviewer's note went nowhere.** It was recorded and then ignored. Now it is
carried into the next propose prompt under a heading saying a human rejected the
earlier attempt and their notes outrank the model's own judgement. Notes
accumulate across rejections.

**Two mappings can write one canonical field.** Asked to also map `Current
Name`, the model mapped both it and `Company Name` to `entity.legal_name`. The
engine applies mappings in order, so the later one won when it had a value.
That produced the right answer by accident, which is worse than being wrong:
nobody chose the behaviour and the review card's fill-rate table, keyed by
canonical field, showed nonsense. Now a duplicate is a failure the validate step raises before
looking at a single row, and the revise step is told the answer is `coalesce`. Round one
took three failures to zero:

```
entity.legal_name   coalesce["Current Name", "Company Name"] -> strip -> collapse_spaces
```

**That last one was found by the human gate, not by us.** The reviewer rejected
ASIC with "also map Current Name to entity.legal_name", and everything above
followed from it. It is the clearest evidence in the project that the human
step does something.

---

# Part 2c — Canonical extraction

`src/engine.py`, `src/extract.py`

Six configs, one engine. Nothing in the engine knows anything about any
particular source. It reads a config, opens the file the config describes, and
writes canonical observations.

**The same code runs twice.** In the validate step, over rows the profile was
not built from, to find what a draft gets wrong. In canonical extraction, over
a full sample, to produce the
deliverable. So what the agent was checked against is exactly what ships.

**The file is opened from the config's own `resource` section**, not from
anything the agent left behind, which proves a config is sufficient on its own.

## The run

| source | observations | with ABN or ACN |
|---|---|---|
| ACNC Registered Charities | 1,000 | 982 |
| ASIC Company Dataset | 1,000 | 1,000 |
| Finance contract notices | 1,000 | 992 |
| Victorian schools ABNs | 1,000 | 993 |
| Victorian liquor licences | 1,000 | 0 |
| WGEA | 1,000 | 1,000 |
| **total** | **6,000** | **4,967** |

**6,000 observations in 1.3 seconds with zero model calls.** Per-record cost is
$0.00 and that is measured, not claimed: `outputs/extraction_report.json`
records zero model calls for the whole stage.

The liquor source contributes 1,000 businesses with no identifier at all, known
only by name and address. That is the hard case for Part 3 and it is useful to
have one.

## What an observation looks like

```json
{
 "source_id": "asic-company-dataset-7b8656f9",
 "source_record_id": "0eaa924bae48e834",
 "observed_at": "2026-09-14T15:25:32",
 "ingested_at": "2026-09-19T16:35:53+00:00",
 "licence": "Creative Commons Attribution 3.0 Australia",
 "extractor_version": "engine0.1/config1.0",
 "confidence": {
  "source_reliability": 0.95,
  "field_confidence": {"entity.legal_name": 0.95, "entity.acn": 1.0, ...}
 },
 "entity": {
  "legal_name": "MONAKA PTY LTD", "acn": "000000019",
  "abn": "89000000019", "entity_type": "company",
  "status": "active", "date_registered": "1990-01-08"
 }
}
```

Fields we could not fill are absent, not blank and not guessed. The `entity` and
`address` blocks only appear when they have something in them.

## The bug canonical extraction exposed

**On four of six sources, `observed_at` equalled `ingested_at` on every row.**
The ontology is explicit that these are different and both matter.

The cause was ours, not the agent's. Those four carry no date column, so the
config falls back to a constant, and we never populated that constant. The
dataset's `metadata_modified` was sitting in the crawl output and was not
carried through.

**Fixed by carrying it through, then re-running from validate onwards and asking the
reviewer to approve again.** The proposal drafts were reused, so the mappings came out
byte-identical: the same 6, 4, 10 and 5 fields, still zero open failures. Only
the envelope date moved.

```
ASIC        2026-09-14    WGEA        2026-01-09
ACNC        2026-09-13    Vic liquor  2026-09-14
```

WGEA's is eight months older than the rest. That is real signal: when two
sources disagree about a company in Part 4, we know which one spoke more
recently.

**The process point.** Those configs were already approved. Rather than editing
approved files in place, we regenerated and went back through the gate. *An
approval that can be silently amended afterwards is not an approval.*

---

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
