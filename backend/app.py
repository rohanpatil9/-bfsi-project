# backend/app.py



import json

import os

import re

from typing import Any



from dotenv import load_dotenv

from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from google.cloud import bigquery

from google import genai

from google.genai import types





# ============================================================

# ENVIRONMENT

# ============================================================



ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



load_dotenv(os.path.join(ROOT, "backend", ".env"))



PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "bfsi-project-510910")

DATASET = os.getenv("BQ_DATASET", "sec_analyst")



CHUNKS_TABLE = os.getenv("BQ_CHUNKS_TABLE", "chunks")

FACTS_FINAL = os.getenv("BQ_FACTS_TABLE", "financial_facts_final")



GEMINI_MODEL = os.getenv(

    "GEMINI_MODEL",

    "gemini-2.5-flash",

)



EMBEDDING_MODEL = os.getenv(

    "EMBEDDING_MODEL",

    "gemini-embedding-001",

)



EMBEDDING_DIM = int(

    os.getenv("EMBEDDING_DIM", "768")

)



TOP_K = 8





# ============================================================

# FASTAPI

# ============================================================



app = FastAPI(

    title="SEC Financial Analyst Copilot",

    version="1.0.0",

)



app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)





# ============================================================

# CLIENTS

# ============================================================



_bq_client = None

_genai_client = None





def bq() -> bigquery.Client:

    global _bq_client



    if _bq_client is None:

        _bq_client = bigquery.Client(

            project=PROJECT

        )



    return _bq_client





def genai_client() -> genai.Client:

    global _genai_client



    if _genai_client is None:

        _genai_client = genai.Client(

            vertexai=True,

            project=PROJECT,

            location=os.getenv(

                "GOOGLE_CLOUD_LOCATION",

                "global",

            ),

        )



    return _genai_client





# ============================================================

# NORMALIZATION

# ============================================================



COMPANY_ALIASES = {

    "amazon": "amazon",

    "amazon.com": "amazon",



    "alphabet": "alphabet",

    "google": "alphabet",



    "meta": "meta",

    "meta platforms": "meta",

    "facebook": "meta",



    "nvidia": "nvidia",

}





ALIASES = {

    "revenue": [

        "revenues",

        "revenue",

        "total revenues",

        "total revenue",

    ],



    "research_and_development": [

        "research and development",

        "research & development",

        "r&d",

        "research development",

    ],



    "operating_income": [

        "income from operations",

        "operating income",

        "income from operating activities",

    ],



    "net_income": [

        "net income",

        "net earnings",

        "net income attributable",

    ],

}





def normalize_company(value: Any) -> str:

    if value is None:
        return ""

    # Gemini can occasionally return a list for a field that was
    # intended to be scalar. For a scalar context, use the first item.
    if isinstance(value, list):
        if not value:
            return ""
        value = value[0]

    value = str(value).strip().lower()

    return COMPANY_ALIASES.get(value, value)


def normalize_quarter(value: str | None) -> str:

    if not value:

        return ""



    value = value.strip().upper()



    match = re.search(

        r"Q([1-4])",

        value,

    )



    if match:

        return f"Q{match.group(1)}"



    return value





def normalize_metric(value: str | None) -> str:

    if not value:

        return ""



    value = value.strip().lower()



    for canonical, aliases in ALIASES.items():

        for alias in aliases:

            if alias.lower() in value:

                return canonical



    return value





def metric_display_name(metric: str) -> str:

    names = {

        "revenue": "Revenue",

        "research_and_development": "Research and Development",

        "operating_income": "Operating Income",

        "net_income": "Net Income",

    }



    return names.get(

        metric,

        metric.replace("_", " ").title(),

    )





def norm(value: Any) -> str:

    if value is None:

        return ""



    return str(value)





# ============================================================

# FISCAL PERIOD HELPERS

# ============================================================



FISCAL_PERIODS = {

    "amazon": {

        (2025, "Q1"): "2025-03-31",

        (2025, "Q2"): "2025-06-30",

        (2025, "Q3"): "2025-09-30",

    },



    "alphabet": {

        (2025, "Q1"): "2025-03-31",

        (2025, "Q2"): "2025-06-30",

        (2025, "Q3"): "2025-09-30",

    },



    "meta": {

        (2025, "Q1"): "2025-03-31",

        (2025, "Q2"): "2025-06-30",

        (2025, "Q3"): "2025-09-30",

    },



    "nvidia": {

        (2026, "Q1"): "2025-04-27",

        (2026, "Q2"): "2025-07-27",

        (2026, "Q3"): "2025-10-26",

    },

}





