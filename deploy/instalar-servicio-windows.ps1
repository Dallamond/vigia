# Instala Vigia como servicio de Windows con NSSM.
#
# Requisitos:
#   - PowerShell como Administrador.
#   - NSSM en el PATH (https://nssm.cc) o pásalo con -Nssm.
#   - El proyecto con su .venv ya creado y probado a mano.
#
# Ejemplo:
#   .\instalar-servicio-windows.ps1 -Ruta "D:\Vigia" -Python "D:\Vigia\.venv\Scripts\python.exe"
#
# El servicio arranca con `python -m vigia.web` en la carpeta del proyecto. Los
# secretos se leen de <Ruta>\.env (VIGIA_TOKEN, TELEGRAM_*). Los logs propios
# de Vigia siguen en <Ruta>\logs\vigia.log; NSSM guarda además stdout/stderr.
[CmdletBinding()]
param(
    [string]$Ruta = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = (Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe"),
    [string]$Nombre = "Vigia",
    [string]$Nssm = "nssm.exe",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Comprobar-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Ejecuta este script en una PowerShell como Administrador."
    }
}

Comprobar-Admin

$Ruta = (Resolve-Path $Ruta).Path
if (-not (Test-Path $Python)) { throw "No existe el Python indicado: $Python" }
if (-not (Test-Path (Join-Path $Ruta "vigia\web.py"))) {
    throw "La ruta no parece el proyecto Vigia (falta vigia\web.py): $Ruta"
}
$nssmCmd = Get-Command $Nssm -ErrorAction SilentlyContinue
if (-not $nssmCmd) {
    throw "No encuentro nssm.exe. Descárgalo de https://nssm.cc y pásalo con -Nssm 'C:\ruta\nssm.exe'."
}
$nssmExe = $nssmCmd.Source

$existe = (& $nssmExe status $Nombre 2>$null)
if ($LASTEXITCODE -eq 0 -and $existe) {
    if (-not $Force) {
        throw "El servicio '$Nombre' ya existe. Usa -Force para reinstalarlo o -Nombre otro."
    }
    Write-Host "Deteniendo y eliminando el servicio existente '$Nombre'…"
    & $nssmExe stop $Nombre | Out-Null
    Start-Sleep -Seconds 2
    & $nssmExe remove $Nombre confirm | Out-Null
}

$logs = Join-Path $Ruta "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

Write-Host "Instalando '$Nombre' -> $Python -m vigia.web (cwd $Ruta)"
& $nssmExe install $Nombre $Python "-m vigia.web"
if ($LASTEXITCODE -ne 0) { throw "nssm install falló con código $LASTEXITCODE" }

& $nssmExe set $Nombre AppDirectory $Ruta
& $nssmExe set $Nombre DisplayName "Vigia (vigilancia local)"
& $nssmExe set $Nombre Description "Panel y motor de vigilancia de Vigia"
& $nssmExe set $Nombre Start SERVICE_AUTO_START
& $nssmExe set $Nombre AppStdout (Join-Path $logs "servicio.out.log")
& $nssmExe set $Nombre AppStderr (Join-Path $logs "servicio.err.log")
& $nssmExe set $Nombre AppRotateFiles 1
& $nssmExe set $Nombre AppRotateBytes 10485760
& $nssmExe set $Nombre AppExit Default Restart
& $nssmExe set $Nombre AppRestartDelay 5000
& $nssmExe set $Nombre AppThrottle 5000
& $nssmExe set $Nombre AppNoConsole 1
& $nssmExe set $Nombre AppEnvironmentExtra "PYTHONUNBUFFERED=1"

& $nssmExe start $Nombre
if ($LASTEXITCODE -ne 0) { throw "El servicio se instaló pero no arrancó. Revisa $logs\servicio.err.log" }

Write-Host ""
Write-Host "Servicio '$Nombre' instalado y arrancado."
Write-Host "Comprobar:  Get-Service $Nombre"
Write-Host "Panel:      http://127.0.0.1:8000/  (o el host/puerto de config.yaml)"
Write-Host "Parar:      nssm stop $Nombre"
Write-Host "Quitar:     nssm remove $Nombre confirm"
