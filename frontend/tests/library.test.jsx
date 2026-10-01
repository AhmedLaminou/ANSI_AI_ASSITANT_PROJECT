/* The library at scale, and the pieces it is built from.
 *
 * What is asserted is what the screen sends: a page of the library is decided by
 * the server, so a filter that never reaches the query string is a filter that does
 * nothing — the kind of defect that looks fine on a ten-document corpus. */

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Overview } from '../src/App.jsx'
import { DocumentsView, readLibraryQuery, writeLibraryQuery } from '../src/Documents.jsx'
import { passwordChecks } from '../src/Profile.jsx'
import Users from '../src/Users.jsx'
import { Pagination, countOf, pageWindow, toCsv } from '../src/ui.jsx'
import { accessibilityViolations, libraryPage, scriptFetch } from './helpers.js'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const ADMIN = { id: 1, username: 'admin', role: 'admin', email: 'admin@ansi.ne', department: 'technique',
                department_label: 'Technique / informatique', sees_every_department: true }
const READER = { ...ADMIN, id: 7, username: 'agent', role: 'user', department: 'rh', sees_every_department: false }

function doc(id, title, extra = {}) {
  return {
    id, title, filename: `${title.toLowerCase().replace(/\s+/g, '_')}.pdf`, classification: 'interne',
    allowed_roles: ['admin', 'document_manager', 'user'], created_at: '2026-09-01T00:00:00+00:00', version: 1,
    is_current: true, department: 'rh', department_label: 'Ressources humaines', valid_until: null,
    is_expired: false, owner_id: 1, owner: 'admin', review_due: null, review_status: 'none', ...extra,
  }
}

const TWO = [doc(1, 'Procédure congés'), doc(2, 'Note budgétaire', { department: 'finance', department_label: 'Finance / comptabilité' })]

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

describe('page numbers', () => {
  it.each([
    [1, 5, [1, 2, 3, 4, 5]],
    [1, 14, [1, 2, 3, 4, 5, '…', 14]],
    [7, 14, [1, '…', 6, 7, 8, '…', 14]],
    [13, 14, [1, '…', 10, 11, 12, 13, 14]],
  ])('page %i of %i', (page, pages, expected) => {
    expect(pageWindow(page, pages)).toEqual(expected)
  })

  it('marks the current page and moves on click', () => {
    const onPage = vi.fn()
    render(<Pagination page={3} pages={9} onPage={onPage} />)
    expect(screen.getByRole('button', { name: 'Page 3' }).getAttribute('aria-current')).toBe('page')
    fireEvent.click(screen.getByRole('button', { name: 'Page suivante' }))
    expect(onPage).toHaveBeenCalledWith(4)
  })

  it('says nothing when everything fits on one page', () => {
    const { container } = render(<Pagination page={1} pages={1} onPage={() => undefined} />)
    expect(container.innerHTML).toBe('')
  })
})

describe('the address of a view', () => {
  it('reads defaults and keeps the address short', () => {
    const state = readLibraryQuery({ service: 'rh', page: '3', tri: 'nimporte' })
    expect(state).toMatchObject({ service: 'rh', page: 3, tri: 'recent', taille: 10, mode: 'titres' })
    expect(writeLibraryQuery(state)).toEqual(expect.objectContaining({ service: 'rh', page: 3, tri: undefined, taille: undefined }))
  })
})

// ---------------------------------------------------------------------------
// The library
// ---------------------------------------------------------------------------

