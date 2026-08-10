import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { CountrySummary } from '../api/countries'
import {
  countryCodeOf,
  featureBounds,
  featureToPath,
  type Bounds,
  type GeoFeature,
  type GeoFeatureCollection,
} from '../lib/geo'
import {
  buildGenreColorMap,
  countryColor,
  legendFor,
  NO_DATA_COLOR,
  type ColorMode,
} from '../lib/mapColors'

type Props = {
  countries: CountrySummary[]
  selectedCode: string | null
  onSelect: (country: CountrySummary) => void
  colorMode: ColorMode
}

/**
 * Country outlines. Fetched at runtime rather than bundled, because the file
 * is a few hundred KB of geometry that has nothing to do with the app's own
 * code and never changes.
 *
 * Swapping map sources should only mean changing this URL - lib/geo.ts
 * accepts alpha-2, alpha-3 and numeric feature ids, since sources disagree on
 * which they use. To avoid depending on a CDN at runtime, download this file
 * into frontend/public/ and point this at '/countries.geo.json' instead.
 *
 * Both are GeoJSON, deliberately. world-atlas is the better-known package but
 * ships TopoJSON, which needs a decoder this app doesn't have a dependency
 * for - so it isn't listed here at all rather than being tried and always
 * rejected. The first keys features by alpha-3, the second by properties.
 */
const GEOJSON_URL =
  'https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json'
const GEOJSON_FALLBACK_URL =
  'https://d2ad6b4ur7yvpq.cloudfront.net/naturalearth-3.3.0/ne_110m_admin_0_countries.geojson'

// Equirectangular means the viewBox is simply 2:1.
const WIDTH = 1000
const HEIGHT = 500

const MIN_SCALE = 1
const MAX_SCALE = 40
const BUTTON_ZOOM_STEP = 1.6
// Leaves a margin around a country when fitting it, so it isn't flush against
// the panel edge with no surrounding context to orient by.
const FIT_PADDING = 0.65
// Below this movement a pointer gesture counts as a click, not a drag -
// without it, the tiny cursor drift during a click swallows the selection.
const DRAG_THRESHOLD_PX = 4

type Transform = { k: number; x: number; y: number }

const IDENTITY: Transform = { k: 1, x: 0, y: 0 }

const clampScale = (k: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, k))

/**
 * Keep the scaled map covering the viewport, so it can't be dragged off into
 * empty space with no way back but the reset button.
 *
 * The map spans k*WIDTH after scaling, so its left edge must sit at or left
 * of 0 and its right edge at or right of WIDTH. At k = 1 those collapse to a
 * single value, which correctly pins the world view in place.
 */
function clampTranslate(t: Transform): Transform {
  const minX = WIDTH - t.k * WIDTH
  const minY = HEIGHT - t.k * HEIGHT
  return {
    k: t.k,
    x: Math.min(0, Math.max(minX, t.x)),
    y: Math.min(0, Math.max(minY, t.y)),
  }
}

/** Scale around a fixed point, so that point stays put on screen. */
function zoomAround(t: Transform, factor: number, px: number, py: number): Transform {
  const k = clampScale(t.k * factor)
  // Map coordinate currently under (px, py).
  const mx = (px - t.x) / t.k
  const my = (py - t.y) / t.k
  return clampTranslate({ k, x: px - k * mx, y: py - k * my })
}

/** Transform that centres `bounds` in the viewport. */
function fitBounds(bounds: Bounds): Transform {
  const [minX, minY, maxX, maxY] = bounds
  const boxWidth = Math.max(maxX - minX, 1e-6)
  const boxHeight = Math.max(maxY - minY, 1e-6)
  const k = clampScale(
    Math.min(WIDTH / boxWidth, HEIGHT / boxHeight) * FIT_PADDING,
  )
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  // Clamped too: a country near an edge of the map (Iceland, New Zealand)
  // can't be perfectly centred without exposing blank space beyond the
  // map's border, so it settles as close as the edge allows.
  return clampTranslate({ k, x: WIDTH / 2 - k * cx, y: HEIGHT / 2 - k * cy })
}

function asFeatureCollection(payload: unknown): GeoFeatureCollection | null {
  if (!payload || typeof payload !== 'object') return null
  const candidate = payload as { type?: string; features?: unknown }
  if (candidate.type !== 'FeatureCollection' || !Array.isArray(candidate.features)) {
    return null
  }
  return candidate as unknown as GeoFeatureCollection
}

