export const meta = {
  name: 'etown-feature',
  description: 'Plan a feature against the three-layer architecture, implement it, review it through the specialists, and apply the blocking fixes',
  whenToUse:
    'For a feature or change that spans the endpoint/util/syncing triplet or the agent tool layer. Pass the request as args (a string). THIS WORKFLOW EDITS FILES — the implement and fix phases write to the working tree.',
  phases: [
    { title: 'Plan', detail: 'architecture agent maps the change onto the three layers' },
    { title: 'Implement', detail: 'one writer agent, routed by what the plan touches' },
    { title: 'Review', detail: 'specialists review the resulting diff in parallel' },
    { title: 'Fix', detail: 'apply only the blocking findings' },
  ],
}

const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    understanding: { type: 'string', description: 'What is being built, in 3 sentences or fewer' },
    layer: {
      type: 'string',
      enum: ['endpoint', 'agent_tool', 'both'],
      description: 'endpoint = HTTP/domain work; agent_tool = LangGraph tool work; both = spans them',
    },
    domain: { type: 'string', description: 'The <domain> this belongs to, e.g. booking, mytown, provider' },
    files_to_touch: { type: 'array', items: { type: 'string' } },
    steps: { type: 'array', items: { type: 'string' }, description: 'Ordered implementation steps' },
    sql_required: { type: 'boolean' },
    sql_detail: { type: 'string', description: 'The DDL/DML needed, and which environments must receive it' },
    mirror_required: { type: 'boolean', description: 'Whether the domain mirrors to Dataverse and this change must too' },
    risks: { type: 'array', items: { type: 'string' } },
    blocked_on: {
      type: 'string',
      description: 'A question only the user can answer, or empty string if none',
    },
  },
  required: ['understanding', 'layer', 'domain', 'files_to_touch', 'steps', 'sql_required', 'mirror_required'],
}

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    files_changed: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string', description: 'What was actually implemented' },
    deviations: { type: 'string', description: 'Where you departed from the plan and why; empty if none' },
    sql_written: { type: 'string', description: 'Path of any .sql file written, or empty string' },
    not_done: { type: 'string', description: 'Anything in the plan you did NOT implement, and why; empty if none' },
    updates_entries: { type: 'array', items: { type: 'string' }, description: 'The docstring Updates lines added' },
  },
  required: ['files_changed', 'summary'],
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
          blocking: { type: 'boolean', description: 'true only if this must be fixed before the change can land' },
          claim: { type: 'string' },
          failure_scenario: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['file', 'blocking', 'claim', 'failure_scenario', 'fix'],
      },
    },
  },
  required: ['findings'],
}

const request = typeof args === 'string' ? args.trim() : args && args.request ? String(args.request) : ''

if (!request) {
  log('No feature request passed. Call this workflow with args set to the request text.')
  return { error: 'missing args: pass the feature request as a string' }
}

