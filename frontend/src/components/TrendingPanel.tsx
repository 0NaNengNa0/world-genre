import { useEffect, useState } from 'react'

import { fetchTrendingGenres, type CountrySummary, type TrendingGenre } from '../api/countries'
import { CountryFlag } from './CountryFlag'

type Props = {
  countries: CountrySummary[]
}

/**
 * Genres rising and falling between the two most recent pipeline runs.
 *
 * Backed by a `LAG()` window function over snapshot dates
 * (sql/queries/trending_genres.sql). It legitimately returns nothing until
 * the pipeline has run on two different days, which is why the empty state
 * explains itself rather than looking like a failure.
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
        <strong>No trends yet.</strong> This compares each country's two most recent
        pipeline runs, so it needs the pipeline to have run on two different days. Come
        back after the next scheduled run.
      </div>
    )
  }

  const rising = genres.filter((g) => g.delta > 0).slice(0, 15)
  const falling = genres.filter((g) => g.delta < 0).slice(-15).reverse()

  const row = (g: TrendingGenre) => (
    <li key={`${g.country_code}-${g.genre}`} className="trend__item">
      <span className="trend__flag">
        <CountryFlag code={g.country_code} />
      </span>
      <span className="trend__country">{nameOf(g.country_code)}</span>
      <span className="chip">{g.genre}</span>
      <span className={g.delta > 0 ? 'trend__up' : 'trend__down'}>
        {g.delta > 0 ? '+' : ''}
        {g.delta}
      </span>
    </li>
  )

  return (
    <div className="trend">
      <section>
        <h3 className="detail__subtitle">Rising</h3>
        {rising.length ? <ul className="trend__list">{rising.map(row)}</ul> : (
          <p className="detail__empty">Nothing rising this run.</p>
        )}
      </section>
      <section>
        <h3 className="detail__subtitle">Falling</h3>
        {falling.length ? <ul className="trend__list">{falling.map(row)}</ul> : (
          <p className="detail__empty">Nothing falling this run.</p>
        )}
      </section>
    </div>
  )
}
