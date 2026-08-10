import { useEffect, useState } from 'react'

import { fetchGenreDetail, type GenreDetail } from '../api/countries'

type Props = {
  countryCode: string
  genre: string
  onClose: () => void
}

/**
 * What a genre is, and who plays it in this country.
 *
 * The artist list comes from the same tag data that produced the genre's
 * score, joined to chart streams so the examples are ordered by what people
 * actually listen to rather than alphabetically.
 */
export function GenreDetailPanel({ countryCode, genre, onClose }: Props) {
  const [detail, setDetail] = useState<GenreDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError('')

    fetchGenreDetail(countryCode, genre)
      .then((data) => !cancelled && setDetail(data))
      .catch((e: Error) => !cancelled && setError(e.message))

    return () => {
      cancelled = true
    }
  }, [countryCode, genre])

  return (
    <section className="genre-panel">
      <header className="genre-panel__head">
        <h3 className="genre-panel__title">{genre}</h3>
        <button
          type="button"
          className="detail__close"
          onClick={onClose}
          aria-label={`Close ${genre} details`}
        >
          ×
        </button>
      </header>

      {error && <p className="notice notice--error">{error}</p>}
      {!detail && !error && <p className="detail__empty">Loading…</p>}

      {detail && (
        <>
          {detail.summary ? (
            <p className="genre-panel__summary">{detail.summary}</p>
          ) : (
            // Distinguishes "we haven't fetched descriptions yet" from "this
            // genre has none", since both look the same from here.
            <p className="detail__caption">
              No description available for this genre. Descriptions come from
              Last.fm and are fetched by <code>run_extract_genre_info</code>.
            </p>
          )}

          {detail.artists.length > 0 ? (
            <>
              <h4 className="detail__subtitle">
                {detail.country_name} artists{' '}
                <span className="detail__count">most-streamed first</span>
              </h4>
              <ul className="genre-panel__artists">
                {detail.artists.map((a) => (
                  <li key={a.artist} className="genre-panel__artist">
                    <span className="gems__name">{a.artist}</span>
                    <span className="tracks__streams">
                      {a.streams === null ? 'not charting' : a.streams.toLocaleString()}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="detail__caption">
                Artists whose tags placed them in this genre. "Not charting" means
                they're a known example here but aren't on the current chart.
              </p>
            </>
          ) : (
            <p className="detail__empty">
              No artists linked to this genre in {detail.country_name} yet.
            </p>
          )}

          {detail.url && (
            <a
              className="genre-panel__link"
              href={detail.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              More about {genre} on Last.fm →
            </a>
          )}
        </>
      )}
    </section>
  )
}
