# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FastAPI backend for **Etown** — a local service marketplace (members find/book providers; providers manage listings, offers, bookings). It hosts a LangGraph conversational agent ("Chaatie"), pgvector semantic search, a notification centre (push/SMS/WhatsApp/email), and a background mirror into Microsoft Dataverse (the legacy CRM). Python 3.11, asyncpg over Azure PostgreSQL + pgvector.

## Commands

```powershell
# Dev server (matches .vscode/launch.json)
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001

# Install (uv is what the Dockerfile uses)
uv pip install -r pyproject.toml
pip install -e ".[dev]"        # adds pytest, pytest-asyncio, pytest-cov

# Tests — three files under tests/unit_tests/ have content: test_notification_templates.py,
# test_chaatie_issue_fixes.py, test_product_search_relevance.py. unit_test.py,
# integration_tests/integration_test.py and security_tests/security_test.py are empty stubs,
# as are tests/pytest.ini and tests/conftest.py (no shared fixtures). Run from repo root.
pytest tests/ -v
pytest tests/unit_tests/test_notification_templates.py::test_name -v

# Convention checker (comments, SQL comments, docstring rules) — run before every commit
python scripts/check_conventions.py                 # all tracked .py files
python scripts/check_conventions.py app/etown/user  # just the paths you touched

# Regenerate the schema snapshot in tables.sql (public, notifications, scheduler schemas)
python scripts/update_tables_sql.py

# Container (prod runs gunicorn + 4 uvicorn workers on :80)
docker build -t etown-agents-api:latest .
```

There is no linter, formatter, type-checker, or CI config in the repo (`scripts/check_conventions.py` is the only automated check) — the README's sections on Black/mypy/pre-commit/GitHub Actions describe an aspiration, not reality. `docker/docker-compose.yml` is an empty file, and the `/health`, `/api/v1/health/db` endpoints the README mentions do not exist (`GET /` is the only health check; `GET /api/logs/health` covers the logging subsystem).

## Environment targeting

`app/core/config.py` runs `load_env_file()` at import and builds a module-level `settings` singleton. Load order: `.env.local` → `.env` → `.env.<APP_ENV>.local` → `.env.<APP_ENV>`.

**`.env` is tracked in git** (`.gitignore` excludes `.env.*` but not `.env`), so it is the de-facto dev config and secrets have historically landed in history. Put new secrets in `.env.development` / `.env.production`.

To run anything against production, pass the file explicitly (e.g. `--env-file .env.production` in one-off scripts) — setting `APP_ENV=production` alone is unreliable because `load_dotenv` calls here don't override already-set vars.

Secrets resolve through `Settings._fetch_secret_from_keyvault(secret_name, fallback_env_var, vault_url)`: Azure Key Vault when `AZURE_KV_*` is fully configured, otherwise the named env var. A wrong secret name fails **silently** back to the env var — check the `[config]` prints on startup when a key seems stale. DB env vars are `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` (bare `PORT`/`USER` are reserved on Azure App Service).

## Architecture

### Request path — three layers, strictly

1. `app/api/v1/endpoints/<domain>.py` — HTTP shape only: validation, `Depends(verify_token)`, response models, scheduling `BackgroundTasks`.
2. `app/etown/<domain>/<domain>_utils.py` — all Postgres business logic. This is where the real work lives; endpoints stay thin.
3. `app/db/client.py` — `Database` classmethods (`fetch`, `fetchrow`, `fetchval`, `execute`, `execute_queries`, `fetch_once`, `open_listener_connection`). Single shared asyncpg pool, sized from `DB_POOL_TOTAL_MAX_CONN / WEB_CONCURRENCY`.

`Database.fetch` retries transient errors via tenacity — 5 attempts total (`stop_after_attempt(5)`), 0.2s→5s exponential backoff — and a server-side statement timeout counts as transient, which multiplies load on already-slow queries. Expensive read paths must use **`Database.fetch_once`** (no retry). AI search and other heavy paths already do.

`app/api/v1/router.py` mounts every endpoint router, and `main.py` mounts that at `/api`. **Routes are `/api/<prefix>/...`, not `/api/v1/...`** despite the package name.

### Dataverse mirror

