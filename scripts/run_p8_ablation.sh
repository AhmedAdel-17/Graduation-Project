#!/bin/bash
# P8 Ablation Runner — per-ticker, per-window, fully isolated
# Compatible with Bash 3.2 (macOS default) — no associative arrays.
#
# Each test × window × ticker writes to its own output directory.
# Never skips based on old reports — isolation is by directory, not filename.
# Uses --resume within each output directory for crash recovery only.
#
# Usage:
#   bash scripts/run_p8_ablation.sh              # run all Phase 5a tests
#   bash scripts/run_p8_ablation.sh C-only        # run a single test (all windows)
#   bash scripts/run_p8_ablation.sh --phase5b     # run stacking tests
#   bash scripts/run_p8_ablation.sh --window W4   # run all tests, W4 only

set -a && source .env && set +a

# ─── Ticker sets ──────────────────────────────────────────────────────────
S2_TICKERS="COMI.CA ETEL.CA FWRY.CA SWDY.CA TMGH.CA ADIB.CA EAST.CA PHDC.CA EFIH.CA HRHO.CA"

INTERVAL=10
ANALYSTS="market,fundamentals,news,social"

# ─── Output base directory ───────────────────────────────────────────────
BASE_DIR="eval_results/p8_ablation"
mkdir -p "$BASE_DIR"

# ─── Window lookup (Bash 3.2 compatible) ─────────────────────────────────
get_window_start() {
    case "$1" in
        W3) echo "2024-01-01" ;;
        W4) echo "2024-07-15" ;;
        W5) echo "2024-10-01" ;;
        W6) echo "2024-07-15" ;;
        *)  echo "" ;;
    esac
}

get_window_end() {
    case "$1" in
        W3) echo "2024-07-14" ;;
        W4) echo "2024-09-30" ;;
        W5) echo "2024-12-29" ;;
        W6) echo "2024-12-29" ;;
        *)  echo "" ;;
    esac
}

# ─── Test config lookup (Bash 3.2 compatible) ────────────────────────────
get_test_config() {
    case "$1" in
        P7-baseline) echo "" ;;
        C-only)      echo "MARKET_BREADTH_ENABLED=1" ;;
        A2-only)     echo "ANTI_CHURN_ENABLED=1 ANTI_CHURN_VARIANT=A2" ;;
        B1-only)     echo "B1_WEAKEST_LINK=1" ;;
        B2-only)     echo "B2_NEWS_NEUTRAL=1" ;;
        B3-only)     echo "B3_CONF_FLOOR=1" ;;
        B4-only)     echo "B4_SIZING_FLOOR=1" ;;
        # Comparison variants
        A1-only)     echo "ANTI_CHURN_ENABLED=1 ANTI_CHURN_VARIANT=A1" ;;
        A3-only)     echo "ANTI_CHURN_ENABLED=1 ANTI_CHURN_VARIANT=A3" ;;
        A4-only)     echo "ANTI_CHURN_ENABLED=1 ANTI_CHURN_VARIANT=A4 MARKET_BREADTH_ENABLED=1" ;;
        # Phase 5b stacking
        C+A2)        echo "MARKET_BREADTH_ENABLED=1 ANTI_CHURN_ENABLED=1 ANTI_CHURN_VARIANT=A2" ;;
        *)           return 1 ;;
    esac
    return 0
}

ALL_PHASE5A="P7-baseline C-only A2-only B1-only B2-only B3-only B4-only"
ALL_PHASE5B="C+A2"
ALL_COMPARISON="A1-only A3-only A4-only"