# ============================================================

# GEMINI EMBEDDINGS

# ============================================================



def create_embedding(text: str) -> list[float]:

    client = genai_client()



    response = client.models.embed_content(

        model=EMBEDDING_MODEL,

        contents=text,

        config=types.EmbedContentConfig(

            output_dimensionality=EMBEDDING_DIM,

        ),

    )



    return list(

        response.embeddings[0].values

    )





# ============================================================

# VECTOR SEARCH

# ============================================================



def vector_search(
    question: str,
    company: str | None = None,
    fiscal_year: int | None = None,
    fiscal_quarter: str | None = None,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:

    embedding = create_embedding(question)
    client = bq()

    company = normalize_company(company)
    fiscal_quarter = normalize_quarter(fiscal_quarter)

    filters = []
    params = [
        bigquery.ArrayQueryParameter(
            "query_embedding", "FLOAT64", embedding
        ),
        bigquery.ScalarQueryParameter(
            "top_k", "INT64", top_k
        ),
    ]

    if company:
        filters.append("LOWER(company) = @company")
        params.append(
            bigquery.ScalarQueryParameter(
                "company", "STRING", company
            )
        )

    if fiscal_year:
        filters.append("fiscal_year = @fiscal_year")
        params.append(
            bigquery.ScalarQueryParameter(
                "fiscal_year", "INT64", int(fiscal_year)
            )
        )

    if fiscal_quarter:
        filters.append("UPPER(fiscal_quarter) = @fiscal_quarter")
        params.append(
            bigquery.ScalarQueryParameter(
                "fiscal_quarter", "STRING", fiscal_quarter
            )
        )

    table_expr = f"SELECT * FROM `{PROJECT}.{DATASET}.{CHUNKS_TABLE}`"
    if filters:
        table_expr += " WHERE " + " AND ".join(filters)

    sql = f"""
    SELECT
        chunk_id,
        company,
        fiscal_year,
        fiscal_quarter,
        period_end,
        pdf_page,
        section,
        table_name,
        chunk_type,
        source_file,
        content,
        distance
    FROM VECTOR_SEARCH(
        ({table_expr}),
        'embedding',
        (SELECT @query_embedding AS embedding),
        top_k => @top_k,
        distance_type => 'COSINE'
    )
    ORDER BY distance
    """

    rows = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=params
        ),
    ).result()

    return [dict(row) for row in rows]


def facts(

    company: str,

    fiscal_year: int,

    fiscal_quarter: str,

    metric: str,

) -> list[dict[str, Any]]:



    company = normalize_company(company)

    fiscal_quarter = normalize_quarter(

        fiscal_quarter

    )

    metric = normalize_metric(metric)



    client = bq()



    sql = f"""

    SELECT

        company,

        fiscal_year,

        fiscal_quarter,

        period_end,

        metric,

        source_label,

        period,

        duration_months,

        value,

        unit,

        pdf_page,

        section,

        source_file

    FROM `{PROJECT}.{DATASET}.{FACTS_FINAL}`

    WHERE LOWER(company) = @company

      AND fiscal_year = @fiscal_year

      AND UPPER(fiscal_quarter) = @fiscal_quarter

      AND metric = @metric

      AND duration_months = 3

    ORDER BY pdf_page

    """



    params = [

        bigquery.ScalarQueryParameter(

            "company",

            "STRING",

            company,

        ),

        bigquery.ScalarQueryParameter(

            "fiscal_year",

            "INT64",

            fiscal_year,

        ),

        bigquery.ScalarQueryParameter(

            "fiscal_quarter",

            "STRING",

            fiscal_quarter,

        ),

        bigquery.ScalarQueryParameter(

            "metric",

            "STRING",

            metric,

        ),

    ]



    rows = client.query(

        sql,

        job_config=bigquery.QueryJobConfig(

            query_parameters=params

        ),

    ).result()



    return [dict(row) for row in rows]





# ============================================================

# TARGETED CITATION SEARCH

# ============================================================

#

# Vector search is useful for semantic retrieval, but financial

