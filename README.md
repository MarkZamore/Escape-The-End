# Escape The End

Rainline, выпуск 0.1. Minecraft 1.20.1, Forge 47.4.10, Java 17. На CurseForge версия представлена как «Escape The End-1.20.1.zip», имя загруженного автором файла `Escape The End-0.1.zip`. Точный архив занимает 60 449 095 байт, SHA‑256 `af7be8b4042f40f6839ada6b526803b4d4d06cee8955b2a3daead55283839294`. [Авторский выпуск CurseForge](https://www.curseforge.com/minecraft/modpacks/escape-the-end/files/9001599)

Будущая карточка занимает `minecraft-20` и использует отдельный путь `Escape The End`. Запись каталога заменяет строку Levitated одним изменением после приёмки. Без алиасов пути и источника лаунчер оставляет миры и установку Levitated в исходных папках. На выпущенном лаунчере 500 проверены раздельные папки миров и кеши источников. Если лаунчер когда-либо видел Levitated только как локальную сборку, она может остаться в локальном списке.

## Подготовка на стенде

Получите официальный ZIP и укажите путь к нему в `$authorZip`. Запускайте из корня чистых исходников LANMinecraft. `$stage` — новый каталог внутри принадлежащего задаче `artifacts/agent-runs/<task-id>` с `RUN.json`.

```powershell
python tools/workspace_storage.py --check
python -B tools/PackData/CurseForge/prepare.py --project-id 1712767 --file-id 9001599 --client-archive $authorZip --expect LocalSources/Escape-The-End/tools/upstream-client-lock.json --java-major 17 --placements LocalSources/Escape-The-End/tools/curseforge-placements.json --pack-source LocalSources/Escape-The-End --output $stage
```

Исходный lock содержит 147 обязательных зависимостей и хеши 1222 авторских файлов. Конфиги, задания, скрипты Rainline и те же девять ZIP‑зависимостей проверяются в отдельных папках стенда. В сборке закреплены параметры открытия мира и журналирования. `options.txt` не входит в список принудительно обновляемых файлов. Русский язык при первом запуске задаёт общий механизм лаунчера.

## Приёмка и публикация

Пройдите запуск точной версии Forge, создание мира, безопасное появление хоста и гостя на дальних островах Края, журнал заданий, сохранение и повторный вход. В паузе проверьте публикацию одним нажатием, отсутствие промежуточного окна, кнопку «Жалобы», сообщения о локальном порте и готовом Steam-адресе. Повторите в мирах с включёнными и отключёнными командами и проверьте реальную смену времени командой с клиента гостя. Сверьте локальный TCP, собственное Steam-лобби и отдельный прогон двух аккаунтов, если он согласован. Фактический ModList, официальные страницы, превью и QA-квитанции потребуются перед публикацией карточки. [Правила приёмки LANMinecraft](https://github.com/MarkZamore/LANMinecraft/blob/main/tools/ADDING-A-LIBRARY-PACK.md)

Права на полную копию для GitHub Releases пока не подтверждены. У авторского экспорта All Rights Reserved. Лицензия BSL требует отдельного разрешения для распространения. Несколько обязательных ресурспаков, включая Stay True, также обозначены All Rights Reserved. Текущий лаунчер скачивает игровой состав только из GitHub Releases. Проверка полного локального клиента и публикация карточки ещё впереди. [Сборка Rainline](https://www.curseforge.com/minecraft/modpacks/escape-the-end) · [Условия BSL](https://www.curseforge.com/minecraft/shaders/bsl-shaders#description) · [Stay True 1.20](https://www.curseforge.com/minecraft/texture-packs/stay-true/files/4624707)
