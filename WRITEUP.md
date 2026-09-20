# Write-up
---

## 1. Cost
### Overall

| Metric | Total Run (6 Sources) | Per Source (Clean Run) |
| :--- | :--- | :--- |
| **Cost** | $0.159 | $0.027 |
| **Model Time** | ~114 seconds | ~19 seconds |
| **Sources Processed** | 6 | 1 |
| **API Calls** | 11 | ~1.8 |
| **Input Tokens** | 35,540 | ~5,923 |
| **Output Tokens** | 35,309 | ~5,885 |

### Models

| model | used for | why | price per 1M tokens |
|---|---|---|---|
| `gemini-3.1-flash-lite` | Part 1: triaging datasets | reading a title and a short description | $0.25 in / $1.50 out |
| `gemini-3.7-flash` | Part 2: proposing and correcting mappings | reads a column profile, writes structured JSON, self-validate | $0.75 in / $3.75 out |

Gemini model family is used due to its cheapness and good general knowledge

### Calls

| step | calls | total tokens | tokens per call | total cost | cost per call |
|---|---|---|---|---|---|
| triaging 120 datasets | 6 | 40,297 | 6,716 | $0.0258 | $0.0043 |
| proposing a mapping | 6 | 43,926 | 7,321 | $0.0977 | $0.0163 |
| correcting a mapping | 5 | 26,923 | 5,384 | $0.0614 | $0.0123 |
| **total** | **17** | **111,146** | **6,538** | **$0.1849** | **$0.0109** |

Source: `runs/llm_calls.jsonl`, written one line per call as the pipeline ran.


### Time

| stage | seconds |
|---|---|
| probe the file | 5.5 per source |
| profile the columns | under 0.1 per source |
| propose a mapping | 10.8 per source |
| validate on unseen rows | under 0.1 per source |
| correct the mapping | 7.5 per source |
| build the review card | under 0.1 per source |
| **total, one source through the agent** | **23.9** |
| extract 6,000 records | 1.4 |
| extract 126,540 records | 6.5 |

---

## 2. Agent design

### Onboarding one source

```
   a source URL
        │
        ▼
   1 probe
        │
        ▼
   2 profile
        │
        ▼
   3 propose  ◄──────────────────────────┐   MODEL CALL
        │                                │
        ▼                                │
   4 validate ◄────────┐                 │
        │              │                 │
        ├─ failures ─> 5 revise          │   MODEL CALL
        │              2 rounds max      │
        ▼                                │
   6 review ──────────── rejected ───────┘   HUMAN
        │
        ▼ approved
   7 freeze  ──>  configs/<source_id>.json
        │
        ▼
   the engine reads the config and emits observations
```

| step | what it does | produces | model |
|---|---|---|---|
| **1 probe** | Get the real format from magic bytes, not the catalogue's label. Unwrap zips, find the header row as the one with most non-empty cells. Split the records into a 400-row sample for the model. | `records.jsonl` and the `resource` block: format, delimiter, header row, sheet, zip member | no |
| **2 profile** | Per column: null rate, distinct count, real example values, and the full value list when there are fewer than 25. Plus the share of values passing the ABN and ACN checksums, and which date formats match. | `column_profile.json` | no |
| **3 propose** | Render the ontology and the 20 transforms into the prompt alongside the profile. Ask for JSON against a declared schema, with transform arguments as a string. | draft config: record id, validity dates, field mappings, unmapped columns, unfillable fields | yes |
| **4 validate** | Run the draft over the holdout rows and classify every failure: dropped a value the source had, enum fell to a default, two mappings on one field. Compare each field's fill rate to its source column's, so a sparse column is not mistaken for a broken mapping. | failure report with real example values | no |
| **5 revise** | Send the failure report back and re-validate the result. Accept only if failures did not increase **and** no validating transform was removed, because the model tends to delete some configs to cheat. | corrected config, or the source flagged for a human | yes |
| **6 review** | Show one card: fields mapped with fill rates and transform chains, refusals with reasons, failures with real values, five records raw beside canonical. A rejection deletes any frozen config and reruns steps 3–6 with the reviewer's note in the prompt. | `review_card.md`, `approval.json` | human |
| **7 freeze** | Stamp the model, the revision count, the approver and any unresolved failures into the config itself, then write it. | `configs/<source_id>.json` | no |

