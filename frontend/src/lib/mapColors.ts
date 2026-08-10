import type { CountrySummary } from '../api/countries'

export type ColorMode = 'genre' | 'domestic' | 'none'

/** Muted grey for countries the current mode can say nothing about. Kept
 *  visually distinct from every data colour so "no data" never reads as a
 *  low value - the most common way a choropleth misleads. */
export const NO_DATA_COLOR = '#2f2f40'

/**
 * Palette for dominant-genre shading. Qualitative, not sequential: genres
 * are categories with no ordering, so a gradient would imply a ranking that
 * doesn't exist.
 */
const GENRE_PALETTE = [
  '#6366f1', '#ec4899', '#14b8a6', '#f59e0b', '#8b5cf6',
  '#06b6d4', '#ef4444', '#84cc16', '#f97316', '#3b82f6',
  '#a855f7', '#10b981',
]

/**
 * Sequential scale for domestic share, light (imported) to dark (domestic).
 * Ordered data, so an ordered ramp is the honest encoding.
 */
const DOMESTIC_RAMP = ['#dbeafe', '#93c5fd', '#60a5fa', '#3b82f6', '#2563eb', '#1d4ed8']

/**
 * Assigns a colour to each genre that appears as some country's top genre.
 *
 * Built from the data rather than hardcoded, so the legend only ever lists
 * genres actually present. Sorted by how many countries lead with each genre,
 * which keeps colours stable between renders and gives the most common genres
 * the most distinguishable hues.
 */
export function buildGenreColorMap(countries: CountrySummary[]): Map<string, string> {
  const counts = new Map<string, number>()
  for (const country of countries) {
    const top = country.top_genres[0]
    if (top) counts.set(top, (counts.get(top) ?? 0) + 1)
  }

  const ordered = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([genre]) => genre)

  return new Map(ordered.map((genre, i) => [genre, GENRE_PALETTE[i % GENRE_PALETTE.length]]))
}

/** Bucket a 0-100 domestic share onto the sequential ramp. */
export function domesticColor(percentage: number): string {
  const clamped = Math.min(100, Math.max(0, percentage))
  const index = Math.min(
    DOMESTIC_RAMP.length - 1,
    Math.floor((clamped / 100) * DOMESTIC_RAMP.length),
  )
  return DOMESTIC_RAMP[index]
}

/**
 * Fill colour for one country under the current mode, or null to mean
 * "no data" (the caller applies NO_DATA_COLOR).
 *
 * Domestic mode deliberately withholds a colour below `minCoverage`. A share
 * computed from 2 percent of a country's streams is not a measurement, and
 * colouring it identically to one computed from 90 percent would let the map
 * assert something the data doesn't support.
 */
export function countryColor(
  country: CountrySummary | undefined,
  mode: ColorMode,
  genreColors: Map<string, string>,
  minCoverage = 20,
): string | null {
  if (!country) return null
  if (mode === 'none') return null

  if (mode === 'genre') {
    const top = country.top_genres[0]
    return top ? genreColors.get(top) ?? null : null
  }

  const share = country.domestic_share
  if (!share || share.coverage_percentage < minCoverage) return null
  return domesticColor(share.domestic_percentage)
}

/** Legend entries for the current mode, in display order. */
export function legendFor(
  mode: ColorMode,
  genreColors: Map<string, string>,
): { label: string; color: string }[] {
  if (mode === 'genre') {
    return [...genreColors.entries()].map(([label, color]) => ({ label, color }))
  }
  if (mode === 'domestic') {
    return DOMESTIC_RAMP.map((color, i) => ({
      label: `${Math.round((i / DOMESTIC_RAMP.length) * 100)}–${Math.round(
        ((i + 1) / DOMESTIC_RAMP.length) * 100,
      )}%`,
      color,
    }))
  }
  return []
}
