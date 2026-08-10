import { useEffect, useState } from 'react'

import { fetchGlobalArtists, type GlobalArtist } from '../api/countries'
import { CountryFlag } from './CountryFlag'

/**
 * The biggest artists worldwide, by streams summed across every country's
 * chart.
 *
 * `country_count` is shown next to the total on purpose: it's what separates
 * an artist charting modestly across forty markets from one dominating a
 * single large one. Both can top this list, and which it is matters.
 */
export function GlobalArtistsPanel() {
  const [artists, setArtists] = useState<GlobalArtist[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchGlobalArtists()
      .then((data) => !cancelled && setArtists(data))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return <p className="notice notice--error">{error}</p>
  if (!artists) return <p className="notice">Loading…</p>
  if (artists.length === 0) {
    return (
      <div className="notice">
        <strong>No chart data yet.</strong> Run the pipeline to populate chart entries.
      </div>
    )
  }

  const hasAnyDelta = artists.some((a) => a.delta !== null)

  return (
    <div>
      <ul className="global">
        <li className="global__head">
          <span />
          <span>Artist</span>
          <span>Countries</span>
          <span>Daily streams</span>
          {hasAnyDelta && <span>Change</span>}
        </li>
        {artists.map((a, i) => (
          <li key={a.artist} className="global__row">
            <span className="artists__rank">{i + 1}</span>
            <span className="global__name">
              <CountryFlag code={a.origin_country} />{' '}
              {a.artist}
            </span>
            <span className="global__num">{a.country_count}</span>
            <span className="global__num">{a.streams.toLocaleString()}</span>
            {hasAnyDelta && (
              <span
                className={
                  a.delta === null
                    ? 'detail__empty'
                    : a.delta >= 0
                      ? 'trend__up'
                      : 'trend__down'
                }
              >
                {a.delta === null
                  ? '—'
                  : `${a.delta > 0 ? '+' : ''}${a.delta.toLocaleString()}`}
              </span>
            )}
          </li>
        ))}
      </ul>

      <p className="detail__caption">
        Streams summed across all country charts, so an artist charting modestly
        everywhere can outrank one dominating a single market — the "Countries" column is
        how you tell those apart.
        {!hasAnyDelta &&
          ' Change appears once the pipeline has run on two different days.'}
      </p>
    </div>
  )
}
