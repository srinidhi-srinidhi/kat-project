"""SQL Validator - parses LLM-generated SQL and admits only a bounded SELECT over the caller's curated views.

Agent-layer guard between SQL generation and execution for query_my_data: it parses the statement with sqlglot, rejects anything but one SELECT over the scope's chaatie_ro views, checks functions, columns and joins against allow-lists, then pins the schema and a row limit.

Features:
    - Exactly one statement, and it must be a SELECT or a set operation of SELECTs
    - Write, DDL, SET, COPY, SELECT INTO, row locks, parameters and recursive CTEs rejected
    - Tables limited to the scope's chaatie_ro views (CTE names aside); table functions rejected
    - Function allow-list by sqlglot class, so pg_sleep, set_config, current_setting and dblink never pass
    - Column allow-list from the catalogue plus the query's own aliases, catching hallucinated columns
    - Join cap and a mandatory ON/USING, so no cartesian products
    - LIMIT added or lowered to the row cap; comments stripped from the executed SQL
    - Syntactic validity is never enough: the read-only role and scoped views remain the primary boundary

Classes:
    ValidationResult: Outcome of one validation - ok flag, rewritten SQL, reason and views read

Functions:
    validate_sql(): Validate and rewrite one generated statement for a scope

Dependencies:
    - sqlglot: Postgres-dialect parsing and SQL regeneration
    - app.agent_infrastructure.data_query.catalog: the view and column allow-lists

Updates:
    27-Oct-2026: Initial sqlglot validator for query_my_data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.agent_infrastructure.data_query.catalog import SCHEMA, allowed_columns

MAX_SQL_CHARS = 4000
MAX_JOINS = 3

_ALLOWED_FUNCTIONS = frozenset({
    "And", "Or", "Xor", "RegexpLike", "RegexpILike",
    "Abs", "Avg", "Case", "Cast", "Ceil", "Coalesce", "Concat", "Count", "CurrentDate",
    "CurrentTimestamp", "Date", "DateTrunc", "DenseRank", "Extract", "Floor", "Greatest",
    "GroupConcat", "If", "Initcap", "Least", "Length", "Lower", "Max", "Min", "Nullif",
    "Rank", "Round", "RowNumber", "Substring", "Sum", "TimeToStr", "TimestampTrunc",
    "Trim", "TryCast", "Upper",
})

_FORBIDDEN_NODE_NAMES = (
    "Alter", "AlterTable", "Analyze", "Cache", "Command", "Commit", "Copy", "Create",
    "Delete", "Describe", "Drop", "Grant", "Insert", "Into", "Kill", "LoadData", "Lock",
    "Merge", "Parameter", "Placeholder", "Pragma", "Refresh", "Revoke", "Rollback", "Set",
    "Show", "Transaction", "TruncateTable", "Uncache", "Update", "Use",
)
_FORBIDDEN_NODE_TYPES = tuple(
    node_type for node_type in (getattr(exp, name, None) for name in _FORBIDDEN_NODE_NAMES)
    if isinstance(node_type, type)
)
_QUERY_ROOT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one generated statement.

    Attributes:
        ok: True when the statement may be executed.
        sql: The rewritten, schema-qualified and row-limited SQL to execute (empty when rejected).
        reason: Why the statement was rejected, phrased so the generator can correct it.
        views: The chaatie_ro views the statement reads.
    """

    ok: bool
    sql: str = ""
    reason: str = ""
    views: Tuple[str, ...] = ()


def _reject(reason: str) -> ValidationResult:
    """Return a failed ValidationResult carrying the given reason."""
    return ValidationResult(ok=False, reason=reason)


def validate_sql(sql: str, scope: str, max_rows: int) -> ValidationResult:
    """Validate one generated statement for a member or provider scope and return it rewritten for execution.

    Args:
        sql: The SQL text the model produced.
        scope: 'member' or 'provider'; selects which views and columns are allowed.
        max_rows: Row cap applied as LIMIT.

    Returns:
        A ValidationResult; ok is False with a reason whenever any rule fails.
    """
    if not sql or not sql.strip():
        return _reject("No SQL was produced.")
    if len(sql) > MAX_SQL_CHARS:
        return _reject(f"The SQL is longer than {MAX_SQL_CHARS} characters.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as e:
        return _reject(f"The SQL could not be parsed: {str(e)[:200]}")
    if len(statements) != 1:
        return _reject("Exactly one SQL statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, _QUERY_ROOT_TYPES):
        return _reject("Only a SELECT query is allowed.")

    with_clause = tree.args.get("with")
    if with_clause is not None and with_clause.args.get("recursive"):
        return _reject("Recursive CTEs are not allowed.")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            return _reject(f"{type(node).__name__} is not allowed in a read-only query.")
        if isinstance(node, exp.Func) and type(node).__name__ not in _ALLOWED_FUNCTIONS:
            name = node.name if isinstance(node, exp.Anonymous) else type(node).__name__
            return _reject(f"The function {name} is not allowed.")

    allowed = allowed_columns(scope)
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used_views = set()
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return _reject("Table functions are not allowed; read only the listed views.")
        if table.catalog:
            return _reject("Database-qualified names are not allowed.")
        name = table.name.lower()
        schema = (table.db or "").lower()
        if not schema and name in cte_names:
            continue
        if schema not in ("", SCHEMA):
            return _reject(f"Only {SCHEMA} views may be queried.")
        if name not in allowed:
            return _reject(f"{name} is not an available view. Available views: {', '.join(sorted(allowed))}.")
        used_views.add(name)
        table.set("db", exp.to_identifier(SCHEMA))
    if not used_views:
        return _reject("The query must read at least one of the listed views.")

    known_columns = set().union(*(allowed[view] for view in used_views))
    aliases = {alias.alias.lower() for alias in tree.find_all(exp.Alias) if alias.alias}
    for table_alias in tree.find_all(exp.TableAlias):
        if table_alias.name:
            aliases.add(table_alias.name.lower())
        aliases.update(column.name.lower() for column in table_alias.columns)
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        name = column.name.lower()
        if name and name not in known_columns and name not in aliases:
            return _reject(f"Unknown column {column.name}; use only the columns listed for {', '.join(sorted(used_views))}.")

    joins = list(tree.find_all(exp.Join))
    if len(joins) > MAX_JOINS:
        return _reject(f"At most {MAX_JOINS} joins are allowed.")
    if any(not (join.args.get("on") or join.args.get("using")) for join in joins):
        return _reject("Every join needs an ON or USING condition.")

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows, copy=False)
    else:
        value = limit.args.get("expression")
        if not (isinstance(value, exp.Literal) and value.is_int):
            return _reject("LIMIT must be a whole number.")
        if int(value.this) > max_rows:
            limit.set("expression", exp.Literal.number(max_rows))

    return ValidationResult(
        ok=True,
        sql=tree.sql(dialect="postgres", comments=False),
        views=tuple(sorted(used_views)),
    )
