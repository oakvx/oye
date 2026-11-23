# Oye v42 - Windows one-click source launcher, no global pip changes.
# Execute from AVVIA_OYE.bat. Only installer dependencies are downloaded;
# Oye training, templates, logs, state and config are never overwritten.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$Runtime = Join-Path $Root '.oye_runtime'
$VenvPython = Join-Path $Runtime 'Scripts\python.exe'
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'avvio_v44.log'

function Say([string]$msg) { Write-Host ('[OYE] ' + $msg) }
function RequireSuccess([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw ("$step non riuscito (codice $LASTEXITCODE). Controlla $Log") }
}
function Select-Python {
    $check = 'import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,13) and sys.maxsize > 2**32 else 7)'
    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += ,@((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
        $candidates += ,@((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'))
        $candidates += ,@((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'))
    }
    $candidates += ,@('py', '-3.12')
    $candidates += ,@('py', '-3')
    $candidates += ,@('python')
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $cmd = Get-Command $exe -ErrorAction SilentlyContinue
        if ((-not $cmd) -and (-not (Test-Path $exe))) { continue }
        $rest = @()
        if ($candidate.Length -gt 1) { $rest = @($candidate[1..($candidate.Length-1)]) }
        try {
            & $exe @rest -c $check 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return ,$candidate }
        } catch { }
    }
    return $null
}
function Winget-Install([string]$package) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    Say "Installo $package tramite Windows Package Manager (connessione Internet richiesta)."
    & winget install --id $package --exact --silent --accept-package-agreements --accept-source-agreements 2>&1 |
        Out-File -LiteralPath $Log -Append -Encoding UTF8
    return ($LASTEXITCODE -eq 0)
}
function Has-Tesseract {
    if (Get-Command tesseract -ErrorAction SilentlyContinue) { return $true }
    foreach ($path in @('C:\Program Files\Tesseract-OCR\tesseract.exe',
                        'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe')) {
        if (Test-Path $path) { return $true }
    }
    return $false
}

try {
    Say 'Verifico il sistema e preparo Oye senza cancellare gli apprendimenti.'
    $py = Select-Python
    if (-not $py) {
        $ok = Winget-Install 'Python.Python.3.12'
        if ($ok) { $py = Select-Python }
        if (-not $py) {
            throw 'Python x64 3.11-3.13 non trovato. Installa Python 3.12 da python.org oppure attiva winget e riesegui AVVIA_OYE.bat.'
        }
    }
    if (-not (Test-Path $VenvPython)) {
        Say 'Creo il runtime Python privato di Oye (solo la prima volta).'
        New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
        $cmd = $py[0]
        $args = @()
        if ($py.Length -gt 1) { $args = @($py[1..($py.Length-1)]) }
        & $cmd @args -m venv $Runtime 2>&1 | Out-File -LiteralPath $Log -Append -Encoding UTF8
        RequireSuccess 'Creazione ambiente virtuale'
    }
    $req1 = Join-Path $Root 'requirements.txt'
    $req2 = Join-Path $Root 'requirements-ui.txt'
    $signature = (Get-FileHash -Algorithm SHA256 $req1).Hash + ':' + (Get-FileHash -Algorithm SHA256 $req2).Hash
    $marker = Join-Path $Runtime 'requirements.sha256'
    $hasImports = $false
    if (Test-Path $marker) {
        try {
            & $VenvPython -c 'import cv2,numpy,mss,pyautogui,pynput,pytesseract;import PIL' 2>$null | Out-Null
            $hasImports = ($LASTEXITCODE -eq 0)
        } catch { }
    }
    if ((-not $hasImports) -or ((Get-Content $marker -Raw -ErrorAction SilentlyContinue).Trim() -ne $signature)) {
        Say 'Verifico e installo le librerie necessarie (la prima volta serve Internet).'
        & $VenvPython -m pip install --disable-pip-version-check -r $req1 2>&1 | Out-File -LiteralPath $Log -Append -Encoding UTF8
        RequireSuccess 'Installazione librerie principali'
        & $VenvPython -m pip install --disable-pip-version-check -r $req2 2>&1 | Out-File -LiteralPath $Log -Append -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { Say 'Finestra WebView non disponibile, uso il browser (registrato nel log).' }
        [System.IO.File]::WriteAllText($marker, $signature)
    }
    if (-not (Has-Tesseract)) {
        $ok = Winget-Install 'UB-Mannheim.TesseractOCR'
        if ((-not $ok) -or (-not (Has-Tesseract))) {
            Say 'ATTENZIONE: Tesseract OCR non trovato. La modalita Avvio rapido A TEMPO puo procedere, ma non riconoscera le scritte spettatore/riavvio.'
            Say 'Oye si apre comunque. Installa Tesseract OCR se necessario.'
        }
    }
    Say 'Controllo e salvo i vecchi training. Nessun dato viene sostituito.'
    & $VenvPython (Join-Path $Root 'tools\preflight.py') 2>&1 | Out-File -LiteralPath $Log -Append -Encoding UTF8
    RequireSuccess 'Preparazione archivio e backup'
    Say 'Avvio la dashboard locale. Per fermare gli input usa F9.'
    $LaunchArgs = @()
    if ($env:OYE_OPEN_TAB -eq 'timeline') { $LaunchArgs += '--timeline' }
    if ($env:OYE_BROWSER_ONLY -eq '1') { $LaunchArgs += '--browser' }
    & $VenvPython (Join-Path $Root 'studio.py') @LaunchArgs
    RequireSuccess 'Avvio di Oye'
} catch {
    Write-Host ''
    Write-Host ('ERRORE AVVIO OYE: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('Log: ' + $Log)
    exit 1
}
exit 0
