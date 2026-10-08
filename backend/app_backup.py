from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.cloud import bigquery


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "backend" / ".env"
load_dotenv(ENV_FILE)

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

DATASET = os.getenv("BQ_DATASET", "sec_analyst")
CHUNKS = os.getenv("BQ_CHUNKS_TABLE", "chunks")
FACTS_V2 = os.getenv("BQ_FACTS_V2_TABLE", "financial_facts_v2")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
TOP_K = int(os.getenv("TOP_K", "8"))

FACT_INSERT_BATCH = 100
CHUNK_INSERT_BATCH = 50
EMBED_BATCH = 32


COMPANIES = {
    "amazon": (2025, {
        "Q1": "2025-03-31", "Q2": "2025-06-30", "Q3": "2025-09-30"
    }),
    "alphabet": (2025, {
        "Q1": "2025-03-31", "Q2": "2025-06-30", "Q3": "2025-09-30"
    }),
    "meta": (2025, {
        "Q1": "2025-03-31", "Q2": "2025-06-30", "Q3": "2025-09-30"
    }),
    "nvidia": (2026, {
        "Q1": "2025-04-27", "Q2": "2025-07-27", "Q3": "2025-10-26"
    }),
}

ALIASES = {
    "revenue": ["revenues", "revenue", "total revenues", "total revenue"],
    "research_and_development": [
        "research and development", "research & development", "r&d"
    ],
    "operating_income": ["income from operations", "operating income"],
    "net_income": ["net income", "net earnings"],
}


def bq() -> bigquery.Client:
    if not PROJECT:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is missing. Check backend/.env.")
    return bigquery.Client(project=PROJECT)


def genai_client() -> genai.Client:
    if not PROJECT:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is missing. Check backend/.env.")
    return genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def sid(*values: Any) -> str:
    return hashlib.sha1("|".join(map(str, values)).encode("utf-8")).hexdigest()


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    s = norm(value)
    if not s or s.upper() in {"-", "—", "–", "N/A", "NM"}:
        return None

    negative = s.startswith("(") and s.endswith(")")
    s = (
        s.replace(",", "")
        .replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .replace("%", "")
        .strip()
        .strip("()")
    )

    try:
        number = float(s)
    except ValueError:
        return None

    return -number if negative else number


def detect_unit(rows: list[list[str]]) -> str:
    table_text = " ".join(
        norm(cell) for row in rows for cell in row
    ).lower()

    if "in millions" in table_text:
        return "USD millions"
    if "in billions" in table_text:
        return "USD billions"
    if "%" in table_text:
        return "percent"
    return "unknown"


def duration_from_text(text: str) -> int | None:
    text = norm(text).lower()

    if "three months ended" in text:
        return 3
    if "six months ended" in text:
        return 6
    if "nine months ended" in text:
        return 9
    if "twelve months ended" in text:
        return 12

    return None


def period_label(year: int, fiscal_quarter: str, duration_months: int) -> str:
    if duration_months == 3:
        return f"{year}-{fiscal_quarter}"
    if duration_months == 6:
        return f"{year}-H1"
    if duration_months == 9:
        return f"{year}-9M"
    if duration_months == 12:
        return f"{year}-FY"
    return f"{year}-{fiscal_quarter}-{duration_months}M"


def detect_period_columns(
    rows: list[list[str]],
    fiscal_quarter: str,
) -> dict[int, dict[str, Any]]:
    """
    Detect year/duration for each physical table column.

    This intentionally uses the extracted table structure instead of
    assuming that the final two numeric cells are the desired values.
    """

    result: dict[int, dict[str, Any]] = {}
    current_duration: int | None = None

    for row in rows:
        row_text = " ".join(norm(x) for x in row if norm(x))
        row_duration = duration_from_text(row_text)

        if row_duration is not None:
            current_duration = row_duration

        for column_index, cell in enumerate(row):
            cell_text = norm(cell)

            if not re.fullmatch(r"20\d{2}", cell_text):
                continue

            # Conservative fallback for ordinary quarterly tables.
            duration = current_duration or 3

            result[column_index] = {
                "year": int(cell_text),
                "duration_months": duration,
            }

    return result


