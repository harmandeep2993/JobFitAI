/**
 * Shared analysis results panel - used by the Analyzer tab and the History
 * modal that reopens past analyses. Renders score ring, section breakdown,
 * keywords, strengths/gaps, and focus recommendation.
 */
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

// === Keyword chip ===
const CHIP_STYLES = {
  matched: { background: 'rgba(22,163,74,0.08)',  borderColor: 'rgba(22,163,74,0.25)',  color: '#16a34a' },
  partial: { background: 'rgba(217,119,6,0.08)',  borderColor: 'rgba(217,119,6,0.25)',  color: '#d97706' },
  missing: { background: 'rgba(220,38,38,0.08)', borderColor: 'rgba(220,38,38,0.25)', color: '#dc2626' },
}

function KeywordChip({ text, kind }) {
  return (
    <span className="px-2.5 py-0.5 rounded-sm text-[12px] font-medium border" style={CHIP_STYLES[kind] || CHIP_STYLES.matched}>
      {text}
    </span>
  )
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

export function ResultsPanel({ result, delta = null, footer = null }) {
  // Sections the JD said nothing about come back as null - skip their bars
  // rather than drawing a misleading zero.
  const breakdownScores = Object.fromEntries(
    Object.entries(result.breakdown || {})
      .map(([k, v]) => [k, typeof v === 'object' ? v.score : v])
      .filter(([, v]) => v !== null && v !== undefined)
  )
  const matchedKw = result.keywords?.matched || []
  const partialKw = result.keywords?.partial || []
  const missingKw = result.keywords?.missing || []

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
            {result.summary?.profile && (
              <div className="flex-1 min-w-0">
                <SectionLabel>Profile summary</SectionLabel>
                <p className="text-[13px] text-t2 leading-relaxed">
                  {Array.isArray(result.summary.profile) ? result.summary.profile.join(' ') : result.summary.profile}
                </p>
              </div>
            )}
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

      <DutiesPanel duties={result.duties} />

      {(matchedKw.length > 0 || partialKw.length > 0 || missingKw.length > 0) && (
        <div className={`grid grid-cols-1 gap-3 ${partialKw.length > 0 ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
          {[
            { label: 'Matched keywords', items: matchedKw, kind: 'matched', empty: 'No matches found.' },
            ...(partialKw.length > 0
              ? [{ label: 'Related skills', items: partialKw, kind: 'partial', empty: '', hint: 'You have something similar - half credit' }]
              : []),
            { label: 'Missing keywords', items: missingKw, kind: 'missing', empty: 'All keywords covered!' },
          ].map(({ label, items, kind, empty, hint }) => (
            <CardSection key={label} title={label} action={
              <span className="text-[11px] font-bold" style={{ color: CHIP_STYLES[kind].color }}>{items.length}</span>
            }>
              {hint && <p className="text-[11.5px] text-t3 mb-2">{hint}</p>}
              {items.length > 0
                ? <div className="flex flex-wrap gap-1.5">{items.map(k => <KeywordChip key={k} text={k} kind={kind} />)}</div>
                : <p className="text-[13px] text-t3">{empty}</p>
              }
            </CardSection>
          ))}
        </div>
      )}

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
                    {item}
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
            <p className="text-[13px] text-t2 leading-relaxed">
              {Array.isArray(result.summary.focus) ? result.summary.focus.join(' ') : result.summary.focus}
            </p>
          </CardBody>
        </Card>
      )}

      {footer}
    </motion.div>
  )
}
