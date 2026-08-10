import type { CountrySummary } from '../api/countries'
import { CountryFlag } from './CountryFlag'

type Props = {
  country: CountrySummary
  onSelect: (country: CountrySummary) => void
}

export function CountryCard({ country, onSelect }: Props) {
  const hasGenres = country.top_genres.length > 0

  return (
    // A real <button> rather than a click handler on the article, so keyboard
    // and screen-reader users get the same affordance for free.
    <button
      type="button"
      className="card"
      onClick={() => onSelect(country)}
      aria-label={`View details for ${country.name}`}
    >
      <div className="card__cover">
        {country.cover_image ? (
          // Two copies of the same image. Artist photos are square (Deezer
          // serves 250x250, Wikidata thumbnails likewise) while this box is
          // 16:9, so object-fit:cover was slicing roughly 44 percent of the
          // height off - which on a portrait means the top of the head. The
          // blurred copy fills the box, the sharp one sits over it uncropped,
          // so nothing is lost and there are no empty letterbox bars either.
          <>
            <img
              src={country.cover_image}
              alt=""
              aria-hidden="true"
              className="card__cover-backdrop"
              loading="lazy"
            />
            <img
              src={country.cover_image}
              alt=""
              className="card__cover-img"
              loading="lazy"
            />
          </>
        ) : (
          <div className="card__cover-placeholder" />
        )}
        <span className="card__flag">
          <CountryFlag code={country.code} size="md" />
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
    </button>
  )
}
