-- Partition-projection check: 2020 statements must resolve to 7 rows in this corpus.
SELECT
    doc_id,
    event_date
FROM vfl_docs.vfl_documents_clean
WHERE year = '2020'
  AND doc_type = 'statement'
ORDER BY event_date;
