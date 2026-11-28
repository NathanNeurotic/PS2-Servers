@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

for /f "usebackq delims=" %%G in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; " ^
  "$props = Get-Content '%SCRIPT_DIR%\gradle\wrapper\gradle-wrapper.properties'; " ^
  "$urlLine = ($props | Where-Object { $_ -like 'distributionUrl=*' } | Select-Object -First 1); " ^
  "$shaLine = ($props | Where-Object { $_ -like 'distributionSha256Sum=*' } | Select-Object -First 1); " ^
  "$distUrl = ($urlLine.Substring('distributionUrl='.Length)) -replace '\\:', ':'; " ^
  "$expectedSha = $shaLine.Substring('distributionSha256Sum='.Length).Trim().ToLowerInvariant(); " ^
  "$archiveName = [System.IO.Path]::GetFileName($distUrl); " ^
  "$dirName = $archiveName -replace '-bin\.zip$', ''; " ^
  "$userHome = if ($env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME } else { Join-Path $env:USERPROFILE '.gradle' }; " ^
  "$baseDir = Join-Path $userHome ('wrapper\dists\ps2servers-' + $dirName); " ^
  "$gradleBat = Join-Path $baseDir ($dirName + '\bin\gradle.bat'); " ^
  "if (-not (Test-Path $gradleBat)) { " ^
  "  New-Item -ItemType Directory -Force -Path $baseDir | Out-Null; " ^
  "  $zipPath = Join-Path $baseDir $archiveName; " ^
  "  $tmpPath = $zipPath + '.part'; " ^
  "  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
  "  Invoke-WebRequest -Uri $distUrl -OutFile $tmpPath -UseBasicParsing; " ^
  "  $actualSha = (Get-FileHash -Algorithm SHA256 -Path $tmpPath).Hash.ToLowerInvariant(); " ^
  "  if ($actualSha -ne $expectedSha) { Remove-Item -Force $tmpPath; throw ('Gradle checksum mismatch: ' + $actualSha) }; " ^
  "  Move-Item -Force $tmpPath $zipPath; " ^
  "  Expand-Archive -Path $zipPath -DestinationPath $baseDir -Force; " ^
  "} " ^
  "Write-Output $gradleBat"`) do set "GRADLE_BAT=%%G"

if errorlevel 1 exit /b 1
if not defined GRADLE_BAT exit /b 1

call "%GRADLE_BAT%" %*
exit /b %errorlevel%
