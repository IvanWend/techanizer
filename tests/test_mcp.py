import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool

from techanizer.mcp.server import mcp, sql_query


def _listed() -> dict[str, Tool]:
    # FastMCP 1.29: list_tools is async, and the schema attribute is inputSchema
    # (camelCase). No DB — this is the contract the host sees, not a query.
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def test_tool_schemas() -> None:
    tools = _listed()
    assert set(tools) == {"sql_query", "vector_search", "role_detail"}

    sql = tools["sql_query"].inputSchema
    assert sql["properties"]["query"]["enum"] == [
        "skill_frequency",
        "salary_by_currency",
        "salary_by_seniority",
        "top_salaries",
    ]
    assert sql["properties"]["limit"]["default"] == 20
    assert sql["properties"]["currency"]["default"] == "USD"
    assert sql["required"] == ["query"]
    desc = (tools["sql_query"].description or "").lower()
    for fact in ("monthly", "postings", "currency"):
        assert fact in desc

    search = tools["vector_search"].inputSchema["properties"]
    assert set(search) == {"query", "limit", "seniority", "remote"}
    assert search["seniority"]["anyOf"][0]["items"]["enum"] == [
        "intern",
        "junior",
        "mid",
        "senior",
        "staff+",
    ]
    assert search["remote"]["anyOf"][0]["items"]["enum"] == ["remote", "hybrid", "onsite"]
    desc = (tools["vector_search"].description or "").lower()
    for fact in ("similarity", "monthly"):
        assert fact in desc

    detail = tools["role_detail"].inputSchema
    assert set(detail["properties"]) == {"raw_posting_id", "role_index"}
    assert detail["properties"]["raw_posting_id"]["type"] == "integer"
    assert detail["properties"]["role_index"]["type"] == "integer"
    assert detail["required"] == ["raw_posting_id", "role_index"]
    desc = (tools["role_detail"].description or "").lower()
    for fact in ("source_quotes", "raw_posting_id", "monthly"):
        assert fact in desc


def test_sql_query_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Connect is call-time. Importing the module must not need DATABASE_URL.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ToolError, match="DATABASE_URL"):
        sql_query(query="skill_frequency")
