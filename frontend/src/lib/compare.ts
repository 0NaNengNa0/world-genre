import type { GenreShare } from '../api/countries'

export type GenreDiff = {
  genre: string
  leftPercentage: number
  rightPercentage: number
  /** left minus right, in percentage points. */
  difference: number
}

/**
 * How much two countries' listening overlaps, 0-100.
 *
 * Histogram intersection: sum the smaller of the two shares for every genre.
 * If both countries spend 20 percent on pop, 20 points of overlap are real
 * regardless of what else differs; if one spends 30 and the other 5, only 5
 * points are shared. The result is a genuine percentage - "these two agree
 * about 62 percent of the time" - rather than an abstract distance score
 * nobody can interpret.
 *
 * Counting shared genre NAMES instead would be much cruder: two countries
 * both listing "pop" would look identical whether pop is 40 percent of one
 * and 2 percent of the other.
 */
export function similarity(left: GenreShare[], right: GenreShare[]): number {
  const rightByGenre = new Map(right.map((g) => [g.genre, g.percentage]))
  let shared = 0
  for (const genre of left) {
    const other = rightByGenre.get(genre.genre)
    if (other !== undefined) shared += Math.min(genre.percentage, other)
  }
  return Math.round(shared * 10) / 10
}

/**
 * Per-genre percentage-point differences, biggest gap first.
 *
 * Genres missing from one side count as 0 there rather than being skipped -
 * "Japan 11 percent j-pop, Brazil 0" is the single most informative row in
 * the table, and dropping it would hide exactly what makes them differ.
 */
export function genreDifferences(
  left: GenreShare[],
  right: GenreShare[],
): GenreDiff[] {
  const leftByGenre = new Map(left.map((g) => [g.genre, g.percentage]))
  const rightByGenre = new Map(right.map((g) => [g.genre, g.percentage]))

  const genres = new Set([...leftByGenre.keys(), ...rightByGenre.keys()])
  const diffs: GenreDiff[] = []
  for (const genre of genres) {
    const l = leftByGenre.get(genre) ?? 0
    const r = rightByGenre.get(genre) ?? 0
    diffs.push({
      genre,
      leftPercentage: l,
      rightPercentage: r,
      difference: Math.round((l - r) * 10) / 10,
    })
  }

  return diffs.sort((a, b) => Math.abs(b.difference) - Math.abs(a.difference))
}
