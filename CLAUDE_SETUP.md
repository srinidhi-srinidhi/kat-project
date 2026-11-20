# Claude Code setup guide

This repository ships a complete Claude Code setup. You don't need to be good at prompting, know the codebase, or remember the house rules: describe what you want, and the setup takes care of approach, quality checks and safety.

This guide explains what's included, how to use it day to day, what happens automatically, and how to maintain it.

---

## 1. Getting started

1. Install Claude Code and open this repository in it (terminal `claude`, VS Code, JetBrains or the desktop app).
2. Make sure `python` is on your PATH, with the project's dependencies installed (see the README Quick start). The hooks run `python scripts/check_conventions.py`.
3. Start a session. The first message lists the available commands, and Claude Code turns on the git pre-commit hook for you (`git config core.hooksPath .githooks`).
4. The first time, Claude Code may ask you to trust the project's hooks and settings. Accept; they are what keep the code clean.

Not using Claude Code? Run `git config core.hooksPath .githooks` once, so your commits are still checked.

---

## 2. Everyday use

### Just ask in plain words

You can type a normal request:

> add a filter for verified providers to the provider dashboard

Claude follows the repo's working method on every request, typed command or not:

1. **Understand**: restate the goal, read the relevant code and its docstrings, work out what "done" means.
2. **Size and route**:
   - **Small** (1-3 files): Claude edits directly.
   - **Medium** (one feature or tool): Claude hands the work to the matching specialist agent.
   - **Big** (several modules, a migration, a sweep): Claude offers to run the `etown-build` workflow. Reply **yes**. Workflows only start when you agree.
3. **Verify**: convention checker, full test suite, and a review for medium and big jobs.
4. **Report**: what changed, how it was verified, and anything you must do yourself.

### Slash commands (for when you want to be explicit)

| Command | Use it to | Example |
|---|---|---|
| `/etown-task <request>` | Make any change; sizes and routes it for you | `/etown-task add a cancel reason to provider bookings` |
| `/etown-build <request>` | Build something big with parallel specialists | `/etown-build monthly earnings report for providers with endpoint, utils, Dataverse sync and tests` |
| `/etown-fix <problem>` | Fix a bug with root cause and a regression test | `/etown-fix POST /api/book/update returns 500 when servicetype is empty` |
| `/etown-ship [scope]` | Get a change ready to commit | `/etown-ship` |
| `/etown-conventions [scope]` | Only fix rule violations | `/etown-conventions app/etown/booking` |
| `/etown-docs-sync [scope]` | Only refresh docstrings after a behaviour change | `/etown-docs-sync` |
| `/etown-review [scope]` | Review a change with specialist agents, read-only | `/etown-review dev...HEAD` |
| `/etown-claims-audit [doc]` | Check a document's claims against the code | `/etown-claims-audit README.md` |

`[scope]` means an optional path or git range. Leave it out to use your uncommitted changes.

### Tips for better results

- Say what should be true when it's done: "providers see X on the dashboard", "the endpoint returns Y for Z".
- For bugs, paste the exact error, say which environment (dev or prod), and give the request that fails.
- Run `/etown-ship` before every commit.

---

## 3. How big jobs run: parallel agents in git worktrees

