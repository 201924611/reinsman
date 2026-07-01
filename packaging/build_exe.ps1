# Build the single-file agent-core desktop .exe.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1
# Note: the bundle is large (claude-agent-sdk ships its own claude.exe). Build takes a while.
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install --upgrade pyinstaller pywebview
python -m PyInstaller packaging/agent-core.spec --noconfirm --clean
Write-Host ""
Write-Host "[agent-core] built: dist\agent-core.exe"
Write-Host "[agent-core] first run seeds ~/.agent-core (templates/agents/knowledge/.env); authenticate once there."
