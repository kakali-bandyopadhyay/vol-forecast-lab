-- Text-length summary by document type.
SELECT
    doc_type,
    AVG(n_chars) AS avg_n_chars,
    MIN(n_chars) AS min_n_chars
FROM vfl_docs.vfl_documents_clean
GROUP BY doc_type
ORDER BY doc_type;
