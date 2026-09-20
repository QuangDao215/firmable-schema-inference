# Review: Victorian Government Schools ABNs

Publisher     Department of Education
Licence       Creative Commons Attribution 3.0 Australia
File          XLSX
Landing page  https://data.gov.au/dataset/victorian-government-schools-abns

The agent took 2 correction round(s). It still has an unresolved failure.

## What it decided

Record id: source_field over ['SchoolNumber']
  why: SchoolNumber is unique and non-null for every record.
Source reliability: 0.85

## Fields it mapped

  canonical field            from column                 column  field  conf
  entity.legal_name          SchoolName                    100%   100%  0.95
      strip -> collapse_spaces
  entity.abn                 ABN                           100%    99%   0.9
      abn_normalize

## What it refused to map

This is where you are most likely to disagree.

  entity.trading_name        No distinct trading name column provided.
  entity.acn                 No Australian Company Number column present.
  entity.nzbn                Not a New Zealand registry dataset.
  entity.entity_type         No entity structure or legal type indicator provided.
  entity.status              No operating or registration status column provided.
  entity.website             No website URL provided in source data.
  entity.industry_code       No industry classification or ANZSIC code provided.
  entity.date_registered     No entity registration date provided.
  address.full               No physical or postal address information present.
  address.locality           No suburb or locality information present.
  address.state              No state or jurisdiction column present.
  address.postcode           No postcode column present.
  address.country            No country column present.

  Source columns left alone: 3
    SchoolNumber                   Internal departmental identifier used as record_id, not an o
    Fin_Period                     Represents the reporting accounting period (e.g. 201506), no
    LoadDate                       Used for record-level observed_at timestamp.

## What broke on 800 rows the model never saw

  entity.abn                 dropped a value the source had
      7 rows (0.9%), real values: ["142 547 710", "14 209 385 ", "71 802 971 ", "85 269 418 ", "97 085 612 "]

## Correction rounds

  round 1: failures 1 -> 1, kept
  round 2: failures 1 -> 1, kept

## Five real records

  RAW:
    SchoolNumber                 2265
    SchoolName                   Koondrook Primary School
    Fin_Period                   201506
    LoadDate                     2015-07-15 06:32:28.103000
    ABN                          57761295834
  BECOMES:
    entity.legal_name             Koondrook Primary School                     conf 0.95
    entity.abn                    57761295834                                  conf 0.9
    record id  2265

  RAW:
    SchoolNumber                 2269
    SchoolName                   Katunga South Primary School
    Fin_Period                   201506
    LoadDate                     2015-07-14 06:31:58.197000
    ABN                          12060022547
  BECOMES:
    entity.legal_name             Katunga South Primary School                 conf 0.95
    entity.abn                    12060022547                                  conf 0.9
    record id  2269

  RAW:
    SchoolNumber                 2271
    SchoolName                   Magpie Primary School
    Fin_Period                   201506
    LoadDate                     2015-07-06 06:30:37.587000
    ABN                          61567681482
  BECOMES:
    entity.legal_name             Magpie Primary School                        conf 0.95
    entity.abn                    61567681482                                  conf 0.9
    record id  2271

## To decide

  python -m src.approve victorian-government-schools-abns-1ca29b03 --yes --by <your name>
  python -m src.approve victorian-government-schools-abns-1ca29b03 --no  --by <your name> --note "what is wrong"