# tables need deterministic citation retrieval.

#

# This query searches the actual chunks table for the requested

# metric + company + filing period and strongly prefers tables

# such as "CONSOLIDATED STATEMENTS OF INCOME".

# ============================================================



def targeted_citation_search(

    company: str,

    fiscal_year: int,

    fiscal_quarter: str,

    metrics: list[str],

) -> list[dict[str, Any]]:



    company = normalize_company(company)

    fiscal_quarter = normalize_quarter(

        fiscal_quarter

    )



    if not metrics:

        return []



    client = bq()



    metric_aliases = []



    for metric in metrics:

        metric = normalize_metric(metric)



        for alias in ALIASES.get(

            metric,

            [metric],

        ):

            metric_aliases.append(

                alias.lower()

            )



    # Remove duplicates while preserving order.

    metric_aliases = list(

        dict.fromkeys(metric_aliases)

    )



    sql = f"""

    SELECT

        chunk_id,

        company,

        fiscal_year,

        fiscal_quarter,

        period_end,

        pdf_page,

        section,

        table_name,

        chunk_type,

        source_file,

        content

    FROM `{PROJECT}.{DATASET}.{CHUNKS_TABLE}`

    WHERE LOWER(company) = @company

      AND fiscal_year = @fiscal_year

      AND UPPER(fiscal_quarter) = @fiscal_quarter

      AND (

            chunk_type = 'table'

            OR LOWER(COALESCE(table_name, '')) != ''

            OR LOWER(COALESCE(section, '')) != ''

          )

      AND LOWER(content) LIKE ANY(

            @metric_patterns

          )

    LIMIT 100

    """



    patterns = [

        f"%{alias}%"

        for alias in metric_aliases

    ]



    params = [

        bigquery.ScalarQueryParameter(

            "company",

            "STRING",

            company,

        ),

        bigquery.ScalarQueryParameter(

            "fiscal_year",

            "INT64",

            fiscal_year,

        ),

        bigquery.ScalarQueryParameter(

            "fiscal_quarter",

            "STRING",

            fiscal_quarter,

        ),

        bigquery.ArrayQueryParameter(

            "metric_patterns",

            "STRING",

            patterns,

        ),

    ]



    try:

        rows = client.query(

            sql,

            job_config=bigquery.QueryJobConfig(

                query_parameters=params

            ),

        ).result()



    except Exception:

        # Fallback for BigQuery environments where LIKE ANY

        # is unavailable.

        return targeted_citation_search_fallback(

            company,

            fiscal_year,

            fiscal_quarter,

            metric_aliases,

        )



    results = [

        dict(row)

        for row in rows

    ]



    return rank_citation_chunks(

        results,

        metrics,

    )





def targeted_citation_search_fallback(

    company: str,

    fiscal_year: int,

    fiscal_quarter: str,

    metric_aliases: list[str],

) -> list[dict[str, Any]]:



    client = bq()



    conditions = []



    for index, alias in enumerate(

        metric_aliases

    ):

        conditions.append(

            f"LOWER(content) LIKE @pattern_{index}"

        )



    if not conditions:

        return []



    sql = f"""

    SELECT

        chunk_id,

        company,

        fiscal_year,

        fiscal_quarter,

        period_end,

        pdf_page,

        section,

        table_name,

        chunk_type,

        source_file,

        content

    FROM `{PROJECT}.{DATASET}.{CHUNKS_TABLE}`

    WHERE LOWER(company) = @company

      AND fiscal_year = @fiscal_year

      AND UPPER(fiscal_quarter) = @fiscal_quarter

      AND (

        {" OR ".join(conditions)}

      )

    LIMIT 100

    """



    params = [

        bigquery.ScalarQueryParameter(

            "company",

            "STRING",

            company,

        ),

        bigquery.ScalarQueryParameter(

            "fiscal_year",

            "INT64",

            fiscal_year,

        ),

        bigquery.ScalarQueryParameter(

            "fiscal_quarter",

            "STRING",

            fiscal_quarter,

        ),

    ]



    for index, alias in enumerate(

        metric_aliases

    ):

        params.append(

            bigquery.ScalarQueryParameter(

                f"pattern_{index}",

                "STRING",

                f"%{alias}%",

            )

        )



    rows = client.query(

        sql,

        job_config=bigquery.QueryJobConfig(

            query_parameters=params

        ),

    ).result()



    results = [

        dict(row)

        for row in rows

    ]



    return results





