SELECT
  company,
  fiscal_year,
  fiscal_quarter,
  metric,
  source_label,
  period_values,
  pdf_page,
  section,
  table_index,
  source_file
FROM fsi-project-510910.sec_analyst.financial_facts_v2
ORDER BY company, fiscal_year, fiscal_quarter, metric, pdf_page;