`/etown-build` (or "yes" to Claude's offer) runs this pipeline:

1. **Plan**: the architecture agent splits the request into independent units. Each file belongs to exactly one unit, and tests go with the code they test. If the request is too vague, it stops and asks you questions instead of guessing.
2. **Build**: each unit is built by the right specialist (endpoint developer, Chaatie tool developer, or general), each in its own **git worktree**, so parallel work never collides. Units that depend on each other run in waves.
3. **Integrate**: the patches are applied to your working tree. The checker and the full test suite run.
4. **Docs**: `etown-docs-sync` updates the docstrings of the changed files.
5. **Review**: `etown-review` runs the relevant specialists and has each finding checked by an independent agent.
6. **Fix**: confirmed critical, high, medium and correctness findings are fixed.

Good to know:

- Worktrees start from your **last commit** (`worktree.baseRef: head`). Commit or stash first if a build must see uncommitted edits.
- Everything lands in your working tree **uncommitted**. Review `git diff`, then commit yourself.
- It's thorough, not cheap: a small four-file feature took about 20 agents and 18 minutes. Use `/etown-task` for small changes.

---

## 4. What happens automatically

| When | What | Where it's defined |
|---|---|---|
| Session starts | Command list shown; working method given to Claude; git pre-commit hook enabled | `.claude/settings.json` (SessionStart) |
| Claude runs a shell command | `git push` and `psql` are blocked, in any form | `.claude/settings.json` (PreToolUse, `--guard`) and `permissions.deny` |
| Claude edits a `.py` file | The file is checked; Claude has to fix every violation before continuing | PostToolUse, `--hook` |
| Claude is about to finish | Every changed `.py` file is checked again; Claude has to fix leftovers | Stop, `--changed` |
| Claude wants to commit or edit `.env`, `tables.sql` or `docker/` | You're asked first | `permissions.ask` |
| Anyone commits | `tables.sql` is regenerated from the database (staged only if the schema really changed), and staged `.py` files are checked. A violation blocks the commit | `.githooks/pre-commit` |

The hook messages are addressed to Claude and tell it exactly how to fix each problem, so it corrects itself without involving you.

Two things are yours to do: **pushing** (`git push origin <branch>`) and **running SQL** against a database (migrations are `.sql` files under `scripts/` that you apply by hand to dev and prod).

The pre-commit hook takes about two minutes, because it regenerates `tables.sql`. If the database is unreachable (VPN, firewall, missing credentials), it warns and lets the commit through.

---

## 5. The rules being enforced

The full rules, with examples, are in [CONVENTIONS.md](CONVENTIONS.md). In short:

1. No comments in source: no `#` in Python and no `--` inside SQL strings.
2. One `Updates:` entry per date in each module docstring.
3. `Updates:` entries are short titles, with no rationale.
4. Each entry is one physical line.
5. Docstring sections stay accurate, one item per line; `Functions:`/`Classes:` list exactly what the file defines.
6. Every module opens with `"""<Title> - <purpose>.` and a one-line summary.
7. Every function and method has a one-line docstring.
8. Architecture rules:
   - **Always:** no bare `except:`, no hard-coded secrets, no awaited Dataverse syncs in endpoints.
   - **Ratchet** (no new ones beyond `scripts/conventions_baseline.json`): no `print()` in `app/`, no SQL or `Database` calls in endpoints, no routes without `verify_token`.

Check anything yourself with:

```powershell
python scripts/check_conventions.py                 # every tracked .py file
python scripts/check_conventions.py app/etown/user  # just some paths
```

### Tests

The whole suite passes today, and `tests/known_failures.txt` is empty. If a test ever has to fail temporarily (for example, it needs a database), list its id there: `tests/conftest.py` then marks it as expected (`xfailed`), so the run stays readable and **any `failed` still means your change broke something**. When it's fixed (it shows `XPASS`), delete its line. Never add a line to hide a new failure.

---

## 6. What's in the setup

| Path | What it is |
|---|---|
| `CLAUDE.md` | Instructions Claude reads every session: architecture, rules, working method, gotchas |
| `CONVENTIONS.md` | The rules, with right and wrong examples |
| `CLAUDE_SETUP.md` | This guide |
| `.claude/settings.json` | Hooks, permissions, worktree base |
| `.claude/skills/` | `/etown-task`, `/etown-fix`, `/etown-ship` |
| `.claude/workflows/` | `etown-build`, `etown-review`, `etown-feature`, `etown-conventions`, `etown-docs-sync`, `etown-claims-audit` |
| `.claude/agents/` | Eight specialists: architecture, endpoint developer, tool developer, database performance, security, debugger, code quality, automation |
| `scripts/check_conventions.py` | The checker behind every hook, plus the guard |
| `scripts/conventions_baseline.json` | Recorded legacy counts for the ratchet rules |
| `.githooks/pre-commit` | Regenerates `tables.sql` and checks staged files |
| `tests/conftest.py`, `tests/known_failures.txt` | Known-failure handling |
| `.gitattributes` | Keeps workflow scripts and git hooks LF on every machine (Claude Code refuses CRLF workflow scripts) |

---

## 7. Maintaining the setup

- **Anything under `.claude/`, `scripts/`, `tests/`, and any `.md` file is gitignored.** Add changes with `git add -f <exact path>`. Never `git add -f .claude/` wholesale, because `.claude/worktrees/` holds temporary agent worktrees.
- **Changing a rule:** edit `scripts/check_conventions.py`, update `CONVENTIONS.md` and the rule list in `CLAUDE.md`, and run the checker on the whole repo.
- **Paying down legacy violations:** after removing old `print()` calls, endpoint SQL or unauthenticated routes, run `python scripts/check_conventions.py --update-baseline` so the counts can only go down. Never raise a count to get a change through.
- **Fixing a known test failure:** fix the test, then delete its line from `tests/known_failures.txt`.
- **Adding a workflow:** put it in `.claude/workflows/<name>.js` with LF line endings (`.gitattributes` enforces this on checkout), and list it in `CLAUDE.md` and in this guide.
- **Adding a skill (slash command):** create `.claude/skills/<name>/SKILL.md` with `name`, `description` and `argument-hint` frontmatter. Don't reuse a workflow's name.
- **Adding or editing an agent:** `.claude/agents/<name>.md`. Keep its guidance consistent with `CONVENTIONS.md`.

---

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Hooks error with `python: command not found` | Put Python on your PATH (or activate `.venv`) and restart Claude Code |
| Hooks don't seem to run | Open `/hooks` in Claude Code to reload them, or restart the session. Check you trusted the project settings |
| "script contains control characters" when running a workflow | The workflow file has CRLF line endings. Run `git checkout -- .claude/workflows` (`.gitattributes` restores LF) and start a new session |
| Every commit takes about two minutes | The pre-commit hook is regenerating `tables.sql`. That's expected |
| Pre-commit warns it can't regenerate `tables.sql` | The database is unreachable from your machine (VPN, firewall allow-list, credentials). The commit still goes through |
| Commit blocked by the pre-commit hook | A staged `.py` file breaks the rules. Run `/etown-conventions` or fix what it printed, then commit again |
| Claude refuses to push | By design. Push yourself: `git push origin <branch>` |
| A test fails that you didn't touch | If it isn't listed in `tests/known_failures.txt`, it's a real regression. Investigate with `/etown-fix` |