# ============================================================

# CITATION RANKING

# ============================================================



def rank_citation_chunks(

    chunks: list[dict[str, Any]],

    metrics: list[str],

) -> list[dict[str, Any]]:



    normalized_metrics = [

        normalize_metric(metric)

        for metric in metrics

    ]



    scored = []



    for item in chunks:



        content = norm(

            item.get("content")

        ).lower()



        section = norm(

            item.get("section")

        ).lower()



        table_name = norm(

            item.get("table_name")

        ).lower()



        score = 0



        # Actual table chunks are much more useful

        # for financial-number citations.

        if item.get("chunk_type") == "table":

            score += 30



        # Financial statement tables.

        if (

            "consolidated statements of income"

            in section

        ):

            score += 40



        if (

            "consolidated statements of income"

            in table_name

        ):

            score += 40



        if (

            "consolidated statements of operations"

            in section

        ):

            score += 35



        if (

            "consolidated statements of operations"

            in table_name

        ):

            score += 35



        # Metric-specific relevance.

        for metric in normalized_metrics:



            aliases = ALIASES.get(

                metric,

                [metric],

            )



            for alias in aliases:

                alias = alias.lower()



                if alias in content:

                    score += 25



                if alias in section:

                    score += 10



                if alias in table_name:

                    score += 10



        # Prefer chunks that contain multiple relevant

        # financial metrics. This is particularly useful

        # for questions comparing R&D vs revenue.

        relevant_metric_count = 0



        for metric in normalized_metrics:



            aliases = ALIASES.get(

                metric,

                [metric],

            )



            if any(

                alias.lower() in content

                for alias in aliases

            ):

                relevant_metric_count += 1



        score += (

            relevant_metric_count * 20

        )



        # Penalize clearly non-table text.

        if (

            item.get("chunk_type") == "text"

            and score < 30

        ):

            score -= 10



        scored.append(

            (

                score,

                item,

            )

        )



    scored.sort(

        key=lambda x: (

            x[0],

            -(x[1].get("pdf_page") or 999999),

        ),

        reverse=True,

    )



    return [

        item

        for score, item in scored

    ]





# ============================================================

# DETERMINISTIC YOY CALCULATION

# ============================================================



def calculate_metric_yoy(

    company: str,

    fiscal_year: int,

    fiscal_quarter: str,

    metric: str,

) -> dict[str, Any] | None:



    company = normalize_company(company)

    fiscal_quarter = normalize_quarter(

        fiscal_quarter

    )

    metric = normalize_metric(metric)



    current_facts = facts(

        company,

        fiscal_year,

        fiscal_quarter,

        metric,

    )



    if not current_facts:

        return None



    previous_facts = facts(

        company,

        fiscal_year - 1,

        fiscal_quarter,

        metric,

    )



    if not previous_facts:

        return None



    current = current_facts[0]

    previous = previous_facts[0]



    current_value = float(

        current["value"]

    )



    previous_value = float(

        previous["value"]

    )



    if previous_value == 0:

        return None



    growth = round(

        (

            (

                current_value

                - previous_value

            )

            / previous_value

        )

        * 100,

        2,

    )



    return {

        "name": metric,

        "metric": metric,



        "current_period": (

            f"{fiscal_year}-{fiscal_quarter}"

        ),



        "previous_period": (

            f"{fiscal_year - 1}-{fiscal_quarter}"

        ),



        "current_value": current_value,

        "previous_value": previous_value,



        "growth_percent": growth,

        "result": growth,

        "unit": "%",



        # ASCII deliberately used here to avoid

        # encoding/mojibake problems.

        "formula": (

            f"({current_value:,.2f} - "

            f"{previous_value:,.2f}) / "

            f"{previous_value:,.2f} * 100"

        ),



        "source": {

            "current": current,

            "previous": previous,

        },

    }





# ============================================================

# DIRECT METRIC LOOKUP

# ============================================================



