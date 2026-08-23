# ---------------------------------------------------------------------------
#  Create .env from .env.example with freshly generated secrets (Windows).
#
#  Secrets are never committed, so a fresh clone has no .env and the stack
#  refuses to start. Run this once, then `docker compose up -d --build`.
#
#  Existing .env files are left alone - this never overwrites your secrets.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')

if (Test-Path .env) {
    Write-Host '.env already exists - leaving it untouched.'
    exit 0
}
if (-not (Test-Path .env.example)) {
    Write-Error '.env.example is missing.'
}

function New-Secret([int]$Length) {
    $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

# SMTP_PASSWORD is deliberately left as CHANGE-ME: only you can supply a
# Gmail app password.
$lines = Get-Content .env.example | ForEach-Object {
    switch -Regex ($_) {
        '^MONGO_ROOT_PASSWORD=' { "MONGO_ROOT_PASSWORD=$(New-Secret 32)"; break }
        '^MONGO_APP_PASSWORD='  { "MONGO_APP_PASSWORD=$(New-Secret 32)";  break }
        '^SESSION_SECRET='      { "SESSION_SECRET=$(New-Secret 64)";      break }
        default                 { $_ }
    }
}
Set-Content -Path .env -Value $lines -Encoding utf8

Write-Host '.env created with generated secrets.'
Write-Host ''
Write-Host 'Still to fill in by hand before mail features work:'
Write-Host '  SMTP_PASSWORD   - Gmail app password (daily report + password resets)'
Write-Host '  PUBLIC_ORIGIN   - currently https://surveys.example.com;'
Write-Host '                    set to http://localhost:8080 for a local run'
Write-Host ''
Write-Host 'Then:  docker compose up -d --build'
