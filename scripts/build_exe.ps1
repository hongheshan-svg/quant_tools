###############################################################################
# A股量化交易系统 - 一键打包脚本
# 用法: .venv\Scripts\python.exe -m PyInstaller AStockQuantQt6.spec --noconfirm --clean
# 或直接运行此脚本: powershell .\scripts\build_exe.ps1
###############################################################################
Param(
    [string]$PythonExe = ".venv\Scripts\python.exe",
    [switch]$NoBuild,
    [switch]$SkipClean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  A股量化交易系统 - EXE 打包工具" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# 1. 清理旧的构建
if (-not $SkipClean) {
    Write-Host "[1/4] 清理旧构建文件..." -ForegroundColor Yellow
    if (Test-Path ".\build") { Remove-Item ".\build" -Recurse -Force }
    if (Test-Path ".\dist\AStockQuantQt6.exe") { Remove-Item ".\dist\AStockQuantQt6.exe" -Force }
}

# 2. 确保目录存在
Write-Host "[2/4] 创建运行时目录..." -ForegroundColor Yellow
@(".\logs", ".\data", ".\config") | ForEach-Object {
    if (!(Test-Path $_)) { New-Item $_ -ItemType Directory | Out-Null }
}

# 3. 执行打包
if (-not $NoBuild) {
    Write-Host "[3/4] 执行 PyInstaller 打包（单文件 EXE）..." -ForegroundColor Yellow
    Write-Host "  这可能需要 3-8 分钟，请耐心等待..." -ForegroundColor DarkGray
    $env:QT_API = "pyqt6"
    & $PythonExe -m PyInstaller AStockQuantQt6.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) {
        Write-Host "打包失败！请检查上方错误信息。" -ForegroundColor Red
        exit 1
    }
}

# 4. 复制运行时需要的文件到 dist 目录
Write-Host "[4/4] 复制配置和数据文件..." -ForegroundColor Yellow
$distDir = ".\dist"
if (!(Test-Path "$distDir\config")) { New-Item "$distDir\config" -ItemType Directory | Out-Null }
if (!(Test-Path "$distDir\data")) { New-Item "$distDir\data" -ItemType Directory | Out-Null }
if (!(Test-Path "$distDir\logs")) { New-Item "$distDir\logs" -ItemType Directory | Out-Null }

# 复制配置文件
Copy-Item ".\config\settings.yaml" "$distDir\config\settings.yaml" -Force
if (Test-Path ".\config\stock_pool.yaml") {
    Copy-Item ".\config\stock_pool.yaml" "$distDir\config\stock_pool.yaml" -Force
}

# 检查结果
$exePath = "$distDir\AStockQuantQt6.exe"
if (Test-Path $exePath) {
    $size = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  打包成功！" -ForegroundColor Green
    Write-Host "  输出: $exePath" -ForegroundColor Green
    Write-Host "  大小: ${size} MB" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "使用方式:" -ForegroundColor Cyan
    Write-Host "  1. 将 dist 文件夹整体复制到目标电脑" -ForegroundColor White
    Write-Host "  2. 确保 config\settings.yaml 中 API Key 已配置" -ForegroundColor White
    Write-Host "  3. 双击 AStockQuantQt6.exe 即可运行" -ForegroundColor White
    Write-Host ""
    Write-Host "  dist\" -ForegroundColor DarkGray
    Write-Host "    ├── AStockQuantQt6.exe    (主程序)" -ForegroundColor DarkGray
    Write-Host "    ├── config\" -ForegroundColor DarkGray
    Write-Host "    │   └── settings.yaml     (配置文件，需修改API Key)" -ForegroundColor DarkGray
    Write-Host "    ├── data\                  (数据库，自动创建)" -ForegroundColor DarkGray
    Write-Host "    └── logs\                  (日志，自动创建)" -ForegroundColor DarkGray
} else {
    Write-Host "打包失败: 未找到输出文件 $exePath" -ForegroundColor Red
    exit 1
}
