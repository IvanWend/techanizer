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
        ext["LLM extraction + Pydantic validation<br/>(DeepSeek V4.1 Flash)<br/>→ structured roles + verbatim quotes"]
    end
    subgraph STORE["Storage"]
        db[("Postgres + pgvector<br/>text-embedding-v4 (1024-dim)")]
    end
    subgraph QUERY["Query"]
        sql["SQL analytics<br/>(skill freq, salary)"]
        vec["Hybrid search<br/>(pgvector + tsvector, RRF)"]
    end
    subgraph AGENT["Agent"]
        agent["Two-stage agent<br/>(GLM 5.3 → Qwen Flash)<br/>tool-calling → payload → prose"]
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


1. На вход в Postgres поступают сырые данные из 4 источников.
2. DeepSeek V4.1 Flash извлекает структуру по системному промпту, вывод проходит Pydantic-валидацию; после нормализации данные сохраняются в таблицу структурированных ролей.
3. Хранение: каждая роль получает эмбеддинг text-embedding-v4 и сохраняется в pgvector.
4. Запросы: SQL-аналитика (частота навыков, распределения зарплат с медианами); гибридный поиск (векторный и лексический).
5. Агент, две стадии. Первая — GLM 5.3, вызов инструментов: модель выбирает из трёх (SQL-запрос, векторный поиск, детальная о роли) и заполняет payload (таблицы, цитаты, факты). Вторая — Qwen 3.8 Flash: превращает payload в текст ответа на нужном языке.
6. Вывод: FastAPI стримит ответ по SSE в React-интерфейс; тот же набор инструментов доступен отдельным MCP-сервером.
