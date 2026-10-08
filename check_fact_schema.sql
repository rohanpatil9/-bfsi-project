SELECT
  column_name,
  data_type,
  is_nullable
FROM `bfsi-project-510910.sec_analyst.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'financial_facts'
ORDER BY ordinal_position;