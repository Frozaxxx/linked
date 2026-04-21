# SEO Linked

Упрощенный анализатор внутренней перелинковки. Backend лежит в корне проекта, frontend отдается тем же FastAPI-приложением.

## Запуск

Сайт и API:

```bash
python main.py
```

После запуска:

- Frontend: `http://127.0.0.1:8000/`
- Swagger UI: `http://127.0.0.1:8000/docs`
- Browser health: `GET http://127.0.0.1:8000/health/browser`
- Analyze endpoint: `POST http://127.0.0.1:8000/analyze`

CLI-анализ одной страницы:

```bash
python main.py "https://example.com/catalog/target-page" --pretty
```

Если нужен прямой запуск через uvicorn:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

На Windows лучше не использовать `--reload`, потому что Playwright стабильнее работает без reloader-процесса.

## Browserless

Backend сначала пробует подключиться к browserless из `docker-compose.yml`:

```bash
docker compose up -d browserless
```

По умолчанию используется:

```text
ws://localhost:3000?token=seo-linked-dev-token
```

Endpoint можно переопределить через `.env`: `CHROMIUM_WS_ENDPOINT`, `FETCH_BROWSER_WS_ENDPOINT` или `BROWSER_WS_ENDPOINT`.

## Frontend

Frontend запускается вместе с backend на `http://127.0.0.1:8000/`.

Vite для запуска не нужен. Страница остается React-приложением: React подключается в `frontend/index.html`, а код интерфейса лежит в `frontend/src/main.jsx`.

## Product Rules

- В финальном ответе нужно показывать 3 рекомендации, когда это технически возможно.
- Если строгая семантика нашла меньше 3 доноров, список добирается только URL из родительской ветки целевой страницы.
- Нельзя добирать рекомендации случайными служебными страницами, главной страницей, disclaimer/contact/policy и похожими URL.
- Большое количество внутренних ссылок и красивый заголовок не являются причинами для выбора донора.

## Основные Файлы

- `main.py` - единая точка входа: web server без аргументов, CLI при передаче URL.
- `api.py` - FastAPI routes для `/`, `/docs`, `/health/browser`, `/analyze`.
- `crawler_core/` - Playwright crawl HTML-страниц, HTTPX только для `robots.txt` и `sitemap.xml`.
- `semantic_core/` - локальный ranking top-5 и section-aware семантика.
- `llm.py` - LLM rerank top-5 в top-3 и финальное сообщение.
- `prompts.py` - промпты отдельно от config/settings.
- `config.py` - runtime-константы и чтение `.env`.
- `models.py` - Pydantic-схемы результата.
- `frontend/` - React-интерфейс под текущий backend.
