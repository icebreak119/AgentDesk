param(
    [int]$EnterprisePort = 8770,
    [int]$WecomPort = 8771,
    [int]$DemoPort = 8780
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $Root "tmp\demo_services"
$StatePath = Join-Path $StateDir "services.json"
$EvidencePath = Join-Path $StateDir ("enterprise_business_evidence_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd_HHmmss_fff"))
New-Item -ItemType Directory -Force $StateDir | Out-Null

foreach ($port in @($EnterprisePort, $WecomPort, $DemoPort)) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        throw "端口 $port 已被占用，请先运行 scripts\stop_demo.ps1 或更换端口。"
    }
}

function Start-AgentDeskService {
    param(
        [string]$Name,
        [string[]]$Arguments
    )
    $stdout = Join-Path $StateDir "$Name.stdout.log"
    $stderr = Join-Path $StateDir "$Name.stderr.log"
    $process = Start-Process -FilePath "python" -ArgumentList $Arguments -WorkingDirectory $Root `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "$Name 启动失败，请查看 $stderr"
    }
    return $process.Id
}

$enterprisePid = Start-AgentDeskService "enterprise" @("-m", "enterprise_simulator.server", "--host", "127.0.0.1", "--port", "$EnterprisePort", "--evidence-log", $EvidencePath)
$wecomPid = Start-AgentDeskService "wecom" @("-m", "runtime.wecom.server", "--host", "127.0.0.1", "--port", "$WecomPort")
$demoPid = Start-AgentDeskService "demo" @("-m", "demo_runtime.server", "--repo-root", $Root, "--host", "127.0.0.1", "--port", "$DemoPort", "--enterprise-url", "http://127.0.0.1:$EnterprisePort", "--wecom-url", "http://127.0.0.1:$WecomPort", "--gateway-url", "http://127.0.0.1:$DemoPort")

@{
    root = $Root
    pids = @{
        enterprise = $enterprisePid
        wecom = $wecomPid
        demo = $demoPid
    }
    urls = @{
        enterprise = "http://127.0.0.1:$EnterprisePort"
        wecom = "http://127.0.0.1:$WecomPort"
        demo = "http://127.0.0.1:$DemoPort/"
    }
} | ConvertTo-Json -Depth 4 | Set-Content -Path $StatePath -Encoding UTF8

Write-Host "AgentDesk 演示服务已启动"
Write-Host "实时演示: http://127.0.0.1:$DemoPort/"
Write-Host "企业动作: http://127.0.0.1:$EnterprisePort/enterprise/ping"
Write-Host "企微 Webhook: http://127.0.0.1:$WecomPort/webhooks/wecom/ping"
Write-Host "停止服务: .\scripts\stop_demo.ps1"
