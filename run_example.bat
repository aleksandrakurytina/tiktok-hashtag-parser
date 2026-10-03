@echo off
chcp 65001 >nul
call .venv\Scripts\activate.bat 2>nul

echo === 1) Демо-прогон без интернета (проверка, что всё работает) ===
python tt_parser.py --demo --tags работа,заработок,удаленка --limit 8
echo.

echo === 2) Боевой пример (раскомментируй нужную строку и сохрани файл) ===
echo python tt_parser.py --tags работа,заработок,удаленка --limit 30 --no-headless
echo python tt_parser.py --save-login session.json
echo python tt_parser.py --tags заработок --limit 30 --storage-state session.json --no-headless
echo.
pause
