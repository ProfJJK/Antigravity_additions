Write-Host "Deleting legacy .7z backups..."
Get-ChildItem -Path D:\__CoChem\__agentic -Recurse -Include *.7z, *.7z.tmp -Force | Remove-Item -Force

Write-Host "Copying Gemini config..."
if (Test-Path D:\__CoChem\__agentic\gemini_config_export) {
    Remove-Item -Recurse -Force D:\__CoChem\__agentic\gemini_config_export
}
Copy-Item -Path C:\Users\ansac\.gemini\config -Destination D:\__CoChem\__agentic\gemini_config_export -Recurse -Force

Write-Host "Re-initializing Git repo to strip history..."
if (Test-Path D:\__CoChem\__agentic\.git) {
    Remove-Item -Recurse -Force D:\__CoChem\__agentic\.git
}
cd D:\__CoChem\__agentic
git init
git remote add origin https://github.com/ProfJJK/Antigravity_additions.git
git add .
git commit -m "Current State: Pipeline V4.1.2 + Agent Ecosystem"
git push -u origin master --force
