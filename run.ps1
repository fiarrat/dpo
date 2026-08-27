# Zapusk reestra obrashcheniy po personalnym dannym (Windows).
# Prosche vsego: dvoynoy klik po faylu RUN.bat
# Libo vruchnuyu:  powershell -ExecutionPolicy Bypass -File .\run.ps1 -Seed
#
# Ves fayl namerenno napisan tolko latinicey: tak kodirovka perestaet byt
# istochnikom oshibok. PowerShell 5.1 chitaet .ps1 kak ANSI, esli net BOM,
# i lyuboy kirillicheskiy simvol prevrashchaetsya v musor.

param(
    [switch]$Seed,      # napolnit bazu demonstracionnymi dannymi
    [switch]$Reset,     # peresozdat bazu zanovo
    [int]$Port = 8000
)

Set-Location -Path $PSScriptRoot

Write-Host ''
Write-Host '=== Reestr obrashcheniy po personalnym dannym (FZ-152) ===' -ForegroundColor Cyan
Write-Host ''

# --- Poisk Python -------------------------------------------------------
# Malo proverit, chto komanda sushchestvuet: launcher py est v sisteme i togda,
# kogda ni odnogo interpretatora ne ustanovleno. Poetomu kazhdogo kandidata
# zapuskaem i smotrim, chto on realno otvetit.

$candidates = @(
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3'),
    @('python'), @('python3')
)

$python = $null
$found = @()

foreach ($c in $candidates) {
    if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
    $rest = if ($c.Count -gt 1) { $c[1..($c.Count - 1)] } else { @() }
    $ver = ''
    try {
        $ver = (& $c[0] @rest --version 2>&1 | Out-String).Trim()
    } catch {
        continue
    }
    if ($LASTEXITCODE -ne 0) { continue }
    if ($ver -match 'Python\s+(\d+)\.(\d+)') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        $found += "$($c -join ' ') -> $ver"
        if ($major -eq 3 -and $minor -ge 10) {
            $python = $c
            Write-Host "[ok] Python: $ver" -ForegroundColor DarkGray
            break
        }
    }
}

if (-not $python) {
    Write-Host ''
    Write-Host 'PYTHON NE NAYDEN (nuzhna versiya 3.10 ili novee).' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Chto sdelat:' -ForegroundColor Yellow
    Write-Host '  1. Otkroyte https://www.python.org/downloads/' -ForegroundColor Yellow
    Write-Host '  2. Nazhmite bolshuyu zheltuyu knopku Download Python' -ForegroundColor Yellow
    Write-Host '  3. Zapustite ustanovshchik i NA PERVOM EKRANE otmette galochku' -ForegroundColor Yellow
    Write-Host '     "Add python.exe to PATH" - ona vnizu, ee legko propustit' -ForegroundColor Yellow
    Write-Host '  4. Nazhmite Install Now i dozhdites nadpisi Setup was successful' -ForegroundColor Yellow
    Write-Host '  5. Zakroyte eto okno i zapustite RUN.bat zanovo' -ForegroundColor Yellow
    Write-Host ''
    if ($found.Count -gt 0) {
        Write-Host 'Naydeny, no ne podoshli:' -ForegroundColor DarkGray
        $found | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    } else {
        Write-Host 'Ni odnogo interpretatora Python v sisteme ne obnaruzheno.' -ForegroundColor DarkGray
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Write-Host ''
        Write-Host 'Chto vidit launcher py:' -ForegroundColor DarkGray
        try { & py --list 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } } catch { }
    }
    exit 1
}

# --- Predupredit pro kirillicu v puti -----------------------------------
if ($PSScriptRoot -match '[^\u0000-\u007F]') {
    Write-Host ''
    Write-Host 'VNIMANIE: v puti k papke est nelatinskie simvoly:' -ForegroundColor Yellow
    Write-Host "  $PSScriptRoot" -ForegroundColor Yellow
    Write-Host 'Eto inogda lomaet ustanovku bibliotek. Esli budut oshibki -' -ForegroundColor Yellow
    Write-Host 'perenesite papku v prostoy put, naprimer C:\dpo' -ForegroundColor Yellow
    Write-Host ''
}

