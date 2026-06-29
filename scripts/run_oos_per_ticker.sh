#!/bin/bash
# Per-ticker OOS backtest runner — reliability mode
# Each ticker runs independently with its own log.
# Skips tickers that already have a complete OOS report.
# Uses --resume to pick up from partial checkpoints.

set -a && source .env && set +a

TICKERS="FWRY.CA SWDY.CA TMGH.CA ADIB.CA EAST.CA PHDC.CA EFIH.CA HRHO.CA"
START="2024-07-15"
END="2024-12-31"
INTERVAL=10
ANALYSTS="market,fundamentals,news,social"
RECORDS_DIR="./backtest_records_p7_10ticker_oos_20240715"
OUT_DIR="eval_results/thesis_10ticker_p7_oos_20240715"
TIMEOUT_MIN=90  # kill ticker if no log output for this many minutes

for TICKER in $TICKERS; do
    SHORT=$(echo "$TICKER" | sed 's/.CA//')
    LOG="$OUT_DIR/ticker_${SHORT}.log"

    # Check if OOS report already exists
    EXISTING=$(ls -t backtest_results/report_${TICKER}_*.json 2>/dev/null | head -1)
    if [ -n "$EXISTING" ]; then
        FIRST_DATE=$(python3 -c "import json; d=json.load(open('$EXISTING')); print(d['audit_log'][0]['date'])" 2>/dev/null)
        LAST_DATE=$(python3 -c "import json; d=json.load(open('$EXISTING')); print(d['audit_log'][-1]['date'])" 2>/dev/null)
        ENTRIES=$(python3 -c "import json; d=json.load(open('$EXISTING')); print(len(d['audit_log']))" 2>/dev/null)
        if [ "$FIRST_DATE" = "$START" ]; then
            echo "[SKIP] $TICKER — OOS report exists: $(basename $EXISTING) ($ENTRIES entries, $FIRST_DATE→$LAST_DATE)"
            continue
        fi
    fi

    echo "[RUN] $TICKER — logging to $LOG"
    python3 scripts/backtester.py \
        --ticker "$TICKER" \
        --start "$START" \
        --end "$END" \
        --interval "$INTERVAL" \
        --analysts "$ANALYSTS" \
        --resume \
        --record \
        --records-dir "$RECORDS_DIR" \
        > "$LOG" 2>&1
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        # Verify report
        REPORT=$(ls -t backtest_results/report_${TICKER}_*.json 2>/dev/null | head -1)
        if [ -n "$REPORT" ]; then
            FIRST=$(python3 -c "import json; d=json.load(open('$REPORT')); print(d['audit_log'][0]['date'])" 2>/dev/null)
            N=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d['audit_log']))" 2>/dev/null)
            echo "[DONE] $TICKER — $N entries starting $FIRST (exit $EXIT_CODE)"
        else
            echo "[WARN] $TICKER — exited 0 but no report found"
        fi
    else
        echo "[FAIL] $TICKER — exit code $EXIT_CODE. Check $LOG"
    fi
done

echo ""
echo "=== FINAL STATUS ==="
for TICKER in COMI.CA ETEL.CA FWRY.CA SWDY.CA TMGH.CA ADIB.CA EAST.CA PHDC.CA EFIH.CA HRHO.CA; do
    REPORT=$(ls -t backtest_results/report_${TICKER}_*.json 2>/dev/null | head -1)
    if [ -n "$REPORT" ]; then
        FIRST=$(python3 -c "import json; d=json.load(open('$REPORT')); print(d['audit_log'][0]['date'])" 2>/dev/null)
        N=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d['audit_log']))" 2>/dev/null)
        if [ "$FIRST" = "2024-07-15" ]; then
            echo "  ✓ $TICKER: OOS report ($N entries)"
        else
            echo "  · $TICKER: report exists but wrong window ($FIRST)"
        fi
    else
        echo "  ✗ $TICKER: NO REPORT"
    fi
done
