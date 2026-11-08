# Экран «Причины обращений»

Отдельная страница `/reasons.html` в дизайне основного интерфейса: подключает его собранный CSS (имя файла берётся
из `index.html`, поэтому пересборка не ломает страницу), те же иконки lucide (`icons.js`, извлечены из бандла),
ту же разметку оболочки, карточек, панелей, таблиц и тот же переключатель темы (`localStorage["cqa-theme"]`).
Свои стили (`reasons.css`) — только для полос долей и исходов и тепловой карты, на переменных приложения.

Данные: `GET /api/v1/analytics/reasons`, `GET /api/v1/reasons/taxonomy|versions`, `POST /api/v1/reasons/discover`,
`POST /api/v1/reasons/versions`, `POST /api/v1/reasons/versions/{id}/activate`, список групп — `GET /api/conversations`.

## Правки в собранном бандле — перенести в исходники интерфейса

Исходников React-интерфейса в репозитории нет, поэтому в `frontend/assets/index-*.js` внесены 4 точечные правки
(помечены комментарием `/*cqa-reasons-patch*/`). **При следующей сборке они пропадут** — добавьте их в исходники:

1. **Пункт меню.** В словарь подписей навигации (`dialogs/checklists/appeals/quality/submission`) добавить
   `reasons: "Причины обращений"`, в массив пунктов — `["reasons", ChartPie]` между `quality` и `submission`
   (иконка `ChartPie` из `lucide-react`).
2. **Переход.** В обработчике клика по пункту: `key === "reasons" ? location.assign("/reasons.html") : setPage(key)`.
3. **Вкладка из адреса.** Начальное значение активной вкладки: `location.hash` (`#checklists`, `#appeals`, `#quality`,
   `#submission`), иначе `dialogs`; после чтения — `history.replaceState(null, "", location.pathname)`.
   Нужно для меню на странице причин.
4. **Диалог из адреса.** Начальное значение выбранного диалога: `#dialog=<conversation id>` → открыть карточку
   (и так же очистить адрес). Нужно для ссылок «диалоги с провалом» на странице причин.
