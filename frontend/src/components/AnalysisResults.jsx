/**
 * Shared analysis results panel - used by the Analyzer tab and the History
 * modal that reopens past analyses. Renders score ring, section breakdown,
 * keywords, strengths/gaps, and focus recommendation.
 */
import { useState } from 'react'
import { motion } from 'framer-motion'
import { Card, CardBody, CardSection, SectionLabel, ScoreLabel } from './ui.jsx'

// === Score ring ===
function ScoreRing({ score, delta }) {
  const r = 38
  const circ = 2 * Math.PI * r
  const offset = circ - (score / 100) * circ
  const color = score >= 80 ? '#16a34a' : score >= 60 ? '#6366f1' : score >= 40 ? '#d97706' : '#dc2626'

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r={r} fill="none" stroke="rgba(0,0,0,0.06)" strokeWidth="6" />
        <circle
          cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={circ} strokeDashoffset={offset} transform="rotate(-90 50 50)"
          style={{ transition: 'stroke-dashoffset 0.7s cubic-bezier(0.4,0,0.2,1)' }}
        />
        <text x="50" y="46" textAnchor="middle" fontSize="24" fontWeight="700" fill={color} fontFamily="'Plus Jakarta Sans',Inter,sans-serif">{score}</text>
        <text x="50" y="62" textAnchor="middle" fontSize="9" fill="rgb(156,163,175)" fontFamily="Inter,sans-serif">/ 100</text>
      </svg>
      <ScoreLabel score={score} />
      {delta !== null && delta !== undefined && delta !== 0 && (
        <span className="text-[11.5px] font-semibold px-2 py-0.5 rounded-sm"
          style={delta > 0
            ? { background: 'rgba(22,163,74,0.08)', color: '#16a34a' }
            : { background: 'rgba(220,38,38,0.08)', color: '#dc2626' }}>
          {delta > 0 ? '+' : ''}{delta} vs last run
        </span>
      )}
    </div>
  )
}

// === Score bar ===
// value === null means the JD listed nothing for this section - it was
// excluded from the overall score, so show "not in JD" instead of a 0 bar.
function ScoreBar({ label, value }) {
  const name = label.replace(/_/g, ' ')
  if (value === null || value === undefined) {
    return (
      <div className="flex items-center gap-3">
        <div className="w-28 text-[12px] text-t3 capitalize flex-shrink-0">{name}</div>
        <div className="flex-1 h-1.5 rounded-full" style={{ background: 'rgba(0,0,0,0.04)' }} />
        <div className="w-14 text-[11px] text-t3 text-right flex-shrink-0">not in JD</div>
      </div>
    )
  }
  const color = value >= 80 ? '#16a34a' : value >= 60 ? '#6366f1' : value >= 40 ? '#d97706' : '#dc2626'
  return (
    <div className="flex items-center gap-3">
      <div className="w-28 text-[12px] text-t2 capitalize flex-shrink-0">{name}</div>
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.06)' }}>
        <motion.div
          className="h-full rounded-full" style={{ background: color }}
          initial={{ width: 0 }} animate={{ width: `${value}%` }}
          transition={{ duration: 0.5, ease: [0.4, 0, 0.2, 1] }}
        />
      </div>
      <div className="w-14 text-[12px] font-bold text-right flex-shrink-0" style={{ color }}>{Math.round(value)}</div>
    </div>
  )
}

// === Rich text ===
// The summary prompt asks the LLM to mark key terms with <strong>...</strong>.
// React escapes raw HTML - correctly, since this text comes from an LLM and must
// never be injected as markup - so the tags would otherwise render literally.
// Parse the marker ourselves and emit real elements. Only <strong> is ever
// produced; any other tag the model invents stays harmless plain text.
const STRONG_RE = /<strong>([\s\S]*?)<\/strong>/gi

