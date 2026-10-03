@echo off
chcp 65001 >nul
echo ============================================
echo   TikTok hashtag parser - установка
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python не найден в PATH.
    echo     Установи Python 3.10+ с https://www.python.org/downloads/
    echo     и поставь галочку "Add python.exe to PATH" при установке.
    pause
    exit /b 1
)

echo [1/4] Создаю виртуальное окружение .venv ...
if not exist .venv python -m venv .venv

echo [2/4] Активирую .venv ...
call .venv\Scripts\activate.bat

echo [3/4] Ставлю зависимости ...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
    echo [!] Не удалось поставить зависимости.
    pause
    exit /b 1
)

echo [4/4] Скачиваю браузер Chromium для Playwright (~170 MB) ...
python -m playwright install chromium
if errorlevel 1 (
    echo [!] Браузер не скачался. Проверь интернет/прокси и повтори.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Готово! Дальше запускай run_example.bat
echo ============================================
pause
