import type { CountrySummary } from '../api/countries'
import { flagEmoji } from '../lib/flag'

type Props = {
  country: CountrySummary
}

export function CountryCard({ country }: Props) {
  const hasGenres = country.top_genres.length > 0

  return (
    <article className="card">
      <div className="card__cover">
        {country.cover_image ? (
          <img
            src={country.cover_image}
            alt=""
            className="card__cover-img"
            loading="lazy"
          />
        ) : (
          <div className="card__cover-placeholder" />
        )}
        <span className="card__flag" aria-hidden="true">
          {flagEmoji(country.code)}
        </span>
      </div>

      <header className="card__header">
        <h2 className="card__title">{country.name}</h2>
        <p className="card__meta">{country.artist_count} artists</p>
      </header>

      {hasGenres ? (
        <ul className="chips">
          {country.top_genres.map((genre) => (
            <li key={genre} className="chip">
              {genre}
            </li>
          ))}
        </ul>
      ) : (
        <p className="card__empty">No genre data yet</p>
      )}

      {country.top_artists.length > 0 && (
        <p className="card__artists">
          Top: {country.top_artists.slice(0, 3).join(', ')}
        </p>
      )}
    </article>
  )
}
