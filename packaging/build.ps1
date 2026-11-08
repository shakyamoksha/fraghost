param(
  [int]$BuildNumber = 0,
  [string]$UpdateRepo = "",
  [string]$Notes = "",
  [string]$PythonVersion = "3.12.10"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Cache = Join-Path $PSScriptRoot "cache"
$Stage = Join-Path $PSScriptRoot "stage\Cairn"
$Out = Join-Path $PSScriptRoot "out"
New-Item -ItemType Directory -Force -Path $Cache, $Out | Out-Null
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

Push-Location (Join-Path $Root "ui")
npm run build
if ($LASTEXITCODE -ne 0) { throw "The interface build failed" }
Pop-Location

$ZipName = "python-$PythonVersion-embed-amd64.zip"
$ZipPath = Join-Path $Cache $ZipName
if (-not (Test-Path $ZipPath)) {
  Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/$ZipName" -OutFile $ZipPath
}
Expand-Archive -Path $ZipPath -DestinationPath $Stage -Force
@"
python312.zip
.
Lib\site-packages
import site
"@ | Set-Content -Encoding ascii -Path (Join-Path $Stage "python312._pth")

$GetPip = Join-Path $Cache "get-pip.py"
if (-not (Test-Path $GetPip)) {
  Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip
}
& (Join-Path $Stage "python.exe") $GetPip
if ($LASTEXITCODE -ne 0) { throw "pip could not be installed" }
& (Join-Path $Stage "python.exe") -m pip install setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Build tools could not be installed" }
& (Join-Path $Stage "python.exe") -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependencies could not be installed" }

Copy-Item -Recurse (Join-Path $Root "shakya") (Join-Path $Stage "shakya")
Get-ChildItem -Recurse -Directory -Filter "__pycache__" (Join-Path $Stage "shakya") | Remove-Item -Recurse -Force
$RepoLiteral = $UpdateRepo.Replace("\", "\\").Replace('"', '\"')
@"
BUILD = $BuildNumber
UPDATE_REPO = "$RepoLiteral"
"@ | Set-Content -Encoding utf8 -Path (Join-Path $Stage "shakya\build_info.py")

New-Item -ItemType Directory -Force -Path (Join-Path $Stage "ui\dist") | Out-Null
Copy-Item -Recurse (Join-Path $Root "ui\dist\*") (Join-Path $Stage "ui\dist")

@"
@echo off
cd /d "%~dp0"
start "" "%~dp0pythonw.exe" -m shakya
"@ | Set-Content -Encoding ascii -Path (Join-Path $Stage "Cairn.cmd")

$Version = "0.1.0"
$VersionJson = @{
  version = $Version
  build = $BuildNumber
  notes = $Notes
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $Out "version.json"), $VersionJson)

$Iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $Iscc) {
  Write-Host "Inno Setup is not installed. The app folder is ready at $Stage"
  Write-Host "Install Inno Setup, then run: ISCC.exe packaging\cairn.iss"
  exit 0
}

& $Iscc (Join-Path $PSScriptRoot "cairn.iss")
if ($LASTEXITCODE -ne 0) { throw "The installer compiler failed" }
Write-Host "Installer: $(Join-Path $Out 'CairnSetup.exe')"