State is written to `runs/<source_id>/state.json` after every step, with the
seconds and costs it used, so a crash costs one step and the cost report is a
sum over real calls.

Proposing the mapping config is the weakest step, because we can't check its reasoning, only the validate step can check the result. Given the same identical input, different numbers of mapped fields for different runs and the quality of the pipeline vary here. Sometimes, one canonical fields has several mappings as well.


### After the config: linking and profiles

```
   the six frozen configs
        │
        ▼
   extract ────> observations.jsonl        one engine, six configs
        │
        ▼
   match ──────> links.jsonl               proposed links
        │        unlinked.jsonl            refusals, with reasons
        │        entities.jsonl            derived from the links
        │
        ├──────> relationships.jsonl       parent and sibling edges
        │
        ▼
   profile ────> company_profiles.jsonl    50 merged businesses
```

| step | what it does | produces |
|---|---|---|
| **extract** | One engine reads a config and opens the file the config describes. | 126,540 observations in 6.5 seconds |
| **match** | Block on identifier and normalised name, score each candidate pair by method, and route anything below threshold or in conflict to the refusal queue. Entities are a view derived from the surviving links. | 1,609 links, 1,025 refusals, 1,434 multi-source entities |
| **relationships** | Find the column in each source that names a *different* business, and record parent edges in their own file. | 12,421 parent edges, 4,674 sibling edges |
| **profile** | For each field, collect every value any source offered, resolve conflicts by a policy chosen per field type, and keep every losing value with its source and date. | 50 profiles, 147 contested fields |

---

## 3. The hard one

The government contract register. Its postcode column drops 1.3% of values, such as
`801`, `97219` and `OX2 6DP`. That last one is in Oxford. The register lists overseas 
suppliers, and our postcode rule only supports Australian postcodes for now.

The agent's fix was to delete the postcode rule and failures became zero, but luckily
the guardrail caught it, and the situation is brought to human reviewer. 

What it actually needs is a business condition: extract a postcode only when the
country is AU if we decide to support AU postcode only, or if we need to support 
other countries' postcodes, I will improve the shared vocabulary for the model calling.

Other hard cases we hit: a declared CSV that was tab-separated, a declared CSV
that was a zip, `0` used as a null ABN, `0` and `1` used as null postcodes,
nine-digit values in a column called ABN, a dataset whose CSV was an index of
its other files, a title banner that made a spreadsheet parse as 16,374
columns, 217 name matches with conflicting ABNs, two schools sharing a name in
different states, an incorporated association matched to a company of the same
name, a foundation matched to its college, a committee matched to its
association, individual licensees matched to companies, a venue brand matched
to its holding company, and one supplier with eight different addresses.

---

## 4. Scale: six sources to five hundred

| what | stage | what we saw | the fix |
|---|---|---|---|
| **Deciding which records to compare** | Part 3, the matcher | We group records that share a name, then compare every pair in the group. Aldi holds a liquor licence at every store, so one group held hundreds of Aldi records and we compared all of them against each other. A clean run over two overlapping liquor registers made 225,587 comparisons it then threw away, against 1,025 for our own six sources. | Never group on a name alone. A name plus a postcode, or a name plus a state. |
| **The person who approves each config** | Part 2, the review step | Two of our six sources needed a human. At 500 sources that is around 167 configs to read, which is a week of somebody's time. | Approve automatically when nothing failed and every field confidence is high. Send a person only the rest. |
| **Configs going stale without anyone noticing** | Part 2, the frozen config | A config pins the download link and the exact column names. ASIC's expects `Company Name`, `ACN`, `ABN`. If the publisher renames a column or moves the file, the config keeps running and quietly produces nothing for that field. At six sources you would spot it. At 500 you would not. | Re-run the existing config against today's file on a schedule and compare the fill rates to the ones recorded when it was approved. It costs nothing, because that step makes no model calls. |
| **The same records arriving twice** | Part 3 and 4 | We already fold 54 yearly editions of the same dataset out of 376. The harder case is two different datasets carrying the same records, such as a federal register mirrored on a state portal. We would onboard both and every entity would gain a duplicate observation that looks like a second source agreeing when it is the same source twice. | Compare new sources against the ones already loaded before onboarding them, and mark an observation as a copy rather than a corroboration. |

