import { useEffect, useRef } from 'react'

import type { CountrySummary } from '../api/countries'
import { flagEmoji } from '../lib/flag'

type Props = {
  country: CountrySummary
  onClose: () => void
  onSeeMore: () => void
}

/**
 * The quick look after clicking a country on the map: top 5 artists, top 5
 * genres, and a way through to the full breakdown.
 *
 * Renders from the summary data the grid already loaded, so opening it costs
 * no request - the detail endpoint is only hit if the user asks for more.
 */
export function CountryPopup({ country, onClose, onSeeMore }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    closeRef.current?.focus()
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <aside className="popup" role="dialog" aria-label={`${country.name} summary`}>
      <header className="popup__header">
        <h2 className="popup__title">
          <span aria-hidden="true">{flagEmoji(country.code)}</span> {country.name}
        </h2>
        <button
          ref={closeRef}
          type="button"
          className="detail__close"
          onClick={onClose}
          aria-label="Close"
        >
          ×
        </button>
      </header>

      <p className="popup__meta">{country.artist_count} artists</p>

      <h3 className="popup__subtitle">Top genres</h3>
      {country.top_genres.length > 0 ? (
        <ul className="popup__chips">
          {country.top_genres.slice(0, 5).map((genre) => (
            <li key={genre} className="chip">
              {genre}
            </li>
          ))}
        </ul>
      ) : (
        <p className="detail__empty">No genre data yet.</p>
      )}

      <h3 className="popup__subtitle">Top artists</h3>
      {country.top_artists.length > 0 ? (
        <ol className="popup__artists">
          {country.top_artists.slice(0, 5).map((artist, index) => (
            <li key={`${artist}-${index}`}>
              <span className="artists__rank">{index + 1}</span> {artist}
            </li>
          ))}
        </ol>
      ) : (
        <p className="detail__empty">No artists resolved.</p>
      )}

      <button type="button" className="popup__more" onClick={onSeeMore}>
        See more about {country.name} →
      </button>
    </aside>
  )
}
