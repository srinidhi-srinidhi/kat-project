---
name: etown-task
description: Do any change to the Etown API from a plain-language request. Sizes the job, picks the right approach (direct edit, specialist agent, or parallel worktree build), and finishes with the repo's full quality gate. Use when a developer describes what they want without saying how.
argument-hint: <what you want, in plain words>
---

# /etown-task

The developer's request: **$ARGUMENTS**

You are the senior engineer on this repo. The developer may not know how to prompt, which files matter, or how the codebase is layered, and that's fine. Take their words and deliver a finished, verified change. Follow these steps in order.

## 1. Understand before touching anything

- Restate the request in one sentence as a concrete outcome, e.g. "GET /api/provider/x returns y for z".
- Find the code involved: grep for the route, table or feature, read the module docstrings of the files you will touch (they are accurate), and read the nearest sibling implementation to copy its shape.
- Work out the acceptance criteria yourself from the code: what must be true when you're done, and which test proves it.
- Ask the developer **at most one question**, and only if the code cannot answer it and a wrong guess would waste the work. Otherwise state your assumption and proceed.

## 2. Size the job and pick the approach

| Size | Signals | Approach |
|---|---|---|
| **Small** | 1-3 files, one layer, obvious change | Do it yourself directly. |
| **Medium** | One feature across the endpoint/utils/syncing triplet, or a Chaatie tool, 3-6 files | Delegate to the matching specialist with the Agent tool: `etown-endpoint-developer-agent` (routes and utils), `etown-tool-developer-agent` (Chaatie tools), `etown-debugger-agent` (a bug with unknown cause), `etown-database-performance-agent` (slow queries). Give it the acceptance criteria. |
| **Big / complex** | Several domains, more than about 6 files, a migration or a sweep, or parts that can proceed independently | Run the **`etown-build`** workflow with the restated request as `args` (`Workflow({name: "etown-build", args: "<request>"})`). It plans file-disjoint units, builds each in its own git worktree in parallel with the right specialist, integrates, syncs docs, reviews and fixes blockers. |

If a big job can't use the workflow, split it yourself: launch parallel `Agent` calls in one message, each with `isolation: "worktree"`, the matching `subagent_type`, one file-disjoint unit and its acceptance criteria. Ask each to return a patch (`git add -N . && git diff --binary > <path>`), then apply the patches in the main tree with `git apply --3way`.

Tell the developer in one line which size you chose and why.

## 3. Build it the Etown way

- Endpoint = HTTP shape only; business logic in `app/etown/<domain>/<domain>_utils.py`; DB access through `app/db/client.py`; Dataverse mirror as a `BackgroundTask` in `<domain>_syncing.py` that never raises.
- Heavy reads use `Database.fetch_once`. Routes are `/api/<prefix>/...`. Auth is `Depends(verify_token)`.
- No comments. Docstrings follow CONVENTIONS.md: module description, one line per section item, one-line function docstrings, today's `Updates:` entry merged. The PostToolUse hook checks every `.py` edit; fix whatever it reports immediately.
- Schema changes go in a new `.sql` file under `scripts/` for a human to run on dev and prod. **Never run SQL against a database yourself.**
- Add or update a unit test under `tests/unit_tests/` when the behaviour is worth pinning.

## 4. Quality gate (always, before saying done)

1. `python scripts/check_conventions.py <every .py file you changed>`: must print "No problems found."
2. `python -m pytest tests/ -q -p no:cacheprovider`: the known pre-existing failures show as `xfailed`, so any `failed` is a regression you must fix before finishing.
3. For medium and big jobs, run the `etown-review` workflow on the change and fix every critical/high finding.
4. Re-read your diff (`git diff`) once as a reviewer would.

## 5. Report back

Tell the developer, briefly:
- what changed, file by file;
- how it was verified (checker, tests, review);
- anything they must do themselves (run a `.sql` migration on dev/prod, set an env var, deploy);
- a suggested commit message.

**Never commit or push** unless the developer explicitly asks.
