/* Pieces used by more than one screen.
 *
 * Extracted from App.jsx when the administration section arrived: a single 1600-line
 * file was already hard to navigate, and two files importing each other would have
 * been a cycle. Everything here is shared downwards only — nothing in this file
 * imports a screen. */

import { cloneElement, useId } from 'react'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const ROLES = ['admin', 'document_manager', 'user']
export const CLASSIFICATIONS = ['interne', 'direction', 'confidentiel']

// Mirrors backend/app/access.py. "transverse" is not a department: it is the
// perimeter of documents that concern everyone.
export const DEPARTMENTS = [
  { value: 'technique', label: 'Technique / informatique' },
  { value: 'finance', label: 'Finance / comptabilité' },
  { value: 'logistique', label: 'Logistique' },
  { value: 'rh', label: 'Ressources humaines' },
]
export const DOCUMENT_DEPARTMENTS = [
  ...DEPARTMENTS,
  { value: 'transverse', label: 'Transverse (tous services)' },
]

export function departmentLabel(value) {
  return DOCUMENT_DEPARTMENTS.find((item) => item.value === value)?.label ?? 'Non rattaché'
}

export async function request(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, { credentials: 'include', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    // The API returns `detail` as a string, including for validation errors.
    // This stays defensive anyway: an array reaching `new Error()` would render
    // as "[object Object]", which is what a user saw before the server-side fix.
    const detail = body.detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item?.msg ?? String(item)).join(' ')
        : 'Une erreur est survenue.'
    // A 401 after sign-in means the session was closed from elsewhere. The two paths
    // excluded answer 401 in the normal course of things: a wrong password, and the
    // first check on page load before anyone has signed in.
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me') {
      window.dispatchEvent(new CustomEvent('ansi:session-lost', { detail: message }))
    }
    throw new Error(message)
  }
  return response.status === 204 ? null : response.json()
}

/** Reads the NDJSON answer stream and hands each event to onEvent. */
export async function streamChat(body, onEvent) {
  const response = await fetch(`${API_URL}/chat/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}))
    const detail = failure.detail
    throw new Error(typeof detail === 'string' ? detail : 'Une erreur est survenue.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line))
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer))
}

export function Icon({ name, className = '' }) {
  return (
    <svg className={`icon ${className}`} aria-hidden="true">
      <use href={`/icons.svg#icon-${name}`} />
    </svg>
  )
}

export function formatDate(value, withTime = true) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const day = date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
  if (!withTime) return day
  return `${day} à ${date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`
}

/** A form field whose input is named by its label alone.
 *
 * Wrapping an input in a <label> names it after *all* the label's text, hint
 * included: a screen reader announced « Adresse professionnelle Les comptes créés
 * avant l'adresse se connectent… » for a single field. Here the name comes from the
 * label text only (aria-labelledby, which takes precedence) and the hint becomes the
 * description (aria-describedby) — read after the name, and only on request. The
 * markup keeps its <label> wrapper, so the layout does not change. */
export function Field({ label, hint, children, className }) {
  const id = useId()
  const labelId = `${id}-label`
  const hintId = `${id}-hint`
  return (
    <label className={className}>
      <span id={labelId}>{label}</span>
      {cloneElement(children, {
        'aria-labelledby': labelId,
        'aria-describedby': hint ? hintId : undefined,
      })}
      {hint && (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      )}
    </label>
  )
}
