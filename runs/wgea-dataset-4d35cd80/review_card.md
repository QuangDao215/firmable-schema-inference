# Review: WGEA Dataset

Publisher     Workplace Gender Equality Agency (WGEA)
Licence       Creative Commons Attribution 3.0 Australia
File          ZIP  (catalogue said CSV)
Landing page  https://data.gov.au/dataset/wgea-dataset

The agent took 0 correction round(s). It resolved everything.

## What it decided

Record id: hash_of_fields over ['employer_abn', 'question_index', 'reporting_year']
  why: Composite key ensuring unique identification per employer questionnaire response record.
Source reliability: 0.95

## Fields it mapped

  canonical field            from column                 column  field  conf
  entity.legal_name          employer_name                 100%   100%  0.95
      strip -> collapse_spaces
  entity.abn                 employer_abn                  100%   100%   1.0
      strip -> abn_normalize
  entity.industry_code       anzsic_code                   100%   100%  0.95
      strip

## What it refused to map

This is where you are most likely to disagree.

  entity.trading_name        Source does not distinguish or provide separate trading names.
  entity.acn                 ACN is not explicitly provided in the dataset.
  entity.nzbn                Dataset contains Australian entities only.
  entity.entity_type         Legal entity type is not provided in source data.
  entity.status              Entity operational status is not recorded.
  entity.website             Entity website URL is not provided.
  entity.date_registered     Entity registration date is not provided.
  address.full               No address details are included in the dataset.
  address.locality           Locality is not provided in the dataset.
  address.state              State is not provided in the dataset.
  address.postcode           Postcode is not provided in the dataset.
  address.country            Country address is not provided in the dataset.

  Source columns left alone: 17
    reporting_year                 Represents a reporting period range rather than a specific d
    corporate_group_name           Refers to the parent corporate group rather than the specifi
    is_relevant_employer           Administrative compliance flag not modeled in canonical enti
    employer_size                  Categorical headcount bracket not modeled in canonical entit
    anzsic_division                Text description of ANZSIC division represented by anzsic_co
    anzsic_subdivision             Text description of ANZSIC subdivision represented by anzsic
    anzsic_group                   Text description of ANZSIC group represented by anzsic_code.
    anzsic_class                   Text description of ANZSIC class represented by anzsic_code.
    ... and 9 more, all listed in the config

## What broke on 800 rows the model never saw

  Nothing.

## Five real records

  RAW:
    reporting_year               2024-25
    corporate_group_name         Mayflower Brighton
    employer_name                Mayflower Brighton
    employer_abn                 57004507644
    is_relevant_employer         TRUE
    employer_size                250- 499
    anzsic_code                  8601
    anzsic_division              Health Care and Social Assistance
    anzsic_subdivision           Residential Care Services
    anzsic_group                 Residential Care Services
    anzsic_class                 Aged Care Residential Services
    section                      Action on gender equality
    subsection                   Gender Pay Gap
    question_index               EAct.Act.N
  BECOMES:
    entity.legal_name             Mayflower Brighton                           conf 0.95
    entity.abn                    57004507644                                  conf 1.0
    entity.industry_code          8601                                         conf 0.95
    record id  ae8d00fecf9591ca

  RAW:
    reporting_year               2024-25
    corporate_group_name         Mayflower Brighton
    employer_name                Mayflower Reservoir Limited
    employer_abn                 73130299544
    is_relevant_employer         TRUE
    employer_size                250- 499
    anzsic_code                  8601
    anzsic_division              Health Care and Social Assistance
    anzsic_subdivision           Residential Care Services
    anzsic_group                 Residential Care Services
    anzsic_class                 Aged Care Residential Services
    section                      Action on gender equality
    subsection                   Gender Pay Gap
    question_index               EAct.Act.N
  BECOMES:
    entity.legal_name             Mayflower Reservoir Limited                  conf 0.95
    entity.abn                    73130299544                                  conf 1.0
    entity.industry_code          8601                                         conf 0.95
    record id  83421f60b01fd5c0

  RAW:
    reporting_year               2024-25
    corporate_group_name         Loscam Holdings Australia Pty Ltd
    employer_name                Loscam Australia Pty Ltd
    employer_abn                 26006440991
    is_relevant_employer         TRUE
    employer_size                <250
    anzsic_code                  6639
    anzsic_division              Rental, Hiring and Real Estate Services
    anzsic_subdivision           Rental and Hiring Services (except Real Estate)
    anzsic_group                 Other Goods and Equipment Rental and Hiring
    anzsic_class                 Other Goods and Equipment Rental and Hiring n.e.c.
    section                      Action on gender equality
    subsection                   Gender Pay Gap
    question_index               EAct.Act.N
  BECOMES:
    entity.legal_name             Loscam Australia Pty Ltd                     conf 0.95
    entity.abn                    26006440991                                  conf 1.0
    entity.industry_code          6639                                         conf 0.95
    record id  f8c219754f88c67c

## To decide

  python -m src.approve wgea-dataset-4d35cd80 --yes --by <your name>
  python -m src.approve wgea-dataset-4d35cd80 --no  --by <your name> --note "what is wrong"
