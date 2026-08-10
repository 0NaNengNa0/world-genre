import { useEffect, useMemo, useState } from 'react'

import {
  fetchCountryDetail,
  type CountryDetail,
  type CountrySummary,
} from '../api/countries'
import { genreDifferences, similarity } from '../lib/compare'
import { flagEmoji } from '../lib/flag'

type Props = {
  countries: CountrySummary[]
}

/**
 * Two countries side by side, quantified.
 *
 * Fetches both details rather than working off the summary lists, because
 * the interesting comparison is by percentage share, not by which genre
 * names happen to appear. Two countries both listing "pop" look identical
 * in a name comparison whether pop is 40 percent of one and 2 of the other.
 */
export function ComparePanel({ countries }: Props) {
  const sorted = useMemo(
    () => [...countries].sort((a, b) => a.name.localeCompare(b.name)),
    [countries],
  )
  const [leftCode, setLeftCode] = useState(sorted[0]?.code ?? '')
  const [rightCode, setRightCode] = useState(sorted[1]?.code ?? '')
  const [left, setLeft] = useState<CountryDetail | null>(null)
  const [right, setRight] = useState<CountryDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLeft(null)
    setRight(null)
    setError('')

    Promise.all([fetchCountryDetail(leftCode), fetchCountryDetail(rightCode)])
      .then(([a, b]) => {
        if (cancelled) return
        setLeft(a)
        setRight(b)
      })
      .catch((e: Error) => !cancelled && setError(e.message))

    return () => {
      cancelled = true
    }
  }, [leftCode, rightCode])

  const overlap = useMemo(
    () =>
      left && right ? similarity(left.popularity.genres, right.popularity.genres) : null,
    [left, right],
  )
  const diffs = useMemo(
    () =>
      left && right
        ? genreDifferences(left.popularity.genres, right.popularity.genres).slice(0, 10)
        : [],
    [left, right],
  )

  const picker = (value: string, onChange: (v: string) => void, label: string) => (
    <label className="compare__picker">
      <span className="detail__subtitle">{label}</span>
      <select
        className="compare__select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {sorted.map((c) => (
          <option key={c.code} value={c.code}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <div className="compare">
      <div className="compare__pickers">
        {picker(leftCode, setLeftCode, 'Country A')}
        {picker(rightCode, setRightCode, 'Country B')}
      </div>

      {error && <p className="notice notice--error">{error}</p>}
      {!left && !right && !error && <p className="notice">Comparing…</p>}

      {left && right && (
        <>
          <div className="stat">
            <span className="stat__value">{overlap}%</span>
            <span className="stat__label">
              of {left.name} and {right.name}'s listening overlaps
            </span>
          </div>
          <p className="detail__caption">
            Histogram intersection — for each genre, the smaller of the two shares,
            summed. If both spend 20% on pop that's 20 points of genuine overlap; if one
            spends 30% and the other 5%, only 5 points are shared.
          </p>

          <h3 className="detail__subtitle" style={{ marginTop: '2rem' }}>
            Biggest differences{' '}
            <span className="detail__count">in percentage points</span>
          </h3>
          <ul className="diff">
            <li className="diff__head">
              <span />
              <span>
                {flagEmoji(left.code)} {left.name}
              </span>
              <span>
                {flagEmoji(right.code)} {right.name}
              </span>
              <span>Gap</span>
            </li>
            {diffs.map((d) => (
              <li key={d.genre} className="diff__row">
                <span className="chip">{d.genre}</span>
                <span className="diff__num">{d.leftPercentage.toFixed(1)}%</span>
                <span className="diff__num">{d.rightPercentage.toFixed(1)}%</span>
                <span
                  className={d.difference >= 0 ? 'diff__left' : 'diff__right'}
                  title={
                    d.difference >= 0
                      ? `Higher in ${left.name}`
                      : `Higher in ${right.name}`
                  }
                >
                  {d.difference > 0 ? '+' : ''}
                  {d.difference.toFixed(1)}
                </span>
              </li>
            ))}
          </ul>
          <p className="detail__caption">
            A genre absent from one country counts as 0% there rather than being skipped
            — that's usually the most revealing row in the table.
          </p>
        </>
      )}
    </div>
  )
}
