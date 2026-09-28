/* The plumbing every screen relies on: errors, lost sessions, addresses, the toast
 * hook. Each case here is a defect that reached a user before being fixed. */

import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useToasts } from '../src/App.jsx'
import { locationToRoute, routeToPath } from '../src/routing.js'
import { departmentLabel, request } from '../src/shared.jsx'
import { scriptFetch } from './helpers.js'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request() errors', () => {
  it('shows the sentence the API sends', async () => {
    scriptFetch({ 'POST /auth/register': { status: 422, body: { detail: 'Le mot de passe doit contenir au moins 12 caractères.' } } })
    await expect(request('/auth/register', { method: 'POST' })).rejects.toThrow('12 caractères')
  })

  it('never renders a list of errors as [object Object]', async () => {
    // Observed before the server-side fix: seven registration attempts in a row,
    // each failing with nothing readable on screen.
    scriptFetch({ 'POST /auth/register': { status: 422, body: { detail: [{ msg: 'Champ requis' }, { msg: 'Trop court' }] } } })
    const error = await request('/auth/register', { method: 'POST' }).catch((failure) => failure)
    expect(error.message).toBe('Champ requis Trop court')
    expect(error.message).not.toContain('[object Object]')
  })

  it('falls back to a sentence when the body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500, json: async () => { throw new Error('html') } })))
    await expect(request('/documents')).rejects.toThrow('Une erreur est survenue.')
  })
})

describe('a session closed from elsewhere', () => {
  it('announces itself so the interface returns to sign-in', async () => {
    scriptFetch({ 'GET /documents': { status: 401, body: { detail: 'Cette session a été fermée. Reconnectez-vous.' } } })
    const heard = vi.fn()
    window.addEventListener('ansi:session-lost', heard)
    await request('/documents').catch(() => undefined)
    window.removeEventListener('ansi:session-lost', heard)
    expect(heard).toHaveBeenCalledTimes(1)
    expect(heard.mock.calls[0][0].detail).toContain('fermée')
  })

  it('stays quiet for a wrong password and for the first check on page load', async () => {
    scriptFetch({
      'POST /auth/login': { status: 401, body: { detail: 'Identifiants invalides.' } },
      'GET /auth/me': { status: 401, body: { detail: 'Connexion requise.' } },
    })
    const heard = vi.fn()
    window.addEventListener('ansi:session-lost', heard)
    await request('/auth/login', { method: 'POST' }).catch(() => undefined)
    await request('/auth/me').catch(() => undefined)
    window.removeEventListener('ansi:session-lost', heard)
    expect(heard).not.toHaveBeenCalled()
  })
})

describe('useToasts', () => {
  it('keeps the same push function across renders', () => {
    // A fresh function each render refired every effect that depended on it:
    // nine identical GET /documents in a row, observed in the server log.
    const { result, rerender } = renderHook(() => useToasts())
    const first = result.current.push
    act(() => first('Bonjour'))
    rerender()
    expect(result.current.push).toBe(first)
  })
})

describe('addresses', () => {
  it.each([
    ['/admin/feedback', 'admin', 'retours'],
    ['/admin/gaps', 'admin', 'lacunes'],
    ['/admin/audit', 'admin', 'journal'],
    ['/administration/reponses-validees', 'admin', 'reponses'],
    ['/profile', 'profile', undefined],
    ['/administration/', 'admin', 'supervision'],
  ])('%s opens the right section', (path, tab, section) => {
    const route = locationToRoute(path)
    expect(route.tab).toBe(tab)
    if (section) expect(route.section).toBe(section)
  })

  it('sends an unknown address to the overview rather than a blank screen', () => {
    expect(locationToRoute('/nimporte-quoi').tab).toBe('overview')
  })

  it('gives every section a canonical path', () => {
    expect(routeToPath('admin', 'contacts')).toBe('/administration/contacts')
    expect(routeToPath('chat')).toBe('/assistant')
  })
})

it('labels a missing department instead of printing nothing', () => {
  expect(departmentLabel('rh')).toBe('Ressources humaines')
  expect(departmentLabel(null)).toBe('Non rattaché')
})
