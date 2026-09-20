# Review: ASIC - Company Dataset

Publisher     Australian Securities and Investments Commission (ASIC)
Licence       Creative Commons Attribution 3.0 Australia
File          TSV  (catalogue said CSV)
Landing page  https://data.gov.au/dataset/asic-companies

The agent took 1 correction round(s). It resolved everything.

## What it decided

Record id: hash_of_fields over ['ACN', 'Company Name']
  why: ACN alone is not unique because the dataset includes historical name records per company.
Source reliability: 0.98

## Fields it mapped

  canonical field            from column                 column  field  conf
  entity.legal_name          Company Name                  100%   100%  0.95
      coalesce -> strip -> collapse_spaces
  entity.acn                 ACN                           100%   100%   1.0
      acn_normalize
  entity.abn                 ABN                           100%    96%  0.95
      null_if -> abn_normalize
  entity.entity_type         Type                          100%   100%  0.95
      map_values
  entity.status              Status                        100%   100%  0.95
      map_values
  entity.date_registered     Date of Registration          100%   100%  0.95
      parse_date

## What it refused to map

This is where you are most likely to disagree.

  entity.trading_name        Source only contains registered corporate legal names, not trading names.
  entity.nzbn                Dataset covers Australian entities; New Zealand Business Number is not applicable.
  entity.website             Entity website URL is not captured in this ASIC registry dataset.
  entity.industry_code       Industry/ANZSIC classification codes are not provided in this extract.
  address.full               No address fields are present in this dataset.
  address.locality           No locality data is present in this dataset.
  address.state              Only historical state of registration is present, not entity address state.
  address.postcode           No postcode data is present in this dataset.
  address.country            No country address data is present in this dataset.

  Source columns left alone: 7
    Class                          Internal ASIC company structure classification not represent
    Sub Class                      Internal ASIC company structure sub-classification not repre
    Previous State of Registration Refers to historical state corporate registry, not physical 
    State Registration number      Historical pre-ACN state registration identifier.
    Modified since last report     Empty dataset export change-tracking flag.
    Current Name Indicator         Indicator flag distinguishing current vs previous names, not
    Current Name Start Date        Effective date of current name change, not represented in ca

## What broke on 800 rows the model never saw

  Nothing.

## Correction rounds

  round 1: failures 2 -> 0, kept

## Five real records

  RAW:
    Company Name                 SONNERDALE RICHARDSON DAVID BROWN LTD
    ACN                          000008640
    Type                         APTY
    Class                        LMSH
    Sub Class                    PROP
    Status                       REGD
    Date of Registration         09/02/1920
    Previous State of Registrati NSW
    State Registration number    00656422
    ABN                          15000008640
    Current Name                 DAVID BROWN SANTASALO AUSTRALIA PTY LTD
  BECOMES:
    entity.legal_name             DAVID BROWN SANTASALO AUSTRALIA PTY LTD      conf 0.95
    entity.acn                    000008640                                    conf 1.0
    entity.abn                    15000008640                                  conf 0.95
    entity.entity_type            company                                      conf 0.95
    entity.status                 active                                       conf 0.95
    entity.date_registered        1920-02-09                                   conf 0.95
    record id  7ad50e8c885954a4

  RAW:
    Company Name                 CHARLES PARSONS & CO PTY LTD
    ACN                          000008668
    Type                         APTY
    Class                        LMSH
    Sub Class                    PROP
    Status                       REGD
    Date of Registration         12/02/1920
    Previous State of Registrati NSW
    State Registration number    00657027
    Current Name Indicator       Y
    ABN                          96000008668
  BECOMES:
    entity.legal_name             CHARLES PARSONS & CO PTY LTD                 conf 0.95
    entity.acn                    000008668                                    conf 1.0
    entity.abn                    96000008668                                  conf 0.95
    entity.entity_type            company                                      conf 0.95
    entity.status                 active                                       conf 0.95
    entity.date_registered        1920-02-12                                   conf 0.95
    record id  4c8e5e78e38a2da6

  RAW:
    Company Name                 ALLIED MILLS INDUSTRIES PTY LTD
    ACN                          000008739
    Type                         APTY
    Class                        LMSH
    Sub Class                    PROP
    Status                       REGD
    Date of Registration         27/02/1920
    Previous State of Registrati NSW
    State Registration number    00659805
    ABN                          24000008739
    Current Name                 ALLIED PINNACLE NSW PTY LIMITED
  BECOMES:
    entity.legal_name             ALLIED PINNACLE NSW PTY LIMITED              conf 0.95
    entity.acn                    000008739                                    conf 1.0
    entity.abn                    24000008739                                  conf 0.95
    entity.entity_type            company                                      conf 0.95
    entity.status                 active                                       conf 0.95
    entity.date_registered        1920-02-27                                   conf 0.95
    record id  dd31659ce545702f

## To decide

  python -m src.approve asic-company-dataset-7b8656f9 --yes --by <your name>
  python -m src.approve asic-company-dataset-7b8656f9 --no  --by <your name> --note "what is wrong"
