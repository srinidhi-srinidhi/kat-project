export const meta = {
  name: 'etown-docs-sync',
  description: 'After a behaviour change, bring each touched file\'s docstrings back in line with its code and merge today\'s Updates entry, then independently verify every claim',
  whenToUse:
    'After changing behaviour, before committing. Pass args as a git range (e.g. "dev...HEAD") or path prefix; omit to cover uncommitted changes. WRITES FILES: docstrings only. Never commits.',
  phases: [
    { title: 'Scout', detail: 'changed .py files, what changed in each, and today\'s date' },
    { title: 'Sync', detail: 'one agent per file updates its docstrings to match the code' },
    { title: 'Verify', detail: 'an independent skeptic checks each file\'s docstrings against the code; one repair round' },
    { title: 'Report', detail: 'what was updated and anything left unresolved' },
  ],
}

const SCOUT_SCHEMA = {
  type: 'object',
  properties: {
    scope: { type: 'string' },
    today: { type: 'string', description: 'Today as DD-Mon-YYYY, e.g. 25-Sep-2026' },
    files: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string', description: 'Repo-relative .py path' },
          change_summary: { type: 'string', description: 'What changed in behaviour, 2 sentences max' },
          touched_functions: { type: 'array', items: { type: 'string' }, description: 'Functions/methods added, changed or removed' },
        },
        required: ['file', 'change_summary', 'touched_functions'],
      },
    },
  },
  required: ['scope', 'today', 'files'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    inaccurate: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          where: { type: 'string', description: 'Module description, a section, or a function name' },
          claim: { type: 'string' },
          reality: { type: 'string', description: 'What the code actually does, with the line that proves it' },
        },
        required: ['where', 'claim', 'reality'],
      },
    },
    checker_clean: { type: 'boolean' },
    checker_output: { type: 'array', items: { type: 'string' } },
  },
  required: ['inaccurate', 'checker_clean', 'checker_output'],
}

const target = typeof args === 'string' && args.trim() ? args.trim() : null

phase('Scout')

const scout = await agent(
  [
    'List the Python files whose behaviour changed, for a documentation sync.',
    target
      ? `Scope: "${target}". If it contains ".." it is a git range: use \`git diff ${target}\`. Otherwise it is a path prefix: use \`git diff HEAD -- ${target}\`.`
      : 'Scope: uncommitted work: `git diff HEAD` plus untracked .py files from `git ls-files --others --exclude-standard`.',
    'For each changed .py file, read its diff and summarise the behaviour change in at most 2 sentences, and list the functions or methods that were added, changed or removed.',
    'Skip files whose diff touches only docstrings or comments.',
    'Get today\'s date by running `date +%d-%b-%Y` and return it.',
    'Do not edit anything.',
  ].join('\n'),
  { label: 'scout', phase: 'Scout', schema: SCOUT_SCHEMA, agentType: 'general-purpose' },
)

if (!scout || scout.files.length === 0) {
  log(scout ? 'No behaviour changes found; nothing to sync.' : 'Scout failed; nothing was changed.')
  return { scope: scout ? scout.scope : target, synced: [], unresolved: [] }
}
log(`${scout.files.length} file(s) to sync; Updates entries dated ${scout.today}.`)

const SYNC_RULES = [
  'Rules (CONVENTIONS.md):',
  '- Module description: `"""<Title> - <purpose>.` on the opening quotes, blank line, one-line present-tense summary (<=60 words), blank line, then sections. Rewrite it only if the change makes it untrue.',
  '- Features/Classes/Functions/Dependencies: one line per item; Functions/Classes list exactly what the file defines; drop removed items, add new ones, fix changed ones.',
  '- Every added or changed function/method: one-line imperative docstring (<=25 words, full stop); only Args/Returns/Raises/Yields may follow, one line per item; Args lists exactly the signature parameters.',
  '- Updates: exactly one entry per date, one line, a short title with no rationale. If an entry for today already exists, rewrite it to cover both changes instead of adding a second line.',
  '- No `#` comments anywhere. Change ONLY docstrings: never code. Keep line endings. Use the Edit tool.',
].join('\n')

phase('Sync')

const synced = await pipeline(
  scout.files,
  (item) =>
    agent(
      [
        `Bring the docstrings of \`${item.file}\` in line with its current code.`,
        `What changed: ${item.change_summary}`,
        `Functions added/changed/removed: ${item.touched_functions.join(', ') || 'none listed; read the diff'}`,
        `Read the diff (${target && target.includes('..') ? `git diff ${target} -- ${item.file}` : `git diff HEAD -- ${item.file}`}) and the code, not the old docstrings.`,
        `Merge an Updates entry dated ${scout.today} naming this change.`,
        '',
        SYNC_RULES,
        '',
        `Finish by running \`python scripts/check_conventions.py ${item.file}\` and fixing everything it reports.`,
      ].join('\n'),
      { label: `sync:${item.file}`, phase: 'Sync', agentType: 'general-purpose' },
    ),
  (_, item) =>
    agent(
      [
        `You are a skeptical reviewer. Check every docstring claim in \`${item.file}\` that relates to this change against the actual code: the module description, the Features/Functions/Dependencies items, the ${scout.today} Updates entry, and the docstrings of: ${item.touched_functions.join(', ') || 'the functions in the diff'}.`,
        'Report only claims the code contradicts, each with the line that proves it. Do not report style preferences.',
        `Also run \`python scripts/check_conventions.py ${item.file}\` and report its output.`,
        'Do not edit anything.',
      ].join('\n'),
      { label: `verify:${item.file}`, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'general-purpose' },
    ),
  async (verdict, item) => {
    if (!verdict || (verdict.inaccurate.length === 0 && verdict.checker_clean)) return { file: item.file, verdict }
    await agent(
      [
        `Fix these problems in the docstrings of \`${item.file}\`:`,
        ...verdict.inaccurate.map((x) => `- ${x.where}: says "${x.claim}", but ${x.reality}`),
        ...(verdict.checker_clean ? [] : ['Checker output:', ...verdict.checker_output.map((p) => `  ${p}`)]),
        '',
        SYNC_RULES,
        `Finish with \`python scripts/check_conventions.py ${item.file}\` clean.`,
      ].join('\n'),
      { label: `repair:${item.file}`, phase: 'Verify', agentType: 'general-purpose' },
    )
    const again = await agent(
      `Run \`python scripts/check_conventions.py ${item.file}\` and re-check only these previously wrong claims against the code: ${verdict.inaccurate.map((x) => x.where).join('; ') || 'none'}. Report any that are still wrong. Do not edit anything.`,
      { label: `reverify:${item.file}`, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'general-purpose' },
    )
    return { file: item.file, verdict: again }
  },
)

phase('Report')

const done = synced.filter(Boolean)
const ok = done.filter((r) => r.verdict && r.verdict.inaccurate.length === 0 && r.verdict.checker_clean).map((r) => r.file)
const unresolved = done
  .filter((r) => !r.verdict || r.verdict.inaccurate.length > 0 || !r.verdict.checker_clean)
  .map((r) => ({ file: r.file, inaccurate: r.verdict ? r.verdict.inaccurate : [], checker_output: r.verdict ? r.verdict.checker_output : ['verify agent failed'] }))
const lost = scout.files.length - done.length
if (lost) log(`${lost} file(s) dropped because an agent failed; rerun on them.`)
log(`Synced ${ok.length}; unresolved ${unresolved.length}.`)

return { scope: scout.scope, today: scout.today, synced: ok, unresolved, note: 'Review the diff before committing. Nothing was committed.' }
