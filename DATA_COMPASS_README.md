# Data Compass Converter

Локально преобразует один export Data Compass / Apache Superset `.zip` в один компактный `.docx` для ручного импорта в Atlassian Confluence Data Center.

## Запуск EXE

1. Скачайте `DataCompassConverter.exe` из ветки `test-source-types`.
2. Скопируйте его в локальную папку VDI.
3. Запустите двойным щелчком.
4. Выберите export `.zip`.
5. Выберите место сохранения `.docx`.

Python и установка библиотек на VDI не нужны. EXE не скачивает зависимости, не подключается к Data Compass, БД или внешним сервисам.

## Запуск из исходного кода

```powershell
python -m pip install -e .
python -m data_compass
```

CLI:

```powershell
data-compass-doc dashboard_export.zip dashboard_documentation.docx
```

Коды завершения: `0` — OK, `1` — неверный вход, `2` — PARTIAL, `3` — ошибка сохранения, `4` — блокировка политики VDI, `5` — повреждённая EXE-сборка.

Поддержка считается предварительной до проверки на разрешённом обезличенном корпоративном export и ручного импорта результата в Confluence.
