# Save an Outlook draft for every prospect that has not been drafted yet,
# ready for Michael to read and click Send. This script never sends.
#
#   powershell -File scripts\outreach_to_outlook.ps1
#   powershell -File scripts\outreach_to_outlook.ps1 -Force   # redo one/all
#
# Who has already been written to is decided by docs/outreach/prospects.json
# (the "drafted" field), never by reading the Drafts folder. On 30 Sep 2026
# the folder-reading version came back empty while the Exchange store was
# syncing, wrote four second copies, and seven firms received the same email
# two or three times. A file on disk cannot go stale mid-sync.
#
# After writing, the script stamps "drafted" with today's date and saves the
# ledger, so a second run does nothing.
param([switch]$Force, [string]$Only)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root 'docs\outreach\emails'
$ledgerPath = Join-Path $root 'docs\outreach\prospects.json'
$fromAddress = 'support@ukpropertyinsight.co.uk'
$today = (Get-Date).ToString('yyyy-MM-dd')

$ledger = Get-Content -Path $ledgerPath -Raw -Encoding UTF8 | ConvertFrom-Json
$outlook = New-Object -ComObject Outlook.Application
$account = $outlook.Session.Accounts | Where-Object { $_.SmtpAddress -eq $fromAddress } | Select-Object -First 1
if (-not $account) { Write-Output "account $fromAddress not found, using the default sender" }

$files = @{}
foreach ($file in Get-ChildItem -Path $dir -Filter '*.md' | Where-Object { $_.Name -ne '00-INDEX.md' }) {
    $text = Get-Content -Path $file.FullName -Raw -Encoding UTF8
    $to = [regex]::Match($text, '(?m)^\*\*To:\*\*\s*(.+?)\s*$').Groups[1].Value
    $subject = [regex]::Match($text, '(?m)^\*\*Subject:\*\*\s*(.+?)\s*$').Groups[1].Value
    $parts = $text -split "(?m)^---\s*$"
    if ($parts.Count -lt 3 -or -not $to -or -not $subject) { Write-Output ("could not read " + $file.Name); continue }
    $files[$to.ToLower()] = @{ Subject = $subject; Body = $parts[1].Trim(); Name = $file.Name }
}

$made = 0; $already = 0; $missing = 0
foreach ($p in $ledger) {
    $key = $p.email.ToLower()
    if ($Only -and $key -ne $Only.ToLower()) { continue }
    if ($p.drafted -and -not $Force) { $already++; continue }
    if (-not $files.ContainsKey($key)) {
        $missing++
        Write-Output ("no email file for " + $p.email + "; run scripts/outreach_build_emails.py first")
        continue
    }
    $f = $files[$key]
    $mail = $outlook.CreateItem(0)
    $mail.To = $p.email
    $mail.Subject = $f.Subject
    $mail.Body = $f.Body
    if ($account) { $mail.SendUsingAccount = $account }
    $mail.Save()
    $p.drafted = $today
    $made++
    Write-Output ("draft saved: " + $p.email + "  |  " + $f.Name)
}

if ($made -gt 0) {
    $ledger | ConvertTo-Json -Depth 5 | Set-Content -Path $ledgerPath -Encoding UTF8
}
Write-Output ""
Write-Output ("drafts written: " + $made + ", already drafted before: " + $already + ", no file: " + $missing)
Write-Output "nothing has been sent; they are in Drafts, waiting for you"
