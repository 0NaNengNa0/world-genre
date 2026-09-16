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
 * Fonts the headline cycles through, one every ROTATE_MS.
 *
 * System stacks rather than a webfont CDN, on purpose: six Google Fonts would
 * be six network requests plus a layout shift on every swap, to decorate two
 * lines of text. Every stack below ends in a generic family, so a machine
 * missing the first choice still lands somewhere deliberate instead of falling
 * back to the body font and making the rotation look broken.
 */
const HEADLINE_FONTS = [
  'ui-serif, Georgia, "Times New Roman", serif',
  'ui-monospace, "Cascadia Code", Consolas, "Courier New", monospace',
  '"Trebuchet MS", "Segoe UI", system-ui, sans-serif',
  '"Arial Black", Impact, sans-serif',
  'Verdana, Geneva, sans-serif',
  '"Palatino Linotype", "Book Antiqua", Palatino, serif',
]

const TITLE = 'World Genre'
const SUBTITLE = 'The sound of the charts, country by country.'

// One counter drives both lines: the title consumes the first TITLE.length
// steps, the subtitle the rest. That is what makes them type SEQUENTIALLY at
// one speed. Two independent counters would finish at different times because
// the strings are different lengths, which reads as two animations fighting
// rather than one typewriter.
const TOTAL_CHARS = TITLE.length + SUBTITLE.length
const TYPE_MS = 45
const CYCLE_MS = 10_000

// Read once. The rotation is decorative motion, so under this setting it does
// not run at all - the headline simply renders complete in the default font.
const PREFERS_REDUCED_MOTION =
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

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
  const [fontIndex, setFontIndex] = useState(0)
  const [cycle, setCycle] = useState(0)
  // Starts COMPLETE, not empty: the first paint should show the finished
  // headline rather than animating in, which would delay the page's own title
  // behind an effect.
  const [typed, setTyped] = useState(TOTAL_CHARS)

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

  // Tick the cycle every CYCLE_MS. Kept separate from the typing effect below
  // so the cadence stays exactly 10s regardless of how long typing takes.
  useEffect(() => {
    if (PREFERS_REDUCED_MOTION) return
    const id = window.setInterval(() => setCycle((c) => c + 1), CYCLE_MS)
    return () => window.clearInterval(id)
  }, [])

  // Each new cycle: pick a different font, then retype from nothing.
  useEffect(() => {
    if (PREFERS_REDUCED_MOTION || cycle === 0) return

    setFontIndex((current) => {
      // A random STEP of 1..n-1 rather than a random index, so the next font is
      // uniformly chosen among the others and can never repeat the current one.
      // The obvious `while (next === current)` version has no guaranteed
      // termination; this has no loop at all.
      const step = 1 + Math.floor(Math.random() * (HEADLINE_FONTS.length - 1))
      return (current + step) % HEADLINE_FONTS.length
    })

    setTyped(0)
    // A local counter rather than a functional setState that clears its own
    // interval: the cleanup below is then the only place the timer is stopped,
    // so a cycle change mid-type cannot leave one running.
    let n = 0
    const id = window.setInterval(() => {
      n += 1
      setTyped(n)
      if (n >= TOTAL_CHARS) window.clearInterval(id)
    }, TYPE_MS)

    return () => window.clearInterval(id)
  }, [cycle])

  const headlineFont = HEADLINE_FONTS[fontIndex]
  const typedTitle = TITLE.slice(0, Math.min(typed, TITLE.length))
  const typedSubtitle = SUBTITLE.slice(0, Math.max(0, typed - TITLE.length))
  const titleTyping = typed < TITLE.length
  const subtitleTyping = typed >= TITLE.length && typed < TOTAL_CHARS

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
            {/* Non-breaking space when a line is empty: an empty h1 collapses
                to zero height and shunts the whole page up on every cycle. */}
            <h1 className="page__title" style={{ fontFamily: headlineFont }}>
              {typedTitle || '\u00A0'}
              {titleTyping && <span className="type-cursor" aria-hidden="true" />}
            </h1>
            <p className="page__subtitle" style={{ fontFamily: headlineFont }}>
              {typedSubtitle || '\u00A0'}
              {subtitleTyping && <span className="type-cursor" aria-hidden="true" />}
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
