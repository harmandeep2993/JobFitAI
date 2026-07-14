/**
 * ATS Check tab - resume picker (stored or new upload) + JD side by side.
 * Check runs the deterministic scan; Optimise rewrites via LLM with a
 * before/after coverage comparison and DOCX download.
 */
import { useState } from 'react'
import { apiFetch } from '../../lib/auth.js'
import { errMsg } from '../../lib/errors.js'
import { useToast } from '../Toast.jsx'
import { ResumePicker } from '../ResumePicker.jsx'
import {
  PageHeader, Card, CardBody, SectionLabel, Spinner,
} from '../ui.jsx'

const INNER_BG     = 'rgba(99,102,241,0.04)'
const INNER_BORDER = 'rgba(99,102,241,0.18)'
const BOX_HEIGHT   = '218px'

function ClearBtn({ onClick }) {
  return (
    <button
      onClick={onClick}
      className="w-6 h-6 flex items-center justify-center rounded-sm transition-colors flex-shrink-0"
      title="Clear"
      style={{ color: 'rgb(var(--accent))', background: 'rgba(99,102,241,0.08)' }}
      onMouseEnter={e => { e.currentTarget.style.background = 'rgba(99,102,241,0.16)' }}
      onMouseLeave={e => { e.currentTarget.style.background = 'rgba(99,102,241,0.08)' }}
    >
      <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 2l10 10M12 2L2 12"/>
      </svg>
    </button>
  )
}

function SectionFlag({ label, ok, suggestion }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-sm border text-[12.5px] font-medium"
      title={suggestion || ''}
      style={ok
        ? { background: 'rgba(22,163,74,0.08)', borderColor: 'rgba(22,163,74,0.25)', color: '#16a34a' }
        : { background: 'rgba(220,38,38,0.08)', borderColor: 'rgba(220,38,38,0.25)', color: '#dc2626' }
      }
    >
      {ok
        ? <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 7l3 3 6-6"/></svg>
        : <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3l8 8M11 3L3 11"/></svg>
      }
      {label}
    </div>
  )
}

function CoverageChips({ coverage }) {
  if (!coverage) return null
  return (
    <div className="space-y-2">
      {coverage.matched?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {coverage.matched.map(k => (
            <span key={k} className="px-2 py-0.5 text-[11px] font-medium rounded-sm border"
              style={{ background: 'rgba(22,163,74,0.08)', borderColor: 'rgba(22,163,74,0.25)', color: '#16a34a' }}>{k}</span>
          ))}
        </div>
      )}
      {coverage.missing?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {coverage.missing.map(k => (
            <span key={k} className="px-2 py-0.5 text-[11px] font-medium rounded-sm border"
              style={{ background: 'rgba(220,38,38,0.08)', borderColor: 'rgba(220,38,38,0.25)', color: '#dc2626' }}>{k}</span>
          ))}
        </div>
      )}
    </div>
  )
}

// Resolve the request body identifying the selected resume: stored id or temp token
async function resumeBody(resumeSrc, toast) {
  if (resumeSrc.resumeId) return { resume_id: resumeSrc.resumeId }
  const fd = new FormData()
  fd.append('file', resumeSrc.file)
  const up = await apiFetch('/api/upload', { method: 'POST', body: fd })
  const upData = await up?.json().catch(() => ({}))
  if (!up?.ok || !upData.ok) { toast(errMsg(upData, 'Upload failed'), 'error'); return null }
  return { tmp: upData.tmp }
}

