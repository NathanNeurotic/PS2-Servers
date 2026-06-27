#Requires -RunAsAdministrator

# Remove PS2 Servers Windows Firewall rules.
#
# Run from an elevated PowerShell prompt:
#   powershell -ExecutionPolicy Bypass -File .\tools\remove-windows-firewall-rules.ps1
#
# This only removes firewall rules whose display names start with "PS2 Servers - ".
# It does not enable, disable, install, or remove Windows SMB1 optional features.

$ErrorActionPreference = "Stop"

$rules = @(Get-NetFirewallRule -DisplayName "PS2 Servers - *" -ErrorAction SilentlyContinue)

if ($rules.Count -eq 0) {
    Write-Host "No PS2 Servers firewall rules found."
    exit 0
}

foreach ($rule in $rules) {
    Write-Host ("Removing firewall rule: " + $rule.DisplayName)
}

$rules | Remove-NetFirewallRule -ErrorAction Stop

Write-Host ("Removed PS2 Servers firewall rules: " + $rules.Count)
