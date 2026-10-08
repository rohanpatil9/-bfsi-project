CREATE TABLE IF NOT EXISTS
`bfsi-project-510910.sec_analyst.financial_facts_v2`
(
  company STRING,
  fiscal_year INT64,
  fiscal_quarter STRING,
  period_end DATE,
  metric STRING,
  source_label STRING,
  unit STRING,
  period_values ARRAY<STRUCT<
    period STRING,
    value FLOAT64,
    duration_months INT64
  >>,
  pdf_page INT64,
  section STRING,
  table_index INT64,
  source_file STRING,
  created_at TIMESTAMP
);
