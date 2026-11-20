export const meta = {
  name: 'etown-conventions',
  description: 'Find every CONVENTIONS.md violation in a scope, fix each file in parallel, and re-check until the checker is clean',
  whenToUse:
    'Before committing, or after pulling code written outside Claude Code. Pass args as a path prefix or git range (e.g. "app/etown/booking" or "dev...HEAD"); omit to cover uncommitted and untracked .py files. WRITES FILES: only comments and docstrings, never code. Never commits.',
  phases: [
    { title: 'Scout', detail: 'list the .py files in scope and run the checker on them' },
    { title: 'Fix', detail: 'one agent per failing file, then a re-check and one repair round' },
    { title: 'Report', detail: 'what was fixed and anything still failing' },
  ],
}

const SCOUT_SCHEMA = {
  type: 'object',
  properties: {
    scope: { type: 'string', description: 'The path prefix or git range actually used' },
    files_checked: { type: 'integer' },
    failing: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string', description: 'Repo-relative path' },
          problems: { type: 'array', items: { type: 'string' }, description: 'Checker lines for this file, verbatim' },
        },
        required: ['file', 'problems'],
      },
    },
  },
  required: ['scope', 'files_checked', 'failing'],
}

const CHECK_SCHEMA = {
  type: 'object',
  properties: {
    clean: { type: 'boolean', description: 'True when the checker reports no problems for the file' },
    remaining: { type: 'array', items: { type: 'string' }, description: 'Checker lines still reported, verbatim' },
    changed_code: { type: 'boolean', description: 'True if anything other than comments or docstrings changed' },
  },
  required: ['clean', 'remaining', 'changed_code'],
}

const target = typeof args === 'string' && args.trim() ? args.trim() : null

phase('Scout')

const scout = await agent(
  [
    'Build the work list for a conventions fix in this repo.',
    target
      ? `Scope: "${target}". If it contains ".." it is a git range: take the .py files from \`git diff --name-only ${target}\`. Otherwise treat it as a path prefix: take the tracked .py files under it from \`git ls-files\`.`
      : 'Scope: uncommitted work. Take the .py files from `git diff --name-only HEAD` plus untracked ones from `git ls-files --others --exclude-standard`.',
    'Keep only files that still exist. Run `python scripts/check_conventions.py <file> ...` on them (pass each untracked file explicitly).',
    'Return every file the checker reports, with its problem lines copied verbatim. Skip a file whose only problem is "could not tokenize" or "could not parse" and say so in its problems list instead of dropping it.',
    'Do not edit anything.',
  ].join('\n'),
  { label: 'scout', phase: 'Scout', schema: SCOUT_SCHEMA, agentType: 'general-purpose' },
)

if (!scout || scout.failing.length === 0) {
  log(scout ? `Checked ${scout.files_checked} file(s): all clean.` : 'Scout failed; nothing was changed.')
  return { scope: scout ? scout.scope : target, fixed: [], still_failing: [], unparseable: [] }
}

const unparseable = scout.failing.filter((f) => f.problems.some((p) => /could not (tokenize|parse)/.test(p)))
const work = scout.failing.filter((f) => !unparseable.includes(f))
if (unparseable.length) log(`Skipping ${unparseable.length} file(s) that do not parse: ${unparseable.map((f) => f.file).join(', ')}`)
log(`${work.length} file(s) to fix out of ${scout.files_checked} checked.`)

const FIX_RULES = [
  'Follow CONVENTIONS.md exactly. The rules that matter here:',
  '- No `#` comments and no `--` comment lines inside SQL strings (functional markers like `# noqa` are allowed). Delete them; never move rationale into a docstring.',
  '- Module docstring: `"""<Title> - <purpose>.` on the opening quotes, blank line, one-line summary (<=60 words), blank line, then sections. Every section item on one physical line.',
  '- Updates: one entry per date, one line each, short titles. Merge same-date entries.',
  '- Functions/Classes sections list exactly what the file defines.',
  '- Every module-level function and class method: one-line imperative docstring (<=25 words, full stop). Only Args/Returns/Raises/Yields may follow, one line per item; Args lists exactly the signature parameters.',
  'Read the code before writing any description so it is accurate. Change ONLY comments and docstrings: never code, signatures, imports or string literals other than SQL comment lines. Keep the file\'s line endings. Use the Edit tool.',
].join('\n')

phase('Fix')

const results = await pipeline(
  work,
  (item) =>
    agent(
      [`Fix every convention problem in \`${item.file}\`.`, 'Checker output:', ...item.problems.map((p) => `  ${p}`), '', FIX_RULES].join('\n'),
      { label: `fix:${item.file}`, phase: 'Fix', agentType: 'general-purpose' },
    ),
  (_, item) =>
    agent(
      [
        `Run \`python scripts/check_conventions.py ${item.file}\` and report what it prints.`,
        `Then run \`git diff -- ${item.file}\` (or inspect the file if untracked) and say whether anything other than comments, docstrings or SQL comment lines changed.`,
        'Do not edit anything.',
      ].join('\n'),
      { label: `check:${item.file}`, phase: 'Fix', schema: CHECK_SCHEMA, agentType: 'general-purpose' },
    ),
  async (check, item) => {
    if (!check || (check.clean && !check.changed_code)) return { file: item.file, check }
    const repair = [
      `A previous pass on \`${item.file}\` left problems.`,
      check.changed_code ? 'It changed code, not just comments/docstrings. Revert every non-docstring, non-comment change exactly, keeping the docstring fixes.' : '',
      ...(check.remaining.length ? ['Remaining checker output:', ...check.remaining.map((p) => `  ${p}`)] : []),
      '',
      FIX_RULES,
      `Finish by running \`python scripts/check_conventions.py ${item.file}\` and fixing anything it still reports.`,
    ].join('\n')
    await agent(repair, { label: `repair:${item.file}`, phase: 'Fix', agentType: 'general-purpose' })
    const recheck = await agent(
      `Run \`python scripts/check_conventions.py ${item.file}\` and \`git diff -- ${item.file}\`; report the checker output and whether anything other than comments/docstrings changed. Do not edit anything.`,
      { label: `recheck:${item.file}`, phase: 'Fix', schema: CHECK_SCHEMA, agentType: 'general-purpose' },
    )
    return { file: item.file, check: recheck }
  },
)

phase('Report')

const done = results.filter(Boolean)
const fixed = done.filter((r) => r.check && r.check.clean && !r.check.changed_code).map((r) => r.file)
const stillFailing = done
  .filter((r) => !r.check || !r.check.clean || r.check.changed_code)
  .map((r) => ({ file: r.file, remaining: r.check ? r.check.remaining : ['check agent failed'], changed_code: r.check ? r.check.changed_code : null }))
const lost = work.length - done.length
if (lost) log(`${lost} file(s) dropped because an agent failed; rerun the workflow on them.`)
log(`Fixed ${fixed.length}; still failing ${stillFailing.length}.`)

return {
  scope: scout.scope,
  fixed,
  still_failing: stillFailing,
  unparseable: unparseable.map((f) => f.file),
  note: 'Review the diff before committing. Nothing was committed.',
}
