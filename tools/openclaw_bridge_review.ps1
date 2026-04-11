param(
    [Parameter(Mandatory = $true)][ValidateSet("review-draft", "review-confirm")][string]$Action,
    [string]$BaseUrl = "http://127.0.0.1:3210",
    [string]$ApiKey = $env:OPENCLAW_API_KEY,
    [Parameter(Mandatory = $true)][string]$TaskId,
    [Parameter(Mandatory = $true)][string]$ResultId,
    [string]$Decision = "approve",
    [string]$QuotaId = "",
    [string]$QuotaName = "",
    [string]$QuotaUnit = "",
    [string]$QuotaSource = "search",
    [Nullable[double]]$ParamScore = $null,
    [Nullable[double]]$RerankScore = $null,
    [string]$ReviewNote = "",
    [Nullable[int]]$ReviewConfidence = $null,
    [ValidateSet("agree", "override_within_candidates", "retry_search_then_select", "candidate_pool_insufficient", "abstain")][string]$DecisionType = "agree",
    [string]$ErrorStage = "",
    [string]$ErrorType = "",
    [string]$RetryQuery = "",
    [string[]]$ReasonCodes = @()
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "OPENCLAW_API_KEY is required. Pass -ApiKey or set the environment variable."
}

$Headers = @{
    "X-OpenClaw-Key" = $ApiKey
    "Content-Type" = "application/json; charset=utf-8"
}

function Invoke-BridgeRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $false)]$BodyObject
    )

    $Url = ($BaseUrl.TrimEnd('/')) + $Path
    Write-Host ""
    Write-Host "==> $Method $Url" -ForegroundColor Cyan

    $Json = $null
    if ($null -ne $BodyObject) {
        $Json = $BodyObject | ConvertTo-Json -Depth 12
        Write-Host $Json -ForegroundColor DarkGray
    }

    try {
        if ($null -ne $Json) {
            $Response = Invoke-RestMethod -Uri $Url -Headers $Headers -Method $Method -Body $Json
        }
        else {
            $Response = Invoke-RestMethod -Uri $Url -Headers $Headers -Method $Method
        }
        $Response | ConvertTo-Json -Depth 12
    }
    catch {
        Write-Host "Request failed: $Url" -ForegroundColor Red
        if ($_.Exception.Response) {
            $ResponseStream = $_.Exception.Response.GetResponseStream()
            if ($ResponseStream) {
                $Reader = New-Object System.IO.StreamReader($ResponseStream, [System.Text.Encoding]::UTF8)
                $Body = $Reader.ReadToEnd()
                if ($Body) { Write-Host $Body -ForegroundColor DarkRed }
            }
            try {
                Write-Host ("StatusCode: " + [int]$_.Exception.Response.StatusCode.value__) -ForegroundColor DarkRed
            }
            catch {}
        }
        throw
    }
}

if ($Action -eq "review-draft") {
    if ([string]::IsNullOrWhiteSpace($QuotaId) -and [string]::IsNullOrWhiteSpace($QuotaName)) {
        throw "review-draft requires -QuotaId or -QuotaName."
    }

    $SuggestedQuota = @{
        quota_id = $QuotaId
        name = $QuotaName
        unit = $QuotaUnit
        source = $QuotaSource
    }
    if ($null -ne $ParamScore) {
        $SuggestedQuota.param_score = [double]$ParamScore
    }
    if ($null -ne $RerankScore) {
        $SuggestedQuota.rerank_score = [double]$RerankScore
    }
    $SuggestedQuotas = @($SuggestedQuota)

    $Body = @{
        openclaw_suggested_quotas = $SuggestedQuotas
        openclaw_review_note = $ReviewNote
        openclaw_decision_type = $DecisionType
        openclaw_review_payload = @{
            decision_type = $DecisionType
            note = $ReviewNote
            suggested_quotas = $SuggestedQuotas
        }
    }

    if ($null -ne $ReviewConfidence) {
        $Body.openclaw_review_confidence = [int]$ReviewConfidence
    }
    if (-not [string]::IsNullOrWhiteSpace($ErrorStage)) {
        $Body.openclaw_error_stage = $ErrorStage
    }
    if (-not [string]::IsNullOrWhiteSpace($ErrorType)) {
        $Body.openclaw_error_type = $ErrorType
    }
    if (-not [string]::IsNullOrWhiteSpace($RetryQuery)) {
        $Body.openclaw_retry_query = $RetryQuery
    }
    if ($ReasonCodes.Count -gt 0) {
        $Body.openclaw_reason_codes = $ReasonCodes
    }

    Invoke-BridgeRequest -Method Put -Path "/api/openclaw/tasks/$TaskId/results/$ResultId/review-draft" -BodyObject $Body
    exit 0
}

if ($Action -eq "review-confirm") {
    $Body = @{
        decision = $Decision
        review_note = $ReviewNote
    }

    Invoke-BridgeRequest -Method Post -Path "/api/openclaw/tasks/$TaskId/results/$ResultId/review-confirm" -BodyObject $Body
    exit 0
}
