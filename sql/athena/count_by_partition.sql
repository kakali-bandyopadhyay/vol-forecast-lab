-- Documents per projected partition (year/doc_type) in the clean lake.
SELECT
    year,
    doc_type,
    COUNT(*) AS n_docs
FROM vfl_docs.vfl_documents_clean
GROUP BY year, doc_type
ORDER BY year, doc_type;
