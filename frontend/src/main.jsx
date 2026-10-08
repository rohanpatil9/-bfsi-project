import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUp,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  FileText,
  Loader2,
  Search,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import "./style.css";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

const examples = [
  "Did Alphabet's R&D grow faster than revenue in Q1 2025?",
  "What was Alphabet's revenue in Q1 2025?",
  "What was Alphabet's R&D expense in Q1 2025?",
  "Compare Amazon, Alphabet and Meta revenue in Q1 2025.",
  "Did NVIDIA's R&D grow faster than revenue in Q1 FY2026?",
  "What factors contributed to Alphabet's revenue growth?",
];

function App() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [backendOnline, setBackendOnline] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((r) => {
        if (!r.ok) throw new Error("Backend unavailable");
        return r.json();
      })
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false));
  }, []);

  const canSubmit = question.trim().length >= 3 && !loading;

  async function ask(value = question) {
    const q = value.trim();
    if (q.length < 3 || loading) return;

    setQuestion(q);
    setLoading(true);
    setError("");
    setResult(null);

    try {
      const response = await fetch(`${API_BASE}/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "The analysis request failed.");
      }

      setResult(data);
    } catch (err) {
      setError(err.message || "Unable to reach the backend.");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      ask();
    }
  }

  const calculationCount = result?.calculations?.length || 0;
  const citationCount = result?.citations?.length || 0;

  const statusLabel = useMemo(() => {
    if (backendOnline === true) return "Backend connected";
    if (backendOnline === false) return "Backend offline";
    return "Checking backend";
  }, [backendOnline]);

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <BarChart3 size={18} />
          </div>
          <div>
            <div className="brand-name">SEC Financial Analyst Copilot</div>
            <div className="brand-subtitle">Evidence-backed financial reasoning</div>
          </div>
        </div>

        <div className="topbar-meta">
          <div className={`status-pill ${backendOnline === false ? "offline" : ""}`}>
            <span className="status-dot" />
            {statusLabel}
          </div>
          <span className="filing-count">12 filings · 4 companies</span>
        </div>
      </header>

      <main className="container">
        <section className="hero">
          <div className="eyebrow">
            <Sparkles size={14} />
            RAG + DETERMINISTIC FINANCIAL CALCULATIONS
          </div>

          <h1>
            Ask questions across
            <span> SEC 10-Q filings.</span>
          </h1>

          <p>
            Retrieve filing evidence, calculate financial metrics deterministically,
            and return answers with page-level source references.
          </p>
        </section>

        <section className="query-card">
          <div className="query-label">
            <Search size={15} />
            Analyst question
          </div>

          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. Did Alphabet's R&D grow faster than revenue in Q1 2025?"
            rows={4}
          />

          <div className="query-footer">
            <span className="shortcut">Ctrl + Enter to analyze</span>

            <button
              className="analyze-button"
              onClick={() => ask()}
              disabled={!canSubmit}
            >
              {loading ? (
                <>
                  <Loader2 size={17} className="spin" />
                  Analyzing
                </>
              ) : (
                <>
                  Analyze
                  <ArrowUp size={17} />
                </>
              )}
            </button>
          </div>
        </section>

        <section className="examples-section">
          <div className="section-kicker">TRY AN EXAMPLE</div>

          <div className="example-grid">
            {examples.map((example) => (
              <button
                key={example}
                className="example-card"
                onClick={() => ask(example)}
                disabled={loading}
              >
                <span>{example}</span>
                <ChevronRight size={16} />
              </button>
            ))}
          </div>
        </section>

        {loading && (
          <section className="processing-card">
            <div className="processing-icon">
              <Loader2 size={22} className="spin" />
            </div>
            <div>
              <strong>Analyzing filing evidence</strong>
              <p>
                Retrieving relevant tables, running deterministic calculations,
                and preparing the cited response.
              </p>
            </div>
          </section>
        )}

        {error && (
          <section className="error-card">
            <CircleAlert size={20} />
            <div>
              <strong>Analysis failed</strong>
              <p>{error}</p>
              <button onClick={() => ask()}>Retry</button>
            </div>
          </section>
        )}

        {result && !loading && (
          <section className="results">
            <div className="result-header">
              <div>
                <div className="section-kicker">ANALYSIS RESULT</div>
                <h2>Financial analysis</h2>
              </div>

              <div className="result-stats">
                <span>
                  <FileText size={14} />
                  {citationCount} sources
                </span>
                <span>
                  <TrendingUp size={14} />
                  {calculationCount} calculations
                </span>
              </div>
            </div>

            <div className="answer-card">
              <div className="card-heading">
                <div className="heading-icon">
                  <ShieldCheck size={17} />
                </div>
                <span>Grounded answer</span>
              </div>

              <div className="answer-text">{result.answer}</div>
            </div>

            {calculationCount > 0 && (
              <section className="result-section">
                <div className="section-title-row">
                  <div>
                    <div className="section-kicker">DETERMINISTIC</div>
                    <h3>Calculations</h3>
                  </div>
                  <span className="small-badge">Source values only</span>
                </div>

                <div className="calculation-grid">
                  {result.calculations.map((calculation, index) => (
                    <div className="calculation-card" key={index}>
                      <div className="calculation-top">
                        <span>{calculation.name}</span>
                        <CheckCircle2 size={16} />
                      </div>

                      <div className="calculation-result">
                        {calculation.result}
                        {calculation.unit ? (
                          <small>{calculation.unit}</small>
                        ) : null}
                      </div>

                      {calculation.formula && (
                        <div className="formula">{calculation.formula}</div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section className="result-section">
              <div className="section-title-row">
                <div>
                  <div className="section-kicker">SOURCE EVIDENCE</div>
                  <h3>Filing citations</h3>
                </div>
                <span className="small-badge">Page-level metadata</span>
              </div>

              {citationCount === 0 ? (
                <div className="empty-state">
                  No citation metadata was returned for this response.
                </div>
              ) : (
                <div className="citation-list">
                  {result.citations.map((citation, index) => (
                    <div className="citation-card" key={citation.chunk_id || index}>
                      <div className="citation-number">S{index + 1}</div>

                      <div className="citation-main">
                        <div className="citation-title">
                          {(citation.company || "Unknown").toUpperCase()}
                          {" · "}
                          FY{citation.fiscal_year} {citation.fiscal_quarter}
                        </div>

                        <div className="citation-meta">
                          {citation.pdf_page != null && (
                            <span>PDF page {citation.pdf_page}</span>
                          )}
                          {citation.section && <span>{citation.section}</span>}
                          {citation.table_name && <span>{citation.table_name}</span>}
                        </div>

                        {citation.source_file && (
                          <div className="citation-file">
                            {citation.source_file}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>


          </section>
        )}

        {!result && !loading && !error && (
          <section className="trust-row">
            <div>
              <ShieldCheck size={18} />
              <span>
                <strong>Grounded answers</strong>
                <small>Financial values come from retrieved filing evidence.</small>
              </span>
            </div>
            <div>
              <TrendingUp size={18} />
              <span>
                <strong>Deterministic math</strong>
                <small>Growth calculations are performed by the backend.</small>
              </span>
            </div>
            <div>
              <FileText size={18} />
              <span>
                <strong>Source citations</strong>
                <small>Company, quarter, page and table metadata.</small>
              </span>
            </div>
          </section>
        )}
      </main>

      <footer>
        SEC Financial Analyst Copilot · Development environment
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