# ─── Core runner ──────────────────────────────────────────────────────────
run_test_window() {
    local TEST_ID="$1"
    local WINDOW="$2"
    local ENV_OVERRIDES="$3"

    local START
    START=$(get_window_start "$WINDOW")
    local END
    END=$(get_window_end "$WINDOW")

    if [ -z "$START" ] || [ -z "$END" ]; then
        echo "[ERROR] Unknown window: $WINDOW"
        return 1
    fi

    local OUT_DIR="$BASE_DIR/${TEST_ID}/${WINDOW}"
    local RECORDS_DIR="$BASE_DIR/${TEST_ID}/${WINDOW}_records"
    local LOG_DIR="$BASE_DIR/${TEST_ID}/${WINDOW}_logs"

    mkdir -p "$OUT_DIR" "$LOG_DIR"

    echo ""
    echo "──────────────────────────────────────────────────────────────"
    echo "  TEST: $TEST_ID  |  WINDOW: $WINDOW ($START → $END)"
    echo "  Config: ${ENV_OVERRIDES:-'(P7 baseline — no overrides)'}"
    echo "  Output: $OUT_DIR"
    echo "──────────────────────────────────────────────────────────────"

    for TICKER in $S2_TICKERS; do
        SHORT=$(echo "$TICKER" | sed 's/.CA//')
        LOG="$LOG_DIR/ticker_${SHORT}.log"

        # Check if a report already exists in THIS output directory
        EXISTING=$(ls "$OUT_DIR"/report_${TICKER}_*.json 2>/dev/null | head -1)
        if [ -n "$EXISTING" ]; then
            N=$(python3 -c "import json; d=json.load(open('$EXISTING')); print(len(d['audit_log']))" 2>/dev/null)
            echo "[SKIP] $TICKER — report exists in output dir ($N entries)"
            continue
        fi

        echo "[RUN] $TEST_ID/$WINDOW/$TICKER → $LOG"

        env $ENV_OVERRIDES python3 scripts/backtester.py \
            --ticker "$TICKER" \
            --start "$START" \
            --end "$END" \
            --interval "$INTERVAL" \
            --analysts "$ANALYSTS" \
            --output-dir "$OUT_DIR" \
            --resume \
            --record \
            --records-dir "$RECORDS_DIR" \
            > "$LOG" 2>&1
        EXIT_CODE=$?

        if [ $EXIT_CODE -eq 0 ]; then
            echo "[DONE] $TICKER — exit $EXIT_CODE"
        else
            echo "[FAIL] $TICKER — exit $EXIT_CODE. Check $LOG"
        fi
    done

    echo ""
    echo "--- $TEST_ID / $WINDOW STATUS ---"
    local DONE=0 MISSING=0
    for TICKER in $S2_TICKERS; do
        REPORT=$(ls "$OUT_DIR"/report_${TICKER}_*.json 2>/dev/null | head -1)
        if [ -n "$REPORT" ]; then
            N=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d['audit_log']))" 2>/dev/null)
            echo "  + $TICKER: report ($N entries)"
            DONE=$((DONE + 1))
        else
            echo "  - $TICKER: NO REPORT"
            MISSING=$((MISSING + 1))
        fi
    done
    echo "  Total: $DONE done, $MISSING missing"
}

run_test_all_windows() {
    local TEST_ID="$1"
    local ENV_OVERRIDES="$2"
    local WINDOWS="${3:-W4 W5 W6}"

    for W in $WINDOWS; do
        run_test_window "$TEST_ID" "$W" "$ENV_OVERRIDES"
    done
}

# ─── Main ─────────────────────────────────────────────────────────────────

FILTER=""
WINDOW_FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --phase5b)
            FILTER="__phase5b__"; shift ;;
        --window)
            WINDOW_FILTER="$2"; shift 2 ;;
        *)
            FILTER="$1"; shift ;;
    esac
done

WINDOWS="${WINDOW_FILTER:-W4 W5 W6}"

if [ "$FILTER" = "__phase5b__" ]; then
    echo "=== Phase 5b: Stacking tests ==="
    for TEST_ID in $ALL_PHASE5B; do
        CFG=$(get_test_config "$TEST_ID")
        run_test_all_windows "$TEST_ID" "$CFG" "$WINDOWS"
    done
elif [ -n "$FILTER" ]; then
    CFG=$(get_test_config "$FILTER")
    if [ $? -eq 0 ]; then
        run_test_all_windows "$FILTER" "$CFG" "$WINDOWS"
    else
        echo "Unknown test ID: $FILTER"
        echo "Available: $ALL_PHASE5A $ALL_COMPARISON $ALL_PHASE5B"
        exit 1
    fi
else
    echo "=== Phase 5a: Individual ablation tests ==="
    for TEST_ID in $ALL_PHASE5A; do
        CFG=$(get_test_config "$TEST_ID")
        run_test_all_windows "$TEST_ID" "$CFG" "$WINDOWS"
    done
fi

echo ""
echo "=== ALL TESTS COMPLETE ==="
echo "Results tree:"
find "$BASE_DIR" -name "report_*.json" | sort | head -50
echo ""
echo "Run portfolio_sim.py per-window for analysis."
