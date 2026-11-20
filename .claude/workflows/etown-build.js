export const meta = {
  name: 'etown-build',
  description: 'Turn a plain-language request into a plan of file-disjoint units, build each in its own git worktree in parallel, integrate, sync docs, review, and fix blockers',
  whenToUse:
    'For big or complex changes: several domains, more than a handful of files, a migration or a sweep. Pass args as the request in plain words (e.g. "add a cancel-reason report for providers with an endpoint, utils and Dataverse sync"). WRITES FILES in the working tree. Never runs SQL against a database and never commits.',
  phases: [
    { title: 'Plan', detail: 'architecture agent splits the request into independent units with owners and acceptance criteria' },
    { title: 'Build', detail: 'one specialist per unit, each in its own git worktree, in dependency waves' },
    { title: 'Integrate', detail: 'apply every unit patch to the working tree, run the checker and tests' },
    { title: 'Docs', detail: 'etown-docs-sync on the integrated change' },
    { title: 'Review', detail: 'etown-review on the integrated change' },
    { title: 'Fix', detail: 'apply critical, high, medium and correctness review findings, re-run checker and tests' },
    { title: 'Report', detail: 'what was built, verified, and left open' },
  ],
}

const AGENT_TYPES = [
  'etown-endpoint-developer-agent',
  'etown-tool-developer-agent',
  'general-purpose',
]

const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    understood_goal: { type: 'string', description: 'The request restated as a concrete goal' },
    blocking_questions: { type: 'array', items: { type: 'string' }, description: 'Only questions without which the work would be guesswork; empty if the code answers them' },
    common_dir: { type: 'string', description: 'Absolute output of `git rev-parse --git-common-dir` (made absolute)' },
    run_tag: { type: 'string', description: 'Short slug for this run: request keywords plus `date +%H%M%S`' },
    units: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string', description: 'Short kebab-case id' },
          title: { type: 'string' },
          goal: { type: 'string' },
          files: { type: 'array', items: { type: 'string' }, description: 'Repo-relative files this unit owns; no file may appear in two units' },
          agent_type: { type: 'string', enum: AGENT_TYPES },
          acceptance: { type: 'array', items: { type: 'string' }, description: 'Checkable criteria, including tests to add or run' },
          depends_on: { type: 'array', items: { type: 'string' }, description: 'Unit ids whose changes this unit needs first' },
        },
        required: ['id', 'title', 'goal', 'files', 'agent_type', 'acceptance', 'depends_on'],
      },
    },
    risks: { type: 'array', items: { type: 'string' } },
  },
  required: ['understood_goal', 'blocking_questions', 'common_dir', 'run_tag', 'units', 'risks'],
}

const UNIT_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    patch_path: { type: 'string', description: 'Absolute path of the patch file written' },
    files_changed: { type: 'array', items: { type: 'string' } },
    checker_clean: { type: 'boolean' },
    tests: { type: 'string', description: 'Which tests ran and their result' },
    acceptance_met: { type: 'array', items: { type: 'string' } },
    open_issues: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'patch_path', 'files_changed', 'checker_clean', 'tests', 'acceptance_met', 'open_issues'],
}

const INTEGRATE_SCHEMA = {
  type: 'object',
  properties: {
    applied: { type: 'array', items: { type: 'string' }, description: 'Unit ids applied cleanly or after resolving conflicts' },
    failed: { type: 'array', items: { type: 'string' }, description: 'Unit ids that could not be applied, with the reason' },
    files_changed: { type: 'array', items: { type: 'string' } },
    checker_clean: { type: 'boolean' },
    tests: { type: 'string', description: 'pytest summary line and any failure that is new versus the unchanged tree' },
  },
  required: ['applied', 'failed', 'files_changed', 'checker_clean', 'tests'],
}

const request = typeof args === 'string' ? args.trim() : ''
if (!request) {
  log('No request given. Pass the change you want in plain words as args.')
  return { error: 'missing request' }
}

phase('Plan')

