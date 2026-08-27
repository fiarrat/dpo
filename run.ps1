# Запуск реестра обращений на Windows.
# Открыть PowerShell в папке проекта и выполнить:  .\run.ps1
# Первый запуск с демонстрационными данными:       .\run.ps1 -Seed

param(
    [switch]$Seed,      # наполнить базу демонстрационными данными
    [switch]$Reset,     # пересоздать базу заново
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# ── Python ───────────────────────────────────────────────────────────────
$python = $null
foreach ($candidate in @('py -3.11', 'py -3', 'python', 'python3')) {
    $parts = $candidate.Split(' ')
    $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
    if ($exe) { $python = $candidate; break }
}
if (-not $python) {
    Write-Host 'Не найден Python. Установите его с https://www.python.org/downloads/' -ForegroundColor Red
    Write-Host 'При установке обязательно отметьте галочку "Add python.exe to PATH".' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path '.venv')) {
    Write-Host '-> Создаю виртуальное окружение...' -ForegroundColor Cyan
    Invoke-Expression "$python -m venv .venv"
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "Не удалось создать окружение: нет файла $venvPython" -ForegroundColor Red
    exit 1
}

Write-Host '-> Проверяю зависимости...' -ForegroundColor Cyan
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q -r backend\requirements.txt

# ── Распознавание фото и сканов ──────────────────────────────────────────
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Host ''
    Write-Host 'Tesseract не найден: фотографии и сканы распознаваться не будут.' -ForegroundColor Yellow
    Write-Host 'Всё остальное - реестр, сроки, флажки, PDF с текстовым слоем, DOCX - работает.' -ForegroundColor Yellow
    Write-Host 'Чтобы включить распознавание:  winget install UB-Mannheim.TesseractOCR' -ForegroundColor Yellow
    Write-Host 'При установке отметьте русский язык (Russian) в списке Additional language data.' -ForegroundColor Yellow
    Write-Host ''
}

# ── База ─────────────────────────────────────────────────────────────────
if ($Seed -or $Reset) {
    Write-Host '-> Наполняю базу демонстрационными данными...' -ForegroundColor Cyan
    Push-Location backend
    if ($Reset) { & $venvPython -m app.seed --reset } else { & $venvPython -m app.seed }
    Pop-Location
}

# ── Запуск ───────────────────────────────────────────────────────────────
$url = "http://127.0.0.1:$Port"
Write-Host ''
Write-Host "-> Запускаю на $url" -ForegroundColor Green
Write-Host '   Остановить: Ctrl+C' -ForegroundColor DarkGray
Write-Host ''
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 4; Start-Process $u } -ArgumentList $url | Out-Null

Push-Location backend
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
Pop-Location
