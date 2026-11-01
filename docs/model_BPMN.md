# Architecture (роль модели в техническом процессе)

Диаграмма основана на схеме потока данных из README проекта.

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        hn["Hacker News (Algolia)"]
        rem["Remotive (API)"]
        w3["Web3.career (API)"]
        hab["Habr Career (API)"]
    end
    subgraph INGEST["Ingestion"]
        raw[("Postgres raw postings<br/>idempotent upsert")]
    end
    subgraph EXTRACT["Extraction"]
        ext["LLM extraction + Pydantic validation<br/>(DeepSeek V4 Flash)<br/>→ structured roles + verbatim quotes"]
    end
    subgraph STORE["Storage"]
        db[("Postgres + pgvector<br/>bge-m3 (1024-dim)")]
    end
    subgraph QUERY["Query"]
        sql["SQL analytics<br/>(skill freq, salary)"]
        vec["HNSW vector search<br/>(semantic match)"]
    end
    subgraph AGENT["Agent"]
        agent["Two-stage agent<br/>(DeepSeek V4 Pro → Kimi/DS)<br/>tool-calling → payload → prose"]
    end
    subgraph SERVE["Serving"]
        ui["FastAPI + SSE · MCP server · React UI"]
    end

    hn & rem & w3 & hab --> raw
    raw --> ext
    ext --> db
    db --> sql & vec
    sql & vec --> agent
    agent --> ui
```

Краткое описание
- Задача модели: преобразовать вакансии из разных источников в единый структурированный формат, пригодный для фильтрации, аналитики и поиска.
- Почему используется LLM: описания вакансий различаются по структуре и языку; LLM может извлекать поля из свободного текста без отдельной модели для каждого источника.
- Вход: очищенный текст вакансии или комментария. Для некоторых источников часть полей уже приходит в структурированном виде и берётся непосредственно из данных источника.
- Выход: проверенный JSON с полями вакансии и фрагменты исходного текста; далее данные нормализуются и сохраняются.
- Использование результата: SQL-аналитика и векторный поиск предоставляют данные агенту, который формирует ответ на вопрос пользователя.