def extract_period_values(
    rows: list[list[str]],
    row: list[str],
    fiscal_quarter: str,
) -> list[dict[str, Any]]:

    period_columns = detect_period_columns(rows, fiscal_quarter)
    output = []

    for column_index, cell in enumerate(row):
        if column_index == 0:
            continue

        value = parse_number(cell)
        if value is None:
            continue

        header = period_columns.get(column_index)
        if not header:
            continue

        output.append({
            "period": period_label(
                header["year"],
                fiscal_quarter,
                header["duration_months"],
            ),
            "value": value,
            "duration_months": header["duration_months"],
        })

    return output


def parse_file(path: Path) -> tuple[str, int, str]:
    match = re.search(r"FY(\d+)_Q([123])_10Q", path.stem, re.IGNORECASE)
    if not match:
        raise ValueError(f"Bad filename: {path}")

    company = path.parent.name.lower()
    if company not in COMPANIES:
        raise ValueError(f"Unknown company directory: {company}")

    return company, int(match.group(1)), f"Q{match.group(2)}"


def detect_section(text: str) -> str | None:
    patterns = [
        r"ITEM\s+\d+[A-Z]?\s*\.?.{0,120}",
        r"CONSOLIDATED STATEMENTS? OF [A-Z ]+",
        r"NOTES TO CONSOLIDATED FINANCIAL STATEMENTS",
        r"MANAGEMENT['’]S DISCUSSION AND ANALYSIS.{0,120}",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return norm(match.group(0))[:180]

    return None


def extract_tables(page: fitz.Page) -> list[tuple[int, list[list[str]]]]:
    output = []

    try:
        for index, table in enumerate(page.find_tables().tables):
            rows = table.extract()
            clean_rows = [
                [norm(cell) for cell in row]
                for row in rows
                if any(norm(cell) for cell in row)
            ]
            if clean_rows:
                output.append((index, clean_rows))
    except Exception:
        pass

    return output


def extract_pdf(
    pdf: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

    company, fiscal_year, fiscal_quarter = parse_file(pdf)
    period_end = COMPANIES[company][1][fiscal_quarter]

    document = fitz.open(pdf)
    chunks = []
    facts = []

    for page_number, page in enumerate(document, start=1):
        text = norm(page.get_text("text"))
        section = detect_section(text)
        parts = []

        if text:
            parts.append(("text", None, text))

        for table_index, rows in extract_tables(page):
            table_text = "\n".join(" | ".join(row) for row in rows)
            table_name = f"table_{table_index}"
            parts.append(("table", table_name, table_text))

            unit = detect_unit(rows)

            for row in rows:
                if not row:
                    continue

                source_label = norm(row[0])
                label_lower = source_label.lower()

                metric = next(
                    (
                        name
                        for name, aliases in ALIASES.items()
                        if any(alias in label_lower for alias in aliases)
                    ),
                    None,
                )

                if not metric:
                    continue

                period_values = extract_period_values(
                    rows,
                    row,
                    fiscal_quarter,
                )

                if not period_values:
                    continue

                facts.append({
                    "company": company,
                    "fiscal_year": fiscal_year,
                    "fiscal_quarter": fiscal_quarter,
                    "period_end": period_end,
                    "metric": metric,
                    "source_label": source_label,
                    "unit": unit,
                    "period_values": period_values,
                    "pdf_page": page_number,
                    "section": section,
                    "table_index": table_index,
                    "source_file": pdf.name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

        for _, (chunk_type, table_name, content) in enumerate(parts):
            if not content:
                continue

            if len(content) <= 6000 or chunk_type == "table":
                pieces = [content]
            else:
                pieces = [
                    content[i:i + 5500]
                    for i in range(0, len(content), 5000)
                ]

            for piece_index, piece in enumerate(pieces):
                chunks.append({
                    "chunk_id": sid(
                        company,
                        fiscal_year,
                        fiscal_quarter,
                        page_number,
                        chunk_type,
                        table_name,
                        piece_index,
                    ),
                    "company": company,
                    "fiscal_year": fiscal_year,
                    "fiscal_quarter": fiscal_quarter,
                    "period_end": period_end,
                    "pdf_page": page_number,
                    "section": section,
                    "table_name": table_name,
                    "chunk_type": chunk_type,
                    "source_file": pdf.name,
                    "content": piece,
                })

    document.close()
    return chunks, facts


def ensure_facts_v2_table() -> None:
    client = bq()
    table_id = f"{PROJECT}.{DATASET}.{FACTS_V2}"

    schema = [
        bigquery.SchemaField("company", "STRING"),
        bigquery.SchemaField("fiscal_year", "INT64"),
        bigquery.SchemaField("fiscal_quarter", "STRING"),
        bigquery.SchemaField("period_end", "DATE"),
        bigquery.SchemaField("metric", "STRING"),
        bigquery.SchemaField("source_label", "STRING"),
        bigquery.SchemaField("unit", "STRING"),
        bigquery.SchemaField(
            "period_values",
            "RECORD",
            mode="REPEATED",
            fields=[
                bigquery.SchemaField("period", "STRING"),
                bigquery.SchemaField("value", "FLOAT64"),
                bigquery.SchemaField("duration_months", "INT64"),
            ],
        ),
        bigquery.SchemaField("pdf_page", "INT64"),
        bigquery.SchemaField("section", "STRING"),
        bigquery.SchemaField("table_index", "INT64"),
        bigquery.SchemaField("source_file", "STRING"),
        bigquery.SchemaField("created_at", "TIMESTAMP"),
    ]

    client.create_table(
        bigquery.Table(table_id, schema=schema),
        exists_ok=True,
    )


def insert_rows_batched(
    table_id: str,
    rows: list[dict[str, Any]],
    batch_size: int,
) -> None:

    client = bq()

    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        errors = client.insert_rows_json(table_id, batch)

        if errors:
            raise RuntimeError(
                f"BigQuery insert failed for {table_id}: {errors}"
            )


def ingest_facts_v2() -> None:
    """
    Rebuild only financial_facts_v2.
    Existing chunks and embeddings are untouched.
    """

    client = bq()
    dataset_id = f"{PROJECT}.{DATASET}"

    client.create_dataset(
        bigquery.Dataset(dataset_id),
        exists_ok=True,
    )

    ensure_facts_v2_table()

    data = ROOT / "data"
    if not data.exists():
        data = ROOT.parent / "data"

    pdfs = sorted(data.glob("*/*.pdf"))

    if not pdfs:
        raise RuntimeError("No PDFs found under data/<company>/*.pdf")

    all_facts = []

    for pdf in pdfs:
        print(f"Parsing facts: {pdf}")
        _, facts = extract_pdf(pdf)
        all_facts.extend(facts)

    table_id = f"{PROJECT}.{DATASET}.{FACTS_V2}"

    client.query(f"TRUNCATE TABLE `{table_id}`").result()

    insert_rows_batched(
        table_id,
        all_facts,
        FACT_INSERT_BATCH,
    )

    artifact_dir = ROOT / "backend" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    (artifact_dir / "facts_v2.jsonl").write_text(
        "\n".join(
            json.dumps(x, ensure_ascii=False)
            for x in all_facts
        ),
        encoding="utf-8",
    )

    print("\nFACT V2 INGESTION COMPLETE")
    print(f"PDFs: {len(pdfs)}")
    print(f"Facts: {len(all_facts)}")
    print(f"Table: {table_id}")


def facts(
    company: str,
    fiscal_year: int,
    fiscal_quarter: str,
    metric: str,
) -> list[dict[str, Any]]:

    client = bq()

    sql = f"""
    SELECT *
    FROM `{PROJECT}.{DATASET}.{FACTS_V2}`
    WHERE LOWER(company) = @company
      AND fiscal_year = @fiscal_year
      AND UPPER(fiscal_quarter) = @fiscal_quarter
      AND metric = @metric
    ORDER BY pdf_page
    """

    params = [
        bigquery.ScalarQueryParameter("company", "STRING", company.lower()),
        bigquery.ScalarQueryParameter("fiscal_year", "INT64", fiscal_year),
        bigquery.ScalarQueryParameter(
            "fiscal_quarter", "STRING", fiscal_quarter.upper()
        ),
        bigquery.ScalarQueryParameter("metric", "STRING", metric),
    ]

    return [
        dict(row)
        for row in client.query(
            sql,
            job_config=bigquery.QueryJobConfig(query_parameters=params),
        ).result()
    ]


def get_period_value(
    fact: dict[str, Any],
    period: str,
    duration_months: int | None = None,
) -> float | None:

    for item in fact.get("period_values", []):
        if item.get("period") != period:
            continue

        if (
            duration_months is not None
            and item.get("duration_months") != duration_months
        ):
            continue

        if item.get("value") is not None:
            return float(item["value"])

    return None


def find_fact_for_period(
    fact_rows: list[dict[str, Any]],
    period: str,
    duration_months: int | None = None,
) -> tuple[float | None, dict[str, Any] | None]:

    for fact in fact_rows:
        value = get_period_value(
            fact,
            period,
            duration_months,
        )

        if value is not None:
            return value, fact

    return None, None


def calculate_metric_yoy(
    company: str,
    fiscal_year: int,
    fiscal_quarter: str,
    metric: str,
) -> dict[str, Any] | None:

    current_period = f"{fiscal_year}-{fiscal_quarter}"
    previous_period = f"{fiscal_year - 1}-{fiscal_quarter}"

    rows = facts(
        company,
        fiscal_year,
        fiscal_quarter,
        metric,
    )

    current, current_fact = find_fact_for_period(
        rows,
        current_period,
        3,
    )

    previous, previous_fact = find_fact_for_period(
        rows,
        previous_period,
        3,
    )

    if current is None or previous is None:
        return None

    if previous == 0:
        return None

    growth = round(
        ((current - previous) / previous) * 100,
        2,
    )

    return {
        "metric": metric,
        "current_period": current_period,
        "previous_period": previous_period,
        "current_value": current,
        "previous_value": previous,
        "growth_percent": growth,
        "unit": current_fact.get("unit") if current_fact else "unknown",
        "current_source": current_fact,
        "previous_source": previous_fact,
        "formula": "(current - previous) / previous * 100",
    }


# The existing vector-search, intent, RAG-answer and FastAPI portions
# can continue to use the same structure as your current application.
# The important change is that all financial calculations now read
# period_values from financial_facts_v2 instead of the unsafe values[].


def embed(
    texts: list[str],
    task: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:

    client = genai_client()
    output = []

    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]

        response = client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task,
                output_dimensionality=EMBED_DIM,
                auto_truncate=True,
            ),
        )

        output.extend(
            [list(embedding.values) for embedding in response.embeddings]
        )

    return output


def search(
    question: str,
    company: str | None = None,
    fiscal_year: int | None = None,
    fiscal_quarter: str | None = None,
) -> list[dict[str, Any]]:

    query_embedding = embed(
        [question],
        "RETRIEVAL_QUERY",
    )[0]

    client = bq()
    where = []

    params = [
        bigquery.ArrayQueryParameter(
            "embedding", "FLOAT64", query_embedding
        ),
        bigquery.ScalarQueryParameter(
            "question", "STRING", question
        ),
        bigquery.ScalarQueryParameter(
            "topk", "INT64", TOP_K
        ),
    ]

    if company:
        where.append("company = @company")
        params.append(
            bigquery.ScalarQueryParameter(
                "company", "STRING", company.lower()
            )
        )

    if fiscal_year:
        where.append("fiscal_year = @fy")
        params.append(
            bigquery.ScalarQueryParameter(
                "fy", "INT64", fiscal_year
            )
        )

    if fiscal_quarter:
        where.append("fiscal_quarter = @q")
        params.append(
            bigquery.ScalarQueryParameter(
                "q", "STRING", fiscal_quarter.upper()
            )
        )

    base = f"""
        SELECT *
        FROM `{PROJECT}.{DATASET}.{CHUNKS}`
    """

    if where:
        base += " WHERE " + " AND ".join(where)

    sql = f"""
    SELECT
        base.chunk_id,
        base.company,
        base.fiscal_year,
        base.fiscal_quarter,
        base.period_end,
        base.pdf_page,
        base.section,
        base.table_name,
        base.chunk_type,
        base.source_file,
        base.content,
        distance
    FROM VECTOR_SEARCH(
        ({base}),
        'embedding',
        query_value => @embedding,
        lexical_search_columns => ['content'],
        lexical_search_query_value => @question,
        top_k => @topk,
        distance_type => 'COSINE'
    )
    ORDER BY distance
    """

    return [
        dict(row)
        for row in client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=params
            ),
        ).result()
    ]


INTENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "mode": {
            "type": "STRING",
            "enum": [
                "lookup",
                "calculation",
                "comparison",
                "narrative",
                "unknown",
            ],
        },
        "company": {"type": "STRING"},
        "fiscal_year": {"type": "INTEGER"},
        "fiscal_quarter": {"type": "STRING"},
        "metrics": {
            "type": "ARRAY",
            "items": {
                "type": "STRING",
                "enum": [
                    "revenue",
                    "research_and_development",
                    "operating_income",
                    "net_income",
                ],
            },
        },
        "calculation": {
            "type": "STRING",
            "enum": [
                "none",
                "yoy_growth",
                "qoq_growth",
                "margin",
                "absolute_change",
                "compare_growth",
            ],
        },
    },
    "required": ["mode"],
}


def intent(question: str) -> dict[str, Any]:

    client = genai_client()

    prompt = f"""
Classify this question about SEC 10-Q financial filings.

Return only JSON matching the supplied schema.

Rules:
- Never invent a company, year or quarter.
- metrics may contain more than one metric.
- Use "comparison" when the user compares companies or metrics.
- Use "calculation" when arithmetic is required.
- Use "compare_growth" when comparing growth rates of two metrics.
- Supported metrics:
  revenue
  research_and_development
  operating_income
  net_income

Question:
{question}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=INTENT_SCHEMA,
        ),
    )

    return json.loads(response.text)


def calculate(
    intent_result: dict[str, Any],
) -> list[dict[str, Any]]:

    company = intent_result.get("company")
    fiscal_year = intent_result.get("fiscal_year")
    fiscal_quarter = intent_result.get("fiscal_quarter")
    metrics = intent_result.get("metrics") or []

    if not all([company, fiscal_year, fiscal_quarter, metrics]):
        return []

    calculation_type = intent_result.get(
        "calculation",
        "none",
    )

    results = []

    if calculation_type in {
        "yoy_growth",
        "compare_growth",
    }:
        for metric in metrics:
            result = calculate_metric_yoy(
                company,
                int(fiscal_year),
                fiscal_quarter,
                metric,
            )

            if result:
                results.append(result)

    if (
        calculation_type == "compare_growth"
        and len(results) >= 2
    ):
        first = results[0]
        second = results[1]

        difference = round(
            first["growth_percent"]
            - second["growth_percent"],
            2,
        )

        results.append({
            "type": "growth_rate_comparison",
            "metric_a": first["metric"],
            "growth_a": first["growth_percent"],
            "metric_b": second["metric"],
            "growth_b": second["growth_percent"],
            "difference_percentage_points": difference,
            "formula": "growth_a - growth_b",
        })

    return results


def answer(
    question: str,
    evidence: list[dict[str, Any]],
    calculations: list[dict[str, Any]],
) -> str:

    client = genai_client()

    evidence_blocks = []

    for number, item in enumerate(evidence, start=1):
        evidence_blocks.append(
            f"""