export default function ATS() {
  const toast = useToast()
  const [resumeSrc, setResumeSrc] = useState(null)
  const [jd, setJd] = useState('')
  const [checkResult, setCheckResult] = useState(null)
  const [optimResult, setOptimResult] = useState(null)
  const [loading, setLoading] = useState('')

  async function check() {
    if (!resumeSrc) { toast('Select or upload a resume first', 'warn'); return }
    setLoading('check'); setCheckResult(null)
    try {
      const ident = await resumeBody(resumeSrc, toast)
      if (!ident) return
      const res = await apiFetch('/api/ats/check', { method: 'POST', body: JSON.stringify({ ...ident, jd }) })
      const data = await res?.json().catch(() => ({}))
      if (!res?.ok || !data.ok) { toast(errMsg(data, 'Check failed'), 'error'); return }
      setCheckResult(data)
    } finally { setLoading('') }
  }

  async function optimise() {
    if (!resumeSrc) { toast('Select or upload a resume first', 'warn'); return }
    if (jd.trim().length < 100) { toast('Paste a job description to optimise against', 'warn'); return }
    setLoading('optimise'); setOptimResult(null)
    try {
      const ident = await resumeBody(resumeSrc, toast)
      if (!ident) return
      const res = await apiFetch('/api/ats/optimise', { method: 'POST', body: JSON.stringify({ ...ident, jd }) })
      const data = await res?.json().catch(() => ({}))
      if (!res?.ok || !data.ok) { toast(errMsg(data, 'Optimise failed'), 'error'); return }
      setOptimResult(data)
    } finally { setLoading('') }
  }

  async function downloadDocx(layout = 'single') {
    if (!optimResult?.resume) return
    const res = await apiFetch('/api/ats/docx', {
      method: 'POST',
      body: JSON.stringify({ resume: optimResult.resume, layout }),
    })
    if (!res?.ok) { toast('Download failed', 'error'); return }
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = layout === 'two_column'
      ? 'resume_two_column.docx'
      : 'resume_ats_optimised.docx'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  function copyText() {
    if (!optimResult?.plain_text) return
    navigator.clipboard.writeText(optimResult.plain_text)
    toast('Copied to clipboard', 'success')
  }

  const atsScore = checkResult?.ats_score
  const scoreColor =
    atsScore?.score >= 80 ? '#16a34a' :
    atsScore?.score >= 60 ? '#6366f1' :
    atsScore?.score >= 40 ? '#d97706' : '#dc2626'

  const covBefore = optimResult?.coverage_before?.pct
  const covAfter = optimResult?.coverage_after?.pct

  return (
    <div className="space-y-5">
      <PageHeader
        title="ATS Check"
        description="Choose a stored resume or upload a new one to scan for ATS compatibility issues."
      />

      <div className="space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Resume picker */}
          <Card>
            <div className="px-5 py-3 border-b flex items-center justify-between" style={{ borderColor: 'rgba(0,0,0,0.06)' }}>
              <span className="text-[13px] font-semibold text-t1">Resume</span>
              {resumeSrc && <ClearBtn onClick={() => { setResumeSrc(null); setCheckResult(null); setOptimResult(null) }} />}
            </div>
            <CardBody className="p-4">
              <ResumePicker
                selected={resumeSrc}
                onSelect={src => { setResumeSrc(src); setCheckResult(null); setOptimResult(null) }}
                onClear={() => { setResumeSrc(null); setCheckResult(null); setOptimResult(null) }}
                height={BOX_HEIGHT}
              />
            </CardBody>
          </Card>

          {/* JD box */}
          <Card>
            <div className="px-5 py-3 border-b flex items-center justify-between" style={{ borderColor: 'rgba(0,0,0,0.06)' }}>
              <span className="text-[13px] font-semibold text-t1">Job Description</span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-t3">Optional for Check - required for AI Optimise</span>
                {jd && <ClearBtn onClick={() => { setJd(''); setOptimResult(null) }} />}
              </div>
            </div>
            <CardBody className="p-4">
              <div className="relative" style={{ height: BOX_HEIGHT }}>
                {!jd && (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 pointer-events-none select-none rounded-lg"
                    style={{ background: INNER_BG, border: `2px dashed ${INNER_BORDER}` }}>
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: 'rgba(99,102,241,0.1)' }}>
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="rgb(var(--accent))" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/>
                      </svg>
                    </div>
                    <div className="text-[13px] font-semibold text-t2">Paste job description</div>
                    <div className="text-[12px] text-t3 text-center px-6">Paste the full job description here...</div>
                  </div>
                )}
                <textarea
                  value={jd}
                  onChange={e => setJd(e.target.value)}
                  className="input-base resize-none w-full h-full"
                  style={{
                    background: jd ? INNER_BG : 'transparent',
                    border: jd ? `2px dashed ${INNER_BORDER}` : '2px dashed transparent',
                    position: 'relative',
                    zIndex: 1,
                  }}
                />
              </div>
            </CardBody>
          </Card>
        </div>

        {/* Action buttons - bottom right */}
        <div className="flex justify-end gap-2">
          <button onClick={check} disabled={!!loading} className="btn-secondary px-5">
            {loading === 'check' ? <><Spinner size={13} /> Checking...</> : 'ATS Check'}
          </button>
          <button onClick={optimise} disabled={!!loading} className="btn-primary px-5">
            {loading === 'optimise' ? <><Spinner size={13} /> Optimising (30-60s)...</> : 'AI Optimise'}
          </button>
        </div>
      </div>

      {/* ATS check result */}
      {checkResult && (
        <Card>
          <CardBody className="space-y-5">
            <div className="flex items-center gap-3">
              <SectionLabel>ATS keyword score</SectionLabel>
              {atsScore?.has_jd ? (
                <span className="text-2xl font-bold" style={{ color: scoreColor }}>
                  {Math.round(atsScore.score || 0)}
                  <span className="text-[13px] font-normal text-t3 ml-1">/ 100</span>
                </span>
              ) : (
                <span className="text-[12.5px] text-t3">Paste a job description to get a keyword score</span>
              )}
            </div>

            {checkResult.coverage && (
              <div>
                <SectionLabel>
                  Keyword coverage - {checkResult.coverage.matched?.length || 0} of {checkResult.coverage.total || 0} found
                </SectionLabel>
                <CoverageChips coverage={checkResult.coverage} />
              </div>
            )}

            {checkResult.section_flags?.length > 0 && (
              <div>
                <SectionLabel>Section presence</SectionLabel>
                <div className="flex flex-wrap gap-2">
                  {checkResult.section_flags.map(f => (
                    <SectionFlag key={f.name} label={f.name} ok={f.found} suggestion={f.suggestion} />
                  ))}
                </div>
                {checkResult.section_flags.some(f => !f.found) && (
                  <ul className="mt-3 space-y-1">
                    {checkResult.section_flags.filter(f => !f.found && f.suggestion).map(f => (
                      <li key={f.name} className="text-[12.5px] text-t2">- {f.suggestion}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {checkResult.formatting_flags?.length > 0 ? (
              <div>
                <SectionLabel>Formatting warnings</SectionLabel>
                <ul className="space-y-1.5">
                  {checkResult.formatting_flags.map((f, i) => (
                    <li key={i} className="flex items-start gap-2 text-[13px]" style={{ color: '#d97706' }}>
                      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="flex-shrink-0 mt-0.5">
                        <path d="M8 3v5M8 11v.5"/><path d="M8 1L1 13h14L8 1z"/>
                      </svg>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-[13px]" style={{ color: '#16a34a' }}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 8l3.5 3.5L13 5"/></svg>
                No formatting issues detected
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {/* Optimise result */}
      {optimResult && (
        <Card>
          <CardBody className="space-y-4">
            <div className="flex items-center justify-between">
              <SectionLabel>Optimised Resume</SectionLabel>
              <div className="flex gap-2">
                <button onClick={copyText} className="btn-secondary h-7 px-3 text-[12.5px]">Copy text</button>
                <button onClick={() => downloadDocx('two_column')} className="btn-secondary h-7 px-3 text-[12.5px]"
                  title="Two columns via Word column layout - never a table, so ATS still reads it in order">
                  Two-column DOCX
                </button>
                <button onClick={() => downloadDocx('single')} className="btn-primary h-7 px-3 text-[12.5px]"
                  title="Single column - the safest layout for any ATS">
                  Download DOCX
                </button>
              </div>
            </div>

            {covBefore !== undefined && covAfter !== undefined && (
              <div className="flex items-center gap-3 rounded-lg px-4 py-3" style={{ background: 'rgb(var(--surface-2))' }}>
                <span className="text-[12.5px] text-t2">Keyword coverage:</span>
                <span className="font-bold" style={{ color: '#dc2626' }}>{Math.round(covBefore)}%</span>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="rgb(var(--t3))" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 8h10M9 4l4 4-4 4"/></svg>
                <span className="font-bold" style={{ color: '#16a34a' }}>{Math.round(covAfter)}%</span>
                {optimResult.coverage_after?.missing?.length > 0 && (
                  <span className="text-[12px] text-t3 ml-2">
                    Still missing: {optimResult.coverage_after.missing.slice(0, 5).join(', ')}
                  </span>
                )}
              </div>
            )}

            {/* Anything the writer claimed that the source resume does not back up.
                Shown so the user can trust what is left - and see what would have
                been a lie on a document they sign their name to. */}
            {optimResult.removed_unsupported?.length > 0 && (
              <div className="rounded-lg border p-3.5"
                style={{ background: 'rgba(22,163,74,0.05)', borderColor: 'rgba(22,163,74,0.25)' }}>
                <div className="flex items-center gap-2 mb-1.5">
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="#16a34a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M8 1.5l5.5 2.5v4c0 3-2.2 5-5.5 6-3.3-1-5.5-3-5.5-6v-4L8 1.5z"/><path d="M6 8l1.5 1.5L10.5 6.5"/>
                  </svg>
                  <span className="text-[12.5px] font-semibold" style={{ color: '#16a34a' }}>
                    Accuracy check: removed {optimResult.removed_unsupported.length} unsupported claim{optimResult.removed_unsupported.length > 1 ? 's' : ''}
                  </span>
                </div>
                <p className="text-[12px] text-t2 leading-relaxed mb-2">
                  The AI suggested these, but your resume does not evidence them - so they were stripped.
                  Everything below is backed by your real experience.
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {optimResult.removed_unsupported.map((r, i) => (
                    <span key={i} className="px-2 py-0.5 text-[11.5px] rounded-sm line-through"
                      style={{ background: 'rgb(var(--surface-2))', color: 'rgb(var(--t3))' }}>
                      {r}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {optimResult.plain_text && (
              <pre className="rounded-lg p-4 text-[12px] text-t1 whitespace-pre-wrap overflow-auto max-h-96 leading-relaxed"
                style={{ background: 'rgb(var(--surface-2))', border: '1px solid rgba(0,0,0,0.06)' }}>
                {optimResult.plain_text}
              </pre>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  )
}