const plan = await agent(
  [
    `Plan this change for the Etown API repo: "${request}"`,
    'Read CLAUDE.md, the module docstrings of every file you expect to touch, and the nearest sibling implementation before planning.',
    'Split the work into the fewest independent units that let specialists work in parallel (1 to 8). Rules:',
    '- Every file belongs to exactly one unit. A feature normally spans endpoints/x.py, etown/x/x_utils.py and etown/x/x_syncing.py; keep a feature\'s triplet in one unit unless it is large.',
    '- Tests go in the SAME unit as the code they test; never make a tests-only unit. Split only where parts can truly be built independently.',
    '- Use depends_on only when a unit truly needs another unit\'s code first; independent units build in parallel.',
    `- agent_type: etown-endpoint-developer-agent for routes and domain utils, etown-tool-developer-agent for Chaatie tools/registries, general-purpose for anything else (services, workers, scripts, tests).`,
    '- Acceptance criteria must be checkable: a test that passes, a route that returns X, the checker clean.',
    '- Never plan SQL execution against dev or prod. A schema change is a new .sql file under scripts/ for a human to run.',
    'Only put a question in blocking_questions if the code cannot answer it and guessing would likely waste the work.',
    'Also run `git rev-parse --git-common-dir`, make it absolute, and return it as common_dir; build run_tag from 2-3 request keywords plus `date +%H%M%S`.',
    'Do not edit anything.',
  ].join('\n'),
  { label: 'plan', phase: 'Plan', schema: PLAN_SCHEMA, agentType: 'etown-architecture-agent' },
)

if (!plan) return { error: 'planning failed; nothing was changed' }
if (plan.blocking_questions.length) {
  log('The plan needs answers before any code is written.')
  return { understood_goal: plan.understood_goal, blocking_questions: plan.blocking_questions, note: 'Answer these and run again. Nothing was changed.' }
}

let units = plan.units
if (units.length > 8) {
  log(`Plan had ${units.length} units; building the first 8 and reporting the rest as not started.`)
  units = units.slice(0, 8)
}
const ids = new Set(units.map((u) => u.id))
const notStarted = plan.units.slice(8).map((u) => u.id)
const patchDir = `${plan.common_dir.replace(/\\/g, '/')}/etown-build/${plan.run_tag}`
log(`Goal: ${plan.understood_goal}`)
log(`${units.length} unit(s); patches go to ${patchDir}`)

const waves = []
const placed = new Set()
while (placed.size < units.length) {
  const wave = units.filter((u) => !placed.has(u.id) && u.depends_on.filter((d) => ids.has(d)).every((d) => placed.has(d)))
  if (!wave.length) {
    log('Circular depends_on in the plan; building the remaining units together.')
    waves.push(units.filter((u) => !placed.has(u.id)))
    break
  }
  wave.forEach((u) => placed.add(u.id))
  waves.push(wave)
}

phase('Build')

const built = {}
for (const [n, wave] of waves.entries()) {
  log(`Wave ${n + 1}/${waves.length}: ${wave.map((u) => u.id).join(', ')}`)
  const results = await parallel(
    wave.map((u) => () => {
      const deps = u.depends_on.filter((d) => built[d] && built[d].patch_path)
      return agent(
        [
          `You are building one unit of a larger change, inside your own git worktree. Overall goal: ${plan.understood_goal}`,
          `Unit "${u.id}": ${u.title}`,
          `Goal: ${u.goal}`,
          `Files you own: ${u.files.join(', ')}. Never create or edit a file another unit owns; if you truly need one outside your list that no unit owns, create it and list it.`,
          'tests/, scripts/ and .claude/ are gitignored, so this worktree only has the tracked tests. Run the tests you add or touch; the full suite runs at integration.',
          'Acceptance criteria:',
          ...u.acceptance.map((a) => `- ${a}`),
          deps.length ? `First apply the patches of the units you depend on: ${deps.map((d) => `git apply --3way "${built[d].patch_path}"`).join(' && ')}` : '',
          'Then run `git add -A` so the starting state (including any dependency patches) becomes your baseline in the index. Do not stage anything after that.',
          '',
          'Work the Etown way (CLAUDE.md): endpoint -> utils -> Database layers, Dataverse mirror as a BackgroundTask that never raises, no comments, docstrings per CONVENTIONS.md, today\'s Updates entry merged. Add or update a unit test under tests/unit_tests/ when behaviour is worth pinning.',
          'Before finishing: run `python scripts/check_conventions.py <every .py file you changed>` until clean, and run the relevant pytest files.',
          `Then write a patch of only your own changes (the index holds the baseline). New files under gitignored folders (tests/, scripts/, .claude/, any .md) must be marked first with \`git add -N -f <file>\`. Then run \`mkdir -p "${patchDir}" && git add -N . && git diff --binary > "${patchDir}/${u.id}.patch"\` and check the patch lists every file you created. Return that absolute path as patch_path.`,
          'Never commit, never push, never run SQL against a database.',
        ].filter(Boolean).join('\n'),
        { label: `build:${u.id}`, phase: 'Build', schema: UNIT_SCHEMA, agentType: u.agent_type, isolation: 'worktree' },
      ).then((r) => ({ id: u.id, r }))
    }),
  )
  for (const item of results.filter(Boolean)) built[item.id] = item.r
}

