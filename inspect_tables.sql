SELECT
  company,
  fiscal_year,
  fiscal_quarter,
  pdf_page,
  section,
  table_name,
  chunk_type,
  LEFT(content, 3000) AS content
FROM `bfsi-project-510910.sec_analyst.chunks`
WHERE chunk_type = 'table'
ORDER BY
  company,
  fiscal_year,
  fiscal_quarter,
  pdf_page
LIMIT 20;