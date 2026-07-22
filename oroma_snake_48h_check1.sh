#!/bin/bash
# ORÓMA 48h Dauerlauf-Auswertung (Read-Only)

cd /opt/ai/oroma || exit 1

START_TS="$(date -d '2026-07-19 13:01:21 CEST' +%s)"
REPORT="/tmp/oroma_snake_48h_check_$(date +%Y%m%d_%H%M%S).log"

{
    echo "============================================================"
    echo "ORÓMA SNAKE 48H DAUERTEST"
    echo "============================================================"
    echo "Auswertung:       $(date --iso-8601=seconds)"
    echo "Testbeginn:       2026-07-19 13:01:21 CEST"
    echo "Start-Epoch:      ${START_TS}"
    echo "Hostname:         $(hostname)"
    echo "Kernel:           $(uname -a)"
    echo

    echo "============================================================"
    echo "1. SERVICES"
    echo "============================================================"
    systemctl --failed --no-pager
    echo
    systemctl status \
        oroma.service \
        oroma-orchestrator.service \
        oroma-db-writer.service \
        --no-pager -l
    echo

    echo "============================================================"
    echo "2. SERVICE-RESTARTS UND FEHLER SEIT TESTBEGINN"
    echo "============================================================"
    journalctl \
        -u oroma.service \
        -u oroma-orchestrator.service \
        -u oroma-db-writer.service \
        --since '2026-07-19 13:01:21' \
        --no-pager |
        egrep -i \
        'started|stopped|restart|failed|failure|error|exception|traceback|timeout|killed|oom|segfault|database is locked|readonly|dbwriter_disabled' \
        || true
    echo

    echo "============================================================"
    echo "3. SNAKE-POLICY AKTUELL"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            namespace,
            COUNT(*) AS rules,
            COALESCE(SUM(n), 0) AS samples,
            COALESCE(SUM(pos), 0) AS positive,
            COALESCE(SUM(neg), 0) AS negative,
            COALESCE(SUM(draw), 0) AS draws,
            ROUND(COALESCE(AVG(q), 0), 6) AS q_avg,
            ROUND(COALESCE(MIN(q), 0), 6) AS q_min,
            ROUND(COALESCE(MAX(q), 0), 6) AS q_max,
            MAX(last_ts) AS newest_rule_ts,
            datetime(MAX(last_ts), 'unixepoch', 'localtime') AS newest_rule_local
        FROM policy_rules
        WHERE namespace LIKE 'game:snake%'
        GROUP BY namespace
        ORDER BY namespace;
    "
    echo

    echo "============================================================"
    echo "4. PROMOTIONEN SEIT TESTBEGINN"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            status,
            COUNT(*) AS count,
            MIN(id) AS first_id,
            MAX(id) AS last_id,
            datetime(MIN(created_ts), 'unixepoch', 'localtime') AS first_local,
            datetime(MAX(created_ts), 'unixepoch', 'localtime') AS last_local
        FROM gap_policy_promotion_queue
        WHERE created_ts >= ${START_TS}
          AND namespace LIKE 'game:snake%'
        GROUP BY status
        ORDER BY status;
    "
    echo

    echo "============================================================"
    echo "5. TARGETED ACQUISITION LIFECYCLE SEIT TESTBEGINN"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            status,
            COUNT(*) AS count,
            SUM(CASE WHEN direct_outcome_acquired = 1 THEN 1 ELSE 0 END)
                AS direct_outcomes,
            COALESCE(SUM(attempts_executed), 0) AS attempts_executed,
            MIN(promotion_id) AS first_promotion,
            MAX(promotion_id) AS last_promotion,
            datetime(MIN(created_ts), 'unixepoch', 'localtime') AS first_local,
            datetime(MAX(updated_ts), 'unixepoch', 'localtime') AS last_local
        FROM gap_targeted_acquisition_lifecycle
        WHERE created_ts >= ${START_TS}
          AND namespace LIKE 'game:snake%'
        GROUP BY status
        ORDER BY status;
    "
    echo

    echo "============================================================"
    echo "6. OUTCOME QUEUE SEIT TESTBEGINN"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            status,
            outcome,
            policy_write_allowed,
            COUNT(*) AS count,
            MIN(id) AS first_id,
            MAX(id) AS last_id,
            datetime(MIN(created_ts), 'unixepoch', 'localtime') AS first_local,
            datetime(MAX(updated_ts), 'unixepoch', 'localtime') AS last_local
        FROM gap_evidence_outcome_queue
        WHERE created_ts >= ${START_TS}
          AND namespace LIKE 'game:snake%'
        GROUP BY status, outcome, policy_write_allowed
        ORDER BY status, outcome, policy_write_allowed;
    "
    echo

    echo "============================================================"
    echo "7. POLICY MINI-WRITE LEDGER SEIT TESTBEGINN"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            status,
            policy_written,
            COALESCE(blocked_reason, '-') AS blocked_reason,
            COUNT(*) AS count,
            SUM(n_inc) AS n_inc,
            SUM(pos_inc) AS pos_inc,
            SUM(neg_inc) AS neg_inc,
            SUM(draw_inc) AS draw_inc,
            MIN(id) AS first_ledger_id,
            MAX(id) AS last_ledger_id
        FROM gap_policy_mini_write_ledger
        WHERE created_ts >= ${START_TS}
          AND namespace LIKE 'game:snake%'
        GROUP BY status, policy_written, COALESCE(blocked_reason, '-')
        ORDER BY policy_written DESC, status, blocked_reason;
    "
    echo

    echo "============================================================"
    echo "8. PIPELINE-GESAMTSUMMEN SEIT TESTBEGINN"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            (
                SELECT COUNT(*)
                FROM gap_policy_promotion_queue
                WHERE created_ts >= ${START_TS}
                  AND namespace LIKE 'game:snake%'
            ) AS promotions,

            (
                SELECT COUNT(*)
                FROM gap_targeted_acquisition_lifecycle
                WHERE created_ts >= ${START_TS}
                  AND namespace LIKE 'game:snake%'
            ) AS acquisitions,

            (
                SELECT COUNT(*)
                FROM gap_targeted_acquisition_lifecycle
                WHERE created_ts >= ${START_TS}
                  AND namespace LIKE 'game:snake%'
                  AND direct_outcome_acquired = 1
            ) AS acquisitions_with_outcome,

            (
                SELECT COUNT(*)
                FROM gap_evidence_outcome_queue
                WHERE created_ts >= ${START_TS}
                  AND namespace LIKE 'game:snake%'
            ) AS outcome_rows,

            (
                SELECT COUNT(*)
                FROM gap_policy_mini_write_ledger
                WHERE created_ts >= ${START_TS}
                  AND namespace LIKE 'game:snake%'
                  AND policy_written = 1
            ) AS policy_writes,

            (
                SELECT COUNT(*)
                FROM gap_policy_mini_write_ledger
                WHERE created_ts >= ${START_TS}
                  AND namespace LIKE 'game:snake%'
                  AND status = 'blocked'
            ) AS blocked_writes;
    "
    echo

    echo "============================================================"
    echo "9. LETZTE 30 PIPELINE-ERGEBNISSE"
    echo "============================================================"
    sqlite3 -header -column data/oroma.db "
        SELECT
            id AS ledger_id,
            promotion_id,
            namespace,
            state_hash,
            action,
            outcome,
            status,
            policy_written,
            COALESCE(blocked_reason, '-') AS blocked_reason,
            n_inc,
            pos_inc,
            neg_inc,
            draw_inc,
            datetime(created_ts, 'unixepoch', 'localtime') AS created_local
        FROM gap_policy_mini_write_ledger
        WHERE created_ts >= ${START_TS}
          AND namespace LIKE 'game:snake%'
        ORDER BY id DESC
        LIMIT 30;
    "
    echo

    echo "============================================================"
    echo "10. DATENBANK-INTEGRITÄT"
    echo "============================================================"
    sqlite3 data/oroma.db "PRAGMA quick_check;"
    echo

    echo "============================================================"
    echo "11. SYSTEMRESSOURCEN"
    echo "============================================================"
    uptime
    free -h
    df -h /
    echo

    echo "============================================================"
    echo "ENDE DER 48H-AUSWERTUNG"
    echo "============================================================"
} 2>&1 | tee "${REPORT}"

echo
echo "Fertiger Bericht: ${REPORT}"