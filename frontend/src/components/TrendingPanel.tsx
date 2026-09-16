import { useEffect, useState } from 'react'

import { fetchTrendingGenres, type CountrySummary, type TrendingGenre } from '../api/countries'
import { CountryFlag } from './CountryFlag'

type Props = {
  countries: CountrySummary[]
}

/**
 * Genres gaining or losing ground in each country, week over week.
 *
 * `delta` is a change in SHARE, in percentage points - each genre as a
 * fraction of its country's total, compared against the snapshot roughly
 * seven days earlier (sql/bigquery/queries/trending_genres.sql).
 *
 * Both of those are corrections. Comparing consecutive days showed nothing:
 * chart churn is high but happens mostly within the same genres, so a
 * country's genre mix barely moves overnight. Ranking by raw score favoured
 * big genres in big markets, where the same underlying nothing moves more
 * points. The query returns 25 of each direction; this renders 15.
 */
export function TrendingPanel({ countries }: Props) {
  const [genres, setGenres] = useState<TrendingGenre[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchTrendingGenres()
      .then((data) => !cancelled && setGenres(data))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [])

  // Falls back to the raw code only if a country somehow isn't in the loaded
  // list - which shouldn't happen, since both come from the same pipeline.
  const nameOf = (code: string) =>
    countries.find((c) => c.code === code)?.name ?? code

  if (error) return <p className="notice notice--error">{error}</p>
  if (!genres) return <p className="notice">Loading trends…</p>

  if (genres.length === 0) {
    return (
      <div className="notice">
        <strong>No trends yet.</strong> This compares each country against its own
        snapshot about a week earlier, so it needs the pipeline to have run on at least
        two different days. Come back after the next scheduled run.
      </div>
    )
  }

  const rising = genres.filter((g) => g.delta > 0).slice(0, 15)
  const falling = genres.filter((g) => g.delta < 0).slice(-15).reverse()

  // Percentage points, so a raw number would render as 0.11999999999999. Two
  // decimals is also about the resolution the underlying counts support -
  // showing more would imply precision the data does not have.
  const pts = (d: number) => `${d > 0 ? '+' : ''}${d.toFixed(2)}`

  const row = (g: TrendingGenre) => (
    <li key={`${g.country_code}-${g.genre}`} className="trend__item">
      <span className="trend__flag">
        <CountryFlag code={g.country_code} />
      </span>
      <span className="trend__country">{nameOf(g.country_code)}</span>
      <span className="chip">{g.genre}</span>
      <span className={g.delta > 0 ? 'trend__up' : 'trend__down'}>
        {pts(g.delta)} pts
      </span>
    </li>
  )

  return (
    <div className="trend">
      <section>
        <h3 className="detail__subtitle">Rising</h3>
        {rising.length ? <ul className="trend__list">{rising.map(row)}</ul> : (
          <p className="detail__empty">Nothing rising this week.</p>
        )}
      </section>
      <section>
        <h3 className="detail__subtitle">Falling</h3>
        {falling.length ? <ul className="trend__list">{falling.map(row)}</ul> : (
          <p className="detail__empty">Nothing falling this week.</p>
        )}
      </section>
    </div>
  )
}