export function WorldMap({ countries, selectedCode, onSelect, colorMode }: Props) {
  const [features, setFeatures] = useState<GeoFeature[] | null>(null)
  const [error, setError] = useState('')
  const [hovered, setHovered] = useState<string | null>(null)
  const [transform, setTransform] = useState<Transform>(IDENTITY)
  // Animate button/click-driven moves, but not wheel or drag - transitioning
  // those makes the map lag a frame behind the cursor.
  const [animate, setAnimate] = useState(true)

  const svgRef = useRef<SVGSVGElement>(null)
  const drag = useRef<{
    x: number
    y: number
    tx: number
    ty: number
    moved: boolean
    pointerId: number
  } | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      for (const url of [GEOJSON_URL, GEOJSON_FALLBACK_URL]) {
        try {
          const response = await fetch(url)
          if (!response.ok) continue
          const collection = asFeatureCollection(await response.json())
          if (collection) {
            if (!cancelled) setFeatures(collection.features)
            return
          }
        } catch {
          // Try the next source rather than failing on the first hiccup.
        }
      }
      if (!cancelled) {
        setError(
          'Could not load map geometry. Check your connection, or vendor the ' +
            'GeoJSON into frontend/public/ (see WorldMap.tsx).',
        )
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const byCode = useMemo(() => new Map(countries.map((c) => [c.code, c])), [countries])
  const genreColors = useMemo(() => buildGenreColorMap(countries), [countries])
  const legend = useMemo(() => legendFor(colorMode, genreColors), [colorMode, genreColors])

  // Derived from geometry that never changes, so this is memoised apart from
  // the data - rebuilding a few hundred path strings on every hover or zoom
  // would make interaction crawl.
  const shapes = useMemo(() => {
    if (!features) return []
    return features
      .map((feature) => ({
        code: countryCodeOf(feature),
        path: featureToPath(feature, WIDTH, HEIGHT),
        bounds: featureBounds(feature, WIDTH, HEIGHT),
      }))
      .filter((shape) => shape.path)
  }, [features])

  const boundsByCode = useMemo(() => {
    const map = new Map<string, Bounds>()
    for (const shape of shapes) {
      if (shape.code && shape.bounds && !map.has(shape.code)) {
        map.set(shape.code, shape.bounds)
      }
    }
    return map
  }, [shapes])

  const matched = useMemo(
    () => shapes.filter((s) => s.code && byCode.has(s.code)).length,
    [shapes, byCode],
  )

  // Fit whenever the selection changes, including when it's driven from
  // elsewhere (the grid view), not only from a click on the map itself.
  useEffect(() => {
    if (!selectedCode) return
    const bounds = boundsByCode.get(selectedCode)
    if (!bounds) return
    setAnimate(true)
    setTransform(fitBounds(bounds))
  }, [selectedCode, boundsByCode])

  /** Client pixel coordinates -> map coordinates. */
  const toMapPoint = useCallback((clientX: number, clientY: number) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return null
    return {
      x: ((clientX - rect.left) / rect.width) * WIDTH,
      y: ((clientY - rect.top) / rect.height) * HEIGHT,
    }
  }, [])

  const zoomByButton = (factor: number) => {
    setAnimate(true)
    setTransform((t) => zoomAround(t, factor, WIDTH / 2, HEIGHT / 2))
  }

  const reset = () => {
    setAnimate(true)
    setTransform(IDENTITY)
  }

  const onWheel = (event: React.WheelEvent<SVGSVGElement>) => {
    const point = toMapPoint(event.clientX, event.clientY)
    if (!point) return
    setAnimate(false)
    // Zoom toward the cursor rather than the centre, so the thing under the
    // pointer is what you end up looking at.
    setTransform((t) => zoomAround(t, event.deltaY < 0 ? 1.15 : 1 / 1.15, point.x, point.y))
  }

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    // Deliberately NOT capturing the pointer here. Capturing on the <svg>
    // retargets the following `click` to the svg itself, so the <path>'s own
    // onClick never fires and clicking a country does nothing. Capture is
    // taken below, only once a real drag starts - by which point swallowing
    // the click is exactly what we want.
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      tx: transform.x,
      ty: transform.y,
      moved: false,
      pointerId: event.pointerId,
    }
  }

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const state = drag.current
    if (!state) return
    const dx = event.clientX - state.x
    const dy = event.clientY - state.y
    if (!state.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return
    if (!state.moved) {
      state.moved = true
      // Now that it's a drag, keep receiving moves even if the pointer
      // leaves the map.
      svgRef.current?.setPointerCapture(state.pointerId)
    }

    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return
    const scaleX = WIDTH / rect.width
    const scaleY = HEIGHT / rect.height
    setAnimate(false)
    setTransform((t) =>
      clampTranslate({ ...t, x: state.tx + dx * scaleX, y: state.ty + dy * scaleY }),
    )
  }

  const endDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    if (svgRef.current?.hasPointerCapture?.(event.pointerId)) {
      svgRef.current.releasePointerCapture(event.pointerId)
    }
    // A click that didn't move clears immediately so the upcoming click event
    // sees no drag. A click that DID move keeps the state until the click
    // handler has read `moved` and discarded it - that's what stops a drag
    // ending over a country from selecting it.
    if (drag.current && !drag.current.moved) drag.current = null
  }

  const handleCountryClick = (country: CountrySummary) => {
    // A drag that happens to finish over a country shouldn't select it.
    if (drag.current?.moved) {
      drag.current = null
      return
    }
    onSelect(country)
  }

  if (error) return <p className="notice notice--error">{error}</p>
  if (!features) return <p className="notice">Loading map…</p>

  return (
    <div className="map">
      <div className="map__controls">
        <button
          type="button"
          className="map__btn"
          onClick={() => zoomByButton(BUTTON_ZOOM_STEP)}
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          type="button"
          className="map__btn"
          onClick={() => zoomByButton(1 / BUTTON_ZOOM_STEP)}
          aria-label="Zoom out"
        >
          −
        </button>
        <button
          type="button"
          className="map__btn map__btn--wide"
          onClick={reset}
          aria-label="Reset zoom"
        >
          Reset
        </button>
      </div>

      <svg
        ref={svgRef}
        // Cursor feedback is CSS-only (:active) rather than a state flag -
        // a ref read during render wouldn't re-render, and tracking drag in
        // state would re-render the whole map on every pointermove.
        className="map__svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="World map. Countries with data are highlighted and clickable."
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <g
          className={animate ? 'map__layer map__layer--animated' : 'map__layer'}
          transform={`translate(${transform.x} ${transform.y}) scale(${transform.k})`}
        >
          {shapes.map((shape, index) => {
            const country = shape.code ? byCode.get(shape.code) : undefined
            const isSelected = country && country.code === selectedCode
            const isHovered = country && country.code === hovered
            const fill = countryColor(country, colorMode, genreColors)
            const className = [
              'map__country',
              country ? 'map__country--has-data' : 'map__country--no-data',
              isSelected ? 'map__country--selected' : '',
              isHovered ? 'map__country--hovered' : '',
            ]
              .filter(Boolean)
              .join(' ')

            return (
              <path
                key={`${shape.code ?? 'x'}-${index}`}
                d={shape.path}
                className={className}
                // Inline fill only when a mode supplies one. Selection and
                // hover are left to CSS, which would otherwise be overridden
                // by an inline style and stop highlighting entirely.
                style={
                  fill && !isSelected && !isHovered
                    ? { fill }
                    : !country && colorMode !== 'none'
                      ? { fill: NO_DATA_COLOR }
                      : undefined
                }
                // Keeps borders hairline-thin at every zoom level instead of
                // growing into thick bands as the layer scales up.
                vectorEffect="non-scaling-stroke"
                onClick={country ? () => handleCountryClick(country) : undefined}
                onMouseEnter={country ? () => setHovered(country.code) : undefined}
                onMouseLeave={country ? () => setHovered(null) : undefined}
                tabIndex={country ? 0 : -1}
                role={country ? 'button' : undefined}
                aria-label={country ? country.name : undefined}
                onKeyDown={
                  country
                    ? (event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          onSelect(country)
                        }
                      }
                    : undefined
                }
              >
                {country && <title>{country.name}</title>}
              </path>
            )
          })}
        </g>
      </svg>

      {legend.length > 0 && (
        <ul className="map__key">
          {legend.map((item) => (
            <li key={item.label} className="map__key-item">
              <span className="legend__swatch" style={{ backgroundColor: item.color }} />
              {item.label}
            </li>
          ))}
          <li className="map__key-item">
            <span className="legend__swatch" style={{ backgroundColor: NO_DATA_COLOR }} />
            {colorMode === 'domestic' ? 'not enough data' : 'no data'}
          </li>
        </ul>
      )}

      <p className="map__legend">
        {matched} of {countries.length} countries matched to the map · scroll or drag to
        explore
        {matched === 0 && countries.length > 0 && (
          <>
            {' '}
            — the geometry source may key its features differently; see
            <code> lib/geo.ts</code>
          </>
        )}
      </p>
    </div>
  )
}
