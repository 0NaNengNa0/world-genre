import { useEffect, useState } from 'react'

import { fetchCountries, type CountrySummary } from './api/countries'
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

  useEffect(() => {
    let cancelled = false

    fetchCountries()
      .then((data) => {
        if (cancelled) return
        setCountries(data)
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
