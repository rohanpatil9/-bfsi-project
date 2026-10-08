CREATE TABLE IF NOT EXISTS
`bfsi-project-510910.sec_analyst.financial_facts_final`
(
  company STRING,
  fiscal_year INT64,
  fiscal_quarter STRING,
  period_end DATE,

  metric STRING,
  source_label STRING,

  period STRING,
  duration_months INT64,

  value FLOAT64,
  unit STRING,

  pdf_page INT64,
  section STRING,
  table_name STRING,

  source_file STRING,

  validation_source STRING,

  created_at TIMESTAMP
);