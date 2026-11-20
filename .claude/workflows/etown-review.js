export const meta = {
  name: 'etown-review',
  description: 'Review changed code through the Etown specialist agents, then adversarially verify every finding before reporting it',
  whenToUse:
    'Before landing a branch or PR on dev. Pass args as a git range/path spec (e.g. "dev...HEAD" or "app/etown/booking") or omit to review the working-tree diff. Read-only: it never edits files.',
  phases: [
    { title: 'Scout', detail: 'enumerate the diff and decide which specialists are relevant' },
    { title: 'Review', detail: 'one specialist agent per relevant dimension' },
    { title: 'Verify', detail: 'adversarial refutation of each finding, against the real code' },
    { title: 'Report', detail: 'dedupe, rank, and write the final report' },
  ],
}

const SCOUT_SCHEMA = {
  type: 'object',
  properties: {
    range: { type: 'string', description: 'The git range or path spec actually inspected' },
    files: { type: 'array', items: { type: 'string' }, description: 'Changed files, repo-relative' },
    summary: { type: 'string', description: 'What this change set does, in 3 sentences or fewer' },
    touches_endpoints: { type: 'boolean' },
    touches_sql: { type: 'boolean' },
    touches_agent_tools: { type: 'boolean' },
    touches_syncing: { type: 'boolean' },
    touches_auth_or_secrets: { type: 'boolean' },
  },
  required: ['range', 'files', 'summary'],
}

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          category: { type: 'string' },
          claim: { type: 'string', description: 'One sentence stating the defect' },
          failure_scenario: { type: 'string', description: 'Concrete inputs/state to wrong output or crash' },
          fix: { type: 'string' },
        },
        required: ['file', 'severity', 'claim', 'failure_scenario'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean', description: 'true if the finding does not hold up against the real code' },
    reasoning: { type: 'string' },
    corrected_severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'not-a-defect'] },
  },
  required: ['refuted', 'reasoning'],
}

const target = typeof args === 'string' && args.trim() ? args.trim() : null

phase('Scout')

const scout = await agent(
  [
    'You are scouting a change set in the Etown FastAPI codebase for a code review.',
    target
      ? `Review target: ${target}. Interpret it as a git range if it looks like one, otherwise as a path filter.`
      : 'No target given. Inspect the uncommitted working-tree diff; if it is empty, fall back to the diff between HEAD and the merge-base with dev.',
    '',
    'Use git (diff --stat, diff --name-only, log --oneline) and read the changed files.',
    'Then decide which specialist reviews are worth running by setting the touches_* booleans:',
    '- touches_endpoints: app/api/v1/endpoints/ or app/api/v1/router.py changed',
    '- touches_sql: any SQL string, query constant, .sql file, or Database.* call changed',
    '- touches_agent_tools: app/agent_infrastructure/ changed',
    '- touches_syncing: any *_syncing.py or Dataverse payload changed',
    '- touches_auth_or_secrets: app/core/security.py, app/core/config.py, .env*, or any auth/OTP path changed',
    '',
    'Be accurate rather than generous: a false true costs a whole specialist review.',
  ].join('\n'),
  { label: 'scout:diff', schema: SCOUT_SCHEMA },
)

if (!scout || !scout.files || scout.files.length === 0) {
  log('Nothing to review — no changed files found.')
  return { range: scout ? scout.range : target, files: [], confirmed: [], note: 'empty diff' }
}

log(`${scout.files.length} changed file(s) in ${scout.range}`)

const fileList = scout.files.join('\n')
const context = [
  `Change set: ${scout.range}`,
  `Summary: ${scout.summary}`,
  '',
  'Changed files:',
  fileList,
  '',
  'Read the actual diff (git diff) and the surrounding code before judging anything.',
  'Report ONLY defects you can anchor to a file and a concrete failure scenario.',
  'Do not report style preferences, missing type hints, or anything the repo does not enforce —',
  'there is no formatter, type-checker, or CI in this project. The one enforced check is',
  '`python scripts/check_conventions.py <paths>` (the CONVENTIONS.md rules), and its violations ARE defects.',
].join('\n')

// Ordered by priority. At most MAX_DIMENSIONS run, so the last entries are the ones
// dropped on a change set that trips every conditional — and the drop is logged, never silent.
const MAX_DIMENSIONS = 4
const VERIFY_PER_DIMENSION = 2

