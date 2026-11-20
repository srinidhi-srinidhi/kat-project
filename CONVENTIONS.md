# Code conventions — comments and module docstrings

These rules apply to every file in this repository, for humans and for Claude Code alike. `CLAUDE.md` imports this file, so Claude Code loads it automatically in every session.

Claude Code enforces these automatically: the shared `.claude/settings.json` runs the checker as a PostToolUse hook after every Write/Edit of a `.py` file, and any violation comes back to Claude as a blocking error to fix. For your own edits, check before committing:

```powershell
python scripts/check_conventions.py                    # every tracked .py file
python scripts/check_conventions.py app/etown/user     # just the paths you touched
```

It exits non-zero and lists every violation. It covers all eight rules for Python files, including `--` comments inside SQL query strings. For `.sql`, shell and config files (rule 1), check by eye.

Two Claude Code workflows automate the fixes: **`etown-conventions`** repairs every violation in your uncommitted files (or a path/range), and **`etown-docs-sync`** updates the docstrings of files whose behaviour you changed and merges today's `Updates:` entry. Ask Claude to "run etown-conventions" or "run etown-docs-sync" before committing.

## 1. No comments in source

- No `#` comments in Python — not in code you write, code you edit, tests, or scripts. Commit `1b559a3` removed them all; don't bring them back.
- The same goes for `--` in SQL, whether it's in a migration file or a query string inside Python, and for `#` in shell, Dockerfiles and YAML.
- Kept because they do something: shebangs, `# noqa`, `# type:`, `# pragma`, `# pylint:`, `# fmt:`, `# isort:`, and headers on generated files (e.g. `tables.sql`).
- If a line seems to need an explanation, rename things until it doesn't. Rationale goes in the commit message; what the module does goes in its docstring.

Wrong:

```python
# etown_provider.deleted is the older text 'true'/'false' column. A member profile has no
# provider row, so p.deleted is NULL there and COALESCE keeps it.
_EXCLUDE_DELETED_P = "AND lower(COALESCE(p.deleted, 'false')) <> 'true'"
```

Right:

```python
_EXCLUDE_DELETED_P = "AND lower(COALESCE(p.deleted, 'false')) <> 'true'"
```

## 2. One `Updates:` entry per date

Every module docstring ends with a dated `Updates:` log. Before you add an entry, look for one already dated today in that file. If it's there, **rewrite that entry** so it covers the earlier change and yours. Never add a second line with the same date.

## 3. Entries are titles, not explanations

- Say what changed in one short clause, about 15 words at most.
- No "because", no rationale, no measurements, no before/after, no bug story. Those go in the commit message.
- When one date covers several changes, join them as clauses separated by semicolons.
- Keep a function or endpoint name when it's what identifies the change.

Wrong:

```
    16-Sep-2026: /users/exists reports unverified_onboarded — a source='FOS' provider on the number whose approve flag is not yet true. Reported on the not-found path too, since an unapproved field capture does not make the number an existing user.
```

Right:

```
    16-Sep-2026: /users/exists reports unverified_onboarded and Etown_staff.
```

## 4. Each entry is one physical line

Don't wrap an entry onto continuation lines, even when it's long. One line per date:

```
Updates:
    23-Sep-2026: Added check_alternate_mobile_status(); alternate-mobile lookup in check_user_exists_in_db(); ContactInfo carries ets_alternatemobile.
    24-Sep-2026: fetch_service_list() drops deleted providers; _fetch_unverified_onboarded() skips review-pending providers.
```

Not:

```
Updates:
    23-Sep-2026: check_user_exists_in_db() also resolves a number held as a contact's
                 alternatemobile, ranked behind every primary match.
```

## 5. The rest of the module docstring stays accurate and one item per line

Every module docstring opens with a summary, then `Features:`, `Classes:`, `Functions:`, `Dependencies:` (or `Requires:`), then `Updates:`. When you change a module, update these sections in the same edit:

- **Description**: the title line and one-line summary described in rule 6.
- **Features:** one bullet per real capability. Remove what's gone; add what's new.
- **Classes:** / **Functions:** one line per top-level definition, `name(): short description`. Every function and class in the file is listed, and nothing is listed that doesn't exist. Renaming or deleting a function means updating its line.
- **Dependencies:** / **Requires:** one line per module, service or library the file actually imports or calls.
- **Each item is one physical line and a short clause**, the same as `Updates:` entries. No wrapped continuation lines, no rationale.

