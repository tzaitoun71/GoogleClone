import { useEffect, useRef, useState } from 'react'
import { search, stats } from './api'
import './App.css'

function SearchIcon() {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" />
    </svg>
  )
}

// Renders the [{text, match}] segments the backend sends. The server tells us
// WHICH characters matched; deciding that a match looks like <mark> is the
// frontend's call. No HTML ever crosses the wire.
function Snippet({ segments }) {
  return (
    <p className="snippet">
      {segments.map((segment, i) =>
        segment.match ? (
          <mark key={i}>{segment.text}</mark>
        ) : (
          <span key={i}>{segment.text}</span>
        ),
      )}
    </p>
  )
}

function Result({ result }) {
  return (
    <li className="result">
      {/* Source line above the title, the way Google has laid results out
          since 2020 — you decide whether you trust the source before you read
          the claim. */}
      <div className="source">
        <span className="avatar" aria-hidden="true">
          {result.title.charAt(0)}
        </span>
        <span className="crumbs">
          <span className="site">corpus</span>
          <span className="sep">›</span>
          {result.url}
        </span>
        <span className="score" title="blended TF-IDF + PageRank score">
          {result.score.toFixed(3)}
        </span>
      </div>
      <h2 className="title">{result.title}</h2>
      <Snippet segments={result.snippet} />
    </li>
  )
}

// Landing-page view of what's actually in the index, ordered by the PageRank
// authority each page earned from the link graph.
function Corpus({ corpus }) {
  // `|| 1` guards the empty-corpus case: Math.max() of nothing is -Infinity,
  // and dividing by it would put NaN into the bar widths.
  const strongest = Math.max(...corpus.pages.map((page) => page.authority)) || 1

  return (
    <section className="corpus">
      <h3 className="corpus-title">
        In the index
        <span>
          {corpus.documents} pages · {corpus.terms} terms
        </span>
      </h3>
      <ul className="corpus-list">
        {corpus.pages.map((page) => (
          <li key={page.id}>
            {/* Bars are relative to the strongest page, not to 1.0 — the whole
                spread here is 0.10–0.22, and against a full scale every bar
                would look identical. */}
            <span
              className="bar"
              style={{ '--w': `${(page.authority / strongest) * 100}%` }}
            />
            <span className="corpus-url">{page.url}</span>
            <span className="corpus-meta">{page.authority.toFixed(4)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function App() {
  // Two separate pieces of state, and the split is the whole point of
  // submit-on-Enter: `input` is what's in the box right now, `submitted` is
  // what was actually searched for. Typing changes the first; only Enter
  // changes the second, and only the second triggers a request.
  const [input, setInput] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [response, setResponse] = useState(null)
  const [corpus, setCorpus] = useState(null)
  const [error, setError] = useState(null)
  const [elapsed, setElapsed] = useState(null)

  const inputRef = useRef(null)

  // Every request gets a number. When one comes back we check it's still the
  // newest — otherwise a slow early request could land after a fast later one
  // and overwrite fresh results with stale ones. Still worth it on Enter:
  // nothing stops you submitting twice in quick succession.
  const latestRequest = useRef(0)

  useEffect(() => {
    stats().then(setCorpus).catch(() => setError('Could not reach the API.'))
  }, [])

  // "/" focuses the box from anywhere, Escape lets go of it.
  useEffect(() => {
    function onKeyDown(event) {
      const typing = document.activeElement === inputRef.current
      if (event.key === '/' && !typing) {
        event.preventDefault() // otherwise the "/" lands in the box
        inputRef.current?.focus()
      } else if (event.key === 'Escape' && typing) {
        inputRef.current?.blur()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    if (!submitted) {
      setResponse(null)
      setElapsed(null)
      return
    }

    const id = ++latestRequest.current
    const started = performance.now()

    search(submitted)
      .then((data) => {
        if (id !== latestRequest.current) return // a newer search already won
        setResponse(data)
        setElapsed(performance.now() - started)
        setError(null)
      })
      .catch(() => {
        if (id === latestRequest.current) setError('Search request failed.')
      })
  }, [submitted])

  function runSearch(raw) {
    setInput(raw)
    setSubmitted(raw.trim())
  }

  function handleSubmit(event) {
    // Without this the browser does a full page reload on submit, which throws
    // away all our state and re-fetches everything.
    event.preventDefault()
    setSubmitted(input.trim())
  }

  function reset() {
    setInput('')
    setSubmitted('')
    inputRef.current?.focus()
  }

  // Keyed off what was SEARCHED, not what's typed — so the layout doesn't jump
  // around while you're still composing a query.
  const searching = submitted.length > 0

  const searchBox = (
    <form className="search-form" onSubmit={handleSubmit} role="search">
      <SearchIcon />
      <input
        ref={inputRef}
        className="search"
        type="text"
        value={input}
        autoFocus
        placeholder="Search the corpus"
        aria-label="Search the corpus"
        onChange={(event) => setInput(event.target.value)}
      />
      {input ? (
        <button className="clear" type="button" onClick={reset} aria-label="Clear">
          ×
        </button>
      ) : (
        <kbd className="hint">/</kbd>
      )}
    </form>
  )

  return (
    <div className={`app ${searching ? 'results-view' : 'home-view'}`}>
      {searching ? (
        <header className="topbar">
          <div className="topbar-inner">
            <button className="logo small" onClick={reset} type="button">
              Zoogle
            </button>
            {searchBox}
          </div>
          {response && (
            <div className="statusbar">
              <span className="count">
                {response.count} {response.count === 1 ? 'result' : 'results'}
                {elapsed !== null && ` (${(elapsed / 1000).toFixed(3)} seconds)`}
              </span>
              {/* Not decoration: these are the normalized tokens the engine
                  actually searched for, which is often not what you typed. */}
              <span className="tokens">
                {response.tokens.map((token) => (
                  <span className="token" key={token}>
                    {token}
                  </span>
                ))}
              </span>
            </div>
          )}
        </header>
      ) : (
        <header className="hero">
          <h1 className="logo">Zoogle</h1>
          {searchBox}
          {/* Suggestions come from the index rather than a constant, so they
              describe whatever corpus is loaded right now. Nothing renders
              until stats arrive — better an empty gap for one frame than a
              row of examples that belong to a corpus we replaced. */}
          {corpus?.suggestions?.length > 0 && (
            <div className="examples">
              <span className="examples-label">Try</span>
              {corpus.suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => runSearch(suggestion)}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}
        </header>
      )}

      <main className="main">
        {error && <p className="notice">{error}</p>}

        {response && response.count === 0 && (
          <p className="notice">
            Nothing in the index contains any of those words.
          </p>
        )}

        {response && response.count > 0 && (
          <ol className="results">
            {response.results.map((result) => (
              <Result key={result.id} result={result} />
            ))}
          </ol>
        )}

        {!searching && corpus && <Corpus corpus={corpus} />}
      </main>
    </div>
  )
}
