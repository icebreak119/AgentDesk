$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StatePath = Join-Path $Root "tmp\demo_services\services.json"

if (-not (Test-Path $StatePath)) {
    Write-Host "没有找到运行状态文件，演示服务可能已经停止。"
    exit 0
}

$state = Get-Content $StatePath -Raw | ConvertFrom-Json
foreach ($processId in @($state.pids.enterprise, $state.pids.wecom, $state.pids.demo)) {
    if ($processId) {
        $process = Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id ([int]$processId) -Force
            Write-Host "已停止进程 $processId"
        }
    }
}
Remove-Item -LiteralPath $StatePath -Force
Write-Host "AgentDesk 演示服务已停止。"