[S{number}]
Company: {item["company"]}
Fiscal year: {item["fiscal_year"]}
Fiscal quarter: {item["fiscal_quarter"]}
PDF page: {item["pdf_page"]}
Section: {item.get("section")}
Table: {item.get("table_name")}
Source file: {item["source_file"]}

{item["content"]}
"""
        )

    prompt = f"""
You are a financial analyst assistant.

Answer the user's question using ONLY the supplied evidence.

Rules:
1. Never invent financial numbers.
2. Deterministic calculations are authoritative.
3. Do not perform arithmetic yourself when a calculation result is supplied.
4. Every factual financial claim must be supported by evidence.
5. Cite evidence inline using [S1], [S2], etc.
6. If evidence is insufficient, say so.
7. Do not cite a source that does not support the claim.
8. Keep the answer concise but explain calculations when relevant.

QUESTION:
{question}

DETERMINISTIC CALCULATIONS:
{json.dumps(calculations, indent=2, default=str)}

EVIDENCE:
{"".join(evidence_blocks)}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
        ),
    )

    return response.text.strip()


class Query(BaseModel):
    question: str = Field(min_length=3)
    company: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: str | None = None


app = FastAPI(
    title="SEC Financial Analyst Copilot",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "project": PROJECT,
        "dataset": DATASET,
        "facts_table": FACTS_V2,
        "model": MODEL,
        "embedding_model": EMBED_MODEL,
        "embedding_dimension": EMBED_DIM,
    }


@app.post("/api/query")
def query(request: Query):

    try:
        intent_result = intent(request.question)

        company = (
            request.company
            or intent_result.get("company")
        )

        fiscal_year = (
            request.fiscal_year
            or intent_result.get("fiscal_year")
        )

        fiscal_quarter = (
            request.fiscal_quarter
            or intent_result.get("fiscal_quarter")
        )

        evidence = search(
            request.question,
            company,
            fiscal_year,
            fiscal_quarter,
        )

        calculation_input = {
            **intent_result,
            "company": company,
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
        }

        calculations = calculate(calculation_input)

        generated_answer = answer(
            request.question,
            evidence,
            calculations,
        )

        citations = []
        seen = set()

        for item in evidence:
            chunk_id = item["chunk_id"]

            if chunk_id in seen:
                continue

            seen.add(chunk_id)

            citations.append({
                key: item.get(key)
                for key in [
                    "company",
                    "fiscal_year",
                    "fiscal_quarter",
                    "pdf_page",
                    "section",
                    "table_name",
                    "chunk_id",
                    "source_file",
                ]
            })

        return {
            "answer": generated_answer,
            "intent": intent_result,
            "calculations": calculations,
            "citations": citations,
            "retrieved_chunks": len(evidence),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


if __name__ == "__main__":

    command = (
        sys.argv[1].lower()
        if len(sys.argv) > 1
        else "help"
    )

    if command == "ingest-facts":
        ingest_facts_v2()

    elif command == "help":
        print("""
SEC Financial Analyst Copilot

Commands:

  python backend/app.py ingest-facts
      Rebuild only financial_facts_v2.

  uvicorn backend.app:app --reload --port 8000
      Start the API.
""")

    else:
        print(f"Unknown command: {command}")
