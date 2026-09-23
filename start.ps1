<#
.SYNOPSIS
    HyPRA 一键启动（桌面端形态：后端 + 程序控制台）

.DESCRIPTION
    桌面端启动后先打开「程序控制台」——模型库、构图调试、插件、技能、预设、
    会话、创作工坊都在里面；桌宠窗与 Web 端由用户在控制台的「模式启动」里选定。

    所以这里默认只拉两样：后端（FastAPI）与桌面端（Electron）。
    每端各占一个 cmd 窗口，日志分开可见，**关掉那个窗口即停止该端**。

.PARAMETER Target
    desktop   后端 + 桌面端（程序控制台）        ← 默认
    web       后端 + Web 前端（浏览器里跑）
    all       后端 + Web 前端 + 桌面端
    backend   只启动后端
    frontend  只启动 Web 前端
    console   只启动桌面端（后端已在别处跑着）
    docker    评审模式：docker compose 起后端（含 Qdrant），再起 Web 前端
    check     只做环境自检，不启动任何服务

.PARAMETER Release
    桌面端用**构建产物**启动（先 npm run build，再 npm start），演示 / 评审用；
    不加则用 npm run dev（改代码即时生效）。

.EXAMPLE
    start.bat
    start.bat web
    start.bat desktop -Release
    start.bat check
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'desktop',

    [switch]$Release
)

# ---- 路径与端口 ----
$Root         = $PSScriptRoot
$PythonExe    = Join-Path $Root '.venv\Scripts\python.exe'
$BackendDir   = Join-Path $Root 'backend'
$FrontendDir  = Join-Path $Root 'frontend'
$DesktopDir   = Join-Path $Root 'desktop'
$EnvFile      = Join-Path $BackendDir '.env'
$EnvExample   = Join-Path $BackendDir '.env.example'
$ComposeFile  = Join-Path $Root 'docker-compose.yml'
$BackendPort  = 8000
$FrontendPort = 3000

# ============================================================
#  辅助函数
# ============================================================

function Show-Usage {
    Write-Host ''
    Write-Host '  HyPRA 一键启动'
    Write-Host ''
    Write-Host '  用法：start.bat [目标] [-Release]'
    Write-Host ''
    Write-Host '    （空）/ desktop  后端 + 桌面端（程序控制台）   默认'
    Write-Host '    web              后端 + Web 前端（浏览器）'
    Write-Host '    all              后端 + Web 前端 + 桌面端'
    Write-Host '    backend          只启动后端'
    Write-Host '    frontend         只启动 Web 前端'
    Write-Host '    console          只启动桌面端（后端已在别处跑）'
    Write-Host '    docker           评审模式：docker compose 起后端，再起 Web 前端'
    Write-Host '    check            只做环境自检'
    Write-Host ''
    Write-Host '  -Release           桌面端用构建产物启动（先 build 再 start），演示用'
    Write-Host ''
    Write-Host '  每端各占一个窗口，日志分开可见；关掉窗口即停止该端。'
    Write-Host '  桌宠窗与 Web 端不会自动打开——都在「程序控制台 → 模式启动」里选。'
    Write-Host ''
}

function Wait-CloseWindow {
    param([string]$Message = '按回车键关闭本窗口')
    try { Read-Host $Message | Out-Null } catch { }
}

# 端口是否处于监听状态
function Test-PortInUse {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$conn
}

