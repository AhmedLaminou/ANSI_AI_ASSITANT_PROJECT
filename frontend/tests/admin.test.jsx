/* Administration, documents and profile: accessibility, and the signals the
 * supervision screen is supposed to state outright. */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Administration from '../src/Administration.jsx'
import { DocumentsView } from '../src/App.jsx'
import Profile from '../src/Profile.jsx'
import { accessibilityViolations, scriptFetch } from './helpers.js'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const ADMIN = { id: 1, username: 'admin', role: 'admin', email: 'admin@ansi.ne', department: 'technique',
                department_label: 'Technique / informatique', sees_every_department: true }

const OVERVIEW = {
  documents: { total: 3, chunks: 120, by_classification: { interne: 3 }, expired: 0, last_import: null },
  accounts: { total: 4, active: 4, pending: 1, refused: 0, suspended: 0, unattached: 0, without_email: 2,
              password_resets_pending: 1, by_role: { admin: 1, user: 3 } },
  departments: [
    { value: 'finance', label: 'Finance / comptabilité', documents: 0, chunks: 0, accounts: 1, last_import: null },
    { value: 'transverse', label: 'Transverse', documents: 3, chunks: 120, accounts: null, last_import: null },
  ],
  feedback: { useful: 2, wrong: 1 },
  activity_7d: { document_uploaded: 3 },
  reviews: {
    overdue: 1, due_soon: 0, without_owner: 2,
    items: [{ id: 1, title: 'Procédure congés', department_label: 'RH', review_due: '2026-01-01T00:00:00+00:00',
              review_status: 'overdue', owner: 'amina', owner_inactive: false }],
  },
  gaps: { unanswered: 14 },
  contacts_missing: ['Logistique'],
  model: { chat: 'qwen3:4b', embedding: 'embeddinggemma', ocr: true, database: 'SQLite', retention_days: 0 },
}

describe('supervision', () => {
  it('states what the figures imply', async () => {
    scriptFetch({ 'GET /admin/overview': { body: OVERVIEW } })
    render(<Administration user={ADMIN} section="supervision" onSection={() => undefined} onToast={() => undefined} />)
    expect(await screen.findByText(/14 question\(s\) sans réponse/)).toBeTruthy()
    expect(screen.getByText(/1 document\(s\) ont dépassé leur date de révision/)).toBeTruthy()
    expect(screen.getByText(/Aucun contact pour : Logistique/)).toBeTruthy()
    expect(screen.getByText(/1 agent\(s\) attendent un mot de passe provisoire/)).toBeTruthy()
    expect(screen.getByText(/Durée de rétention non fixée/)).toBeTruthy()
  })

  it('has no accessibility violation', async () => {
    scriptFetch({ 'GET /admin/overview': { body: OVERVIEW } })
    const { container } = render(
      <Administration user={ADMIN} section="supervision" onSection={() => undefined} onToast={() => undefined} />,
    )
    await screen.findByText(/14 question\(s\)/)
    expect(await accessibilityViolations(container)).toEqual([])
  })

  it('lists gaps with their count, and has no accessibility violation', async () => {
    scriptFetch({
      'GET /admin/overview': { body: OVERVIEW },
      'GET /admin/gaps': { body: { total_questions: 3, gaps: [{
        representative: 'Politique de covoiturage ?', count: 3, questions: ['a', 'b', 'c'],
        departments: { 'Finance / comptabilité': 3 }, reasons: { 'extraits jugés insuffisants par le modèle': 3 },
        first_seen: '2026-09-01T00:00:00+00:00', last_seen: '2026-09-20T00:00:00+00:00', ids: [1, 2, 3],
      }] } },
    })
    const { container } = render(
      <Administration user={ADMIN} section="lacunes" onSection={() => undefined} onToast={() => undefined} />,
    )
    expect(await screen.findByText('Politique de covoiturage ?')).toBeTruthy()
    expect(screen.getByLabelText('3 demande(s)')).toBeTruthy()
    expect(await accessibilityViolations(container)).toEqual([])
  })
})

describe('documents', () => {
  const documents = [{
    id: 1, title: 'Procédure congés', filename: 'conges.pdf', classification: 'interne',
    allowed_roles: ['admin', 'user'], created_at: '2026-09-01T00:00:00+00:00', version: 1, is_current: true,
    department: 'rh', department_label: 'Ressources humaines', valid_until: null, is_expired: false,
    owner_id: null, owner: null, review_due: null, review_status: 'none',
  }]

  it('has no accessibility violation', async () => {
    scriptFetch({})
    const { container } = render(
      <DocumentsView user={ADMIN} documents={documents} onRefresh={() => undefined} onToast={() => undefined} />,
    )
    expect(await accessibilityViolations(container)).toEqual([])
  })

  it('shows a document nobody answers for', () => {
    scriptFetch({})
    render(<DocumentsView user={ADMIN} documents={documents} onRefresh={() => undefined} onToast={() => undefined} />)
    expect(screen.getByText('sans responsable')).toBeTruthy()
  })
})

describe('profile', () => {
  it('has no accessibility violation', async () => {
    scriptFetch({
      'GET /auth/profile': { body: {
        account: {}, rights: ['poser des questions'],
        readable: { total: 1, by_department: { Transverse: 1 }, unreachable: ['Finance / comptabilité'] },
        activity: { conversations: 1, questions: 2, feedback_useful: 0, feedback_wrong: 0, last_action: null },
        owned_documents: [{ id: 1, title: 'Procédure', department_label: 'RH', review_due: null, review_status: 'none' }],
      } },
      'GET /contacts': { body: [{ department: 'rh', department_label: 'Ressources humaines', name: 'Amina',
                                   email: 'amina@ansi.ne', phone: '', note: '' }] },
    })
    const { container } = render(<Profile user={{ ...ADMIN, role: 'user', sees_every_department: false }}
                                          onToast={() => undefined} />)
    await screen.findByText('Qui contacter')
    expect(await accessibilityViolations(container)).toEqual([])
  })
})