Alongside `<domain>_utils.py` sits `<domain>_syncing.py`. Postgres is written first and is authoritative for the API; the CRM PATCH/POST is fired as a `BackgroundTask` that never raises, so a mirror failure can't break a response. Consequences worth knowing before debugging data drift:

- A PG-only repair to a mirrored column gets reverted the next time the CRM mirrors back — patch both sides in the same pass.
- The mirror is lossy in the CRM→PG direction; missing contact/userprofile/member rows are often lag, not absence.
- Concurrent mirror writes can 404 on `@odata.bind` while still returning 200.

Field names differ per entity (`mobilephone` / `ets_mobilenumber` / `ets_mobile`), which is why payloads are built per-entity. `scripts/export_dataverse_schema.py` dumps `$metadata` into `scripts/dataverse_schema.json` — consult it rather than guessing a field name or type.

### Agent layer (`app/agent_infrastructure/`)

- `agents/etown_agent.py` — three LangGraph agents built with `create_agent(model=gpt41, tools=…, middleware=[dynamic_prompt])`: member, provider, automation. Role differences are enforced in the prompt (providers cannot search or book) **and** by giving each agent a different tool list.
- `tools/tools.py` — every `@tool` definition plus the `member_tools` / `provider_tools` / `automation_tools` registries at the bottom. Implementations live in `tools/tools_utils/<tool>.py`.
- `tools/context_manager.py` — tools do **not** take `contact_id`/`user_profile_id` as LLM-supplied arguments. `chat.py` calls `set_execution_context(...)` before invoking the agent, and tools read identity via `get_context_value()` (contextvars, request-scoped). Clear it in a `finally`.
- `classification/` — a two-stage intent classifier wired in at the pre-LLM seam in `chat.py`, gated by `CLASSIFIER_MODE` (`off` / `shadow` / `live`). `shadow` only logs what it would have done; `live` can dispatch a tool and skip the LLM. Fail-open: any exception returns `None` and the normal LLM path runs. `rules.py` matches frontend template prompts and closed-set shapes deterministically (no embedding call, arguments parsed out of the prompt) and carries most of the traffic; the k-NN vote handles the rest. Only **read-only** intents are in `taxonomy.DISPATCHABLE_INTENTS` — a mis-dispatch must never cost a write.
  - **It is inert without data.** Retrieval reads `etown_intent_examples WHERE is_active`; if that count is 0 (a DB wipe, or a table only ever filled by the re-mining worker, which stages proposals `is_active=FALSE`) every turn returns `no_neighbors` and the LLM silently answers everything. Seed with `scripts/classification/seed_intent_examples.py`, and check `decision_reason` in `etown_classification_decisions` before assuming the gate is mistuned. `scripts/classification/evaluate_classifier.py` is the tuning authority for the constants in `config.py`.
