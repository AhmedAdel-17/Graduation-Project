<#
.SYNOPSIS
  Scheduled-task wrapper for the EGX forward paper-trading harness.

.DESCRIPTION
  Runs the look-ahead-free paper-trading harness on a cadence so forward evidence
  (LLM vs momentum vs factor, net of cost) accumulates without manual nudging.

  - Default run: `score` (close matured decisions) + `report` (print scorecard).
    These need only yfinance/network — no LLM keys, cheap and safe to run daily.
  - With -Record: also runs `record`, which executes the multi-agent graph live
    (needs valid LLM API keys in .env + network; costs API credits and takes a few
    minutes PER ticker). Intended for a weekly cadence.

  All output is appended to logs/paper_trade_cron.log.

.PARAMETER Record
  Also record today's decisions (LLM + baselines + factor strategy).

.PARAMETER Tickers
  Comma-separated EGX tickers for -Record. Default: a liquid representative set.
  A wider universe gives the cross-sectional factor strategy more to rank.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\paper_trade_cron.ps1
  powershell -ExecutionPolicy Bypass -File scripts\paper_trade_cron.ps1 -Record
#>
param(
    [switch]$Record,
    [string]$Tickers = "COMI.CA,ETEL.CA,TMGH.CA,ABUK.CA,HRHO.CA,SWDY.CA,FWRY.CA,JUFO.CA,EAST.CA,ESRS.CA"
)

$ErrorActionPreference = "Continue"
# Repo root = parent of this script's directory.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Route the LLM to DeepSeek-direct: NVIDIA's free tier returns 429 within a single
# graph's call burst, so the LLM arm can't complete there. trading_graph picks
# DEEPSEEK_API_KEY (from .env) automatically when backend_url lacks "nvidia".
# Comment these out to revert to the .env default (NVIDIA).
$env:LLM_BACKEND_URL = "https://api.deepseek.com"
$env:DEEP_THINK_LLM  = "deepseek-chat"
$env:QUICK_THINK_LLM = "deepseek-chat"

$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$Log = Join-Path $LogDir "paper_trade_cron.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $Log -Value $line
    Write-Output $line
}

Write-Log "=== paper_trade_cron start (Record=$Record) ==="

if ($Record) {
    Write-Log "record: $Tickers"
    python scripts/paper_trade.py record --tickers $Tickers --baselines --factor-rank 2>&1 |
        ForEach-Object { Add-Content -Path $Log -Value $_ }
}

Write-Log "score"
python scripts/paper_trade.py score 2>&1 | ForEach-Object { Add-Content -Path $Log -Value $_ }

Write-Log "report"
python scripts/paper_trade.py report --benchmark-return 0.0 2>&1 | ForEach-Object { Add-Content -Path $Log -Value $_ }

Write-Log "=== paper_trade_cron done ==="
