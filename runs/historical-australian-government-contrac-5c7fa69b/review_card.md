# Review: Historical Australian Government Contract Notice Data

Publisher     Department of Finance
Licence       Creative Commons Attribution 3.0 Australia
File          XLSX  (catalogue said EXCEL (.XLSX))
Landing page  https://data.gov.au/dataset/historical-australian-government-contract-data

The agent took 1 correction round(s). It still has an unresolved failure.

## What it decided

Record id: source_field over ['Contract ID']
  why: Contract ID is uniquely populated with 0% null rate across all records.
Source reliability: 0.85

## Fields it mapped

  canonical field            from column                 column  field  conf
  entity.legal_name          Supplier Name                 100%   100%  0.95
      strip -> collapse_spaces
  entity.abn                 Supplier ABN                  100%    99%  0.98
      strip -> abn_normalize
  address.full               Supplier Address               84%   100%   0.9
      concat -> collapse_spaces -> strip
  address.locality           Supplier Suburb               100%   100%  0.95
      strip -> collapse_spaces
  address.state              Supplier State                100%   100%  0.98
      strip -> state_normalize
  address.postcode           Supplier Postcode             100%    98%  0.98
      strip -> postcode_extract
  address.country            Supplier Country              100%   100%  0.98
      strip

## What it refused to map

This is where you are most likely to disagree.

  entity.trading_name        Source dataset does not distinguish or supply trading names separately from supplier legal names.
  entity.acn                 Source dataset only records ABNs, not separate Australian Company Numbers.
  entity.nzbn                Dataset contains Australian government procurement records; no NZBNs are present.
  entity.entity_type         Entity legal structure type is not explicitly provided in contract notice records.
  entity.status              Source contains contract records rather than entity registration status.
  entity.website             No supplier web URLs are provided in the source dataset.
  entity.industry_code       Source provides contract UNSPSC codes rather than ANZSIC industry codes for the supplier entity.
  entity.date_registered     Original entity incorporation/registration dates are not included in procurement notices.

  Source columns left alone: 32
    Agency Name                    Procuring government agency, not the contracting entity itse
    Parent Contract ID             Contract administrative identifier, irrelevant to entity ont
    Value                          Contract procurement value, outside entity ontology scope.
    Amendment Date                 Contract amendment date metadata.
    Amendment Start Date           Contract amendment date metadata.
    Amendments Value               Contract amendment financial value.
    Description                    Description of contract procurement goods/services, not enti
    Agency Ref ID                  Internal procuring agency reference identifier.
    ... and 24 more, all listed in the config

## What broke on 800 rows the model never saw

  address.postcode           dropped a value the source had
      10 rows (1.3%), real values: ["801", "97219", "OX2 6DP", "FY1 9JN", "60603"]

## Correction rounds

  round 1: failures 1 -> 0, REJECTED: removed validating transforms: ['address.postcode:postcode_extract']

## Five real records

  RAW:
    Agency Name                  Administrative Appeals Tribunal
    Contract ID                  CN3650314
    Publish Date                 2019-12-23 00:00:00
    Start Date                   2019-12-02 00:00:00
    End Date                     2020-12-08 00:00:00
    Value                        169985
    Description                  Microsoft Premier support under VSA4 Head Agreement
    Agency Ref ID                4500000521
    UNSPSC Code                  81112200
    UNSPSC Title                 Software maintenance and support
  BECOMES:
    entity.legal_name             DIGITAL TRANSFORMATION AGENCY                conf 0.95
    entity.abn                    96257979159                                  conf 0.98
    address.full                   9880 GPO BOX, CANBERRA, ACT, 2601, Australia conf 0.9
    address.locality               CANBERRA                                     conf 0.95
    address.state                  ACT                                          conf 0.98
    address.postcode               2601                                         conf 0.98
    address.country                Australia                                    conf 0.98
    record id  CN3650314

  RAW:
    Agency Name                  Administrative Appeals Tribunal
    Contract ID                  CN3654193
    Publish Date                 2020-01-20 00:00:00
    Start Date                   2019-07-01 00:00:00
    End Date                     2020-06-30 00:00:00
    Value                        2856482
    Description                  Labour Hire Contractors
    UNSPSC Code                  80111600
    UNSPSC Title                 Temporary personnel services
  BECOMES:
    entity.legal_name             Launch Recruitment                           conf 0.95
    entity.abn                    54119140840                                  conf 0.98
    address.full                   Sydney, NSW, 2000, AUSTRALIA                 conf 0.9
    address.locality               Sydney                                       conf 0.95
    address.state                  NSW                                          conf 0.98
    address.postcode               2000                                         conf 0.98
    address.country                AUSTRALIA                                    conf 0.98
    record id  CN3654193

  RAW:
    Agency Name                  Administrative Appeals Tribunal
    Parent Contract ID           CN3468654
    Contract ID                  CN3468654-A2
    Publish Date                 2017-11-14 00:00:00
    Start Date                   2017-11-10 00:00:00
    End Date                     2018-11-09 00:00:00
    Value                        220825
    Amendment Date               2020-01-22 00:00:00
    Amendment Start Date         2017-11-10 00:00:00
    Amendments Value             220825
    Description                  Technical Developer
    Agency Ref ID                4500000116
    UNSPSC Code                  80111600
    UNSPSC Title                 Temporary personnel services
  BECOMES:
    entity.legal_name             Code from the Corner Pty Ltd                 conf 0.95
    entity.abn                    29102997071                                  conf 0.98
    address.full                   1/44 Shadforth Street, MOSMAN, NSW, 2088, AU conf 0.9
    address.locality               MOSMAN                                       conf 0.95
    address.state                  NSW                                          conf 0.98
    address.postcode               2088                                         conf 0.98
    address.country                AUSTRALIA                                    conf 0.98
    record id  CN3468654-A2

## To decide

  python -m src.approve historical-australian-government-contrac-5c7fa69b --yes --by <your name>
  python -m src.approve historical-australian-government-contrac-5c7fa69b --no  --by <your name> --note "what is wrong"