describe('the library', () => {
  it('asks the server for one page, and for the next one', async () => {
    const calls = scriptFetch({ 'GET /documents/page': { body: libraryPage(TWO, { total: 23, pages: 3, last: 10 }) } })
    render(<DocumentsView user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('Procédure congés')
    expect(calls[0].path).toContain('page=1')
    expect(calls[0].path).toContain('size=10')
    fireEvent.click(screen.getByRole('button', { name: 'Page 2' }))
    await waitFor(() => expect(calls.some((call) => call.path.includes('page=2'))).toBe(true))
  })

  it('sends the search, after the typing stops', async () => {
    const calls = scriptFetch({ 'GET /documents/page': { body: libraryPage(TWO) } })
    render(<DocumentsView user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('Procédure congés')
    fireEvent.change(screen.getByRole('searchbox', { name: 'Rechercher un document' }), { target: { value: 'congés' } })
    await waitFor(() => expect(calls.some((call) => call.path.includes('q=cong'))).toBe(true), { timeout: 2000 })
  })

  it('filters by service from the counts the server gave', async () => {
    const calls = scriptFetch({ 'GET /documents/page': { body: libraryPage(TWO) } })
    render(<DocumentsView user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('Procédure congés')
    const services = screen.getByRole('group', { name: 'Filtrer par service' })
    fireEvent.click(within(services).getByRole('button', { name: /Finances/ }))
    await waitFor(() => expect(calls.some((call) => call.path.includes('department=finance'))).toBe(true))
  })

  it('keeps favourites in this browser and asks only for them', async () => {
    const calls = scriptFetch({ 'GET /documents/page': { body: libraryPage(TWO) } })
    render(<DocumentsView user={READER} onToast={() => undefined} />)
    await screen.findByText('Procédure congés')
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter « Procédure congés » aux favoris' }))
    expect(JSON.parse(window.localStorage.getItem('ansi-favoris-7'))).toEqual([1])
    fireEvent.click(screen.getByRole('button', { name: /Favoris/ }))
    await waitFor(() => expect(calls.some((call) => call.path.includes('ids=1'))).toBe(true))
  })

  it('opens a document beside the list, with what is known about it', async () => {
    const calls = scriptFetch({
      'GET /documents/page': { body: libraryPage(TWO) },
      'GET /documents/1/preview': { body: { document: TWO[0], chunks: [{ page: 2, content: 'Trente jours ouvrables.' }], chunks_total: 14 } },
    })
    const { container } = render(<DocumentsView user={READER} onToast={() => undefined} onAsk={() => undefined} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Procédure congés' }))
    const panel = await screen.findByRole('dialog', { name: 'Procédure congés' })
    expect(within(panel).getByText('Trente jours ouvrables.')).toBeTruthy()
    expect(within(panel).getByText('14')).toBeTruthy()
    expect(calls.some((call) => call.path === '/documents/1/preview')).toBe(true)
    // A reader sees no administration action.
    expect(within(panel).queryByText('Supprimer')).toBeNull()
    expect(await accessibilityViolations(container)).toEqual([])
  })

  it('offers the whole document, not only its first excerpts', async () => {
    scriptFetch({
      'GET /documents/page': { body: libraryPage(TWO) },
      'GET /documents/1/preview': { body: { document: TWO[0], chunks: [{ page: 1, content: 'Début.' }], chunks_total: 14 } },
    })
    render(<DocumentsView user={READER} onToast={() => undefined} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Procédure congés' }))
    const panel = await screen.findByRole('dialog', { name: 'Procédure congés' })
    const link = within(panel).getByRole('link', { name: /Ouvrir le document complet/ })
    expect(link.getAttribute('href')).toMatch(/\/documents\/1\/file$/)
    expect(link.getAttribute('target')).toBe('_blank')
    // The excerpts say what they are: a beginning, not the document.
    expect(within(panel).getByText('Aperçu : le premier extrait sur 14')).toBeTruthy()
  })

  it('offers a Word document as a download, which a browser cannot display', async () => {
    const word = doc(3, 'Note de service', { filename: 'note_de_service.docx' })
    scriptFetch({
      'GET /documents/page': { body: libraryPage([word]) },
      'GET /documents/3/preview': { body: { document: word, chunks: [], chunks_total: 0 } },
    })
    render(<DocumentsView user={READER} onToast={() => undefined} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Note de service' }))
    const panel = await screen.findByRole('dialog', { name: 'Note de service' })
    const link = within(panel).getByRole('link', { name: /Télécharger le document/ })
    expect(link.getAttribute('href')).toMatch(/\/documents\/3\/file\?download=true$/)
  })

  it('moves a selection to another service, document by document', async () => {
    const calls = scriptFetch({
      'GET /documents/page': { body: libraryPage(TWO) },
      'PATCH /documents/1': { body: {} },
      'PATCH /documents/2': { body: {} },
    })
    render(<DocumentsView user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('Procédure congés')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Sélectionner tous les documents de la page' }))
    const bar = screen.getByRole('region', { name: 'Actions sur la sélection' })
    fireEvent.change(within(bar).getByRole('combobox'), { target: { value: 'logistique' } })
    fireEvent.click(within(bar).getByRole('button', { name: /Déplacer/ }))
    await waitFor(() => expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(2))
    expect(JSON.parse(calls.find((call) => call.method === 'PATCH').body)).toEqual({ department: 'logistique' })
  })

  it('offers no selection to someone who cannot act on it', async () => {
    scriptFetch({ 'GET /documents/page': { body: libraryPage(TWO) } })
    render(<DocumentsView user={READER} onToast={() => undefined} />)
    await screen.findByText('Procédure congés')
    expect(screen.queryByRole('checkbox', { name: /Sélectionner/ })).toBeNull()
  })

  it('says what to do when nothing matches', async () => {
    scriptFetch({ 'GET /documents/page': { body: libraryPage([], { readable_total: 40 }) } })
    render(<DocumentsView user={READER} query={{ q: 'introuvable' }} onToast={() => undefined} />)
    expect(await screen.findByText('Aucun document ne correspond')).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// Home, accounts, and small rules
// ---------------------------------------------------------------------------

describe('the home page', () => {
  it('has no accessibility violation', async () => {
    scriptFetch({ 'GET /auth/profile': { body: { activity: { conversations: 3, questions: 12 } } } })
    const { container } = render(
      <Overview user={READER} system={{ chat_model_ready: true, embedding_model_ready: true, chat_model: 'qwen3:4b',
                                        embedding_model: 'embeddinggemma' }}
                library={libraryPage(TWO)} onNavigate={() => undefined} onOpenDocument={() => undefined} />,
    )
    await screen.findByText('12')
    expect(await accessibilityViolations(container)).toEqual([])
  })
})

describe('accounts at scale', () => {
  it('pages the list and searches it', async () => {
    const accounts = Array.from({ length: 12 }, (_, index) => ({
      id: index + 2, username: `agent${index + 1}`, email: `agent${index + 1}@ansi.ne`, role: 'user',
      department: 'rh', department_label: 'Ressources humaines', is_active: true, status: 'active',
      must_change_password: false,
    }))
    scriptFetch({
      'GET /admin/users': { body: accounts },
      'GET /admin/registrations': { body: [] },
      'GET /admin/password-resets': { body: [] },
    })
    render(<Users user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('agent1')
    expect(screen.queryByText('agent11')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Page 2' }))
    expect(await screen.findByText('agent11')).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Rechercher un compte' }), { target: { value: 'agent12' } })
    expect(await screen.findByText('agent12')).toBeTruthy()
    expect(screen.queryByText('agent3')).toBeNull()
  })
})

describe('small rules', () => {
  it('never lets a spreadsheet run a cell as a formula', () => {
    expect(toCsv([['=HYPERLINK("x")', 'a;b', '-2']])).toBe(`"'=HYPERLINK(""x"")";"a;b";'-2`)
  })

  it('agrees with the count, the French way', () => {
    expect(countOf(0, 'document')).toBe('0 document')
    expect(countOf(1, 'compte actif', 'comptes actifs')).toBe('1 compte actif')
    expect(countOf(3, 'compte actif', 'comptes actifs')).toBe('3 comptes actifs')
  })

  it('states the password rules one by one', () => {
    expect(passwordChecks('abcdefghijkl').map((check) => check.ok)).toEqual([true, false, false, false])
    expect(passwordChecks('Abcdefghij-1').every((check) => check.ok)).toBe(true)
  })
})