def calculate_metric_lookup(

    company: str,

    fiscal_year: int,

    fiscal_quarter: str,

    metric: str,

) -> dict[str, Any] | None:



    company = normalize_company(company)

    fiscal_quarter = normalize_quarter(

        fiscal_quarter

    )

    metric = normalize_metric(metric)



    rows = facts(

        company,

        fiscal_year,

        fiscal_quarter,

        metric,

    )



    if not rows:

        return None



    row = rows[0]



    return {

        "name": metric,

        "metric": metric,



        "current_period": (

            f"{fiscal_year}-{fiscal_quarter}"

        ),



        "current_value": float(

            row["value"]

        ),



        "result": float(

            row["value"]

        ),



        "unit": row.get("unit"),



        "source": row,

    }





# ============================================================

# CALCULATION ENGINE

# ============================================================



def calculate(
    intent_data: dict[str, Any]
) -> list[dict[str, Any]]:

    if not isinstance(intent_data, dict):
        return []

    fiscal_year = intent_data.get("fiscal_year")
    fiscal_quarter = normalize_quarter(
        intent_data.get("fiscal_quarter")
    )
    metrics = intent_data.get("metrics") or []
    calculation_type = (
        intent_data.get("calculation", "none") or "none"
    ).lower()

    normalized_metrics = [
        normalize_metric(metric)
        for metric in metrics
        if metric
    ]

    if not fiscal_year or not fiscal_quarter or not normalized_metrics:
        return []

    # --------------------------------------------------------
    # MULTI-COMPANY COMPARISON
    # --------------------------------------------------------
    raw_companies = intent_data.get("companies") or []

    if not raw_companies and intent_data.get("company"):
        raw_companies = [intent_data.get("company")]

    companies = []
    for value in raw_companies:
        normalized = normalize_company(value)
        if normalized and normalized not in companies:
            companies.append(normalized)

    if (
        intent_data.get("mode") == "comparison"
        and len(companies) > 1
        and calculation_type == "none"
    ):
        calculations = []

        for company in companies:
            for metric in normalized_metrics:
                result = calculate_metric_lookup(
                    company,
                    int(fiscal_year),
                    fiscal_quarter,
                    metric,
                )

                if result:
                    result["company"] = company
                    result["name"] = (
                        f"{company.title()} {metric_display_name(metric)}"
                    )
                    calculations.append(result)

        return calculations

    # --------------------------------------------------------
    # SINGLE-COMPANY CALCULATIONS
    # --------------------------------------------------------
    company = normalize_company(intent_data.get("company"))

    if not company:
        if companies:
            company = companies[0]
        else:
            return []

    calculations = []

    if calculation_type in {
        "yoy",
        "yoy_growth",
        "growth",
        "growth_rate",
    }:
        for metric in normalized_metrics:
            result = calculate_metric_yoy(
                company,
                int(fiscal_year),
                fiscal_quarter,
                metric,
            )
            if result:
                calculations.append(result)

        return calculations

    if calculation_type == "compare_growth":
        growth_results = []

        for metric in normalized_metrics:
            result = calculate_metric_yoy(
                company,
                int(fiscal_year),
                fiscal_quarter,
                metric,
            )
            if result:
                calculations.append(result)
                growth_results.append(result)

        if len(growth_results) >= 2:
            first = growth_results[0]
            second = growth_results[1]
            difference = round(
                first["growth_percent"] - second["growth_percent"],
                2,
            )

            calculations.append({
                "name": "Growth difference",
                "type": "growth_rate_comparison",
                "metric_a": first["metric"],
                "growth_a": first["growth_percent"],
                "metric_b": second["metric"],
                "growth_b": second["growth_percent"],
                "difference_percentage_points": difference,
                "result": difference,
                "unit": "percentage points",
                "formula": (
                    f"{first['growth_percent']:.2f}% - "
                    f"{second['growth_percent']:.2f}%"
                ),
            })

        return calculations

    # Direct lookup.
    if calculation_type == "none":
        for metric in normalized_metrics:
            result = calculate_metric_lookup(
                company,
                int(fiscal_year),
                fiscal_quarter,
                metric,
            )
            if result:
                calculations.append(result)

    return calculations


