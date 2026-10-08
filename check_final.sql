SELECT
  COUNT(*) AS total_rows,
  COUNTIF(duration_months = 3) AS quarterly_rows
FROM `bfsi-project-510910.sec_analyst.financial_facts_final`
