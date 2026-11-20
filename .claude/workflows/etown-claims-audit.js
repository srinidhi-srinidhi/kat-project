export const meta = {
  name: 'etown-claims-audit',
  description: 'Check every factual claim in CLAUDE.md (or another doc) against the actual code and report which ones have gone stale',
  whenToUse:
    'When the project docs may have drifted from the code — before trusting CLAUDE.md for a big change, or after a refactor. Pass a doc path as args to audit something else (e.g. "README.md" or a module docstring path). Read-only.',
  phases: [
    { title: 'Extract', detail: 'pull atomic, checkable claims out of the doc' },
    { title: 'Verify', detail: 'check each batch of claims against the code' },
    { title: 'Report', detail: 'list the corrections, with the replacement text' },
  ],
}

const CLAIMS_SCHEMA = {
  type: 'object',
  properties: {
    doc: { type: 'string' },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'integer' },
          claim: { type: 'string', description: 'One atomic, falsifiable assertion, quoted or closely paraphrased' },
          doc_line: { type: 'integer', description: 'Line in the doc where it appears' },
          how_to_check: { type: 'string', description: 'The specific file, symbol, path, or grep that would settle it' },
        },
        required: ['id', 'claim', 'how_to_check'],
      },
    },
  },
  required: ['doc', 'claims'],
}

const VERDICTS_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'integer' },
          status: {
            type: 'string',
            enum: ['confirmed', 'stale', 'wrong', 'unverifiable'],
            description:
              'confirmed = still true; stale = was true, the code has since moved; wrong = never true as written; unverifiable = needs a live database or environment',
          },
          evidence: { type: 'string', description: 'file:line or command output that settles it' },
          correction: { type: 'string', description: 'Replacement wording for the doc, or empty if confirmed' },
        },
        required: ['id', 'status', 'evidence'],
      },
    },
  },
  required: ['verdicts'],
}

const docPath = typeof args === 'string' && args.trim() ? args.trim() : 'CLAUDE.md'
const BATCH_SIZE = 4
const MAX_BATCHES = 8

phase('Extract')

const extracted = await agent(
  [
    `Read ${docPath} in full and extract every factual claim it makes about this codebase.`,
    '',
    'A claim qualifies only if it is checkable against the repository: a file path, a function or column name,',
    'a route shape, a default, a flag, a count, a behaviour ("X retries 5 times", "Y is tracked in git").',
    'Skip anything that is advice, preference, or intent rather than fact ("keep endpoints thin", "prefer X").',
    '',
    'Keep each claim atomic — one assertion per entry, not a whole paragraph — and number them from 1.',
    'For how_to_check, name the exact file, symbol, or grep that would settle it. Be specific: "grep for',
    'OFFSET 0 in app/etown/search/" beats "check the search code".',
    '',
    'Prioritise claims where drift would actually mislead someone: paths, signatures, route shapes, column',
    'names, retry/timeout behaviour, what is tracked in git, and which tables are live.',
  ].join('\n'),
  { label: `extract:${docPath}`, schema: CLAIMS_SCHEMA },
)

if (!extracted || !extracted.claims || extracted.claims.length === 0) {
  log(`No checkable claims extracted from ${docPath}.`)
  return { doc: docPath, claims: 0, findings: [] }
}

const claims = extracted.claims.slice(0, BATCH_SIZE * MAX_BATCHES)
log(`${claims.length} claim(s) extracted from ${docPath}`)
if (extracted.claims.length > claims.length) {
  log(`Capped at ${claims.length}; ${extracted.claims.length - claims.length} claim(s) not audited this run.`)
}

const batches = []
for (let i = 0; i < claims.length; i += BATCH_SIZE) {
  batches.push({ index: batches.length + 1, items: claims.slice(i, i + BATCH_SIZE) })
}

phase('Verify')

const results = await pipeline(batches, (batch) =>
  agent(
    [
      'Verify each of these documented claims against the actual code in this repository. You are auditing the',
      'documentation, not the code — the code is the source of truth.',
      '',
      'For each claim: run the check, read the file, and record the evidence as file:line or command output.',
      'Status rules: "confirmed" = still true today; "stale" = it was true once but the code has moved on',
      '(say what it is now); "wrong" = never true as written; "unverifiable" = it needs a live database or a',
      'deployed environment to settle, so do not guess.',
      '',
      'Do not accept a claim because it sounds right or because another doc repeats it. If a named function,',
      'column, file, or string does not appear in the code, the claim is not confirmed. Check the exact literal',
      'where one is quoted.',
      '',
      'Claims to verify (JSON):',
      JSON.stringify(batch.items, null, 2),
    ].join('\n'),
    {
      label: `verify:batch-${batch.index}`,
      phase: 'Verify',
      agentType: 'etown-architecture-agent',
      schema: VERDICTS_SCHEMA,
    },
  ),
)

const verdicts = results
  .filter(Boolean)
  .flatMap((r) => (r.verdicts ? r.verdicts : []))

const byId = {}
for (const c of claims) byId[c.id] = c

const merged = verdicts.map((v) => ({
  ...v,
  claim: byId[v.id] ? byId[v.id].claim : '(unmatched id)',
  doc_line: byId[v.id] ? byId[v.id].doc_line : null,
}))

const broken = merged.filter((v) => v.status === 'stale' || v.status === 'wrong')
const confirmed = merged.filter((v) => v.status === 'confirmed')
const unverifiable = merged.filter((v) => v.status === 'unverifiable')

log(`${confirmed.length} confirmed, ${broken.length} stale/wrong, ${unverifiable.length} unverifiable`)

if (broken.length === 0) {
  return {
    doc: docPath,
    audited: merged.length,
    confirmed: confirmed.length,
    unverifiable: unverifiable.map((v) => v.claim),
    findings: [],
    report: `Every checkable claim in ${docPath} still holds.`,
  }
}

phase('Report')

const report = await agent(
  [
    `Write the correction list for ${docPath}. Every entry below has already been verified against the code —`,
    'do not re-check them and do not add new ones.',
    '',
    'For each: the claim as written, why it no longer holds (with the evidence), and the exact replacement',
    'wording. Group by section of the doc where you can, and put the claims most likely to mislead someone',
    'mid-task first. End with a one-line summary of how much of the doc is drifting.',
    'Plain and specific — this is a patch list someone will apply by hand.',
    'If the doc is a module docstring, every replacement must satisfy CONVENTIONS.md so that',
    '`python scripts/check_conventions.py` passes: a "<Title> - <purpose>." title line plus a one-line summary',
    '(at most 60 words), one physical line per Features/Classes/Functions/Dependencies item, present tense, no',
    'rationale. Any Updates change is a merge into the single entry for that date, a short one-line title with',
    'no "why". Rationale for a correction belongs in the commit message, never in comments or the docstring.',
    '',
    'Verified corrections (JSON):',
    JSON.stringify(broken, null, 2),
    '',
    unverifiable.length
      ? `Also note these as needing a live environment to settle: ${unverifiable.map((v) => v.claim).join('; ')}`
      : '',
  ]
    .filter(Boolean)
    .join('\n'),
  { label: 'report:corrections', phase: 'Report' },
)

return {
  doc: docPath,
  audited: merged.length,
  confirmed: confirmed.length,
  findings: broken,
  unverifiable: unverifiable.map((v) => v.claim),
  report,
}