def intent(
    question: str
) -> dict[str, Any]:

    client = genai_client()

    prompt = f"""
You are the intent parser for an SEC 10-Q financial analyst copilot.

Return ONLY valid JSON matching this structure:
{{
  "mode": "lookup | comparison | narrative | unknown",
  "company": "amazon | alphabet | meta | nvidia | null",
  "companies": ["amazon", "alphabet", "meta", "nvidia"],
  "fiscal_year": 2025,
  "fiscal_quarter": "Q1 | Q2 | Q3 | null",
  "metrics": ["revenue", "research_and_development", "operating_income", "net_income"],
  "calculation": "none | yoy_growth | qoq_growth | margin | absolute_change | compare_growth"
}}

Rules:
1. Identify ALL companies explicitly mentioned.
2. For a single company, companies must contain one company.
3. For a multi-company comparison, put every company in companies and set mode to comparison.
4. Set company to the first company in companies for compatibility.
5. "R&D" means research_and_development.
6. "revenue" means revenue.
7. If comparing companies on a metric without asking for growth, use calculation = none.
8. If comparing growth rates of two metrics, use calculation = compare_growth.
9. If asking for year-over-year growth, use calculation = yoy_growth.
10. Never invent a company, year, quarter, or metric.
11. NVIDIA Q1/Q2/Q3 filings in this dataset are FY2026.

Examples:
Question: Compare Amazon, Alphabet and Meta revenue in Q1 2025.
JSON:
{{"mode":"comparison","company":"amazon","companies":["amazon","alphabet","meta"],"fiscal_year":2025,"fiscal_quarter":"Q1","metrics":["revenue"],"calculation":"none"}}

Question: Did Alphabet's R&D grow faster than revenue in Q1 2025?
JSON:
{{"mode":"comparison","company":"alphabet","companies":["alphabet"],"fiscal_year":2025,"fiscal_quarter":"Q1","metrics":["research_and_development","revenue"],"calculation":"compare_growth"}}

User question:
{question}
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )

    text_response = (response.text or "{}").strip()

    try:
        data = json.loads(text_response)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text_response, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}

    # Normalize the shape even if Gemini returns a scalar company only.
    if not isinstance(data, dict):
        data = {}

    raw_companies = data.get("companies") or []
    if not isinstance(raw_companies, list):
        raw_companies = [raw_companies]

    company = data.get("company")
    if company and company not in raw_companies:
        raw_companies.insert(0, company)

    normalized_companies = []
    for value in raw_companies:
        normalized = normalize_company(value)
        if normalized and normalized not in normalized_companies:
            normalized_companies.append(normalized)

    if not company and normalized_companies:
        company = normalized_companies[0]

    data["company"] = normalize_company(company) or None
    data["companies"] = normalized_companies
    data["metrics"] = data.get("metrics") or []
    data["calculation"] = data.get("calculation") or "none"

    return data


def build_citations(
    evidence: list[dict[str, Any]],
    calculations: list[dict[str, Any]],
    intent_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:

    metrics = set()
    for calculation in calculations:
        for key in ("metric", "metric_a", "metric_b"):
            value = calculation.get(key)
            if value:
                metrics.add(normalize_metric(value))

    if intent_data:
        for metric in intent_data.get("metrics") or []:
            metrics.add(normalize_metric(metric))

    companies = []
    if intent_data:
        raw = intent_data.get("companies") or []
        if not isinstance(raw, list):
            raw = [raw]
        if intent_data.get("company") and intent_data.get("company") not in raw:
            raw.insert(0, intent_data.get("company"))
        for value in raw:
            normalized = normalize_company(value)
            if normalized and normalized not in companies:
                companies.append(normalized)

    fiscal_year = intent_data.get("fiscal_year") if intent_data else None
    fiscal_quarter = normalize_quarter(
        intent_data.get("fiscal_quarter") if intent_data else None
    )

    candidates = []
    candidates.extend(evidence)

    # Targeted citation retrieval for every requested company.
    for company in companies:
        if fiscal_year and fiscal_quarter and metrics:
            try:
                candidates.extend(
                    targeted_citation_search(
                        company,
                        int(fiscal_year),
                        fiscal_quarter,
                        list(metrics),
                    )
                )
            except Exception as exc:
                print("Citation search warning:", repr(exc))

    scored = []
    seen = set()

    for item in candidates:
        chunk_id = item.get("chunk_id")
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)

        content = norm(item.get("content")).lower()
        section = norm(item.get("section")).lower()
        table_name = norm(item.get("table_name")).lower()
        score = 0

        if item.get("chunk_type") == "table":
            score += 50
        if "consolidated statements of income" in section:
            score += 60
        if "consolidated statements of income" in table_name:
            score += 60
        if "consolidated statements of operations" in section:
            score += 50
        if "consolidated statements of operations" in table_name:
            score += 50

        matched = 0
        for metric in metrics:
            aliases = ALIASES.get(metric, [metric])
            if any(alias.lower() in content for alias in aliases):
                matched += 1
                score += 30

        if len(metrics) >= 2 and matched >= 2:
            score += 70

        # For cross-company comparisons, keep one strong source per company.
        item_company = normalize_company(item.get("company"))
        if item_company in companies:
            score += 20

        scored.append((score, item))

    scored.sort(
        key=lambda x: (
            x[0],
            -(x[1].get("pdf_page") or 999999),
        ),
        reverse=True,
    )

    citations = []
    company_counts = {company: 0 for company in companies}

    # First pass: ensure multi-company comparisons get at least one citation
    # for every requested company.
    for score, item in scored:
        company = normalize_company(item.get("company"))
        if company not in company_counts or company_counts[company] > 0:
            continue

        citations.append({
            "company": item.get("company"),
            "fiscal_year": item.get("fiscal_year"),
            "fiscal_quarter": item.get("fiscal_quarter"),
            "pdf_page": item.get("pdf_page"),
            "section": item.get("section"),
            "table_name": item.get("table_name"),
            "chunk_type": item.get("chunk_type"),
            "chunk_id": item.get("chunk_id"),
            "source_file": item.get("source_file"),
            "relevance_score": score,
            "content": item.get("content"),
        })
        company_counts[company] += 1

    # Second pass: fill remaining slots with highest-quality sources.
    selected_ids = {item.get("chunk_id") for item in citations}
    for score, item in scored:
        if len(citations) >= 6:
            break
        if item.get("chunk_id") in selected_ids:
            continue

        citations.append({
            "company": item.get("company"),
            "fiscal_year": item.get("fiscal_year"),
            "fiscal_quarter": item.get("fiscal_quarter"),
            "pdf_page": item.get("pdf_page"),
            "section": item.get("section"),
            "table_name": item.get("table_name"),
            "chunk_type": item.get("chunk_type"),
            "chunk_id": item.get("chunk_id"),
            "source_file": item.get("source_file"),
            "relevance_score": score,
            "content": item.get("content"),
        })
        selected_ids.add(item.get("chunk_id"))

    return citations


def answer(
    question: str,
    intent_data: dict[str, Any],
    evidence: list[dict[str, Any]],
    calculations: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> str:

    client = genai_client()

    evidence_text = ""
    for index, item in enumerate(citations, start=1):
        evidence_text += f"""
