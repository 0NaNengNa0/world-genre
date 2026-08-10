export type CountrySummary = {
  code: string
  name: string
  artist_count: number
  top_genres: string[]
  distinctive_genres: string[]
  top_artists: string[]
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

export type CountryDetail = {
  code: string
  name: string
  artist_count: number
  artists: string[]
  /** Share of what the country actually plays. */
  popularity: GenreBreakdown
  /** Share of what sets it apart from other countries (TF-IDF weighted). */
  distinctiveness: GenreBreakdown
  cover_image: string | null
}

type CountriesResponse = {
  countries: CountrySummary[]
}

export async function fetchCountries(): Promise<CountrySummary[]> {
  const response = await fetch('/api/countries')
  if (!response.ok) {
    throw new Error(`Failed to fetch countries (${response.status})`)
  }
  const data = (await response.json()) as CountriesResponse
  return data.countries
}

export async function fetchCountryDetail(code: string): Promise<CountryDetail> {
  const response = await fetch(`/api/countries/${code}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch ${code} (${response.status})`)
  }
  return (await response.json()) as CountryDetail
}