const DIMENSIONS = [
  {
    key: 'conventions',
    agentType: 'etown-code-quality-agent',
    always: true,
    prompt: [
      'Review this change set for convention and maintainability defects against CONVENTIONS.md (7 rules).',
      'Every violation that `python scripts/check_conventions.py <changed .py files>` reports is a finding;',
      'if you cannot run it, apply its rules by reading the changed files.',
      'Weight these highest, in order: any added comment (# in Python or -- inside SQL strings, other than',
      'shebang/# noqa/# type:/# pragma markers); a missing Updates entry, a second entry for the same date, or',
      'an entry that carries rationale or wraps onto a continuation line; a module description or',
      'Features/Classes/Functions/Dependencies section left stale by the change; a function or method without',
      'a one-line imperative docstring, or with Args not matching its signature; a helper introduced with a',
      'single call site; business logic that leaked into an endpoint.',
    ].join(' '),
  },
  {
    key: 'security',
    agentType: 'etown-security-agent',
    when: (s) => s.touches_endpoints || s.touches_auth_or_secrets || s.touches_syncing,
    prompt: [
      'Review this change set for security defects. Highest value here: a route without verify_token;',
      'a read scoped to contactid where dual-profile contacts require userprofileid; an unordered LIMIT 1',
      'resolving a person by phone (etown_contact has no unique phone constraint); an auth path that checks',
      'credentials but not account liveness; secrets added to the tracked .env; a new outbound send that',
      'bypasses the notification/WhatsApp governance gates; and any SQL built by string interpolation.',
      'Never print real secret values — name the variable or vault key only.',
    ].join(' '),
  },
  {
    key: 'db-performance',
    agentType: 'etown-database-performance-agent',
    when: (s) => s.touches_sql,
    prompt: [
      'Review this change set for database defects. Highest value here: an expensive read left on',
      'Database.fetch instead of fetch_once (fetch retries 5x and treats a statement timeout as transient);',
      'query parameters passed as a list instead of positionally; a query inside a loop that should be',
      '= ANY($1); filtering or dedup done in Python after a LIMIT instead of DISTINCT ON in SQL; a new',
      'predicate with no supporting index; and any removal of an AS MATERIALIZED optimisation barrier in',
      'the search SQL.',
    ].join(' '),
  },
  {
    key: 'agent-tools',
    agentType: 'etown-tool-developer-agent',
    when: (s) => s.touches_agent_tools,
    prompt: [
      'Review this change set for agent-tool defects. Highest value here: identity accepted as an LLM',
      'argument instead of read via get_context_value(key, current, config, state); a tool returning a bare',
      'string or json.dumps instead of a Command; a response_type that is not the name of a tool that was',
      'actually called; a tool registered in the wrong role list (member vs provider); a write intent added',
      'to DISPATCHABLE_INTENTS; and any mismatch between the @tool signature and its handle_* handler.',
    ].join(' '),
  },
  {
    key: 'layering',
    agentType: 'etown-architecture-agent',
    always: true,
    prompt: [
      'Review this change set for architectural defects: three-layer violations, work that belongs in a',
      'domain util, a new background worker missing its stop_* half or its feature flag, a schema change',
      'with no story for reaching both dev and prod, and any PG-only write to a column the Dataverse',
      'mirror also owns (the mirror will revert it).',
    ].join(' '),
  },
]

const eligible = DIMENSIONS.filter((d) => d.always || (d.when && d.when(scout)))
const active = eligible.slice(0, MAX_DIMENSIONS)
const dropped = eligible.slice(MAX_DIMENSIONS)

log(`Running ${active.length} specialist review(s): ${active.map((d) => d.key).join(', ')}`)
if (dropped.length) {
  log(`Skipped to stay within the agent budget: ${dropped.map((d) => d.key).join(', ')} — run again scoped to those files for full coverage.`)
}

phase('Review')

