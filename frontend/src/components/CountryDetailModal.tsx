import { useEffect, useRef, useState } from 'react'

import { fetchCountryDetail, type CountryDetail } from '../api/countries'
import { flagEmoji } from '../lib/flag'
import { GenrePieChart } from './GenrePieChart'

type Mode = 'popularity' | 'distinctiveness'

const MODES: { id: Mode; label: string; caption: string }[] = [
  {
    id: 'popularity',
    label: 'Real stats',
    caption:
      'Share of what this country actually plays. Looks similar everywhere — pop and rock lead almost every country.',
  },
  {
    id: 'distinctiveness',
    label: 'TF-IDF',
    caption:
      'Share of what makes this country different, weighting each genre by how few other countries listen to it. Genres common to everywhere score zero and drop out.',
  },
]

type Props = {
  code: string
  /** Shown as the heading until the request resolves, so the modal never
   *  opens with an empty header. */
  fallbackName: string
  onClose: () => void
}

export function CountryDetailModal({ code, fallbackName, onClose }: Props) {
  const [detail, setDetail] = useState<CountryDetail | null>(null)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<Mode>('popularity')
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError('')

    fetchCountryDetail(code)
      .then((data) => {
        if (!cancelled) setDetail(data)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [code])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    // Without this the page behind keeps scrolling under the overlay.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  return (
    <div
      className="overlay"
      // Only a click that both starts and ends on the backdrop closes the
      // modal - otherwise a drag that happens to release outside the panel
      // (selecting artist text, say) would dismiss it mid-action.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className="detail"
        role="dialog"
        aria-modal="true"
        aria-label={`${detail?.name ?? fallbackName} details`}
      >
        <header className="detail__header">
          <h2 className="detail__title">
            <span aria-hidden="true">{flagEmoji(code)}</span>{' '}
            {detail?.name ?? fallbackName}
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

        {error && <p className="notice notice--error">{error}</p>}
        {!detail && !error && <p className="notice">Loading…</p>}

        {detail && (
          <div className="detail__body">
            <section className="detail__section">
              <div className="detail__section-head">
                <h3 className="detail__subtitle">Genre breakdown</h3>
                <div className="toggle" role="group" aria-label="Ranking method">
                  {MODES.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      className={
                        m.id === mode ? 'toggle__btn toggle__btn--on' : 'toggle__btn'
                      }
                      aria-pressed={m.id === mode}
                      onClick={() => setMode(m.id)}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>

              <GenrePieChart
                genres={detail[mode].genres}
                otherPercentage={detail[mode].other_percentage}
                otherGenreCount={detail[mode].other_genre_count}
                emptyMessage={
                  mode === 'distinctiveness'
                    ? "Nothing here stands out — this country's genres are all ones most other countries listen to as well."
                    : 'No genre data for this country yet.'
                }
              />

              <p className="detail__caption">
                {MODES.find((m) => m.id === mode)?.caption}
              </p>
            </section>

            <section className="detail__section">
              <h3 className="detail__subtitle">
                Top artists{' '}
                <span className="detail__count">({detail.artists.length})</span>
              </h3>
              {detail.artists.length > 0 ? (
                <ol className="artists">
                  {detail.artists.map((artist, index) => (
                    <li key={`${artist}-${index}`} className="artists__item">
                      <span className="artists__rank">{index + 1}</span>
                      <span className="artists__name">{artist}</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="detail__empty">No artists resolved for this country.</p>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