# 轮询后端 /health：就绪返回 $true，超时返回 $false
function Wait-BackendReady {
    param([int]$Port, [int]$TimeoutSeconds = 40)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

# 新开一个 cmd 窗口跑某端（关窗即停该端）
function Start-EndWindow {
    param([string]$Title, [string]$WorkDir, [string]$Command)
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', "title $Title & $Command" -WorkingDirectory $WorkDir | Out-Null
}

# 环境自检：缺什么报什么；返回 $true 表示可以继续启动
function Test-Environment {
    param(
        [bool]$NeedBackend = $false,
        [bool]$NeedFrontend = $false,
        [bool]$NeedDesktop = $false
    )
    $ok = $true

    if ($NeedBackend) {
        if (-not (Test-Path $PythonExe)) {
            Write-Host '  [X] 缺少 Python 虚拟环境：.venv\Scripts\python.exe'
            Write-Host '      创建：python -m venv .venv'
            Write-Host '      装依赖：.venv\Scripts\python.exe -m pip install -e backend'
            $ok = $false
        }
        if (-not (Test-Path $EnvFile)) {
            Write-Host '  [!] backend\.env 不存在（模型 Key / Qdrant 等配置读不到）'
            if (Test-Path $EnvExample) {
                Write-Host '      复制一份起步：copy backend\.env.example backend\.env'
            }
        }
    }

    if ($NeedFrontend -and -not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
        Write-Host '  [X] frontend\node_modules 不存在（先 cd frontend 再 npm install）'
        $ok = $false
    }

    if ($NeedDesktop -and -not (Test-Path (Join-Path $DesktopDir 'node_modules'))) {
        Write-Host '  [X] desktop\node_modules 不存在（先 cd desktop 再 npm install）'
        $ok = $false
    }

    return $ok
}

# ============================================================
#  目标分派
# ============================================================

$plan = [ordered]@{ Backend = $false; Frontend = $false; Desktop = $false; Docker = $false; Mode = 'run' }

switch ($Target.ToLowerInvariant()) {
    ''         { $plan.Backend = $true; $plan.Desktop = $true }
    'desktop'  { $plan.Backend = $true; $plan.Desktop = $true }
    'web'      { $plan.Backend = $true; $plan.Frontend = $true }
    'all'      { $plan.Backend = $true; $plan.Frontend = $true; $plan.Desktop = $true }
    'backend'  { $plan.Backend = $true }
    'frontend' { $plan.Frontend = $true }
    'console'  { $plan.Desktop = $true }
    'docker'   { $plan.Docker = $true; $plan.Frontend = $true }
    'check'    { $plan.Mode = 'check' }
    'help'     { Show-Usage; exit 0 }
    '-h'       { Show-Usage; exit 0 }
    '--help'   { Show-Usage; exit 0 }
    default {
        Write-Host ''
        Write-Host "  [X] 未知目标：$Target"
        Show-Usage
        exit 1
    }
}

# ------------------------------------------------------------
#  check：只自检，不启动
# ------------------------------------------------------------
if ($plan.Mode -eq 'check') {
    Write-Host ''
    Write-Host '  HyPRA 环境自检'
    Write-Host '  ------------------------------------------------------------'

    $envOk = Test-Environment -NeedBackend $true -NeedFrontend $true -NeedDesktop $true

    $backendPortText = if (Test-PortInUse $BackendPort) { '已被占用（后端可能已在跑）' } else { '空闲' }
    $frontendPortText = if (Test-PortInUse $FrontendPort) { '已被占用（前端可能已在跑）' } else { '空闲' }

    Write-Host "  [i] 后端端口 $BackendPort ：$backendPortText"
    Write-Host "  [i] Web 前端端口 $FrontendPort ：$frontendPortText"

    if (Test-Path $ComposeFile) {
        Write-Host '  [OK] docker-compose.yml 存在（评审模式 docker 可用）'
    } else {
        Write-Host '  [!] 缺少 docker-compose.yml'
    }

    Write-Host ''
    if ($envOk) {
        Write-Host '  [OK] 环境完整：直接 start.bat 即可启动（默认后端 + 桌面端）'
    } else {
        Write-Host '  [!] 环境不完整，请按上面的提示补齐'
    }
    Write-Host ''
    Wait-CloseWindow
    exit 0
}

# ------------------------------------------------------------
#  run：按 plan 启动
# ------------------------------------------------------------
Write-Host ''
Write-Host '============================================================'
Write-Host '  HyPRA 启动中'
if ($Release) {
    Write-Host "  目标：$Target（桌面端用构建产物）"
} else {
    Write-Host "  目标：$Target"
}
Write-Host '============================================================'
Write-Host ''

if ($plan.Docker) {
    if (Test-Path $ComposeFile) {
        Write-Host '  [..] docker compose up -d（首次拉镜像会慢一些）'
        Push-Location $Root
        docker compose up -d
        $composeOk = ($LASTEXITCODE -eq 0)
        Pop-Location

        if ($composeOk) {
            Write-Host "  [OK] docker compose 已启动  后端 http://127.0.0.1:$BackendPort"
            Write-Host '       等待后端就绪 ...'
            if (Wait-BackendReady $BackendPort 90) {
                Write-Host '  [OK] 后端已就绪'
            } else {
                Write-Host '  [!] 后端 90 秒内未就绪（看日志：docker compose logs -f backend）'
            }
        } else {
            Write-Host '  [!] docker compose 启动失败（Docker Desktop 起了吗）'
        }
    } else {
        Write-Host '  [X] 缺少 docker-compose.yml，无法走评审模式'
    }
}

if ($plan.Backend) {
    if (Test-PortInUse $BackendPort) {
        Write-Host "  [--] 后端   端口 $BackendPort 已被占用，跳过启动（复用已在跑的那个）"
    } elseif (-not (Test-Environment -NeedBackend $true)) {
        Write-Host '  [X] 后端环境不完整，已跳过'
    } else {
        Start-EndWindow "HyPRA-后端 :$BackendPort" $BackendDir "..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port $BackendPort"
        Write-Host "  [OK] 后端   http://127.0.0.1:$BackendPort        接口文档 /docs"
        Write-Host '       等待后端就绪 ...'
        if (Wait-BackendReady $BackendPort 40) {
            Write-Host '  [OK] 后端已就绪'
        } else {
            Write-Host '  [!] 后端 40 秒内未就绪，其余端仍会启动（控制台会提示「后端未连接」）'
        }
    }
}

if ($plan.Frontend) {
    if (Test-PortInUse $FrontendPort) {
        Write-Host "  [--] Web 前端  端口 $FrontendPort 已被占用，跳过启动"
    } elseif (-not (Test-Environment -NeedFrontend $true)) {
        Write-Host '  [X] 前端环境不完整，已跳过'
    } else {
        Start-EndWindow "HyPRA-Web前端 :$FrontendPort" $FrontendDir 'npm run dev'
        Write-Host "  [OK] Web 端 http://localhost:$FrontendPort"
    }
}

if ($plan.Desktop) {
    if (-not (Test-Environment -NeedDesktop $true)) {
        Write-Host '  [X] 桌面端环境不完整，已跳过'
    } else {
        if ($Release) {
            Write-Host '  [..] 构建桌面端（npm run build，约 10~30 秒）'
            Push-Location $DesktopDir
            npm run build
            $buildOk = ($LASTEXITCODE -eq 0)
            Pop-Location

            if ($buildOk) {
                Start-EndWindow 'HyPRA-程序控制台' $DesktopDir 'npm start'
                Write-Host '  [OK] 程序控制台 启动中（加载构建产物）'
            } else {
                Write-Host '  [X] 桌面端构建失败，已跳过启动（看上面的构建输出）'
            }
        } else {
            Start-EndWindow 'HyPRA-程序控制台' $DesktopDir 'npm run dev'
            Write-Host '  [OK] 程序控制台 启动中（首次编译约 10~30 秒）'
        }

        if (-not $Release -or $buildOk) {
            Write-Host '       桌宠窗与 Web 端都在它的「模式启动」里选；托盘常驻，随时可再唤出。'
        }
    }
}

if (-not $plan.Backend -and -not $plan.Frontend -and -not $plan.Desktop -and -not $plan.Docker) {
    Write-Host ''
    Write-Host '  [X] 没有可启动的端：依赖缺失或端口均被占用，请看上方提示先补齐环境。'
}

Write-Host ''
Wait-CloseWindow '按回车键关闭本窗口（已启动的服务窗口继续运行，关掉它们即停止）'
exit 0
