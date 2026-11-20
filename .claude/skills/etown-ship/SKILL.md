---
name: etown-ship
description: Pre-commit gate for the Etown API. Fixes convention violations, syncs docstrings with the changed behaviour, reviews the change, runs the tests, and drafts the commit message. Use before every commit or PR.
argument-hint: [optional git range or path, default = uncommitted changes]
---

# /etown-ship

Scope: **$ARGUMENTS** (empty means your uncommitted changes).

Run these steps in order and stop at the first one that finds something you can't fix safely.

1. **Conventions:** `Workflow({name: "etown-conventions", args: "<scope or omit>"})`. Report anything in `still_failing`.
2. **Docs:** `Workflow({name: "etown-docs-sync", args: "<scope or omit>"})`. Report anything in `unresolved`.
3. **Review:** `Workflow({name: "etown-review", args: "<scope or omit>"})`. Fix every critical/high confirmed finding, then re-run `python scripts/check_conventions.py` on the files you touched.
4. **Tests:** `python -m pytest tests/ -q -p no:cacheprovider`. Known failures show as `xfailed`, so any `failed` is a regression: fix it. If a listed known failure now passes (XPASS), remove its line from `tests/known_failures.txt`.
5. **Final check:** `python scripts/check_conventions.py <changed .py files>` must print "No problems found."
6. **Summary for the developer:**
   - `git diff --stat`;
   - what each step found and fixed;
   - manual steps (`.sql` migrations to run on dev and prod, env vars, deploy notes);
   - a commit message: a short subject line, and a body only if the subject can't carry it.
   - Remind them that new files under `scripts/`, `tests/`, `.claude/` and any `.md` need `git add -f`, and that committing runs the pre-commit hook (it refreshes `tables.sql`, which takes about two minutes, and re-runs the checker).

**Never commit or push** unless the developer explicitly asks.
