<#
.SYNOPSIS
  Create or update the World Genre monitoring: one log-based metric, one email
  notification channel, two alerting policies.

.DESCRIPTION
  Re-runnable. Every step checks for an existing resource first and updates it
  rather than creating a second copy, because an alerting policy is identified
  by a generated name and NOT by its displayName - running `policies create`
  twice gives you two identical policies that both page you.

  This is configuration-as-a-committed-script rather than Terraform, and that
  is a deliberate interim choice. The argument for waiting for Terraform (see
  the IaC gap in claude/backend-map.md) is real: this script drifts the moment
  someone edits a policy in the console, and it has no state file to notice.
  The argument against waiting won on the facts of this project - production
  ran undocumented for weeks precisely because infra lived only in a console,
  and "we will codify it properly later" is what produced that. These JSON
  bodies map field-for-field onto google_monitoring_alert_policy, so the
  migration is a rewrite of the wrapper, not of the thinking.

.EXAMPLE
  .\apply.ps1
  .\apply.ps1 -Project world-genre-natt -Email someone@example.com
#>
param(
    [string]$Project = "world-genre-natt",
    [string]$Email   = "hatsuneneng@gmail.com"
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$metric = "world_genre_pipeline_completed"

# --- 0. Preflight -----------------------------------------------------------
# `gcloud monitoring` lives in the alpha and beta component groups, which are
# NOT installed by default. Without this check the first beta command prints a
# "restarting command" notice, opens an installer in a separate window, and
# returns a non-zero exit with empty stdout - which reads downstream as "no
# such channel exists" rather than as "gcloud could not run". The script then
# tries to create the channel, fails the same way, and only the explicit throw
# further down stops it from creating policies that notify nobody.
#
# Fail here instead, with the fix in the message. A missing dependency should
# be reported as a missing dependency, not surface three steps later disguised
# as an empty result.
$installed = (gcloud components list --only-local-state --format="value(id)") -split "\r?\n"
$missing = @("alpha", "beta") | Where-Object { $installed -notcontains $_ }
if ($missing) {
    throw ("gcloud is missing the $($missing -join ' and ') component(s), which " +
           "the monitoring commands need. Install them, then re-run this script:`n`n" +
           "    gcloud components install $($missing -join ' ')")
}

# --- 1. The log-based metric ------------------------------------------------
# Config comes from a file rather than an inline --log-filter, because the
# filter contains both double quotes and a colon, and PowerShell strips quotes
# from native-command arguments. That trap has cost this project three separate
# debugging sessions; a file sidesteps it entirely.
$existing = gcloud logging metrics list --project=$Project --format="value(name)" |
    Where-Object { $_ -eq $metric }

if ($existing) {
    Write-Host "metric $metric exists - updating"
    gcloud logging metrics update $metric --config-from-file="$here\pipeline_completed.yaml" --project=$Project
} else {
    Write-Host "creating metric $metric"
    gcloud logging metrics create $metric --config-from-file="$here\pipeline_completed.yaml" --project=$Project
}

# --- 2. The notification channel --------------------------------------------
# Found by its email label rather than by display name, so renaming the channel
# in the console does not cause this script to create a duplicate.
$channel = gcloud beta monitoring channels list --project=$Project `
    --format="value(name,labels.email_address)" |
    Where-Object { $_ -match [regex]::Escape($Email) } |
    ForEach-Object { ($_ -split "\s+")[0] } |
    Select-Object -First 1

if (-not $channel) {
    Write-Host "creating email notification channel for $Email"
    $channel = gcloud beta monitoring channels create --project=$Project `
        --display-name="World Genre alerts" `
        --type=email `
        --channel-labels=email_address=$Email `
        --format="value(name)"
}
Write-Host "notification channel: $channel"

if (-not $channel) { throw "no notification channel - refusing to create policies that page nobody" }

# --- 3. The alerting policies -----------------------------------------------
# A policy with no notification channel is the worst possible outcome here: it
# looks configured, shows red in the console, and tells nobody. Hence the throw
# above rather than a warning.
$policies = gcloud alpha monitoring policies list --project=$Project --format="value(name,displayName)"

foreach ($file in @("alert_pipeline_failed.json", "alert_pipeline_stale.json")) {
    $body    = Get-Content "$here\$file" -Raw
    $display = ($body | ConvertFrom-Json).displayName
    $rendered = Join-Path ([System.IO.Path]::GetTempPath()) $file
    ($body -replace "__CHANNEL__", $channel) | Set-Content $rendered -Encoding utf8

    $match = $policies | Where-Object { $_ -match [regex]::Escape($display) } |
             ForEach-Object { ($_ -split "\s+")[0] } | Select-Object -First 1

    if ($match) {
        Write-Host "updating policy: $display"
        gcloud alpha monitoring policies update $match --policy-from-file=$rendered --project=$Project
    } else {
        Write-Host "creating policy: $display"
        gcloud alpha monitoring policies create --policy-from-file=$rendered --project=$Project
    }
}

Write-Host ""
Write-Host "Done. Verify with:"
Write-Host "  gcloud alpha monitoring policies list --project=$Project --format=`"table(displayName,enabled)`""
