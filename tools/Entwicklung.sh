sqlite3 /opt/ai/oroma/data/oroma.db '
SELECT kind, AVG(value)
FROM metrics
WHERE kind IN ("snap_quality","mutation_trigger","dream_merge")
GROUP BY kind;
'