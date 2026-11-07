# Полный прогон DEV: Qwen3.5-9B через OpenRouter, чек-лист v2 (08.11.2026)

Конфигурация: `mode=local`, модель `qwen/qwen3.5-9b` (OpenRouter, reasoning выключен, structured outputs обязательны),
чек-лист v2 = `eval/default_checklist.json` (в БД — версия id=2). `qwen/qwen3-8b` на OpenRouter без провайдеров — недоступна.

Команда (bash, из корня):
```
PYTHONPATH=backend LOCAL_LLM_BACKEND=openai LOCAL_LLM_BASE_URL=https://openrouter.ai/api/v1 \
LOCAL_MODEL=qwen3.5-9b LOCAL_MODEL_TAGS="qwen3.5-9b=qwen/qwen3.5-9b" LOCAL_CONCURRENCY=8 \
python -m app.cli run --group-id DEV --dataset DEV --skip-fetch --mode local --checklist-version 2 --force --expect 65 \
  --out eval/runs/2026-11-08_qwen3.5-9b_openrouter_checklist-v2/results.json
```

## Итоги

| Метрика | Значение |
|---|---|
| Диалогов / ошибок | 65 / 0, JSON с первой попытки у всех 65 |
| Время | 90 с на весь набор (8 параллельно), 12–17 с на диалог |
| Стоимость | $0.044 по данным OpenRouter ($0.0545 по оценке приложения) |
| Валидация results.json | пройдена |
| Совпадение с ответами, которые задаёт сам чек-лист в пограничных случаях | 24 из 32 |
| `needs_review` | 52 из 65 диалогов: флаг почти ничего не отсеивает |

## Ошибки модели

1. **`identification = false` у 22 диалогов.** Ожидается 3 (p1-ru-009, p1-kz-015, p1-en-014). Модель считает «раскрытием
   данных счёта» любые инструкции по навигации: где тариф, где вход, путь в меню. Чек-лист прямо относит такое к незащищённому.
   Это самая дорогая ошибка: вес 20 вместо N/A роняет итог на 20+ баллов.
2. **`critical = true` у 11 диалогов.** Пять верные (p1-ru-022/023/029, p1-en-013 и пропущенный p1-kz-013 — см. ниже).
   Ещё 6 — следствие пункта 1: «раскрытие без идентификации» модель переносит в critical, вопреки D1.
3. Пропущены: p1-kz-013 `critical` (казахское предложение платной консультации), p1-ru-016 и p1-en-015 `discovery`,
   p1-ru-025 `consultation` (должно быть N/A), p1-ru-026 и p1-en-007 `consultation` завышены, p1-ru-005 `dialogue` 7 вместо 4,
   p1-kz-008 `closure`.

Вывод: эталоном этот прогон не является. Это базовая линия малой модели на новом чек-листе. Для эталона нужен облачный
судья (cloud/hybrid) или ручная разметка `eval/gold/dev_gold.json` (сейчас там все 224 значения TODO).