[S{index}]
Company: {item.get("company")}
Fiscal Year: {item.get("fiscal_year")}
Fiscal Quarter: {item.get("fiscal_quarter")}
PDF Page: {item.get("pdf_page")}
Section: {item.get("section")}
Table: {item.get("table_name")}
Content:
{item.get("content")}

"""

    # If citation retrieval returned nothing, still expose the vector evidence
    # to Gemini, but do not invent citation identifiers.
    if not citations:
        for index, item in enumerate(evidence[:8], start=1):
            evidence_text += f"""
[S{index}]
Company: {item.get("company")}
Fiscal Year: {item.get("fiscal_year")}
Fiscal Quarter: {item.get("fiscal_quarter")}
PDF Page: {item.get("pdf_page")}
Section: {item.get("section")}
Table: {item.get("table_name")}
Content:
{item.get("content")}

"""

    prompt = f"""
You are a financial analyst copilot.

Answer the user's question using ONLY the supplied evidence and deterministic calculations.

USER QUESTION:
{question}

INTENT:
{json.dumps(intent_data, indent=2, default=str)}

DETERMINISTIC CALCULATIONS:
{json.dumps(calculations, indent=2, default=str)}

EVIDENCE:
{evidence_text}

STRICT RULES:
1. Never invent financial numbers.
2. Deterministic calculations are authoritative.
3. Never perform arithmetic yourself when a deterministic calculation is supplied.
4. Use exact values from calculations.
5. Every factual financial claim must be supported by evidence or a deterministic calculation.
6. Cite factual claims using only [S1], [S2], etc. from the supplied evidence.
7. Never create a citation identifier that does not exist.
8. For multi-company comparisons, clearly name each company and its value.
9. If evidence or calculations are insufficient, say so rather than guessing.
10. Keep the answer concise and analyst-friendly.
11. Do not mention BigQuery, embeddings, vector search, Gemini, or internal implementation details.

