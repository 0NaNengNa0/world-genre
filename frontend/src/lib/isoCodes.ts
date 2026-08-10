/**
 * ISO 3166-1 alpha-2 -> alpha-3 and numeric, for the countries this project
 * covers.
 *
 * GENERATED - do not hand-edit. Regenerate with:
 *   cd backend && python -m scripts.generate_iso_codes
 *
 * Why it exists: the pipeline keys countries by alpha-2 (kworb's codes),
 * but world map geometry files key features by alpha-3 or by numeric code
 * depending on the source. Deriving the mapping from pycountry keeps it
 * exact instead of hand-typing 76 entries, which is the same class of error
 * that made South Korea silently return no data.
 */
export type IsoCodes = { a3: string; numeric: string }

export const ISO_CODES: Record<string, IsoCodes> = {
  ad: { a3: 'AND', numeric: '020' },
  ae: { a3: 'ARE', numeric: '784' },
  ar: { a3: 'ARG', numeric: '032' },
  at: { a3: 'AUT', numeric: '040' },
  au: { a3: 'AUS', numeric: '036' },
  be: { a3: 'BEL', numeric: '056' },
  bg: { a3: 'BGR', numeric: '100' },
  bo: { a3: 'BOL', numeric: '068' },
  br: { a3: 'BRA', numeric: '076' },
  by: { a3: 'BLR', numeric: '112' },
  ca: { a3: 'CAN', numeric: '124' },
  ch: { a3: 'CHE', numeric: '756' },
  cl: { a3: 'CHL', numeric: '152' },
  co: { a3: 'COL', numeric: '170' },
  cr: { a3: 'CRI', numeric: '188' },
  cy: { a3: 'CYP', numeric: '196' },
  cz: { a3: 'CZE', numeric: '203' },
  de: { a3: 'DEU', numeric: '276' },
  dk: { a3: 'DNK', numeric: '208' },
  do: { a3: 'DOM', numeric: '214' },
  ec: { a3: 'ECU', numeric: '218' },
  ee: { a3: 'EST', numeric: '233' },
  eg: { a3: 'EGY', numeric: '818' },
  es: { a3: 'ESP', numeric: '724' },
  fi: { a3: 'FIN', numeric: '246' },
  fr: { a3: 'FRA', numeric: '250' },
  gb: { a3: 'GBR', numeric: '826' },
  gr: { a3: 'GRC', numeric: '300' },
  gt: { a3: 'GTM', numeric: '320' },
  hk: { a3: 'HKG', numeric: '344' },
  hn: { a3: 'HND', numeric: '340' },
  hu: { a3: 'HUN', numeric: '348' },
  id: { a3: 'IDN', numeric: '360' },
  ie: { a3: 'IRL', numeric: '372' },
  il: { a3: 'ISR', numeric: '376' },
  in: { a3: 'IND', numeric: '356' },
  is: { a3: 'ISL', numeric: '352' },
  it: { a3: 'ITA', numeric: '380' },
  jp: { a3: 'JPN', numeric: '392' },
  kr: { a3: 'KOR', numeric: '410' },
  kz: { a3: 'KAZ', numeric: '398' },
  lt: { a3: 'LTU', numeric: '440' },
  lu: { a3: 'LUX', numeric: '442' },
  lv: { a3: 'LVA', numeric: '428' },
  ma: { a3: 'MAR', numeric: '504' },
  mt: { a3: 'MLT', numeric: '470' },
  mx: { a3: 'MEX', numeric: '484' },
  my: { a3: 'MYS', numeric: '458' },
  ng: { a3: 'NGA', numeric: '566' },
  ni: { a3: 'NIC', numeric: '558' },
  nl: { a3: 'NLD', numeric: '528' },
  no: { a3: 'NOR', numeric: '578' },
  nz: { a3: 'NZL', numeric: '554' },
  pa: { a3: 'PAN', numeric: '591' },
  pe: { a3: 'PER', numeric: '604' },
  ph: { a3: 'PHL', numeric: '608' },
  pk: { a3: 'PAK', numeric: '586' },
  pl: { a3: 'POL', numeric: '616' },
  pt: { a3: 'PRT', numeric: '620' },
  py: { a3: 'PRY', numeric: '600' },
  ro: { a3: 'ROU', numeric: '642' },
  ru: { a3: 'RUS', numeric: '643' },
  sa: { a3: 'SAU', numeric: '682' },
  se: { a3: 'SWE', numeric: '752' },
  sg: { a3: 'SGP', numeric: '702' },
  sk: { a3: 'SVK', numeric: '703' },
  sv: { a3: 'SLV', numeric: '222' },
  th: { a3: 'THA', numeric: '764' },
  tr: { a3: 'TUR', numeric: '792' },
  tw: { a3: 'TWN', numeric: '158' },
  ua: { a3: 'UKR', numeric: '804' },
  us: { a3: 'USA', numeric: '840' },
  uy: { a3: 'URY', numeric: '858' },
  ve: { a3: 'VEN', numeric: '862' },
  vn: { a3: 'VNM', numeric: '704' },
  za: { a3: 'ZAF', numeric: '710' },
}

/** alpha-3 -> alpha-2, for looking up a clicked map feature. */
export const A3_TO_A2: Record<string, string> = Object.fromEntries(
  Object.entries(ISO_CODES).map(([a2, { a3 }]) => [a3, a2]),
)

/** numeric -> alpha-2, for map sources that key on numeric ids. */
export const NUMERIC_TO_A2: Record<string, string> = Object.fromEntries(
  Object.entries(ISO_CODES).map(([a2, { numeric }]) => [numeric, a2]),
)
