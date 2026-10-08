# SEC Financial Analyst Copilot

Complete reference implementation for a 12-filing SEC 10-Q RAG project.

## Data
Amazon FY2025 Q1-Q3; Alphabet FY2025 Q1-Q3; Meta FY2025 Q1-Q3; NVIDIA FY2026 Q1-Q3.

Put your existing PDFs in `data/<company>/` at the repository root. Do not rename NVIDIA FY2026 files.

## Pipeline
PDF -> page/table extraction -> metadata-rich chunks -> Gemini embeddings -> BigQuery VECTOR_SEARCH -> deterministic calculator -> Gemini grounded response -> citations -> React UI.

## Setup
1. Create/activate Python 3.11 virtualenv.
2. `pip install -r backend/requirements.txt`
3. Copy `backend/.env.example` to `backend/.env`.
4. Set `GOOGLE_CLOUD_PROJECT`.
5. `gcloud auth application-default login`.
6. Enable BigQuery and Vertex AI APIs.
7. Replace PROJECT_ID in `backend/sql/schema.sql` and run it.
8. From repo root run `python backend/app.py` once to ingest. It will parse PDFs, generate embeddings and load BigQuery.
9. Create the vector index after ingestion using the commented SQL.
10. Start API with `uvicorn backend.app:app --reload --port 8000`.
11. In frontend: `npm install && npm run dev`.

## Important limitation
The automatic financial-fact extractor is deliberately conservative. It only accepts table rows whose labels match known metrics and stores the numeric cells as candidates. Before a production deployment, validate column semantics (current vs prior period) against table headers and expand metric aliases. Do not claim numeric correctness from an unvalidated candidate fact.

## Evaluation
Test direct lookups, YoY/QoQ calculations, cross-company comparisons, narrative questions and an out-of-corpus question. Every numeric answer must be supported by a source chunk and a deterministic calculation.