---

## 5. Change: a better matcher, millions of existing links

We should not store "these two records are the same business" as a bare fact. We should 
store the pair, the rule that matched them, the matcher version, the evidence
and the threshold used at the time. Clusters are built from those links and
can be rebuilt at any point.

So a better matcher is a comparison:

1. Run it over the same groups of records.
2. Line old links up against new ones. Each side is a pointer to a record, so
   re-extracting a source does not break the join.
3. Both agree: nothing to do. New only: sample, measure precision, promote.
   Old only: quarantine and review, never delete on the new model's say-so.
4. Rebuild the clusters from what survives.

The easy thing to forget is the refusals. Without a record of what we declined
to link, we cannot tell whether the new matcher found something real or just
looked at a pair the old one never considered. 

---

## 6. What I would build with three weeks

| What | Limitation | Example | Improvement method |
|---|---|---|---|
| Conditional transforms | The transform list has no way to express "only do this when another field says so" | `97219` and `OX2 6DP` pass as Australian postcodes, and the agent's own fix was to delete the postcode check | Add a `when(field, equals, then_ops)` transform, so a postcode is only extracted when the country is AU |
| Addresses as a list | The ontology holds one address per entity, so every other address a source gives is thrown away | Fujitsu appears at 8 distinct addresses in one source: a registered office, several sites, a GPO box | Model address as a typed list, each entry tagged registered, trading or site, with its own validity window |
| Auto-approve gate | Every config needs a person, whether or not anything went wrong | 2 of our 6 datasets needed a human, so 500 sources means around 167 cards to read | Approve automatically when there are no unresolved failures and every field confidence clears a bar; route only the rest to a person |
| Better blocking | Records are grouped by name alone, so a common name makes a huge group and every pair in it gets compared | Aldi holds a liquor licence at every store: 21,458 comparisons from one name | Group on name plus postcode, or name plus state, so no single name can open a group |
| Recall measurement | We know how many of our links are right, and nothing about how many we miss | We measured precision three ways. There is no recall number to quote, which is the problem | Build a small truth set by hand within a few blocks, run the matcher over it, and report what it failed to find |
| Reproducible crawl | The catalogue is live, so a rerun can legitimately pick different datasets | A clean run picked six different sources and mapped all six, which proves it generalises but makes a result impossible to repeat | Save the crawl as a dated snapshot and let a run replay it, the way the pinned six already work for Part 2 |

---

## 7. How I modelled relationships

Sameness and relatedness are different claims, so they are different files that
join on one key.

```
  links.jsonl  ─────────┐
                        ├──  canonical_entity_key  ──┐
  entities.jsonl  ──────┘                            │
                                                     │
  relationships.jsonl  ──  from_key  ────────────────┘
                           to_key    ──────────────────  another entity
```

```json
// links.jsonl          two records, one business
{ "source_a_record": {...}, "source_b_record": {...},
  "canonical_entity_key": "abn:12647225929", "confidence": 0.99 }

// relationships.jsonl  two businesses, one group
{ "relation": "member_of_corporate_group", "direction": "child_to_parent",
  "from_key": "abn:12647225929", "to_key": "abn:43639336226",
  "asserted_by": "wgea-dataset-4d35cd80", "confidence": 0.90,
  "resolved": { "to": "resolved_by_name", "to_candidates": 1 } }
```

A subsidiary is not its parent, and the temptation to merge them is strongest
when the names look alike. Keeping them apart means a relationship can never
leak into an entity by accident, and the confidence on a relationship can never
be mistaken for confidence that two records match.
