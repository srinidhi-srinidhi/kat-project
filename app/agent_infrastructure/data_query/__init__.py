"""Chaatie Data Query Package - catalogue and validator behind the query_my_data tool.

Agent-layer package that lets Chaatie answer questions about the caller's own bookings, offers, views and ratings by generating SQL over curated chaatie_ro views, validating it, and running it on a separate read-only connection.

Features:
    - Flag-gated: DATA_QUERY_MODE off/shadow/live, default off; the tool is not registered when off.
    - Three boundaries: caller-scoped views, a read-only login role, and an AST validator.
    - Fail closed: no read-only credentials, a rejected statement or a costly plan means no answer.
    - Audited: every attempt is written to etown_data_query_audit.

Modules:
    catalog: The chaatie_ro views, their columns, rules and examples (the allow-list).
    validator: sqlglot validation and rewriting of one generated statement.

Dependencies:
    - sqlglot: SQL parsing for the validator.
    - app.db.client.ReadOnlyDatabase: the restricted execution pool (used by the tool handler).

Updates:
    27-Oct-2026: Initial package for query_my_data.

Notes:
    The orchestration lives in tools_utils/query_my_data.py; the views in scripts/2026_11_chaatie_data_query.sql.
"""
