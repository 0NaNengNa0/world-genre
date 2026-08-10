import type { GenreShare } from '../api/countries'

type Props = {
  genres: GenreShare[]
  otherPercentage: number
  otherGenreCount: number
  /** Shown instead of the chart when there's nothing to draw. Worth wording
   *  per mode: no genres at all is a data gap, whereas no *distinctive*
   *  genres is a real finding about the country. */
  emptyMessage?: string
}

/**
 * Ten genre colours plus a muted grey reserved for the "other" slice, so
 * "other" never reads as just another genre.
 */
const COLORS = [
  '#6366f1', '#ec4899', '#14b8a6', '#f59e0b', '#8b5cf6',
  '#06b6d4', '#ef4444', '#84cc16', '#f97316', '#3b82f6',
]
const OTHER_COLOR = '#4b5563'

const SIZE = 220
const STROKE = 34
const RADIUS = (SIZE - STROKE) / 2
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

/**
 * Donut chart drawn as stroked circle segments rather than SVG arc paths.
 *
 * Each slice is one <circle> whose stroke-dasharray is "sliceLength gap",
 * offset by everything drawn before it. That avoids arc-path trigonometry
 * and its usual edge case: a single slice at 100 percent, where a path arc
 * from a point back to itself renders as nothing at all, while a dashed
 * circle just draws the full ring.
 */
export function GenrePieChart({
  genres,
  otherPercentage,
  otherGenreCount,
  emptyMessage = 'No genre data for this country yet.',
}: Props) {
  const slices = [
    ...genres.map((g, i) => ({
      key: g.genre,
      label: g.genre,
      percentage: g.percentage,
      color: COLORS[i % COLORS.length],
    })),
    ...(otherPercentage > 0
      ? [{
          key: '__other__',
          label: `other (${otherGenreCount} genres)`,
          percentage: otherPercentage,
          color: OTHER_COLOR,
        }]
      : []),
  ]

  if (slices.length === 0) {
    return <p className="detail__empty">{emptyMessage}</p>
  }

  let cumulative = 0

  return (
    <div className="chart">
      <svg
        className="chart__svg"
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width={SIZE}
        height={SIZE}
        role="img"
        aria-label={
          'Genre breakdown: ' +
          slices.map((s) => `${s.label} ${s.percentage.toFixed(1)} percent`).join(', ')
        }
      >
        {/* -90deg so the first slice starts at 12 o'clock instead of 3. */}
        <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
          {slices.map((slice) => {
            const length = (slice.percentage / 100) * CIRCUMFERENCE
            const offset = -(cumulative / 100) * CIRCUMFERENCE
            cumulative += slice.percentage
            return (
              <circle
                key={slice.key}
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={RADIUS}
                fill="none"
                stroke={slice.color}
                strokeWidth={STROKE}
                strokeDasharray={`${length} ${CIRCUMFERENCE - length}`}
                strokeDashoffset={offset}
              />
            )
          })}
        </g>
      </svg>

      <ul className="legend">
        {slices.map((slice) => (
          <li key={slice.key} className="legend__item">
            <span
              className="legend__swatch"
              style={{ backgroundColor: slice.color }}
              aria-hidden="true"
            />
            <span className="legend__label">{slice.label}</span>
            <span className="legend__value">{slice.percentage.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
