/**
 * Maps raw API error codes to human-readable messages with a suggested action.
 * Every tab uses errMsg() so users never see codes like "jd_extraction_failed".
 */

const MESSAGES = {
  jd_too_short: 'The job description is too short - paste the full posting (at least 50 characters).',
  jd_required: 'Paste a job description first.',
  llm_unavailable: 'The AI provider is unreachable right now - try again in a minute.',
  could_not_parse_resume: 'We could not read that resume file - try re-saving it as PDF and uploading again.',
  resume_extraction_failed: 'The AI could not extract data from this resume - try again, or use a simpler layout.',
  jd_extraction_failed: 'The AI could not understand this job description - check it is complete text, then retry.',
  scoring_failed: 'Scoring failed unexpectedly - please try again.',
  could_not_read_resume: 'We could not read that resume file - try re-saving it as PDF and uploading again.',
  resume_not_found: 'That resume is no longer available - select or upload it again.',
  resume_required: 'Select or upload a resume first.',
  not_extracted_yet: 'This resume is still being processed - wait a moment and try again.',
  extraction_failed: 'Extraction failed - the file may be image-based or too short.',
  no_resume: 'Load a resume first: go to Resumes and click "Use for matching".',
  invalid_credentials: 'Wrong email or password.',
  email_already_registered: 'An account with this email already exists - log in instead.',
  wrong_current_password: 'Your password is incorrect.',
  too_many_attempts_try_again_later: 'Too many attempts - wait a minute and try again.',
  admin_required: 'Only the admin can change this setting.',
  invalid_or_expired_token: 'Your session expired - please log in again.',
  invalid_invite_code: 'That invite code is not valid. JobFitAI is currently invite-only - contact us to get access.',
  no_data: 'No stored resume data found - upload a resume in the Resumes tab first.',
  llm_failed: 'The AI provider did not respond - try again in a minute.',
  parse_failed: 'The AI response could not be processed - please try again.',
  expired: 'The full result of this analysis is no longer stored - re-run it from the Analyser.',
  rate_limited: 'You have used a lot of AI requests in a short time - wait a few minutes and try again.',
  payload_too_large: 'That request is too large - the maximum upload size is 10 MB.',
}

const FALLBACK = 'Something went wrong - please try again.'

export function errMsg(dataOrCode, fallback = FALLBACK) {
  const code = typeof dataOrCode === 'string'
    ? dataOrCode
    : dataOrCode?.error || dataOrCode?.detail || dataOrCode?.reason || ''
  if (MESSAGES[code]) return MESSAGES[code]
  // A code containing spaces is already a human sentence - pass it through
  if (typeof code === 'string' && code.includes(' ')) return code
  return fallback
}
