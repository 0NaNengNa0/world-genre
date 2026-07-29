export type CountrySummary = {
  code: string
  name: string
  artist_count: number
  top_genres: string[]
  top_artists: string[]
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
