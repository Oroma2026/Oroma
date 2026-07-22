#!/bin/bash
# ORÓMA 48h Dauerlauf-Auswertung (Read-Only)

cd /opt/ai/oroma || exit 1
START_TS="$(date -d '2026-07-19 13:01:21 CEST' +%s)"
REPORT="/tmp/oroma_snake_48h_check_$(date +%Y%m%d_%H%M%S).log"

{
echo "ORÓMA 48h Dauerlauf"
systemctl --failed --no-pager
systemctl status oroma.service oroma-orchestrator.service oroma-db-writer.service --no-pager -l

sqlite3 -header -column data/oroma.db "
SELECT namespace,COUNT(*) rules,COALESCE(SUM(n),0) samples,
COALESCE(SUM(pos),0) positive,COALESCE(SUM(neg),0) negative,
ROUND(COALESCE(AVG(q),0),6) q_avg
FROM policy_rules
WHERE namespace LIKE 'game:snake%'
GROUP BY namespace;"

sqlite3 -header -column data/oroma.db "
SELECT status,COUNT(*) count
FROM gap_policy_promotion_queue
WHERE created_ts>=${START_TS}
AND namespace LIKE 'game:snake%'
GROUP BY status;"

sqlite3 data/oroma.db "PRAGMA quick_check;"
uptime
free -h
df -h /
} 2>&1 | tee "${REPORT}"

echo "Report: ${REPORT}"
