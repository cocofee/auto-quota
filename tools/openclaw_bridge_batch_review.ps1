param(
    [string]$BaseUrl = "http://127.0.0.1:3210",
    [string]$ApiKey = $env:OPENCLAW_API_KEY,
    [Parameter(Mandatory = $true)][string]$TaskId,
    [ValidateSet("yellow", "red", "all")][string]$LightStatus = "yellow",
    [int]$Limit = 10,
    [string]$QuotaId = "",
    [string]$QuotaName = "",
    [string]$QuotaUnit = "",
    [string]$ReviewNote = "OpenClaw 批量建议",
    [int]$ReviewConfidence = 80,
    [string]$DecisionType = "override_within_candidates",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "OPENCLAW_API_KEY is required. Pass -ApiKey or set the environment variable."
}

if ([string]::IsNullOrWhiteSpace($QuotaId) -and [string]::IsNullOrWhiteSpace($QuotaName)) {
    throw "Batch draft requires -QuotaId or -QuotaName."
}

$Headers = @{
    "X-OpenClaw-Key" = $ApiKey
    "Content-Type" = "application/json; charset=utf-8"
}

function Invoke-BridgeJson {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $false)]$BodyObject
    )

    $Url = ($BaseUrl.TrimEnd('/')) + $Path
    $Json = $null
    if ($null -ne $BodyObject) {
        $Json = $BodyObject | ConvertTo-Json -Depth 12
    }

    if ($null -ne $Json) {
        return Invoke-RestMethod -Uri $Url -Headers $Headers -Method $Method -Body $Json
    }
    return Invoke-RestMethod -Uri $Url -Headers $Headers -Method $Method
}

Write-Host "Fetching review-items..." -ForegroundColor Cyan
$Response = Invoke-BridgeJson -Method Get -Path "/api/openclaw/tasks/$TaskId/review-items"
$Items = @($Response.items)

$Filtered = $Items | Where-Object {
    if ($_.openclaw_review_status -and $_.openclaw_review_status -ne "pending") {
        return $false
    }
    if ($LightStatus -eq "all") {
        return $true
    }
    return $_.light_status -eq $LightStatus
} | Select-Object -First $Limit

Write-Host "Candidates: $($Filtered.Count)" -ForegroundColor Green

if ($Filtered.Count -eq 0) {
    Write-Host "No matching pending items." -ForegroundColor Yellow
    exit 0
}

$Success = 0
$Failed = 0

foreach ($Item in $Filtered) {
    $Body = @{
        openclaw_suggested_quotas = @(@{
            quota_id = $QuotaId
            name = $QuotaName
            unit = $QuotaUnit
        })
        openclaw_review_note = $ReviewNote
        openclaw_review_confidence = $ReviewConfidence
        openclaw_decision_type = $DecisionType
    }

    $Path = "/api/openclaw/tasks/$TaskId/results/$($Item.id)/review-draft"
    Write-Host ""
    Write-Host "[$($Item.index)] $($Item.bill_name)" -ForegroundColor Cyan
    Write-Host "result_id=$($Item.id) light=$($Item.light_status)" -ForegroundColor DarkGray

    if ($WhatIf) {
        Write-Host "WHATIF: PUT $Path" -ForegroundColor Yellow
        continue
    }

    try {
        Invoke-BridgeJson -Method Put -Path $Path -BodyObject $Body | Out-Null
        $Success += 1
        Write-Host "review-draft saved" -ForegroundColor Green
    }
    catch {
        $Failed += 1
        Write-Host "save failed: $($Item.id)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Done success=$Success failed=$Failed" -ForegroundColor Green
