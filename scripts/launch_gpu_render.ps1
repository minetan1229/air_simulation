# ========================================================
#  紙飛行機シミュレーター - 3D描画(RENDER) GPU強制起動スクリプト
#  Target: NVIDIA GeForce RTX 4060 (Discreet High-Performance GPU)
# ========================================================

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " 紙飛行機シミュレーター - 3D描画(RENDER) RTX 4060 強制起動" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# 1. レジストリに DirectX 高パフォーマンス設定 (GpuPreference=2) を書き込み
Write-Host "`n[1/3] Windows DirectX グラフィック優先度を設定中..." -ForegroundColor Yellow
$regPath = "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences"
if (-not (Test-Path $regPath)) {
    New-Item -Path $regPath -Force | Out-Null
}

$browsers = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
)

foreach ($b in $browsers) {
    if (Test-Path $b) {
        Set-ItemProperty -Path $regPath -Name $b -Value "GpuPreference=2;AutoHDREnable=0;"
        Write-Host "  -> 登録完了: $b (GpuPreference=2 / 高パフォーマンス)" -ForegroundColor Green
    }
}

# 2. 古い常駐プロセスの確認とクリーンアップ
Write-Host "`n[2/3] ブラウザのバックグラウンド常駐プロセスをリフレッシュ..." -ForegroundColor Yellow
Write-Host "※ 古いGPUプロセスが残っていると設定が反映されないためリフレッシュします。" -ForegroundColor Gray

Get-Process -Name chrome -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

# 3. 強制フラグ付きで起動
Write-Host "`n[3/3] NVIDIA RTX 4060 強制オプション付きでブラウザを起動..." -ForegroundColor Yellow
$url = "http://127.0.0.1:5000/"
$flags = @(
    "--force_high_performance_gpu",
    "--gpu-preference=high-performance",
    "--enable-gpu-rasterization",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
    $url
)

$chromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$edgeExe = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

if (Test-Path $chromeExe) {
    Write-Host "  -> Google Chrome を RTX 4060 強制で起動します" -ForegroundColor Green
    Start-Process -FilePath $chromeExe -ArgumentList $flags
} elseif (Test-Path $edgeExe) {
    Write-Host "  -> Microsoft Edge を RTX 4060 強制で起動します" -ForegroundColor Green
    Start-Process -FilePath $edgeExe -ArgumentList $flags
} else {
    Start-Process $url
}

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host " 起動完了! 画面上部の [RENDER] が RTX 4060 になります。" -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan
