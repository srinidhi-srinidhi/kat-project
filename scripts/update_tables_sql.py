"""Schema Snapshot Script - regenerates tables.sql from the live database schema.

One-off script that connects to the configured Postgres database and writes extensions plus the functions, tables, indexes, triggers, foreign keys and views of the public, notifications and scheduler schemas into tables.sql as a snapshot, not a migration.

Features:
    - Introspects pg_extension, pg_proc, information_schema, pg_constraint, pg_indexes, pg_trigger and pg_views
    - Emits CREATE ... IF NOT EXISTS tables with non-FK constraints, and foreign keys as trailing ALTER TABLE statements
    - Covers the public, notifications and scheduler schemas, creating the non-public schemas first
    - Connects over SSL with the DB_* settings from app.core.config

Functions:
    get_db_connection(): Open an SSL asyncpg connection from the configured DB settings
    fetch_extensions(): CREATE EXTENSION statements for every installed extension except plpgsql
    fetch_functions(): Function and procedure definitions in a schema
    fetch_tables_and_columns(): CREATE TABLE statements with columns and non-FK constraints
    fetch_indexes(): Index definitions in a schema, excluding primary-key indexes
    fetch_triggers(): Non-internal trigger definitions in a schema
    fetch_foreign_keys(): ALTER TABLE ADD statements for every foreign key in a schema
    fetch_views(): CREATE OR REPLACE VIEW statements for every view in a schema
    main(): Introspect every schema and write the snapshot to tables.sql

Dependencies:
    - asyncio: Runs the async entry point
    - asyncpg: Database connection and catalogue queries
    - urllib.parse: Quotes credentials into the DSN
    - app.core.config.settings: DB host, port, name, user and password

Updates:
    25-Sep-2026: Added module docstring.
"""

import asyncio
import asyncpg
import os
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from app.core.config import settings

OUTPUT_FILE = project_root / "tables.sql"
SCHEMAS = ['public', 'notifications', 'scheduler']

async def get_db_connection():
    """Open an SSL asyncpg connection built from the DB settings."""
    user = urllib.parse.quote_plus(settings.DBUSERNAME)
    password = urllib.parse.quote_plus(settings.DBPASSWORD)
    
    dsn = (
        f"postgresql://{user}:{password}"
        f"@{settings.DBHOST}:{settings.DBPORT}/{settings.DATABASENAME}"
    )
    return await asyncpg.connect(dsn, ssl='require')

async def fetch_extensions(conn):
    """Return a CREATE EXTENSION statement for every installed extension except plpgsql."""
    rows = await conn.fetch("SELECT extname FROM pg_extension WHERE extname != 'plpgsql'")
    return [f"CREATE EXTENSION IF NOT EXISTS {r['extname']};" for r in rows]

async def fetch_functions(conn, schema='public'):
    """Return the definitions of every function and procedure in a schema."""
    query = """
        SELECT pg_get_functiondef(p.oid) as func_def
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = $1
        AND p.prokind IN ('f', 'p')
    """
    rows = await conn.fetch(query, schema)
    return [f"{r['func_def']};" for r in rows]

async def fetch_tables_and_columns(conn, schema='public'):
    """Return a CREATE TABLE statement with columns and non-FK constraints for every table in a schema."""
    tables_query = """
        SELECT tablename 
        FROM pg_tables 
        WHERE schemaname = $1
        ORDER BY tablename
    """
    tables = await conn.fetch(tables_query, schema)
    
    definitions = []
    
    for t_row in tables:
        table_name = t_row['tablename']
        
        cols = await conn.fetch("""
            SELECT column_name, data_type, udt_name, character_maximum_length, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
        """, schema, table_name)
        
        col_defs = []
        for c in cols:
            c_type = c['data_type']
            if c['data_type'] == 'ARRAY':
                c_type = f"{c['udt_name'].replace('_', '')}[]"
            elif c['data_type'] == 'USER-DEFINED' and c['udt_name'] == 'vector':
                c_type = "vector" 
            elif c['character_maximum_length']:
                c_type = f"{c_type}({c['character_maximum_length']})"
            
            nullable = "NULL" if c['is_nullable'] == 'YES' else "NOT NULL"
            default = f"DEFAULT {c['column_default']}" if c['column_default'] else ""
            
            col_defs.append(f"    {c['column_name']} {c_type} {nullable} {default}".strip())
            
        cons = await conn.fetch("""
            SELECT pg_get_constraintdef(c.oid) as con_def, c.contype
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE t.relname = $1 AND n.nspname = $2 AND c.contype != 'f'
        """, table_name, schema)
        for c in cons:
            col_defs.append(f"    {c['con_def']}")
            
        create_stmt = f"CREATE TABLE IF NOT EXISTS {schema}.{table_name} (\n" + ",\n".join(col_defs) + "\n);"
        definitions.append(create_stmt)
        
    return definitions