# --- Virtualnoe okruzhenie ----------------------------------------------
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host '[1/3] Sozdayu virtualnoe okruzhenie...' -ForegroundColor Cyan
    $rest = if ($python.Count -gt 1) { $python[1..($python.Count - 1)] } else { @() }
    & $python[0] @rest -m venv .venv
}

if (-not (Test-Path $venvPython)) {
    Write-Host ''
    Write-Host 'NE UDALOS SOZDAT VIRTUALNOE OKRUZHENIE.' -ForegroundColor Red
    Write-Host 'Vozmozhnye prichiny:' -ForegroundColor Red
    Write-Host '  - Python ustanovlen bez komponenta venv (pereustanovite s python.org)' -ForegroundColor Red
    Write-Host '  - papka proekta dostupna tolko dlya chteniya' -ForegroundColor Red
    Write-Host '  - antivirus zablokiroval sozdanie faylov' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Poprobuyte udalit papku .venv i zapustit zanovo.' -ForegroundColor Yellow
    exit 1
}

Write-Host '[2/3] Proveryayu biblioteki (pervyy raz - neskolko minut)...' -ForegroundColor Cyan
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'NE UDALOS USTANOVIT BIBLIOTEKI.' -ForegroundColor Red
    Write-Host 'Proverte podklyuchenie k internetu.' -ForegroundColor Yellow
    Write-Host 'Esli vy za korporativnym proksi - zadayte HTTP_PROXY i HTTPS_PROXY.' -ForegroundColor Yellow
    exit 1
}

# --- Raspoznavanie foto i skanov ----------------------------------------
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Host ''
    Write-Host 'Tesseract ne nayden: fotografii i skany raspoznavatsya ne budut.' -ForegroundColor Yellow
    Write-Host 'Vse ostalnoe rabotaet: reestr, sroki, flazhki, PDF s tekstom, DOCX.' -ForegroundColor Yellow
    Write-Host 'Chtoby vklyuchit raspoznavanie, ustanovite:' -ForegroundColor Yellow
    Write-Host '  https://github.com/UB-Mannheim/tesseract/wiki' -ForegroundColor Yellow
    Write-Host 'Pri ustanovke otmette Russian v spiske Additional language data.' -ForegroundColor Yellow
    Write-Host ''
}

# --- Baza ---------------------------------------------------------------
if ($Seed -or $Reset) {
    Write-Host '[3/3] Napolnyayu bazu demonstracionnymi dannymi...' -ForegroundColor Cyan
    Push-Location backend
    if ($Reset) { & $venvPython -m app.seed --reset } else { & $venvPython -m app.seed }
    Pop-Location
}

# --- Zapusk -------------------------------------------------------------
$url = "http://127.0.0.1:$Port"
Write-Host ''
Write-Host "Sistema zapushchena: $url" -ForegroundColor Green
Write-Host 'Eto okno zakryvat nelzya: poka ono otkryto, sistema rabotaet.' -ForegroundColor DarkGray
Write-Host 'Ostanovit: Ctrl+C' -ForegroundColor DarkGray
Write-Host ''

# Otkrytie brauzera - udobstvo, a ne neobhodimost: esli ono zapreshcheno
# politikoy, zapusk vse ravno dolzhen sostoyatsya.
try {
    Start-Job -ScriptBlock {
        param($u); Start-Sleep -Seconds 4; Start-Process $u
    } -ArgumentList $url | Out-Null
} catch {
    Write-Host "Brauzer ne otkrylsya sam - otkroyte $url vruchnuyu." -ForegroundColor DarkGray
}

Push-Location backend
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
Pop-Location
