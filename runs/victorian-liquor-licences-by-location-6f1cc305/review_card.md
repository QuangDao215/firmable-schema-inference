# Review: Victorian liquor licences by location

Publisher     Liquor Control Victoria
Licence       Creative Commons Attribution 3.0 Australia
File          XLSX
Landing page  https://data.gov.au/dataset/victorian-liquor-licences-by-location

The agent took 1 correction round(s). It resolved everything.

## What it decided

Record id: source_field over ['Licence Num']
  why: Licence Num is fully populated and unique across records.
Source reliability: 0.9

## Fields it mapped

  canonical field            from column                 column  field  conf
  entity.legal_name          Licensee                      100%   100%  0.95
      strip -> collapse_spaces
  entity.trading_name        Trading As                    100%    99%  0.95
      strip -> collapse_spaces
  address.full               Address                       100%   100%   0.9
      concat -> collapse_spaces -> strip
  address.locality           Suburb                        100%   100%  0.95
      strip -> collapse_spaces -> title_case
  address.postcode           Postcode                      100%    99%  0.98
      null_if -> postcode_extract

## What it refused to map

This is where you are most likely to disagree.

  entity.abn                 No Australian Business Number column in the source dataset.
  entity.acn                 No Australian Company Number column in the source dataset.
  entity.nzbn                Source dataset is Australian and contains no NZBNs.
  entity.entity_type         No structured entity type column is present.
  entity.status              No explicit status column is provided in the source dataset.
  entity.website             No website URL column in the source dataset.
  entity.industry_code       No ANZSIC or industry classification code in the source dataset.
  entity.date_registered     No registration or licensing date provided in the source dataset.
  address.state              No explicit state column present in the source dataset.
  address.country            No explicit country column present in the source dataset.

  Source columns left alone: 16
    Licence Num                    Used as the primary record_id identifier.
    Category                       Licence permit category is specific to liquor licensing and 
    Trading Hours                  Licence trading hours condition is not modeled in the canoni
    After 11 pm                    Licence operational restriction flag not modeled in the cano
    Maximum Capacity               Column is 100% null.
    Latitude                       Geographic coordinates are not modeled in the address ontolo
    Longitude                      Geographic coordinates are not modeled in the address ontolo
    column_12                      Column is 100% null.
    ... and 8 more, all listed in the config

## What broke on 800 rows the model never saw

  Nothing.

## Correction rounds

  round 1: failures 3 -> 0, kept

## Five real records

  RAW:
    Licence Num                  31266277
    Licensee                     TRIPODI MARY
    Trading As                   PALADINO'S PIZZA & PASTA
    Category                     BYO Permit
    Trading Hours                BYO Permit
    After 11 pm                  99
    Address                      32 FAWKNER STREET
    Suburb                       WESTMEADOWS
    Postcode                     3049
    Latitude                     -37.67706200
    Longitude                    144.88674400
  BECOMES:
    entity.legal_name             TRIPODI MARY                                 conf 0.95
    entity.trading_name           PALADINO'S PIZZA & PASTA                     conf 0.95
    address.full                   32 FAWKNER STREET, WESTMEADOWS, 3049         conf 0.9
    address.locality               Westmeadows                                  conf 0.95
    address.postcode               3049                                         conf 0.98
    record id  31266277

  RAW:
    Licence Num                  31266366
    Licensee                     HONG KONG RACHEL PTY LTD
    Trading As                   HONG KONG BBQ & SEAFOOD
    Category                     BYO Permit
    Trading Hours                BYO Permit
    After 11 pm                  99
    Address                      118 HOPKINS STREET
    Suburb                       FOOTSCRAY
    Postcode                     3011
    Latitude                     -37.79980400
    Longitude                    144.90137100
  BECOMES:
    entity.legal_name             HONG KONG RACHEL PTY LTD                     conf 0.95
    entity.trading_name           HONG KONG BBQ & SEAFOOD                      conf 0.95
    address.full                   118 HOPKINS STREET, FOOTSCRAY, 3011          conf 0.9
    address.locality               Footscray                                    conf 0.95
    address.postcode               3011                                         conf 0.98
    record id  31266366

  RAW:
    Licence Num                  31266421
    Licensee                     PATIALA SHAHI RESTAURANTS & SWEETS PTY LTD
    Trading As                   TASTY JUNCTION INDIAN RESTAURANT
    Category                     BYO Permit
    Trading Hours                BYO Permit
    After 11 pm                  99
    Address                      17A HALL STREET
    Suburb                       MOONEE PONDS
    Postcode                     3039
    Latitude                     -37.76633100
    Longitude                    144.92306900
  BECOMES:
    entity.legal_name             PATIALA SHAHI RESTAURANTS & SWEETS PTY LTD   conf 0.95
    entity.trading_name           TASTY JUNCTION INDIAN RESTAURANT             conf 0.95
    address.full                   17A HALL STREET, MOONEE PONDS, 3039          conf 0.9
    address.locality               Moonee Ponds                                 conf 0.95
    address.postcode               3039                                         conf 0.98
    record id  31266421

## To decide

  python -m src.approve victorian-liquor-licences-by-location-6f1cc305 --yes --by <your name>
  python -m src.approve victorian-liquor-licences-by-location-6f1cc305 --no  --by <your name> --note "what is wrong"