async def fetch_indexes(conn, schema='public'):
    """Return the index definitions in a schema, excluding primary-key indexes."""
    query = """
        SELECT indexdef 
        FROM pg_indexes 
        WHERE schemaname = $1 
        AND indexname NOT IN (SELECT conname FROM pg_constraint WHERE contype = 'p')
    """
    rows = await conn.fetch(query, schema)
    return [f"{r['indexdef']};" for r in rows]

async def fetch_triggers(conn, schema='public'):
    """Return the definitions of every non-internal trigger in a schema."""
    query = """
        SELECT pg_get_triggerdef(t.oid) as trig_def
        FROM pg_trigger t
        JOIN pg_class c ON t.tgrelid = c.oid
        JOIN pg_namespace n ON c.relnamespace = n.oid
        WHERE n.nspname = $1 AND t.tgisinternal = false
    """
    rows = await conn.fetch(query, schema)
    return [f"{r['trig_def']};" for r in rows]

async def fetch_foreign_keys(conn, schema='public'):
    """Return an ALTER TABLE ADD statement for every foreign key in a schema."""
    query = """
        SELECT 
            cl.relname as table_name,
            pg_get_constraintdef(c.oid) as con_def
        FROM pg_constraint c
        JOIN pg_class cl ON c.conrelid = cl.oid
        JOIN pg_namespace n ON cl.relnamespace = n.oid
        WHERE c.contype = 'f' AND n.nspname = $1
    """
    rows = await conn.fetch(query, schema)
    return [f"ALTER TABLE {schema}.{r['table_name']} ADD {r['con_def']};" for r in rows]

async def fetch_views(conn, schema='public'):
    """Return a CREATE OR REPLACE VIEW statement for every view in a schema."""
    query = """
        SELECT viewname, definition
        FROM pg_views
        WHERE schemaname = $1
        ORDER BY viewname
    """
    rows = await conn.fetch(query, schema)
    return [
        f"CREATE OR REPLACE VIEW {schema}.{r['viewname']} AS\n{r['definition'].rstrip(';')};"
        for r in rows
    ]

async def main():
    """Introspect the public, notifications and scheduler schemas and write the snapshot to tables.sql."""
    print(f"Connecting to database at {settings.DBHOST}...")
    try:
        conn = await get_db_connection()
    except Exception as e:
        print(f"Error connecting to DB: {e}")
        return

    print("Introspecting database schema...")
    
    sql_lines = ["-- Etown Database Schema Snapshot", f"-- Generated on {datetime.now()}", ""]
    
    print("- Fetching extensions...")
    exts = await fetch_extensions(conn)
    sql_lines.extend(exts)
    sql_lines.append("")

    for schema in SCHEMAS:
        print(f"\n-- Schema: {schema} --")
        sql_lines.append(f"-- ============================================================")
        sql_lines.append(f"-- Schema: {schema}")
        sql_lines.append(f"-- ============================================================")
        sql_lines.append("")

        if schema != 'public':
            sql_lines.append(f"CREATE SCHEMA IF NOT EXISTS {schema};")
            sql_lines.append("")

        print(f"  - Fetching functions ({schema})...")
        funcs = await fetch_functions(conn, schema)
        sql_lines.extend(funcs)
        if funcs:
            sql_lines.append("")

        print(f"  - Fetching tables ({schema})...")
        tables = await fetch_tables_and_columns(conn, schema)
        sql_lines.extend(tables)
        if tables:
            sql_lines.append("")

        print(f"  - Fetching indexes ({schema})...")
        indexes = await fetch_indexes(conn, schema)
        sql_lines.extend(indexes)
        if indexes:
            sql_lines.append("")

        print(f"  - Fetching triggers ({schema})...")
        triggers = await fetch_triggers(conn, schema)
        sql_lines.extend(triggers)
        if triggers:
            sql_lines.append("")

        print(f"  - Fetching foreign keys ({schema})...")
        fks = await fetch_foreign_keys(conn, schema)
        sql_lines.extend(fks)
        if fks:
            sql_lines.append("")

        print(f"  - Fetching views ({schema})...")
        views = await fetch_views(conn, schema)
        sql_lines.extend(views)
        if views:
            sql_lines.append("")

    await conn.close()
    
    print(f"Writing schema to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sql_lines))
    
    print("Done! tables.sql has been updated.")

if __name__ == "__main__":
    asyncio.run(main())