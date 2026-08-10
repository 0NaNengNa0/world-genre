import { A3_TO_A2, ISO_CODES, NUMERIC_TO_A2 } from './isoCodes'

/**
 * Minimal GeoJSON typing - only the parts this app reads. Deliberately loose
 * about properties, because which key holds a country's ISO code differs
 * between map sources (see countryCodeOf).
 */
export type GeoGeometry =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] }

export type GeoFeature = {
  type: 'Feature'
  id?: string | number
  properties?: Record<string, unknown> | null
  geometry: GeoGeometry | null
}

export type GeoFeatureCollection = {
  type: 'FeatureCollection'
  features: GeoFeature[]
}

const CODE_PROPERTY_KEYS = [
  'ISO_A2', 'iso_a2', 'ISO_A3', 'iso_a3',
  'ISO3166-1-Alpha-2', 'ISO3166-1-Alpha-3',
  'adm0_a3', 'ADM0_A3', 'id', 'ISO_N3', 'iso_n3',
]

/**
 * Resolve a map feature to one of our alpha-2 country codes, or null.
 *
 * Map sources disagree on where the country code lives: Natural Earth uses
 * properties.ISO_A2/ISO_A3, world-atlas uses a numeric feature id, and
 * world.geo.json uses an alpha-3 feature id. Rather than couple the app to
 * one source, this tries each convention. That means the map source can be
 * swapped by changing a single URL, instead of by rewriting the lookup.
 */
export function countryCodeOf(feature: GeoFeature): string | null {
  const candidates: unknown[] = [feature.id]
  const properties = feature.properties ?? {}
  for (const key of CODE_PROPERTY_KEYS) {
    if (key in properties) candidates.push(properties[key])
  }

  for (const raw of candidates) {
    if (raw === null || raw === undefined) continue
    const value = String(raw).trim()
    if (!value || value === '-99') continue // Natural Earth's "unknown" marker

    if (/^[A-Za-z]{2}$/.test(value)) {
      const a2 = value.toLowerCase()
      if (a2 in ISO_CODES) return a2
    }
    if (/^[A-Za-z]{3}$/.test(value)) {
      const a3 = value.toUpperCase()
      if (a3 in A3_TO_A2) return A3_TO_A2[a3]
    }
    // Numeric ids arrive as 4, "4" or "004" depending on the source.
    if (/^\d+$/.test(value)) {
      const padded = value.padStart(3, '0')
      if (padded in NUMERIC_TO_A2) return NUMERIC_TO_A2[padded]
    }
  }
  return null
}

/**
 * Equirectangular projection: longitude/latitude straight to x/y.
 *
 * Hand-rolled rather than pulling in d3-geo, which would be a sizeable
 * dependency for one linear transform. The trade-off is real and worth
 * naming: equirectangular badly distorts area near the poles, so Greenland
 * and Antarctica look enormous. For a "click your country" map that's
 * acceptable - and no country in this dataset sits far enough north or south
 * for it to affect clicking.
 */
export function project(
  lon: number,
  lat: number,
  width: number,
  height: number,
): [number, number] {
  return [((lon + 180) / 360) * width, ((90 - lat) / 180) * height]
}

function ringToPath(ring: number[][], width: number, height: number): string {
  let path = ''
  for (let i = 0; i < ring.length; i++) {
    const point = ring[i]
    // Guard against malformed coordinates rather than emitting "NaN,NaN"
    // into the path, which makes the browser drop the whole shape silently.
    if (!point || point.length < 2) continue
    const [x, y] = project(point[0], point[1], width, height)
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue
    path += `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
  }
  return path ? `${path}Z` : ''
}

/** [minX, minY, maxX, maxY] in projected coordinates. */
export type Bounds = [number, number, number, number]

function ringBounds(ring: number[][], width: number, height: number): Bounds | null {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const point of ring) {
    if (!point || point.length < 2) continue
    const [x, y] = project(point[0], point[1], width, height)
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  return minX === Infinity ? null : [minX, minY, maxX, maxY]
}

/** All rings of a feature, outermost ring of each polygon first. */
function outerRings(geometry: GeoGeometry): number[][][] {
  if (geometry.type === 'Polygon') return geometry.coordinates.slice(0, 1)
  // Ring 0 of each polygon is its exterior; the rest are holes and can't
  // extend the bounds, so they're skipped.
  return geometry.coordinates.map((polygon) => polygon[0]).filter(Boolean)
}

/**
 * Bounds of the feature's LARGEST polygon, not of all of them combined.
 *
 * This is what makes "zoom to fit this country" usable. Several countries own
 * territory far from their mainland - Russia's Chukotka crosses the
 * antimeridian, the United States has Alaska and Hawaii - so a bounding box
 * over every polygon spans most of the map and "fitting" it just zooms back
 * out to the whole world. Taking the biggest piece frames the mainland, which
 * is what someone clicking Russia actually wants to see.
 *
 * Size is approximated by point count. It isn't area, but detail scales with
 * size in these datasets, and it's cheap and stable.
 */
export function featureBounds(
  feature: GeoFeature,
  width: number,
  height: number,
): Bounds | null {
  if (!feature.geometry) return null
  const rings = outerRings(feature.geometry)
  if (rings.length === 0) return null

  const largest = rings.reduce((a, b) => (b.length > a.length ? b : a))
  return ringBounds(largest, width, height)
}

/** SVG path data for one feature's geometry, or '' if it has none. */
export function featureToPath(
  feature: GeoFeature,
  width: number,
  height: number,
): string {
  const geometry = feature.geometry
  if (!geometry) return ''

  if (geometry.type === 'Polygon') {
    return geometry.coordinates.map((ring) => ringToPath(ring, width, height)).join('')
  }
  if (geometry.type === 'MultiPolygon') {
    return geometry.coordinates
      .map((polygon) => polygon.map((ring) => ringToPath(ring, width, height)).join(''))
      .join('')
  }
  return ''
}
