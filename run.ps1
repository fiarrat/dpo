# Запуск реестра обращений на Windows.
# Проще всего: двойной клик по файлу Запустить.bat
# Либо вручную:  powershell -ExecutionPolicy Bypass -File .\run.ps1 -Seed

param(
    [switch]$Seed,      # наполнить базу демонстрационными данными
    [switch]$Reset,     # пересоздать базу заново
    [int]$Port = 8000
)

Set-Location -Path $PSScriptRoot

# ── Поиск Python ─────────────────────────────────────────────────────────
# Мало проверить, что команда существует: лаунчер py присутствует в системе
# и тогда, когда ни одного интерпретатора не установлено. Поэтому каждого
# кандидата запускаем и смотрим, что он реально ответит.

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
            Write-Host "-> Python: $ver ($($c -join ' '))" -ForegroundColor DarkGray
            break
        }
    }
}

if (-not $python) {
    Write-Host ''
    Write-Host 'Не найден подходящий Python (нужна версия 3.10 или новее).' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Что сделать:' -ForegroundColor Yellow
    Write-Host '  1. Откройте https://www.python.org/downloads/' -ForegroundColor Yellow
    Write-Host '  2. Нажмите большую жёлтую кнопку Download Python' -ForegroundColor Yellow
    Write-Host '  3. Запустите установщик и НА ПЕРВОМ ЖЕ ЭКРАНЕ отметьте галочку' -ForegroundColor Yellow
    Write-Host '     "Add python.exe to PATH" - она внизу, её легко пропустить' -ForegroundColor Yellow
    Write-Host '  4. Нажмите Install Now и дождитесь конца установки' -ForegroundColor Yellow
    Write-Host '  5. Закройте это окно и запустите Запустить.bat заново' -ForegroundColor Yellow
    Write-Host ''
    if ($found.Count -gt 0) {
        Write-Host 'Найденные, но неподходящие интерпретаторы:' -ForegroundColor DarkGray
        $found | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    } else {
        Write-Host 'Ни одного интерпретатора Python в системе не обнаружено.' -ForegroundColor DarkGray
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Write-Host ''
        Write-Host 'Что видит лаунчер py:' -ForegroundColor DarkGray
        try { & py --list 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } } catch { }
    }
    exit 1
}

# ── Виртуальное окружение ────────────────────────────────────────────────
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host '-> Создаю виртуальное окружение...' -ForegroundColor Cyan
    $rest = if ($python.Count -gt 1) { $python[1..($python.Count - 1)] } else { @() }
    & $python[0] @rest -m venv .venv
}

if (-not (Test-Path $venvPython)) {
    Write-Host ''
    Write-Host 'Не удалось создать виртуальное окружение.' -ForegroundColor Red
    Write-Host 'Возможные причины:' -ForegroundColor Red
    Write-Host '  - Python установлен без компонента venv (переустановите с python.org)' -ForegroundColor Red
    Write-Host '  - папка проекта доступна только для чтения' -ForegroundColor Red
    Write-Host '  - антивирус заблокировал создание файлов' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Попробуйте удалить папку .venv и запустить заново.' -ForegroundColor Yellow
    exit 1
}

Write-Host '-> Проверяю зависимости (первый раз - несколько минут)...' -ForegroundColor Cyan
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Не удалось установить зависимости. Проверьте подключение к интернету.' -ForegroundColor Red
    Write-Host 'Если вы за корпоративным прокси, задайте переменные HTTP_PROXY и HTTPS_PROXY.' -ForegroundColor Yellow
    exit 1
}

# ── Распознавание фото и сканов ──────────────────────────────────────────
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Host ''
    Write-Host 'Tesseract не найден: фотографии и сканы распознаваться не будут.' -ForegroundColor Yellow
    Write-Host 'Всё остальное - реестр, сроки, флажки, PDF с текстовым слоем, DOCX - работает.' -ForegroundColor Yellow
    Write-Host 'Чтобы включить распознавание, установите:' -ForegroundColor Yellow
    Write-Host '  https://github.com/UB-Mannheim/tesseract/wiki' -ForegroundColor Yellow
    Write-Host 'При установке отметьте Russian в списке Additional language data.' -ForegroundColor Yellow
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
Write-Host "-> Открываю $url" -ForegroundColor Green
Write-Host '   Это окно закрывать нельзя: пока оно открыто, система работает.' -ForegroundColor DarkGray
Write-Host '   Остановить: Ctrl+C' -ForegroundColor DarkGray
Write-Host ''

# Открытие браузера - удобство, а не необходимость: если оно запрещено
# политикой, запуск всё равно должен состояться.
try {
    Start-Job -ScriptBlock {
        param($u); Start-Sleep -Seconds 4; Start-Process $u
    } -ArgumentList $url | Out-Null
} catch {
    Write-Host "   Браузер не открылся сам - откройте $url вручную." -ForegroundColor DarkGray
}

Push-Location backend
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
Pop-Location
