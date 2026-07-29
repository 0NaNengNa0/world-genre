import { useEffect, useState } from 'react'

import { fetchCountries, type CountrySummary } from './api/countries'
import { CountryCard } from './components/CountryCard'

type Status = 'loading' | 'ready' | 'error'

function App() {
  const [status, setStatus] = useState<Status>('loading')
  const [countries, setCountries] = useState<CountrySummary[]>([])
  const [error, setError] = useState<string>('')

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

  return (
    <div className="page">
      <header className="page__header">
        <h1 className="page__title">World Genre</h1>
        <p className="page__subtitle">
          The sound of the charts, country by country.
        </p>
      </header>

      {status === 'loading' && <p className="notice">Loading…</p>}
      {status === 'error' && <p className="notice notice--error">{error}</p>}
      {status === 'ready' && (
        <section className="grid">
          {countries.map((country) => (
            <CountryCard key={country.code} country={country} />
          ))}
        </section>
      )}
    </div>
  )
}

export default App