const buildFailed = units.filter((u) => !built[u.id]).map((u) => u.id)
if (buildFailed.length) log(`Build failed for: ${buildFailed.join(', ')}`)
const order = waves.flat().filter((u) => built[u.id]).map((u) => ({ id: u.id, patch: built[u.id].patch_path }))
if (!order.length) return { goal: plan.understood_goal, error: 'no unit produced a patch; nothing was changed', build_failed: buildFailed }

phase('Integrate')

const integrated = await agent(
  [
    'Integrate unit patches into this working tree (the main checkout, not a worktree).',
    'Run `git status --short` first and note any uncommitted or staged changes; never discard or unstage them.',
    'Apply in this order, each with `git apply --3way`. Each patch holds only its own unit\'s changes. Resolve genuine conflicts by keeping both intents. Afterwards unstage ONLY the paths the patches touched (`git reset -q -- <those paths>`); never run a bare `git reset`, which would unstage the developer\'s own work:',
    ...order.map((o) => `- ${o.id}: ${o.patch}`),
    'Then run `python scripts/check_conventions.py <every changed .py file>` and fix what it reports (docstrings/comments only unless it is a real integration bug).',
    'Run `python -m pytest tests/ -q -p no:cacheprovider`. Known pre-existing failures show as xfailed; any `failed` is a regression from this change, so fix it before reporting. Report the summary line.',
    'Never commit.',
  ].join('\n'),
  { label: 'integrate', phase: 'Integrate', schema: INTEGRATE_SCHEMA, agentType: 'general-purpose' },
)

phase('Docs')
let docs = null
try {
  docs = await workflow('etown-docs-sync')
} catch (e) {
  log(`etown-docs-sync did not run: ${e}`)
}

phase('Review')
let review = null
try {
  review = await workflow('etown-review')
} catch (e) {
  log(`etown-review did not run: ${e}`)
}

phase('Fix')
const findings = review && Array.isArray(review.confirmed) ? review.confirmed : []
const severityOf = (f) => String((f.verdict && f.verdict.corrected_severity) || f.corrected_severity || f.severity || '').toLowerCase()
const blocking = findings.filter((f) => ['critical', 'high', 'medium'].includes(severityOf(f)) || /correct/i.test(String(f.category || '')))
let fix = null
if (blocking.length) {
  fix = await agent(
    [
      'Fix these verified review findings in the working tree. Each was confirmed against the code. If a fix would change the API contract the developer asked for, apply the safest version that keeps the contract (for example a sane LIMIT or a status filter) and say so:',
      ...blocking.map((f, i) => `${i + 1}. ${JSON.stringify(f)}`),
      'Keep fixes minimal and in the layer the finding names. Then run `python scripts/check_conventions.py <changed files>` until clean and re-run the relevant tests. Report what you changed and the test result in a few lines. Never commit.',
    ].join('\n'),
    { label: 'fix-blockers', phase: 'Fix', agentType: 'general-purpose' },
  )
} else {
  log('No critical, high, medium or correctness findings to fix.')
}

phase('Report')

return {
  goal: plan.understood_goal,
  units: units.map((u) => ({ id: u.id, title: u.title, built: !!built[u.id], summary: built[u.id] ? built[u.id].summary : null, open_issues: built[u.id] ? built[u.id].open_issues : [] })),
  not_started: notStarted,
  build_failed: buildFailed,
  integration: integrated,
  docs_sync: docs,
  review_findings: findings,
  blockers_fixed: blocking.length ? fix : 'none',
  risks: plan.risks,
  patch_dir: patchDir,
  note: 'Everything is in the working tree, unstaged. Review `git diff`, then commit yourself. Nothing was committed or pushed, and no SQL was run.',
}
