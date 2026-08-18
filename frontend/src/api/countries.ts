/**
 * Where the API lives.
 *
 * Empty by default, which keeps every request a same-origin relative path -
 * exactly what the Vite dev proxy expects, so local development needs no
 * configuration at all.
 *
 * It has to be configurable because the deployed frontend is static hosting
 * with no proxy behind it: a relative `/api/countries` there resolves against
 * the hosting domain and 404s. Set VITE_API_BASE_URL at build time to the
 * API's own origin.
 *
 * Read via `import.meta.env`, so the value is baked in when the bundle is
 * built rather than read at runtime - there is no server to read env from.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export type DomesticShare = {
  /** Share of attributable streams going to artists from this country. */
  domestic_percentage: number
  /** How much of the country's streaming could be attributed at all. The
   *  figure above is uninterpretable without it. */
  coverage_percentage: number
  classified_entries: number
  total_entries: number
}

export type ChartTrack = {
  position: number
  track: string | null
  artist: string
  daily_streams: number | null
  days_on_chart: number | null
}

export type CountrySummary = {
  code: string
  name: string
  artist_count: number
  top_genres: string[]
  distinctive_genres: string[]
  top_artists: string[]
  /** Null until artist origins have been resolved by the pipeline. */
  domestic_share: DomesticShare | null
  cover_image: string | null
}

export type GenreScore = {
  genre: string
  score: number
  distinctiveness: number
  /** Which extractors agreed: lastfm, musicbrainz, or both. */
  sources: string[]
}

export type GenreShare = GenreScore & {
  /** Share of the country's total genre weight, 0-100. */
  percentage: number
}

export type GenreBreakdown = {
  genres: GenreShare[]
  /** Everything outside `genres`, so slices + this add up to 100. */
  other_percentage: number
  other_genre_count: number
}

export type ArtistPopularity = {
  artist: string
  /** Spotify plays on this country's chart. */
  streams: number | null
  /** Last.fm users in this country — a different population entirely. */
  lastfm_listeners: number | null
  /** Deezer follows, global rather than country-scoped. */
  deezer_fans: number | null
}

export type CountryDetail = {
  code: string
  name: string
  artist_count: number
  artists: string[]
  /** Share of what the country actually plays. */
  popularity: GenreBreakdown
  /** Share of what sets it apart from other countries (TF-IDF weighted). */
  distinctiveness: GenreBreakdown
  domestic_share: DomesticShare | null
  /** Actual charting songs with measured streams, from the fact table. */
  top_tracks: ChartTrack[]
  hidden_gems: HiddenGem[]
  artist_popularity: ArtistPopularity[]
  cover_image: string | null
}

export type HiddenGem = {
  artist: string
  streams: number
  best_position: number | null
  /** Out of total_countries — what makes the "hidden" claim checkable. */
  country_count: number
  total_countries: number
  gem_score: number
}

export type GlobalArtist = {
  artist: string
  streams: number
  previous_streams: number | null
  /** Null until a second snapshot exists — not the same as unchanged. */
  delta: number | null
  country_count: number
  origin_country: string | null
}

export type GenreArtist = {
  artist: string
  /** Null when linked by tags but not currently charting. */
  streams: number | null
  best_position: number | null
}

export type GenreDetail = {
  genre: string
  country_code: string
  country_name: string
  /** Null for genres Last.fm has no wiki entry for. */
  summary: string | null
  url: string | null
  score: number | null
  distinctiveness: number | null
  artists: GenreArtist[]
}

export async function fetchGenreDetail(
  code: string,
  genre: string,
): Promise<GenreDetail> {
  const response = await fetch(
    apiUrl(`/api/countries/${code}/genres/${encodeURIComponent(genre)}`),
  )
  if (!response.ok) {
    throw new Error(`Failed to fetch ${genre} (${response.status})`)
  }
  return (await response.json()) as GenreDetail
}

export async function fetchGlobalArtists(): Promise<GlobalArtist[]> {
  const response = await fetch(apiUrl('/api/artists/global'))
  if (!response.ok) {
    throw new Error(`Failed to fetch global artists (${response.status})`)
  }
  const data = (await response.json()) as { artists: GlobalArtist[] }
  return data.artists
}

export type TrendingGenre = {
  country_code: string
  genre: string
  score: number
  previous_score: number
  delta: number
}

export async function fetchTrendingGenres(): Promise<TrendingGenre[]> {
  const response = await fetch(apiUrl('/api/genres/trending'))
  if (!response.ok) {
    throw new Error(`Failed to fetch trending genres (${response.status})`)
  }
  const data = (await response.json()) as { genres: TrendingGenre[] }
  return data.genres
}

type CountriesResponse = {
  countries: CountrySummary[]
}

export async function fetchCountries(): Promise<CountrySummary[]> {
  const response = await fetch(apiUrl('/api/countries'))
  if (!response.ok) {
    throw new Error(`Failed to fetch countries (${response.status})`)
  }
  const data = (await response.json()) as CountriesResponse
  return data.countries
}

export async function fetchCountryDetail(code: string): Promise<CountryDetail> {
  const response = await fetch(apiUrl(`/api/countries/${code}`))
  if (!response.ok) {
    throw new Error(`Failed to fetch ${code} (${response.status})`)
  }
  return (await response.json()) as CountryDetail
}
