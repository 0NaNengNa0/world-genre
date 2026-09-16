import { useEffect, useState } from 'react'

import {
  fetchCountries,
  type CountrySummary,
  type PublishMeta,
} from './api/countries'
import { ComparePanel } from './components/ComparePanel'
import { CountryCard } from './components/CountryCard'
import { CountryDetailModal } from './components/CountryDetailModal'
import { CountryPopup } from './components/CountryPopup'
import { GlobalArtistsPanel } from './components/GlobalArtistsPanel'
import { TrendingPanel } from './components/TrendingPanel'
import { WorldMap } from './components/WorldMap'
import type { ColorMode } from './lib/mapColors'

type Status = 'loading' | 'ready' | 'error'
type View = 'map' | 'grid' | 'trending' | 'artists' | 'compare'

const VIEWS: { id: View; label: string }[] = [
  { id: 'map', label: 'Map' },
  { id: 'grid', label: 'Grid' },
  { id: 'trending', label: 'Trends' },
  { id: 'artists', label: 'Global artists' },
  { id: 'compare', label: 'Compare' },
]

const COLOR_MODES: { id: ColorMode; label: string }[] = [
  { id: 'genre', label: 'Top genre' },
  { id: 'domestic', label: 'Domestic share' },
  { id: 'none', label: 'Plain' },
]

/**
 * "16 Sep 2026" from either a YYYY-MM-DD date or a full ISO timestamp, or null
 * if the value is missing or unparseable.
 *
 * timeZone: 'UTC' is not cosmetic. `new Date('2026-09-16')` is parsed as UTC
 * midnight, so formatting it in any timezone west of Greenwich renders the
 * PREVIOUS day - a freshness indicator that is silently off by one for a third
 * of the world. snapshot_date is a UTC calendar date, so it is displayed as
 * one.
 *
 * Returning null rather than a placeholder lets the caller drop the whole line
 * instead of showing "Charts as of Invalid Date".
 */
function formatAsOf(value: string | null | undefined): string | null {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

function App() {
  const [status, setStatus] = useState<Status>('loading')
  const [countries, setCountries] = useState<CountrySummary[]>([])
  const [error, setError] = useState<string>('')
  const [view, setView] = useState<View>('map')
  const [colorMode, setColorMode] = useState<ColorMode>('genre')
  // Two levels of drill-down: picking a country shows the quick popup, and
  // only then does `detailOpen` pull the heavier per-country breakdown.
  const [selected, setSelected] = useState<CountrySummary | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  // Null until the first payload lands, and stays null for any payload
  // published before build_meta existed.
  const [meta, setMeta] = useState<PublishMeta | null>(null)

  useEffect(() => {
    let cancelled = false

    fetchCountries()
      .then((data) => {
        if (cancelled) return
        setCountries(data.countries)
        setMeta(data.meta ?? null)
        setStatus('ready')
      })
      .catch((err: Error) => {
        if (cancelled) return
        setError(err.message)
        setStatus('error')
      })

    return () => {
      cancelled = true
    }
  }, [])

  // Two lineages, shown separately: chart data advances nightly, the
  // MusicBrainz mirror is a one-off import that can be weeks behind. Collapsing
  // them into one "last updated" would hide exactly that divergence.
  const chartsAsOf = formatAsOf(meta?.snapshot_date)
  const mirrorAsOf = formatAsOf(meta?.mb_imported_at)

  const closeAll = () => {
    setSelected(null)
    setDetailOpen(false)
  }

  return (
    <div className="page">
      <header className="page__header">
        <div className="page__headline">
          <div>
            <h1 className="page__title">World Genre</h1>
            <p className="page__subtitle">
              The sound of the charts, country by country.
            </p>
          </div>

          <div className="toggle" role="group" aria-label="View">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                className={v.id === view ? 'toggle__btn toggle__btn--on' : 'toggle__btn'}
                aria-pressed={v.id === view}
                onClick={() => setView(v.id)}
              >
                {v.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      {status === 'loading' && <p className="notice">Loading…</p>}
      {status === 'error' && <p className="notice notice--error">{error}</p>}

      {status === 'ready' && view === 'map' && (
        <>
          <div className="map-toolbar">
            <span className="detail__subtitle">Shade by</span>
            <div className="toggle" role="group" aria-label="Map colouring">
              {COLOR_MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={
                    m.id === colorMode ? 'toggle__btn toggle__btn--on' : 'toggle__btn'
                  }
                  aria-pressed={m.id === colorMode}
                  onClick={() => setColorMode(m.id)}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          <div className="map-layout">
            <WorldMap
              countries={countries}
              selectedCode={selected?.code ?? null}
              colorMode={colorMode}
              onSelect={(country) => {
                setSelected(country)
                setDetailOpen(false)
              }}
            />
            {selected && !detailOpen && (
              <CountryPopup
                country={selected}
                onClose={closeAll}
                onSeeMore={() => setDetailOpen(true)}
              />
            )}
          </div>
        </>
      )}

      {status === 'ready' && view === 'grid' && (
        <section className="grid">
          {countries.map((country) => (
            <CountryCard
              key={country.code}
              country={country}
              onSelect={(country) => {
                setSelected(country)
                setDetailOpen(true)
              }}
            />
          ))}
        </section>
      )}

      {status === 'ready' && view === 'trending' && <TrendingPanel countries={countries} />}
      {status === 'ready' && view === 'artists' && <GlobalArtistsPanel />}
      {status === 'ready' && view === 'compare' && <ComparePanel countries={countries} />}

      {status === 'ready' && (chartsAsOf || mirrorAsOf) && (
        <footer className="page__footer">
          {chartsAsOf && <span>Charts as of {chartsAsOf}</span>}
          {chartsAsOf && mirrorAsOf && <span aria-hidden="true"> &middot; </span>}
          {mirrorAsOf && <span>MusicBrainz mirror {mirrorAsOf}</span>}
        </footer>
      )}

      {selected && detailOpen && (
        <CountryDetailModal
          code={selected.code}
          fallbackName={selected.name}
          // Closing the full breakdown returns to the map popup rather than
          // dismissing everything, so the drill-down is reversible.
          onClose={() => (view === 'map' ? setDetailOpen(false) : closeAll())}
        />
      )}
    </div>
  )
}

export default App
