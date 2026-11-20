---
name: etown-fix
description: Fix a bug in the Etown API from a plain description or an error message. Reproduces it, finds the root cause with the debugger specialist, applies the smallest correct fix, pins it with a test, and runs the quality gate.
argument-hint: <the bug, error message, or what is going wrong>
---

# /etown-fix

The problem: **$ARGUMENTS**

1. **Pin down the symptom.** Restate it as "doing X gives Y, expected Z". If there is an error message or stack trace, find the exact line. If the environment matters (dev or prod), note that dev and prod schemas drift. Check that the column really exists before blaming the code.
2. **Root cause.** Use the Agent tool with `subagent_type: "etown-debugger-agent"`. Give it the symptom and ask for the root cause with file:line evidence and a proposed minimal fix. It may read `etown_terminal_logs` for deployed errors. **Never query prod without asking the developer first.**
3. **Fix it.** Apply the smallest change that fixes the cause, not the symptom, in the right layer (endpoint / utils / syncing). If the fix touches a Dataverse-mirrored column, fix both sides in the same pass.
4. **Pin it.** Add a unit test under `tests/unit_tests/` that fails without the fix and passes with it, unless that's impossible without a database. In that case, say so.
5. **Docs.** Update the touched module's docstrings and merge today's `Updates:` entry, naming the fix in one short clause.
6. **Gate.**
   - `python scripts/check_conventions.py <changed files>` must be clean.
   - Run `python -m pytest tests/ -q -p no:cacheprovider`: any `failed` (not `xfailed`) is a regression to fix.
7. **Report:**
   - the root cause, in 1-2 sentences, with file:line;
   - the fix;
   - the test;
   - any data repair still needed (as a `.sql` file for a human to run; never run it yourself);
   - a suggested commit message.

**Never commit or push** unless the developer asks.
