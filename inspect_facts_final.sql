SELECT
  company,
  fiscal_year,
  fiscal_quarter,
  metric,
  source_label,
  period,
  duration_months,
  value,
  unit,
  pdf_page,
  section,
  source_file
FROM `bfsi-project-510910.sec_analyst.financial_facts_final`
ORDER BY company, fiscal_year, fiscal_quarter, metric, pdf_page;