Right:

```
Functions:
    check_alternate_mobile_status(): Whether an alternate number is a separate account, another number's alternate, or free to use
    update_member_profile_in_db(): Update a contact and propagate name/email/mobile edits to related records
```

Not:

```
Functions:
    check_alternate_mobile_status(): Whether an alternate number is a separate account, another
        number's alternate, or free to use
```

The checker flags wrapped items, `Functions:`/`Classes:` names that don't exist in the file, and public functions or classes the docstring never mentions. Whether each description is still true is on you.

## 6. Every module opens with the same two-line description

Every non-empty `.py` file starts with a module docstring, and every docstring starts the same way:

```
"""<Title> - <what the module is for, in one clause>.

<One-line summary: 1-3 sentences, at most 60 words. What the module does now, its layer (endpoint, domain utils, Dataverse syncing, agent tool, service, schema, script, test), and the main tables, endpoints or services it works with.>

Features:
    ...
```

- **Title line** sits on the opening `"""`, uses a plain ` - ` separator, ends with a full stop, and stays within 120 characters. Title Case name, then a purpose clause.
- **Summary** is a single physical line after one blank line, followed by a blank line and the first section heading. Nothing else goes before the first section: no extra paragraphs, no code blocks, no history.
- **Present tense, current behaviour only.** No "used to", "previously", "as of", "now also"; no rationale; no measurements.
- Facts that don't fit the summary become one-line items under `Features:` or `Notes:`.

Right:

```
"""User Management Utilities - Postgres business logic for registration, existence checks and profile edits.

Domain layer behind /api/users: registers members and providers, resolves who owns a mobile number, maintains profiles, locations and staff flags, and schedules the Dataverse mirror for each write.

Features:
```

The checker enforces the shape and the length limits. Whether the summary is still true is on you: when a module's behaviour changes, re-read its description in the same edit.

## 7. Every function and method has a one-line docstring

Every module-level function and every class method carries a docstring whose description is **one line**: an imperative sentence that ends in a full stop, at most 25 words, starting on the opening quotes. Nested helper functions defined inside another function are exempt.

```python
def fetch_phone_owner(mobile_number: str) -> Optional[str]:
    """Return the verified-first contact id that owns a mobile number, or None."""
```

The only things allowed after the summary are `Args:`, `Returns:`, `Raises:` and `Yields:`, after one blank line, with one line per item:

```python
async def update_last_login(userprofile_id: str, role: int) -> None:
    """Record a login on a userprofile and reset the signup flags.

    Args:
        userprofile_id: Profile that just logged in.
        role: 1 for provider, 2 for member.
    """
```

- A docstring with no sections closes on the same line: `"""Summary."""`.
- If `Args:` is present, it lists every parameter except `self`/`cls`, and nothing else, with names exactly as in the signature.
- No prose paragraphs, `Note:`, `Example:`, query-parameter tables or history. This applies to route handlers too, even though FastAPI shows their docstring in `/docs`.
- The summary says what the function does now. When you change its behaviour, re-read it in the same edit.
- **Exception:** a LangChain `@tool` whose decorator has no `description=` keeps its docstring as written, because that docstring is the prompt the LLM sees. Prefer adding `description=` to the decorator; the docstring then follows this rule.

## 8. Architecture rules

The checker also enforces the layering and safety rules that newcomers most often break. Every message says exactly how to fix the problem.

| Rule | Where | What to do instead |
|---|---|---|
| `bare-except` | everywhere | Catch the specific exception, or `except Exception as exc:` and log it with `exc_info=True` |
| `secret-literal` | `app/` | Add a setting in `app.core.config` (env var or Key Vault) and read it from `settings` |
| `awaited-sync` | endpoints | Don't await a Dataverse sync; schedule it with `background_tasks.add_task(...)` |
| `print-call` | `app/` | Use `logger` from `app.core.logging` |
| `db-in-endpoint` | endpoints | Move SQL and `Database` calls into `app/etown/<domain>/<domain>_utils.py` |
| `route-without-auth` | endpoints | Add `token: str = Depends(verify_token)`, unless the route is deliberately public |

The first three always fail. The last three are a **ratchet**: the code that existed on 25-Sep-2026 is recorded in `scripts/conventions_baseline.json`, and a file fails only if it adds more. Never raise a count to get a change through. Fix the new code instead. When you remove old violations, a maintainer can lower the counts with `python scripts/check_conventions.py --update-baseline`.
