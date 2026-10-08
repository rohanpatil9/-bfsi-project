CREATE SCHEMA IF NOT EXISTS `bfsi-project-510910.sec_analyst` OPTIONS(location="US");
CREATE TABLE IF NOT EXISTS `bfsi-project-510910.sec_analyst.chunks` (
  chunk_id STRING NOT NULL,
  company STRING,
  fiscal_year INT64,
  fiscal_quarter STRING,
  period_end DATE,
  pdf_page INT64,
  section STRING,
  table_name STRING,
  chunk_type STRING,
  source_file STRING,
  content STRING,
  embedding ARRAY<FLOAT64>
);
CREATE TABLE IF NOT EXISTS `bfsi-project-510910.sec_analyst.financial_facts` (
  company STRING,
  fiscal_year INT64,
  fiscal_quarter STRING,
  period_end DATE,
  metric STRING,
  source_label STRING,
  values ARRAY<FLOAT64>,
  pdf_page INT64,
  section STRING,
  table_index INT64,
  source_file STRING
);
-- Run after ingestion:
-- CREATE VECTOR INDEX chunks_embedding_idx
-- ON `bfsi-project-510910.sec_analyst.chunks`(embedding)
-- OPTIONS(index_type='IVF', distance_type='COSINE', ivf_options='{"num_lists":100}');
