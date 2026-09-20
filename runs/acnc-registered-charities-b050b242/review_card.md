# Review: ACNC Registered Charities

Publisher     Australian Charities and Not-for-profits Commission (ACNC)
Licence       Creative Commons Attribution 3.0 Australia
File          CSV
Landing page  https://data.gov.au/dataset/acnc-register

The agent took 0 correction round(s). It resolved everything.

## What it decided

Record id: hash_of_fields over ['Charity_Legal_Name', 'ABN']
  why: ABN has missing values (2.5%), so hashing Charity_Legal_Name with ABN ensures a unique, persistent identifier.
Source reliability: 0.95

## Fields it mapped

  canonical field            from column                 column  field  conf
  entity.abn                 ABN                            98%    99%  0.99
      abn_normalize
  entity.legal_name          Charity_Legal_Name            100%   100%  0.99
      strip -> collapse_spaces
  entity.trading_name        Other_Organisation_Names       10%    13%  0.85
      strip -> collapse_spaces
  entity.website             Charity_Website                62%    67%  0.95
      strip
  entity.date_registered     Registration_Date             100%   100%  0.98
      parse_date
  address.locality           Town_City                      88%    87%  0.95
      strip -> collapse_spaces
  address.state              State                          88%    86%  0.99
      state_normalize
  address.postcode           Postcode                       88%    87%  0.99
      postcode_extract
  address.country            Country                        85%    87%  0.95
      strip

## What it refused to map

This is where you are most likely to disagree.

  entity.acn                 ACN is not provided as a distinct field in this dataset.
  entity.nzbn                Source dataset covers Australian registered charities only.
  entity.entity_type         Legal entity structure type is not explicitly provided as a structured enum field.
  entity.status              Registration status column is not present in this dataset.
  entity.industry_code       ANZSIC or equivalent industry code is not included in the source data.
  address.full               Source splits address across multiple fields and address components are mapped to discrete canonical fields.

  Source columns left alone: 60
    Address_Type                   Metadata indicating the classification of the address, not p
    Address_Line_1                 Discrete street address line omitted in favor of structured 
    Address_Line_2                 Secondary address line omitted in favor of structured compon
    Address_Line_3                 Tertiary address line omitted in favor of structured compone
    Date_Organisation_Established  Date founded/established prior to regulator registration; do
    Charity_Size                   Financial reporting size tier (Small, Medium, Large) not mod
    Number_of_Responsible_Persons  Governance count metric outside entity core attributes.
    Financial_Year_End             Accounting cycle date not represented in canonical entity sc
    ... and 52 more, all listed in the config

## What broke on 800 rows the model never saw

  Nothing.

## Five real records

  RAW:
    ABN                          98676719663
    Charity_Legal_Name           AUTISTIC HAUS LIMITED
    Address_Type                 Business
    Address_Line_1               12 Dunsford St
    Town_City                    Zillmere
    State                        QLD
    Postcode                     4034
    Country                      Australia
    Charity_Website              www.autistic-haus.org.au
    Registration_Date            19/04/2024
    Date_Organisation_Establishe 19/04/2024
  BECOMES:
    entity.abn                    98676719663                                  conf 0.99
    entity.legal_name             AUTISTIC HAUS LIMITED                        conf 0.99
    entity.website                www.autistic-haus.org.au                     conf 0.95
    entity.date_registered        2024-04-19                                   conf 0.98
    address.locality               Zillmere                                     conf 0.95
    address.state                  QLD                                          conf 0.99
    address.postcode               4034                                         conf 0.99
    address.country                Australia                                    conf 0.95
    record id  865045c2257ae16b

  RAW:
    ABN                          57680566536
    Charity_Legal_Name           PROJECT AJR LTD
    Address_Type                 Business
    Country                      Australia
    Registration_Date            06/09/2024
    Date_Organisation_Establishe 06/09/2024
  BECOMES:
    entity.abn                    57680566536                                  conf 0.99
    entity.legal_name             PROJECT AJR LTD                              conf 0.99
    entity.date_registered        2024-09-06                                   conf 0.98
    address.country                Australia                                    conf 0.95
    record id  1ba886328d064984

  RAW:
    ABN                          30589053959
    Charity_Legal_Name           Celebration of African Australians Inc
    Address_Type                 Business
    Address_Line_1               U 15 56 Kunapalari St
    Town_City                    Throsby
    State                        ACT
    Postcode                     2914
    Country                      Australia
    Charity_Website              www.celebrateafricanaustraliansact.org
    Registration_Date            01/04/2021
    Date_Organisation_Establishe 07/03/2012
  BECOMES:
    entity.abn                    30589053959                                  conf 0.99
    entity.legal_name             Celebration of African Australians Inc       conf 0.99
    entity.website                www.celebrateafricanaustraliansact.org       conf 0.95
    entity.date_registered        2021-04-01                                   conf 0.98
    address.locality               Throsby                                      conf 0.95
    address.state                  ACT                                          conf 0.99
    address.postcode               2914                                         conf 0.99
    address.country                Australia                                    conf 0.95
    record id  ada9f1e62c5bf4e9

## To decide

  python -m src.approve acnc-registered-charities-b050b242 --yes --by <your name>
  python -m src.approve acnc-registered-charities-b050b242 --no  --by <your name> --note "what is wrong"