const HOUSE_RULES = [
  'House rules for this codebase, which override generic best practice:',
  '- Three layers, strictly: endpoints/<domain>.py is HTTP shape only; etown/<domain>/<domain>_utils.py holds',
  '  all Postgres logic; app/db/client.py owns pool access.',
  '- Route decorators are router-relative. The prefix lives in app/api/v1/router.py and main.py mounts it at',
  '  /api, so live paths are /api/<prefix>/... . Auth is dependencies=[Depends(verify_token)].',
  '- Database methods take parameters POSITIONALLY (asyncpg): Database.fetch(QUERY, service_id). Never a list.',
  '- Expensive reads use Database.fetch_once, because fetch retries 5x and treats a statement timeout as transient.',
  '- Agent tools never accept identity as an argument: read it with get_context_value(key, None, config, state),',
  '  and return Command(update={"messages": [ToolMessage(...)], "response_type": "<the tool name>"}).',
  '- CONVENTIONS.md (7 rules) is the code rulebook; read it before editing.',
  '- No comments in source: no # in Python and no -- inside SQL strings. Only functional markers survive',
  '  (shebang, # noqa, # type:, # pragma).',
  '- Module docstrings are the documentation and must stay accurate. When behaviour changes, update the',
  '  description ("""<Title> - <purpose>." + blank line + one-line summary of at most 60 words) and the',
  '  Features/Classes/Functions/Dependencies sections (one line per item; Functions/Classes list exactly what the',
  '  file defines) in the same edit.',
  '- One Updates entry per date per file: if one dated today exists, merge into it. The entry is a short title',
  '  ("    DD-Mon-YYYY: <title>", one clause, ~15 words, no rationale or "why") on ONE physical line.',
  '- Every function and method has a one-line imperative docstring (at most 25 words, ending in a full stop).',
  '  Only Args/Returns/Raises/Yields may follow, one line per item; Args lists exactly the signature\'s params.',
  '- Rationale goes in the commit message ONLY, never in comments or Updates entries.',
  '- python scripts/check_conventions.py <paths> is the convention check; it must pass on every touched file.',
  '  A PostToolUse hook runs it after each Write/Edit of a .py file and blocks on violations.',
  '- Do not extract a helper with a single call site; inline it.',
  '- No migration framework: DDL goes in a .sql file under scripts/ and must be run on dev AND prod by hand.',
  '  tables.sql is a generated snapshot, not a migration.',
  '- Do not commit, push, or branch. Leave the working tree for the user to inspect.',
].join('\n')

phase('Plan')

const plan = await agent(
  [
    'Plan this change for the Etown FastAPI codebase. Read before planning: CLAUDE.md, the closest existing',
    'sibling implementation, and the module docstrings of every file you would touch.',
    '',
    `Request: ${request}`,
    '',
    HOUSE_RULES,
    '',
    'Set layer to route the implementation: "endpoint" for HTTP/domain work, "agent_tool" for LangGraph tool',
    'work, "both" when it genuinely spans them. Set blocked_on ONLY if a decision is truly the user\'s to make',
    'and no reasonable default exists; otherwise leave it empty and state your assumption in understanding.',
  ].join('\n'),
  { label: 'plan:architecture', agentType: 'etown-architecture-agent', schema: PLAN_SCHEMA },
)

if (!plan) return { error: 'planning failed' }

if (plan.blocked_on && plan.blocked_on.trim()) {
  log(`Blocked: ${plan.blocked_on}`)
  return { plan, blocked_on: plan.blocked_on, implemented: false }
}

log(`Plan: ${plan.layer} work in ${plan.domain}, ${plan.files_to_touch.length} file(s)`)

phase('Implement')

const writerType =
  plan.layer === 'agent_tool' ? 'etown-tool-developer-agent' : 'etown-endpoint-developer-agent'

const impl = await agent(
  [
    'Implement this plan in the Etown codebase. You are the only writer — make the edits yourself.',
    '',
    `Request: ${request}`,
    `Understanding: ${plan.understanding}`,
    `Domain: ${plan.domain}`,
    `Files to touch: ${plan.files_to_touch.join(', ')}`,
    '',
    'Steps:',
    ...plan.steps.map((s, i) => `${i + 1}. ${s}`),
    '',
    plan.sql_required
      ? `SQL required: ${plan.sql_detail || 'see plan'}\nWrite it as a .sql file under scripts/ and say so in sql_written. Do NOT run it against any database.`
      : 'No SQL required.',
    plan.mirror_required
      ? 'This domain mirrors to Dataverse: add the syncing half and fire it as a BackgroundTask that cannot raise into the response.'
      : 'No Dataverse mirror work required.',
    '',
    HOUSE_RULES,
    '',
    'If part of the plan turns out to be wrong or impossible, implement everything else in full and record',
    'exactly what you skipped in not_done. Do not silently narrow the scope.',
    '',
    'Before finishing, run `python scripts/check_conventions.py <every file you touched>` and fix everything it',
    'reports until it exits 0.',
  ].join('\n'),
  { label: `implement:${plan.domain}`, agentType: writerType, schema: IMPL_SCHEMA },
)

