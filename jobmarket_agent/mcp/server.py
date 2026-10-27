import os
from typing import Any, Literal

import psycopg
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from jobmarket_agent.agent.tools import (
    QueryName,
    make_role_detail,
    make_sql_query,
    make_vector_search,
)

mcp = FastMCP("techanizer")


def _connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ToolError("DATABASE_URL is not set")
    return psycopg.connect(url)


@mcp.tool(description=make_sql_query(object()).__doc__)
def sql_query(query: QueryName, *, limit: int = 20, currency: str = "USD") -> list[dict[str, Any]]:
    with _connect() as conn:
        return make_sql_query(conn)(query=query, limit=limit, currency=currency)


@mcp.tool(description=make_vector_search(object()).__doc__)
def vector_search(
    query: str,
    *,
    limit: int = 10,
    seniority: list[Literal["intern", "junior", "mid", "senior", "staff+"]] | None = None,
    remote: list[Literal["remote", "hybrid", "onsite"]] | None = None,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        return make_vector_search(conn)(
            query=query, limit=limit, seniority=seniority, remote=remote
        )


@mcp.tool(description=make_role_detail(object()).__doc__)
def role_detail(raw_posting_id: int, role_index: int) -> dict[str, Any] | None:
    with _connect() as conn:
        return make_role_detail(conn)(raw_posting_id=raw_posting_id, role_index=role_index)


if __name__ == "__main__":
    mcp.run()
