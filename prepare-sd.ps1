# prepare-sd.ps1 — Prepare SD card for autonomous Raspberry Pi setup
# Run AFTER Raspberry Pi Imager has written the image and configured settings.
#
# Usage: Right-click > Run with PowerShell
#        or: powershell -ExecutionPolicy Bypass -File prepare-sd.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Prochains Departs — SD Card Preparation ===" -ForegroundColor Cyan
Write-Host ""

# Find the boot partition (removable drive containing cmdline.txt)
$bootDrive = $null
Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    $letter = "$($_.DriveLetter):"
    if (Test-Path "$letter\cmdline.txt") {
        $bootDrive = $letter
    }
}

if (-not $bootDrive) {
    Write-Host "ERROR: Boot partition not found." -ForegroundColor Red
    Write-Host "Make sure the SD card is in the reader and was flashed with Raspberry Pi Imager." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Found boot partition: $bootDrive" -ForegroundColor Green

# Verify firstrun.sh exists (Imager creates it when you configure settings)
$firstrunPath = "$bootDrive\firstrun.sh"
if (-not (Test-Path $firstrunPath)) {
    Write-Host "ERROR: firstrun.sh not found on boot partition." -ForegroundColor Red
    Write-Host ""
    Write-Host "In Raspberry Pi Imager, click the settings icon and configure:" -ForegroundColor Yellow
    Write-Host "  - Username: pi" -ForegroundColor Yellow
    Write-Host "  - Set a password" -ForegroundColor Yellow
    Write-Host "  - Enable SSH (optional, for remote access)" -ForegroundColor Yellow
    Write-Host "  - Configure WiFi (if not using Ethernet)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Then re-flash the SD card and run this script again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Copy setup-pi.sh to boot partition
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$setupSrc = "$scriptDir\setup-pi.sh"
if (-not (Test-Path $setupSrc)) {
    Write-Host "ERROR: setup-pi.sh not found in $scriptDir" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Copy-Item $setupSrc "$bootDrive\setup-pi.sh" -Force
Write-Host "Copied setup-pi.sh to boot partition" -ForegroundColor Green

# Read firstrun.sh content
$firstrun = [System.IO.File]::ReadAllText($firstrunPath)

# Create a one-shot systemd service that runs setup-pi.sh on the NEXT boot
# (firstrun.sh runs on first boot to create the user/WiFi/SSH, then reboots;
#  our service runs on the second boot once the network is up)
$injection = @"

# --- Prochains Departs: schedule app setup for next boot ---
cat > /etc/systemd/system/first-setup.service << 'PDSVC'
[Unit]
Description=Prochains Departs first-time setup
After=network-online.target
Wants=network-online.target
ConditionPathExists=/boot/firmware/setup-pi.sh

[Service]
Type=oneshot
ExecStart=/bin/bash /boot/firmware/setup-pi.sh
ExecStartPost=/bin/rm -f /boot/firmware/setup-pi.sh
ExecStartPost=/bin/systemctl disable first-setup.service
ExecStartPost=/sbin/reboot
RemainAfterExit=yes
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
PDSVC
systemctl enable first-setup.service
# --- End Prochains Departs ---

"@

# Insert before the "rm -f /boot/..." line that cleans up firstrun.sh
if ($firstrun -match '(?m)^rm -f /boot') {
    $firstrun = $firstrun -replace '(?m)(^rm -f /boot)', "$injection`$1"
    Write-Host "Injected auto-setup into firstrun.sh" -ForegroundColor Green
} else {
    # Fallback: insert before the last line
    $lines = $firstrun -split "`n"
    $lastLine = $lines[-1]
    $lines[-1] = $injection
    $lines += $lastLine
    $firstrun = $lines -join "`n"
    Write-Host "Appended auto-setup to firstrun.sh (fallback)" -ForegroundColor Yellow
}

# Write back with Unix line endings
$firstrun = $firstrun -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($firstrunPath, $firstrun, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "SD card is ready!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Safely eject the SD card"
Write-Host "  2. Insert it into the Raspberry Pi and connect power"
Write-Host "  3. Wait ~5 minutes (the Pi reboots twice during setup)"
Write-Host "  4. The app starts automatically on the touchscreen"
Write-Host "  5. Open Settings to enter your IDFM API token"
Write-Host ""
Read-Host "Press Enter to exit"
