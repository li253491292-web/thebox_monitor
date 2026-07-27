param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('morning', 'evening')]
    [string]$Period
)

$ErrorActionPreference = 'Continue'
$project = 'C:\Users\lihongru02\lobsterai\project\xiaoheihe_opinion_monitor'
$python = 'C:\Users\lihongru02\AppData\Local\Programs\Python\Python314\python.exe'
$log = Join-Path $project 'logs\summary_scheduler.log'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:POPO_BOT_APP_KEY = [Environment]::GetEnvironmentVariable('POPO_BOT_APP_KEY', 'User')
$env:POPO_BOT_APP_SECRET = [Environment]::GetEnvironmentVariable('POPO_BOT_APP_SECRET', 'User')

Push-Location $project
try {
    [System.IO.File]::AppendAllText($log, "[$(Get-Date)] START summary period=$Period`r`n", $utf8)
    & $python 'crawler\popo_summary_notifier.py' '--period' $Period *>&1 | Tee-Object -FilePath $log -Append
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $exitCode
