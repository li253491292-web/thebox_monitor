$ErrorActionPreference = 'Continue'
$project = 'C:\Users\lihongru02\lobsterai\project\xiaoheihe_opinion_monitor'
$python = 'C:\Users\lihongru02\AppData\Local\Programs\Python\Python314\python.exe'
$log = Join-Path $project 'logs\task_scheduler.log'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:POPO_BOT_APP_KEY = [Environment]::GetEnvironmentVariable('POPO_BOT_APP_KEY', 'User')
$env:POPO_BOT_APP_SECRET = [Environment]::GetEnvironmentVariable('POPO_BOT_APP_SECRET', 'User')

function Write-TaskLog([object]$value) {
    $text = ($value | Out-String).TrimEnd()
    if ($text) {
        [System.IO.File]::AppendAllText($log, "$text`r`n", $utf8)
    }
}

$started = Get-Date
Write-TaskLog "[$started] START scheduled crawl"
Push-Location $project
try {
    & $python 'crawler\scheduler.py' '--once' *>&1 | ForEach-Object {
        Write-Output $_
        Write-TaskLog $_
    }
    $exitCode = $LASTEXITCODE
} catch {
    Write-TaskLog $_
    $exitCode = 1
} finally {
    Pop-Location
}
$finished = Get-Date
if ($exitCode -eq 0) {
    $message = "Xiaoheihe opinion update completed`n$finished`nHTML report updated."
} else {
    $message = "Xiaoheihe opinion update FAILED`n$finished`nExit code: $exitCode`nSee logs\task_scheduler.log."
}
& "$env:WINDIR\System32\msg.exe" $env:USERNAME /TIME:30 $message | Out-Null
Write-TaskLog "[$finished] END exit_code=$exitCode"
exit $exitCode
