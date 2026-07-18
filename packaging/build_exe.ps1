# Build the single-file reinsman desktop .exe.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1
# Note: the bundle is large (claude-agent-sdk ships its own claude.exe). Build takes a while.
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install --upgrade pyinstaller pywebview
python -m PyInstaller packaging/reinsman.spec --noconfirm --clean
Write-Host ""
Write-Host "[reinsman] built: dist\reinsman.exe"
Write-Host "[reinsman] first run seeds ~/.reinsman (templates/agents/knowledge/.env); authenticate once there."