Return only the final answer.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
        ),
    )

    return (
        response.text
        or "I could not generate an answer from the available evidence."
    ).strip()


from pydantic import BaseModel





class QueryRequest(BaseModel):

    question: str





# ============================================================

# HEALTH

# ============================================================



@app.get("/health")

def health():

    return {

        "status": "ok",

        "project": PROJECT,

        "dataset": DATASET,

        "facts_table": FACTS_FINAL,

        "chunks_table": CHUNKS_TABLE,

    }





# ============================================================

# QUERY ENDPOINT

# ============================================================



@app.post("/api/query")
def query(request: QueryRequest):

    question = request.question.strip()

    if not question:
        return {
            "answer": "Please provide a question.",
            "intent": {},
            "calculations": [],
            "citations": [],
            "retrieved_chunks": 0,
        }

    # --------------------------------------------------------
    # 1. Parse intent
    # --------------------------------------------------------
    intent_data = intent(question)

    fiscal_year = intent_data.get("fiscal_year")
    fiscal_quarter = normalize_quarter(
        intent_data.get("fiscal_quarter")
    )

    raw_companies = intent_data.get("companies") or []
    if not isinstance(raw_companies, list):
        raw_companies = [raw_companies]

    if not raw_companies and intent_data.get("company"):
        raw_companies = [intent_data.get("company")]

    companies = []
    for value in raw_companies:
        normalized = normalize_company(value)
        if normalized and normalized not in companies:
            companies.append(normalized)

    # --------------------------------------------------------
    # 2. Respect explicit request fields if ever added later
    # --------------------------------------------------------
    # QueryRequest currently contains only question, so intent is the source
    # of company/year/quarter. Keeping this logic centralized avoids the old
    # scalar/list mismatch.
    intent_data["companies"] = companies
    intent_data["company"] = companies[0] if companies else None
    intent_data["fiscal_quarter"] = fiscal_quarter or None

    # --------------------------------------------------------
    # 3. Vector retrieval
    # --------------------------------------------------------
    evidence = []

    retrieval_companies = companies or [None]

    for company in retrieval_companies:
        try:
            company_evidence = vector_search(
                question=question,
                company=company,
                fiscal_year=(
                    int(fiscal_year)
                    if fiscal_year
                    else None
                ),
                fiscal_quarter=(
                    fiscal_quarter
                    if fiscal_quarter
                    else None
                ),
                top_k=TOP_K,
            )
            evidence.extend(company_evidence)
        except Exception as exc:
            print(
                f"Vector search error for {company}: {exc!r}"
            )

    # Remove duplicate chunks.
    unique_evidence = []
    seen = set()
    for item in evidence:
        chunk_id = item.get("chunk_id")
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        unique_evidence.append(item)
    evidence = unique_evidence

    # --------------------------------------------------------
    # 4. Deterministic calculation
    # --------------------------------------------------------
    calculations = calculate(intent_data)

    # --------------------------------------------------------
    # 5. Accurate citations
    # --------------------------------------------------------
    citations = build_citations(
        evidence,
        calculations,
        intent_data,
    )

    # --------------------------------------------------------
    # 6. Grounded answer
    # --------------------------------------------------------
    final_answer = answer(
        question,
        intent_data,
        evidence,
        calculations,
        citations,
    )

    # Do not expose the full content field in frontend citation cards.
    frontend_citations = []
    for citation in citations:
        frontend_citations.append({
            key: citation.get(key)
            for key in [
                "company",
                "fiscal_year",
                "fiscal_quarter",
                "pdf_page",
                "section",
                "table_name",
                "chunk_type",
                "chunk_id",
                "source_file",
                "relevance_score",
            ]
        })

    return {
        "answer": final_answer,
        "intent": intent_data,
        "calculations": calculations,
        "citations": frontend_citations,
        "retrieved_chunks": len(evidence),
    }
# LOCAL DEVELOPMENT

# ============================================================



if __name__ == "__main__":



    import uvicorn



    uvicorn.run(

        "app:app",

        host="0.0.0.0",

        port=8000,

        reload=True,

    )