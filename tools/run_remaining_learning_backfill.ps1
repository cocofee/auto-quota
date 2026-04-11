param(
    [int]$BatchSize = 10,
    [switch]$SkipVectorRebuild
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "output\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "learning_backfill_remaining_$stamp.log"
$statusPath = Join-Path $logDir "learning_backfill_remaining_$stamp.status.json"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logPath -Value $line
    Write-Host $line
}

function Update-Status {
    param([hashtable]$Status)
    $json = $Status | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath $statusPath -Value $json -Encoding UTF8
}

function Invoke-PythonSnippet {
    param([string]$Code)
    $output = @"
$Code
"@ | python -
    return ($output | Out-String).Trim()
}

function Invoke-PythonJsonCommand {
    param(
        [string[]]$Args,
        [string]$NoJsonMessage
    )
    $captured = New-Object System.Collections.Generic.List[string]
    & python @Args 2>&1 | ForEach-Object {
        $line = $_.ToString()
        $captured.Add($line)
        Add-Content -LiteralPath $logPath -Value $line
        Write-Host $line
    }
    $text = (($captured.ToArray()) -join [Environment]::NewLine).Trim()
    $jsonStart = $text.IndexOf("{")
    if ($jsonStart -lt 0) {
        throw $NoJsonMessage
    }
    return ($text.Substring($jsonStart) | ConvertFrom-Json)
}

function Invoke-BackfillRegion {
    param(
        [string]$Region,
        [string]$CheckpointFile,
        [int]$Limit
    )
    $args = @(
        "tools/backfill_learning_from_price_documents.py",
        "--region", $Region,
        "--sort-by-row-count",
        "--limit", $Limit.ToString(),
        "--checkpoint-file", $CheckpointFile,
        "--summary-only"
    )
    Write-Log ("RUN python " + ($args -join " "))
    return Invoke-PythonJsonCommand -Args $args -NoJsonMessage "No JSON payload returned for region $Region"
}

function Invoke-BackfillDocumentIds {
    param(
        [int[]]$DocumentIds,
        [string]$CheckpointFile
    )
    if (-not $DocumentIds -or $DocumentIds.Count -eq 0) {
        return $null
    }
    $args = @(
        "tools/backfill_learning_from_price_documents.py",
        "--document-ids"
    ) + ($DocumentIds | ForEach-Object { $_.ToString() }) + @(
        "--checkpoint-file", $CheckpointFile,
        "--summary-only"
    )
    Write-Log ("RUN python " + ($args -join " "))
    return Invoke-PythonJsonCommand -Args $args -NoJsonMessage "No JSON payload returned for explicit document IDs"
}

function Rebuild-Fts {
    Write-Log "Rebuilding experience FTS index"
    $count = Invoke-PythonSnippet @'
from src.experience_db import ExperienceDB
count = ExperienceDB().build_fts_index()
print(count)
'@
    Write-Log "FTS rebuilt, rows=$count"
}

function Rebuild-Vector {
    Write-Log "Rebuilding experience vector index"
    $result = Invoke-PythonSnippet @'
from src.experience_db import ExperienceDB
ExperienceDB().rebuild_vector_index()
print("vector_rebuilt")
'@
    Write-Log $result
}

function Get-EmptyRegionDocumentIds {
    $raw = Invoke-PythonSnippet @'
import sqlite3, json, config
conn = sqlite3.connect(config.get_price_reference_db_path())
cur = conn.cursor()
rows = cur.execute("""
SELECT id
FROM price_documents
WHERE document_type='priced_bill_file'
  AND COALESCE(region, '')=''
ORDER BY id
""").fetchall()
print(json.dumps([row[0] for row in rows], ensure_ascii=False))
conn.close()
'@
    if (-not $raw) {
        return @()
    }
    return @($raw | ConvertFrom-Json)
}

function Run-Lane {
    param(
        [string]$Name,
        [scriptblock]$BatchRunner
    )
    Write-Log "Lane start: $Name"
    $iteration = 0
    $laneWritten = 0
    while ($true) {
        $iteration += 1
        $result = & $BatchRunner
        if ($null -eq $result) {
            break
        }
        Update-Status @{
            lane = $Name
            iteration = $iteration
            last_result = $result
            log_path = $logPath
        }
        $laneWritten += [int]($result.written_learning_items)
        if ([int]$result.documents_selected -eq 0) {
            break
        }
        if (([int]$result.documents_processed -eq 0) -and ([int]$result.documents_failed -gt 0)) {
            Write-Log "Lane stalled: $Name"
            break
        }
    }
    Write-Log "Lane end: $Name written_learning_items=$laneWritten"
    if ($laneWritten -gt 0) {
        Rebuild-Fts
    }
}

$env:LOGURU_LEVEL = "ERROR"

Write-Log "Remaining learning backfill runner started"
Write-Log "Rule-extraction work stays blocked until import reaches completion"

Run-Lane -Name "ZJ_ALL_REMAINING" -BatchRunner {
    Invoke-BackfillRegion -Region "ZJ" -CheckpointFile "output/learning_backfill_zj_le300_checkpoint.json" -Limit $BatchSize
}

Run-Lane -Name "FJ_ALL_REMAINING" -BatchRunner {
    Invoke-BackfillRegion -Region "FJ" -CheckpointFile "output/learning_backfill_fj_le300_checkpoint.json" -Limit $BatchSize
}

Run-Lane -Name "JS_ALL" -BatchRunner {
    Invoke-BackfillRegion -Region "JS" -CheckpointFile "output/learning_backfill_js_checkpoint.json" -Limit $BatchSize
}

Run-Lane -Name "BJ_ALL" -BatchRunner {
    Invoke-BackfillRegion -Region "BJ" -CheckpointFile "output/learning_backfill_bj_checkpoint.json" -Limit $BatchSize
}

Run-Lane -Name "EMPTY_REGION" -BatchRunner {
    $ids = Get-EmptyRegionDocumentIds
    if (-not $ids -or $ids.Count -eq 0) {
        [pscustomobject]@{
            documents_selected = 0
            documents_processed = 0
            documents_failed = 0
            written_learning_items = 0
        }
    }
    else {
        Invoke-BackfillDocumentIds -DocumentIds @($ids[0]) -CheckpointFile "output/learning_backfill_empty_region_checkpoint.json"
    }
}

Rebuild-Fts
if (-not $SkipVectorRebuild) {
    Rebuild-Vector
}

Write-Log "Remaining learning backfill runner finished"