- `query_my_data` (dynamic data questions) — an agent tool, not a classifier intent, gated by `DATA_QUERY_MODE` (`off` / `shadow` / `live`, default `off`; when off the tool isn't registered and the prompt forbids guessing own-data numbers). gpt-4.1 at temperature 0 writes one SELECT over the caller-scoped views in schema `chaatie_ro` (`scripts/2026_11_chaatie_data_query.sql`, described in `data_query/catalog.py`), `data_query/validator.py` (sqlglot) admits only allow-listed views, columns and functions and forces a LIMIT, and `ReadOnlyDatabase` in `app/db/client.py` runs it as the low-privilege login `DATA_QUERY_DB_USER` in a read-only transaction with the caller's ids bound via `set_config`, a statement timeout and an EXPLAIN cost cap. It is inert without that login: no credentials means no answer. Every attempt lands in `etown_data_query_audit`. Change a view and its catalogue entry together; `tests/unit_tests/test_data_query_validator.py` checks the catalogue examples still validate.
- `infrastructure/llm_clients.py` — the only place models are instantiated (Azure OpenAI `gpt41`, `gpt4o_mini`; Gemini flash + `gemini-embedding-001`).

### Embeddings & search

768 dimensions everywhere, centralised in `app/core/embedding_config.py`; pgvector columns are `content_vector`. Two generations of search coexist: `etown/search/aisearch_utils.py` (v1) and `aisearch_v2_utils.py` (v2, behind `GET /api/search/ai_search_v2`). v2 keeps everything in **one indexed statement per search**, guards concurrency with a semaphore (`AISEARCH_MAX_CONCURRENT_SEARCHES`, `endpoints/search.py:75` — fast 503 rather than queueing on the pool), and caches query embeddings. If you touch its SQL, preserve the `AS MATERIALIZED` CTEs (`retired_ids`, ~617 and ~2395) — they're optimisation barriers, not dead code. Earlier revisions expressed the same barrier as `OFFSET 0` fences on the geo legs; that form is gone from the file, so preserve whichever one you find.

### Background workers

`main.py`'s `lifespan` starts 14 workers, each in its own `try/except` so one failure can't block startup: offer expiry, social-insights collector, mytown metadata refresh, notification scheduler + queue runner + LISTEN/NOTIFY config reload, unverified-provider WhatsApp retry sweeper, classification re-mining, terminal-log capture (two starters: `start_terminal_capture` and `start_logging_terminal_capture`), log retention, account purge, plan expiry, plan retention. All 14 have a matching `stop_*` in the `finally`. Add both halves when you add a worker, and make it a no-op unless its feature flag is on.

### Auth

One shared bearer token, not per-user identity: `verify_token` in `app/core/security.py` compares the credential against `etown_bearer_token`. 30 of the 33 endpoint modules reference it. Caller identity arrives as `contact_id` / `user_profile_id` in the request body or query, so an authenticated caller is trusted to say who it is acting for. (`PyJWT` is a dependency only for signing 100ms.live room tokens in `services/voice_call_service.py`.)

### Logging

`logger` from `app/core/logging`. Logs are shipped to Postgres: `etown_terminal_logs` is the live table for debugging deployed errors (`etown_application_logs` is stale).

## Conventions

**Code rules — non-negotiable.** The full rules, with examples, are in @CONVENTIONS.md. Read it before editing any file.

1. **No comments in source.** No `#` comments in Python and no `--` comment lines inside SQL query strings. Commit `1b559a3` removed them all; never add one back. Functional markers (shebang, `# noqa`, `# type:`, `# pragma`) are the only exceptions.
2. **One `Updates:` entry per date.** Before adding an entry, check for one already dated today in that file; if it exists, rewrite it to cover both changes. Never add a second line with the same date.
3. **Entries are short titles.** Say what changed in one clause, about 15 words. No rationale, no "because", no before/after, no measurements. Several changes on one date become semicolon-separated clauses.
4. **Every entry sits on one physical line.** `    DD-Mon-YYYY: <title>` — never wrapped onto continuation lines.
5. **The rest of the module docstring stays accurate, one item per line.** Summary, `Features:`, `Classes:`, `Functions:`, `Dependencies:`/`Requires:` describe the code as it is now; `Functions:`/`Classes:` list exactly the top-level definitions in the file, as `name(): short description`; no item wraps onto a second line.

6. **Every module opens with the same two-line description.** `"""<Title> - <purpose clause>.` on the opening quotes (≤120 chars), a blank line, then a single-line present-tense summary (≤60 words) of what the module does now, its layer and its main tables/endpoints, then a blank line and the first section. No other prose before `Features:`.

7. **Every function and method has a one-line docstring.** Module-level functions and class methods (not nested helpers) open with `"""<Imperative summary, ≤25 words, ending in a full stop>."""`. Only `Args:`/`Returns:`/`Raises:`/`Yields:` may follow, after a blank line, one line per item; `Args:` lists exactly the signature's parameters. No other prose, route handlers included.

8. **Architecture rules.** No bare `except:`, hard-coded secrets, or awaited Dataverse syncs in endpoints. No new `print()` in `app/`, SQL or `Database` calls in endpoints, or routes without `verify_token`, beyond the recorded baseline (`scripts/conventions_baseline.json`). Fix the new code; never raise a baseline count.

Run `python scripts/check_conventions.py <paths you touched>` before finishing any change. It checks all eight rules and exits 1 on any violation. The shared `.claude/settings.json` also runs it as a PostToolUse hook after every Write/Edit of a `.py` file and hands any violation back to Claude as a blocking error; fix it before moving on, never work around the hook.

**Module docstrings are the documentation.** Every module opens with a docstring: summary, `Features:`, `Classes:`, `Functions:`, `Dependencies:`, and a dated `Updates:` log. When you change behaviour, add or merge the day's `Updates:` entry and refresh the affected sections in the same edit.

**Rationale goes in the commit message**, not in comments and not in the `Updates:` entry.

**Don't extract single-use helpers.** Inline anything with one call site rather than adding a wrapper.

Endpoints, utils, and syncing modules are named for their domain and mirror each other — adding a feature usually means touching `endpoints/x.py`, `etown/x/x_utils.py`, and `etown/x/x_syncing.py`.

## Working method

Developers here often describe what they want without saying how. Treat every code-changing request as `/etown-task` does (`.claude/skills/etown-task/SKILL.md`), whether or not they typed the command:

1. **Understand.** Restate the goal as one concrete outcome. Read the module docstrings of the files involved and the nearest sibling implementation. Derive acceptance criteria from the code. Ask at most one question, and only when the code can't answer it.
2. **Size and route.**
   - **Small** (1-3 files, one layer): edit directly.
   - **Medium** (one feature triplet or one Chaatie tool): delegate to the matching specialist agent below.
   - **Big or complex** (several domains, more than about 6 files, a migration or sweep): offer to run the `etown-build` workflow, which splits the work into file-disjoint units built in parallel git worktrees. Without it, launch parallel `Agent` calls with `isolation: "worktree"`, one unit each, and integrate their patches with `git apply --3way`.
3. **Gate before "done".** Run `python scripts/check_conventions.py` on the changed files and pytest on the whole suite, and run `etown-review` for medium and big jobs. Report what changed, how it was verified, and any manual steps (migrations, env vars).

Worktrees branch from local `HEAD` (`worktree.baseRef: head` in `.claude/settings.json`), so they include your commits but not uncommitted edits. Never commit, push, or run SQL against a database unless the developer asks.

Slash commands for developers:

| Command | Does |
|---|---|
| `/etown-task <request>` | Any change from plain words: sizes it, routes it, gates it |
| `/etown-build <request>` | Big change: plan, parallel worktree specialists, integrate, docs, review, fix |
| `/etown-fix <problem>` | Bug: reproduce, root cause via debugger agent, minimal fix, regression test |
| `/etown-ship [scope]` | Pre-commit: conventions, docs sync, review, tests, commit message |
| `/etown-conventions [scope]` | Fix convention violations only |
| `/etown-docs-sync [scope]` | Re-sync docstrings with changed behaviour only |

The human-facing guide to this whole setup (usage, automation, maintenance, troubleshooting) is `CLAUDE_SETUP.md`; keep it in step when you change hooks, skills, workflows or rules. The shared `.claude/settings.json` greets each session with these commands, checks every `.py` edit (PostToolUse hook), and re-checks all changed `.py` files before Claude stops (Stop hook). Hook messages are addressed to Claude: fix what they report yourself, immediately, without asking the developer or bypassing the hook.

Guardrails:

- **Blocked:** pushing to a remote, and the `psql` client. The permission rules deny them and a PreToolUse guard (`check_conventions.py --guard`) blocks any variant. Hand those steps to the developer.
- **Asks first:** `git commit`, and edits to `.env`, `tables.sql` and `docker/`.
- **Pre-commit hook** (`.githooks/pre-commit`, enabled by the SessionStart hook through `git config core.hooksPath .githooks`). On every commit it regenerates `tables.sql` from the database and stages it only if the schema really changed. It warns and continues if the database is unreachable, and blocks the commit if a staged `.py` file fails the checker. Regeneration takes about two minutes.
- **Known test failures** are listed in `tests/known_failures.txt`, and `tests/conftest.py` marks them as non-strict xfail. A clean run reads `N passed, M xfailed`, so any `failed` is a new regression. Remove a line when its test is fixed; never add one to hide a new failure.

## Agents and workflows

Eight subagents in `.claude/agents/` (Claude Code) mirror the same eight in `.github/agents/*.agent.md` (VS Code / Copilot). The Claude set is committed (force-added past the `.claude/` ignore rule) and follows CONVENTIONS.md; the Copilot set is still gitignored and machine-local. The Claude set is the corrected one — the Copilot originals still contain a wrong route decorator, the `Database.fetch(query, [args])` list form, a pre-`Command` tool template, and a `calculate_distance` helper that doesn't exist.

| Agent | Tools | Use for |
|---|---|---|
| `etown-architecture-agent` | read-only | design, migration planning, blast-radius analysis |
| `etown-endpoint-developer-agent` | read/write/bash | new routes and domain utils |
| `etown-tool-developer-agent` | read/write/bash | LangGraph tools, registries, context |
| `etown-database-performance-agent` | read/bash | query plans, indexes, pool tuning |
| `etown-security-agent` | read/bash | auth coverage, secrets, data exposure |
| `etown-debugger-agent` | read/bash/web | log analysis, root cause, env attribution |
| `etown-code-quality-agent` | read/write | convention and duplication cleanup |
| `etown-automation-agent` | read/bash | pytest and the `scratch/_e2e_*` harnesses |

Six workflow scripts in `.claude/workflows/`, committed alongside the agents, invoked as `/name args`, `Workflow({name, args})`, or by asking Claude to run them by name:

- **`etown-review`** — scouts the diff, runs only the specialists the change actually needs, adversarially verifies each finding, then reports. Read-only. Caps at 4 dimensions x 2 verifications and logs whatever it skipped.
- **`etown-feature`** — plan → implement → parallel specialist review → apply blocking fixes. **Writes files.** Never runs SQL and never commits.
- **`etown-claims-audit`** — checks a doc's factual claims against the code and returns a correction list. This is what catches drift in *this* file.
- **`etown-build`** — plan (architecture agent) → build each file-disjoint unit with the right specialist in its own git worktree, in dependency waves → integrate the patches into the working tree → `etown-docs-sync` → `etown-review` → fix critical/high findings. Returns early with questions if the request is too vague to plan. **Writes files.** Never runs SQL and never commits.
- **`etown-conventions`** — runs the convention checker over a scope (default: uncommitted and untracked `.py` files; or a path prefix or git range), fixes each failing file in parallel, re-checks and repairs once, and reports anything still failing. **Writes files** (comments and docstrings only). Never commits.
- **`etown-docs-sync`** — after a behaviour change, updates each touched file's module description, sections and function docstrings to match the code, merges today's `Updates:` entry, then has an independent agent verify every changed claim and repairs once. **Writes files** (docstrings only). Never commits.

The automation agent is deliberately not wired into any workflow: it triggers real notifications and can insert scraped providers, so it runs on an explicit decision each time.

## Gotchas

- `.gitignore` has `*.md`, so **this file needs `git add -f CLAUDE.md`** to be committed. Same for any doc you add.
- **`CONVENTIONS.md`, `scripts/check_conventions.py` and `.claude/settings.json` are gitignored too** (`*.md`, `scripts/*`, `.claude/`). Stage them with `git add -f CONVENTIONS.md scripts/check_conventions.py .claude/settings.json` whenever they change, or teammates lose the rules, the checker and the hook.
- `.gitignore` also has `.claude/` and `.github/`. The committed `.claude/settings.json`, `.claude/agents/*.md`, `.claude/workflows/*.js` and `.claude/skills/*/SKILL.md` were force-added, so **anything new or changed under `.claude/` needs `git add -f`** too. `.github/agents/`, any `.claude/settings.local.json`, and `.claude/worktrees/` (where agent worktrees live) stay machine-local. Force-add specific paths, never `git add -f .claude/` wholesale.
- `.gitignore` also has `scripts/*`, so **anything new under `scripts/` needs `git add -f`** or it never leaves your machine. This is how the classification loader/eval scripts went missing while every module docstring still referenced them.
- `tables.sql` is a generated snapshot, not a migration. Migrations are hand-run `.sql` files in `scripts/` (e.g. `2026_08_aisearch_v2_indexes.sql`) applied to dev and prod manually — there is no migration framework, so a schema change is only real once you've run it on both.
- `scratch/` is the scratch area for one-off ops scripts (ignored by git but the directory is real and in use).
- Dev and prod schemas drift. Before concluding "the code is broken", check the column actually exists in the environment you're hitting.