const reviewed = await pipeline(
  active,
  (dim) =>
    agent([dim.prompt, '', context].join('\n'), {
      label: `review:${dim.key}`,
      phase: 'Review',
      agentType: dim.agentType,
      schema: FINDINGS_SCHEMA,
    }),
  (result, dim) => {
    const found = result && result.findings ? result.findings : []
    // Verify the worst first and cap per dimension: an adversarial pass is only worth
    // spending on findings that would actually change the merge decision.
    const ranked = found
      .slice()
      .sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
      .slice(0, VERIFY_PER_DIMENSION)
    if (ranked.length === 0) return []
    const carried = found.filter((f) => ranked.indexOf(f) === -1)
    if (carried.length) {
      log(`${dim.key}: verifying the top ${ranked.length} of ${found.length} finding(s); the other ${carried.length} are carried through unverified.`)
    }
    return parallel(
      ranked.map((f) => () =>
        agent(
          [
            'Adversarially verify one code-review finding against the real code. Your default is REFUTED.',
            'Read the file, read the callers, and check whether the failure scenario can actually occur.',
            'Set refuted=true if the scenario cannot occur, is already guarded elsewhere, describes intended',
            'behaviour of this codebase, or rests on a function/column/flag that does not exist.',
            '',
            `Dimension: ${dim.key}`,
            `File: ${f.file}${f.line ? ':' + f.line : ''}`,
            `Claim: ${f.claim}`,
            `Claimed failure scenario: ${f.failure_scenario}`,
            '',
            'Known intended behaviours that are NOT defects: one shared bearer token with caller identity',
            'trusted from the request body; the Dataverse mirror being lossy and async; a fast 503 from AI',
            'search v2 under load; two coexisting search generations; hand-run SQL migrations with no framework.',
            'A violation reported by `python scripts/check_conventions.py <file>` is a real defect, not a style preference.',
          ].join('\n'),
          {
            label: `verify:${dim.key}:${f.file.split(/[\\/]/).pop()}`,
            phase: 'Verify',
            agentType: 'etown-debugger-agent',
            schema: VERDICT_SCHEMA,
          },
        ).then((v) => ({ ...f, dimension: dim.key, verdict: v })),
      ),
    ).then((verified) =>
      verified.concat(carried.map((f) => ({ ...f, dimension: dim.key, verdict: null }))),
    )
  },
)

function severityRank(s) {
  if (s === 'critical') return 0
  if (s === 'high') return 1
  if (s === 'medium') return 2
  return 3
}

const judged = reviewed.filter(Boolean).flat().filter(Boolean)
const confirmed = judged.filter((f) => f.verdict && !f.verdict.refuted)
const refuted = judged.filter((f) => f.verdict && f.verdict.refuted)
const unverified = judged.filter((f) => !f.verdict)

log(
  `${confirmed.length} confirmed, ${refuted.length} refuted, ${unverified.length} unverified (budget cap)`,
)

if (confirmed.length === 0) {
  return {
    range: scout.range,
    files: scout.files,
    dimensions: active.map((d) => d.key),
    skipped_dimensions: dropped.map((d) => d.key),
    confirmed: [],
    unverified,
    refuted_count: refuted.length,
    report:
      unverified.length === 0
        ? 'No findings survived adversarial verification.'
        : `No verified findings. ${unverified.length} lower-severity finding(s) were never verified and are returned as-is.`,
  }
}

phase('Report')

const report = await agent(
  [
    'Write the final review report for this Etown change set. Input is a list of findings that already',
    'survived adversarial verification — do not re-litigate them, and do not add new ones.',
    '',
    'Merge duplicates that describe the same defect from different dimensions. Order by what would break',
    'first in production. For each: file:line, one-sentence defect, the failure scenario, and the fix.',
    'Finish with a short "Ship / fix first" verdict naming only the blocking items.',
    'Be plain and specific; no severity theatre, no filler.',
    '',
    `Change set: ${scout.range}`,
    `Summary: ${scout.summary}`,
    '',
    'Verified findings (JSON):',
    JSON.stringify(confirmed, null, 2),
    '',
    unverified.length
      ? [
          'These lower-severity findings were NOT adversarially verified (agent budget cap). List them in a',
          'separate "Unverified — check before acting" section, clearly marked, and do not mix them into the',
          'verified set or the ship/fix verdict:',
          JSON.stringify(unverified, null, 2),
        ].join('\n')
      : '',
    dropped.length
      ? `Coverage note to include verbatim: these specialist reviews did not run this pass — ${dropped
          .map((d) => d.key)
          .join(', ')}.`
      : '',
  ]
    .filter(Boolean)
    .join('\n'),
  { label: 'report:synthesis', phase: 'Report' },
)

return {
  range: scout.range,
  files: scout.files,
  dimensions: active.map((d) => d.key),
  skipped_dimensions: dropped.map((d) => d.key),
  confirmed,
  unverified,
  refuted_count: refuted.length,
  report,
}
