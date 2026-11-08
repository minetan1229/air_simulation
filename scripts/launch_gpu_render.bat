@echo off
chcp 65001 > nul
echo ========================================================
echo  紙飛行機シミュレーター - 3D描画(RENDER) GPU強制起動
echo  Target: NVIDIA GeForce RTX 4060
echo ========================================================
echo.

echo [1/3] Windows DirectX グラフィック優先度設定を登録中...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "New-Item -Path 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences' -Force | Out-Null; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences' -Name 'C:\Program Files\Google\Chrome\Application\chrome.exe' -Value 'GpuPreference=2;AutoHDREnable=0;'; " ^
  "Set-ItemProperty -Path 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences' -Name 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' -Value 'GpuPreference=2;AutoHDREnable=0;'; " ^
  "Write-Host '  -> レジストリ登録完了 (GpuPreference=2 / 高パフォーマンス)' -ForegroundColor Green"

echo.
echo [2/3] ブラウザのバックグラウンド常駐プロセスをリフレッシュ...
echo ※ 既存の古いGPUプロセスが常駐していると新しいGPU設定が反映されないため、
echo   Chrome / Edge のGPUプロセスをクリーンアップします。

taskkill /F /IM chrome.exe > nul 2>&1
timeout /t 1 /nobreak > nul

echo.
echo [3/3] NVIDIA RTX 4060 強制オプション付きでブラウザを起動中...
set URL=http://127.0.0.1:5000/
set FLAGS=--force_high_performance_gpu --gpu-preference=high-performance --enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist

if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    echo   Google Chrome (RTX 4060 強制) を起動します...
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" %FLAGS% "%URL%"
) else if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    echo   Microsoft Edge (RTX 4060 強制) を起動します...
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" %FLAGS% "%URL%"
) else (
    echo   デフォルトブラウザで起動します...
    start "%URL%"
)

echo.
echo ========================================================
echo  完了! ブラウザ上部の [RENDER] 表示が RTX 4060 になっていることをご確認ください。
echo ========================================================
timeout /t 3 > nul