function RichText({ text }) {
  if (typeof text !== 'string') return null
  const parts = []
  let last = 0
  let m
  STRONG_RE.lastIndex = 0
  while ((m = STRONG_RE.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push(
      <strong key={`${m.index}-s`} className="font-semibold text-t1">{m[1]}</strong>
    )
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return <>{parts}</>
}

// === Keyword chip ===
const CHIP_STYLES = {
  matched: { background: 'rgba(22,163,74,0.08)',  borderColor: 'rgba(22,163,74,0.25)',  color: '#16a34a' },
  partial: { background: 'rgba(217,119,6,0.08)',  borderColor: 'rgba(217,119,6,0.25)',  color: '#d97706' },
  missing: { background: 'rgba(220,38,38,0.08)', borderColor: 'rgba(220,38,38,0.25)', color: '#dc2626' },
}

// === Hard requirement gates ===
// Years of experience and language level are pass/fail dealbreakers, not
// fractions of a score, so they are surfaced as warnings beside the number.
function GatesPanel({ gates }) {
  const items = []
  if (gates?.experience) items.push(gates.experience)
  for (const g of gates?.languages || []) items.push(g)
  if (items.length === 0) return null

  const unmet = items.filter(g => !g.met)
  if (unmet.length === 0) {
    return (
      <Card>
        <CardBody className="py-3.5 flex items-center gap-2.5">
          <span className="w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(22,163,74,0.1)', color: '#16a34a' }}>
            <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="2,6 5,9 10,3"/></svg>
          </span>
          <span className="text-[13px] text-t2">
            You meet every hard requirement for this role.
          </span>
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="rounded-lg border p-4"
      style={{ background: 'rgba(217,119,6,0.05)', borderColor: 'rgba(217,119,6,0.25)' }}>
      <div className="flex items-center gap-2 mb-2.5">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#d97706" strokeWidth="2" strokeLinecap="round">
          <path d="M8 1.5L15 14H1L8 1.5zM8 6.5v3M8 11.5v.5"/>
        </svg>
        <span className="text-[13px] font-semibold" style={{ color: '#d97706' }}>
          {unmet.length === 1 ? 'Hard requirement not met' : `${unmet.length} hard requirements not met`}
        </span>
      </div>
      <ul className="space-y-1.5">
        {unmet.map((g, i) => (
          <li key={i} className="text-[12.5px] text-t2 leading-relaxed pl-4 relative">
            <span className="absolute left-0 top-[7px] w-1 h-1 rounded-full" style={{ background: '#d97706' }} />
            {g.message}
          </li>
        ))}
      </ul>

      {/* Which past roles actually counted toward this job, and which did not */}
      {gates?.experience?.roles?.length > 0 && gates.experience.total_years > gates.experience.candidate_years && (
        <div className="mt-3 pt-3" style={{ borderTop: '1px solid rgba(217,119,6,0.2)' }}>
          <div className="text-[11px] font-semibold text-t3 uppercase tracking-wide mb-2">
            Which roles count toward this job
          </div>
          <ul className="space-y-1.5">
            {gates.experience.roles.map((r, i) => (
              <li key={i} className="flex items-center gap-2 text-[12px]">
                <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded-sm flex-shrink-0"
                  style={r.relevant
                    ? { background: 'rgba(22,163,74,0.1)', color: '#16a34a' }
                    : { background: 'rgba(var(--t3) / 0.12)', color: 'rgb(var(--t3))' }}>
                  {r.relevant ? 'Counts' : 'Different field'}
                </span>
                <span className="text-t1 truncate">{r.title}</span>
                {r.company && <span className="text-t3 truncate">· {r.company}</span>}
                <span className="ml-auto text-t2 flex-shrink-0">{r.years}y</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[11.5px] text-t3 mt-2.5">
        These are not counted in the score - they are pass/fail filters a recruiter applies before reading your resume.
      </p>
    </div>
  )
}

// === Per-duty coverage from the LLM judge ===
function DutiesPanel({ duties }) {
  const demonstrated = duties?.demonstrated || []
  const partial = duties?.partial || []
  const missing = duties?.missing || []
  const total = demonstrated.length + partial.length + missing.length
  if (total === 0) return null

  const rows = [
    ...demonstrated.map(d => ({ ...d, kind: 'matched', mark: 'Shown' })),
    ...partial.map(d => ({ ...d, kind: 'partial', mark: 'Partial' })),
    ...missing.map(duty => ({ duty, evidence: '', kind: 'missing', mark: 'Not shown' })),
  ]

  return (
    <CardSection
      title="Job duties you demonstrate"
      action={
        <span className="text-[11px] font-bold" style={{ color: '#16a34a' }}>
          {demonstrated.length} / {total}
        </span>
      }
    >
      <ul className="space-y-2.5">
        {rows.map((r, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded-sm border flex-shrink-0 mt-px"
              style={CHIP_STYLES[r.kind]}>
              {r.mark}
            </span>
            <div className="min-w-0">
              <div className="text-[13px] text-t1 leading-snug">{r.duty}</div>
              {r.evidence && (
                <div className="text-[12px] text-t3 mt-0.5 leading-snug">
                  Your evidence: {r.evidence}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </CardSection>
  )
}

// === What the user is being scored against ===
function JobContext({ job }) {
  if (!job?.title && !job?.company) return null
  const facts = [job.location, job.work_mode, job.employment_type, job.job_level]
    .filter(Boolean)
  return (
    <div className="min-w-0">
      <div className="text-[13.5px] font-semibold text-t1 truncate">
        {job.title}
        {job.company && <span className="font-normal text-t2"> · {job.company}</span>}
      </div>
      {facts.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          {facts.map(f => (
            <span key={f} className="px-1.5 py-0.5 text-[11px] font-medium rounded-sm capitalize"
              style={{ background: 'rgb(var(--surface-2))', color: 'rgb(var(--t2))' }}>
              {f}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// === Skills, with proof of where each one came from ===
const EVIDENCE_LABEL = {
  listed:        { text: 'In your skills list', kind: 'matched' },
  in_experience: { text: 'Proven in your experience', kind: 'matched' },
  similar:       { text: 'Close match in your skills', kind: 'matched' },
  missing:       { text: 'Not found', kind: 'missing' },
}

function SkillRow({ skill, info }) {
  const meta = EVIDENCE_LABEL[info?.how] || EVIDENCE_LABEL.missing
  return (
    <li className="flex items-start gap-2.5">
      <span className="px-2 py-0.5 text-[11.5px] font-medium rounded-sm border flex-shrink-0"
        style={CHIP_STYLES[meta.kind]}>
        {skill}
      </span>
      <div className="min-w-0 pt-0.5">
        <div className="text-[11.5px] text-t3">{meta.text}</div>
        {info?.detail && (
          <div className="text-[12px] text-t2 mt-0.5 leading-snug italic">
            {info.how === 'similar' ? `You have: ${info.detail}` : `"${info.detail}"`}
          </div>
        )}
      </div>
    </li>
  )
}

function SkillsPanel({ title, skills, evidence, hint }) {
  const all = [...(skills.matched || []), ...(skills.partial || []), ...(skills.missing || [])]
  if (all.length === 0) return null
  const covered = (skills.matched || []).length
  return (
    <CardSection
      title={title}
      action={
        <span className="text-[11px] font-bold" style={{ color: covered === all.length ? '#16a34a' : 'rgb(var(--t3))' }}>
          {covered} / {all.length}
        </span>
      }
    >
      {hint && <p className="text-[12px] text-t3 mb-3">{hint}</p>}
      <ul className="space-y-2.5">
        {all.map(s => <SkillRow key={s} skill={s} info={evidence?.[s]} />)}
      </ul>
    </CardSection>
  )
}

// === The ATS insight: skills you have but a recruiter's search cannot find ===
function HiddenSkillsAlert({ evidence }) {
  const hidden = Object.entries(evidence?.required || {})
    .filter(([, v]) => v.how === 'in_experience')
    .map(([k]) => k)
  if (hidden.length === 0) return null

  return (
    <div className="rounded-lg border p-4"
      style={{ background: 'rgba(99,102,241,0.05)', borderColor: 'rgba(99,102,241,0.25)' }}>
      <div className="flex items-center gap-2 mb-2">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="rgb(var(--accent))" strokeWidth="2" strokeLinecap="round">
          <circle cx="8" cy="8" r="6.5"/><path d="M8 5v3.5M8 11v.5"/>
        </svg>
        <span className="text-[13px] font-semibold" style={{ color: 'rgb(var(--accent))' }}>
          Quick win: {hidden.length} skill{hidden.length > 1 ? 's' : ''} hidden from recruiters
        </span>
      </div>
      <p className="text-[12.5px] text-t2 leading-relaxed mb-2">
        You proved {hidden.length > 1 ? 'these' : 'this'} in your experience, but {hidden.length > 1 ? 'they are' : 'it is'} missing
        from your Skills section - so a recruiter searching for {hidden.length > 1 ? 'them' : 'it'} will not find you.
        Add {hidden.length > 1 ? 'them' : 'it'} verbatim.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {hidden.map(s => (
          <span key={s} className="px-2 py-0.5 text-[12px] font-medium rounded-sm border"
            style={{ background: 'rgba(99,102,241,0.08)', borderColor: 'rgba(99,102,241,0.3)', color: 'rgb(var(--accent))' }}>
            {s}
          </span>
        ))}
      </div>
    </div>
  )
}

// === Prioritised action plan: what each missing skill is worth ===
function SkillImpact({ impact }) {
  if (!impact?.length) return null
  return (
    <CardSection title="What would raise your score">
      <ul className="space-y-2">
        {impact.slice(0, 6).map(i => (
          <li key={i.skill} className="flex items-center gap-3">
            <span className="px-2 py-0.5 text-[12px] font-medium rounded-sm border flex-shrink-0"
              style={CHIP_STYLES.missing}>{i.skill}</span>
            <span className="flex-1 h-px" style={{ background: 'rgba(var(--border) / 0.1)' }} />
            <span className="text-[12.5px] font-semibold flex-shrink-0" style={{ color: '#16a34a' }}>
              +{i.gain}
            </span>
            <span className="text-[12px] text-t3 flex-shrink-0 w-14 text-right">
              → {i.new_score}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-[11.5px] text-t3 mt-3">
        Gain if the skill were genuinely present on your resume. Never add a skill you do not have.
      </p>
    </CardSection>
  )
}

// === How the number was actually built ===
function ScoreMath({ rows }) {
  const [open, setOpen] = useState(false)
  if (!rows?.length) return null
  return (
    <Card>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-left"
      >
        <span className="text-[13px] font-semibold text-t1">How this score was calculated</span>
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="rgb(var(--t3))" strokeWidth="2"
          strokeLinecap="round" style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}>
          <path d="M4 6l4 4 4-4"/>
        </svg>
      </button>
      {open && (
        <CardBody className="pt-0">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-t3 text-[11px] uppercase tracking-wide">
                <th className="text-left font-semibold pb-2">Section</th>
                <th className="text-right font-semibold pb-2">Score</th>
                <th className="text-right font-semibold pb-2">Weight</th>
                <th className="text-right font-semibold pb-2">Points</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.section} style={{ borderTop: '1px solid rgba(var(--border) / 0.06)' }}>
                  <td className="py-1.5 text-t1 capitalize">{r.section.replace(/_/g, ' ')}</td>
                  {r.excluded ? (
                    <td colSpan={3} className="py-1.5 text-right text-t3 italic">
                      not mentioned in this job ad - excluded
                    </td>
                  ) : (
                    <>
                      <td className="py-1.5 text-right text-t2">{Math.round(r.score)}</td>
                      <td className="py-1.5 text-right text-t3">{Math.round(r.weight * 100)}%</td>
                      <td className="py-1.5 text-right font-semibold text-t1">{r.points}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[11.5px] text-t3 mt-3 leading-relaxed">
            Sections the job ad says nothing about carry no weight - their share is redistributed
            across the rest, so an absent section never drags your score down.
          </p>
        </CardBody>
      )}
    </Card>
  )
}

export function ResultsPanel({ result, delta = null, footer = null }) {
  // Sections the JD said nothing about come back as null - skip their bars
  // rather than drawing a misleading zero.
  const breakdownScores = Object.fromEntries(
    Object.entries(result.breakdown || {})
      .map(([k, v]) => [k, typeof v === 'object' ? v.score : v])
      .filter(([, v]) => v !== null && v !== undefined)
  )
  // Prefer the per-section breakdown (it carries preferred skills too); fall
  // back to the flat keywords list so cached results from older runs still render.
  const requiredSkills = result.breakdown?.required_skills || {
    matched: result.keywords?.matched || [],
    partial: result.keywords?.partial || [],
    missing: result.keywords?.missing || [],
  }
  const preferredSkills = result.breakdown?.preferred_skills || { matched: [], partial: [], missing: [] }

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.25, 0.1, 0.25, 1] }}
    >
      <Card>
        <CardBody className="p-5">
          {result.cached && (
            <div className="mb-3 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-sm text-[11.5px] font-medium"
              style={{ background: 'rgba(99,102,241,0.08)', color: 'rgb(var(--accent))' }}>
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="8" cy="8" r="6.5"/><path d="M8 5v3l2 2"/></svg>
              Instant result - loaded from a previous run of this exact resume and JD
            </div>
          )}
          <div className="flex gap-5">
            <ScoreRing score={Math.round(result.score)} delta={delta} />
            <div className="flex-1 min-w-0 space-y-3">
              {/* What you are actually being scored against */}
              <JobContext job={result.job} />
              {result.summary?.profile && (
                <div>
                  <SectionLabel>Profile summary</SectionLabel>
                  <p className="text-[13px] text-t2 leading-relaxed">
                    {/* Sentences, so joining reads naturally - but each may carry markup */}
                    {(Array.isArray(result.summary.profile) ? result.summary.profile : [result.summary.profile])
                      .map((s, i) => (
                        <span key={i}>
                          {i > 0 && ' '}
                          <RichText text={s} />
                        </span>
                      ))}
                  </p>
                </div>
              )}
            </div>
          </div>
          {Object.keys(breakdownScores).length > 0 && (
            <div className="mt-5 pt-4 space-y-2.5" style={{ borderTop: '1px solid rgba(0,0,0,0.06)' }}>
              <SectionLabel>Section breakdown</SectionLabel>
              {Object.entries(breakdownScores).map(([k, v]) => <ScoreBar key={k} label={k} value={v} />)}
            </div>
          )}
        </CardBody>
      </Card>

      <GatesPanel gates={result.gates} />

      {/* Skills you can prove but a recruiter's keyword search cannot find */}
      <HiddenSkillsAlert evidence={result.evidence} />

      <DutiesPanel duties={result.duties} />

      {/* Required and preferred skills, each showing where it was found */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <SkillsPanel
          title="Required skills"
          skills={requiredSkills}
          evidence={result.evidence?.required}
        />
        <SkillsPanel
          title="Preferred skills"
          skills={preferredSkills}
          evidence={result.evidence?.preferred}
          hint="Nice-to-haves. Missing these is not a blocker."
        />
      </div>

      <SkillImpact impact={result.skill_impact} />

      {(result.summary?.strengths?.length > 0 || result.summary?.gaps?.length > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[
            { label: 'Strengths', items: result.summary.strengths, color: '#16a34a', icon: <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="2,6 5,9 10,3"/></svg>, bg: 'rgba(22,163,74,0.08)', bd: 'rgba(22,163,74,0.25)' },
            { label: 'Gaps to address', items: result.summary.gaps, color: '#d97706', icon: <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3v3M6 8v.5"/></svg>, bg: 'rgba(217,119,6,0.08)', bd: 'rgba(217,119,6,0.25)' },
          ].filter(s => s.items?.length > 0).map(s => (
            <CardSection key={s.label} title={s.label}>
              <ul className="space-y-2">
                {s.items.map((item, i) => (
                  <li key={i} className="flex items-start gap-2.5 text-[13px] text-t1">
                    <span className="w-4 h-4 rounded-full border flex items-center justify-center flex-shrink-0 mt-0.5"
                      style={{ background: s.bg, borderColor: s.bd, color: s.color }}>{s.icon}</span>
                    <span><RichText text={item} /></span>
                  </li>
                ))}
              </ul>
            </CardSection>
          ))}
        </div>
      )}

      {result.summary?.focus && (Array.isArray(result.summary.focus) ? result.summary.focus.length > 0 : result.summary.focus) && (
        <Card>
          <CardBody>
            <SectionLabel>Recommended focus</SectionLabel>
            {/* Each item is a separate action - a joined paragraph reads as a run-on */}
            <ul className="space-y-2">
              {(Array.isArray(result.summary.focus) ? result.summary.focus : [result.summary.focus]).map((item, i) => (
                <li key={i} className="flex items-start gap-2.5 text-[13px] text-t2 leading-relaxed">
                  <span className="w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 mt-px text-[11px] font-bold"
                    style={{ background: 'rgba(var(--accent) / 0.1)', color: 'rgb(var(--accent))' }}>
                    {i + 1}
                  </span>
                  <span><RichText text={item} /></span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <ScoreMath rows={result.score_math} />

      {footer}
    </motion.div>
  )
}