if (!impl || !impl.files_changed || impl.files_changed.length === 0) {
  return { plan, implemented: false, error: 'implementation produced no changes' }
}

log(`Implemented across ${impl.files_changed.length} file(s)`)

phase('Review')

const reviewContext = [
  `Request: ${request}`,
  `What was implemented: ${impl.summary}`,
  `Files changed: ${impl.files_changed.join(', ')}`,
  impl.deviations ? `Deviations from plan: ${impl.deviations}` : '',
  impl.not_done ? `Not done: ${impl.not_done}` : '',
  '',
  'Review the actual diff (git diff) — not the description above.',
  'Mark blocking=true only for defects that must be fixed before this can land. Convention and docstring',
  'defects ARE blocking in this codebase.',
]
  .filter(Boolean)
  .join('\n')

const REVIEWERS = [
  {
    key: 'conventions',
    agentType: 'etown-code-quality-agent',
    prompt:
      'Review the diff against CONVENTIONS.md for convention defects: any added comment (# in Python, -- in SQL strings, other than functional markers); a missing Updates entry, a second entry for the same date, an entry carrying rationale or wrapped over several lines; a stale module description or Features/Classes/Functions/Dependencies section; a function or method without a one-line imperative docstring or with Args not matching its signature; a single-use helper; logic left in an endpoint. Any violation scripts/check_conventions.py would report is a finding.',
  },
  {
    key: 'security',
    agentType: 'etown-security-agent',
    prompt:
      'Review the diff for security defects: a route missing verify_token, a read scoped to contactid where dual-profile contacts need userprofileid, an unordered LIMIT 1 on phone, interpolated SQL, a secret added to the tracked .env, an outbound send bypassing the governance gates.',
  },
  {
    key: 'db-performance',
    agentType: 'etown-database-performance-agent',
    prompt:
      'Review the diff for database defects: parameters passed as a list instead of positionally, an expensive read on fetch instead of fetch_once, a query inside a loop, Python-side filtering after a LIMIT, a new predicate with no index, a removed AS MATERIALIZED barrier.',
  },
]

const reviews = await parallel(
  REVIEWERS.map((r) => () =>
    agent([r.prompt, '', reviewContext].join('\n'), {
      label: `review:${r.key}`,
      phase: 'Review',
      agentType: r.agentType,
      schema: FINDINGS_SCHEMA,
    }),
  ),
)

const allFindings = reviews
  .filter(Boolean)
  .flatMap((r) => (r.findings ? r.findings : []))
const blocking = allFindings.filter((f) => f.blocking)

log(`${allFindings.length} finding(s), ${blocking.length} blocking`)

if (blocking.length === 0) {
  return {
    plan,
    implementation: impl,
    findings: allFindings,
    fixed: false,
    note: 'No blocking findings; non-blocking findings left for the user to triage.',
  }
}

phase('Fix')

const fix = await agent(
  [
    'Apply ONLY the blocking findings below to the code you are given. Do not refactor anything else, do not',
    'reopen design decisions, and do not touch files outside those named.',
    '',
    'Blocking findings (JSON):',
    JSON.stringify(blocking, null, 2),
    '',
    HOUSE_RULES,
    '',
    'If a finding is wrong, say so in deviations and leave that code alone rather than making a change you',
    'cannot justify. Merge into today\'s existing Updates entry rather than adding a second one, and keep it a',
    'short one-line title with no rationale.',
    '',
    'Before finishing, run `python scripts/check_conventions.py <every file you touched>` and fix everything it',
    'reports until it exits 0.',
  ].join('\n'),
  { label: 'fix:blocking', agentType: writerType, schema: IMPL_SCHEMA },
)

return {
  plan,
  implementation: impl,
  findings: allFindings,
  blocking_count: blocking.length,
  fix,
  fixed: true,
  next_steps: plan.sql_required
    ? 'SQL was written but NOT run. Apply it to dev and prod by hand, then regenerate tables.sql.'
    : 'Run the dev server and exercise the new path before committing.',
}
