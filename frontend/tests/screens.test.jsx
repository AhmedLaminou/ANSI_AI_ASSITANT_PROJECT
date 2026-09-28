/* Screens: what they send, and whether they can be used without a mouse or eyes.
 *
 * The request payloads are asserted because that is where two shipped bugs lived:
 * the account form that never sent a department, and the registration form that
 * applied the sign-in rules. The accessibility checks run axe on each screen. */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ValidatedAnswerEditor } from '../src/Knowledge.jsx'
import { ForcedPasswordChange, Login } from '../src/Login.jsx'
import Users from '../src/Users.jsx'
import { accessibilityViolations, scriptFetch } from './helpers.js'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const ADMIN = { id: 1, username: 'admin', role: 'admin', email: 'admin@ansi.ne' }

describe('sign-in', () => {
  it('asks for the professional address', () => {
    render(<Login onLogin={() => undefined} theme="light" onToggleTheme={() => undefined} />)
    expect(screen.getByLabelText('Adresse professionnelle')).toBeTruthy()
  })

  it('applies the registration rules in registration mode, not the sign-in ones', () => {
    render(<Login onLogin={() => undefined} theme="light" onToggleTheme={() => undefined} />)
    fireEvent.click(screen.getByText('Pas encore de compte ? En créer un'))
    const password = screen.getByLabelText('Mot de passe', { selector: 'input' })
    expect(password.getAttribute('minlength')).toBe('12')
    expect(screen.getByLabelText('Adresse professionnelle').getAttribute('type')).toBe('email')
  })

  it('files a forgotten-password request with the administrator, not a reset', async () => {
    const calls = scriptFetch({ 'POST /auth/password-reset-request': { status: 202, body: { detail: "l'administrateur a été prévenu" } } })
    render(<Login onLogin={() => undefined} theme="light" onToggleTheme={() => undefined} />)
    fireEvent.click(screen.getByText('Mot de passe oublié ?'))
    fireEvent.change(screen.getByLabelText('Adresse professionnelle'), { target: { value: 'amina@ansi.ne' } })
    fireEvent.click(screen.getByText('Prévenir l’administrateur →'))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].path).toBe('/auth/password-reset-request')
    expect(JSON.parse(calls[0].body)).toEqual({ email: 'amina@ansi.ne' })
    await screen.findByText(/administrateur a été prévenu/)
  })

  it('shows why the user was sent back here', () => {
    render(<Login onLogin={() => undefined} theme="light" onToggleTheme={() => undefined}
                  initialNotice="Cette session a été fermée." />)
    expect(screen.getByRole('status').textContent).toContain('fermée')
  })

  it('has no accessibility violation', async () => {
    const { container } = render(<Login onLogin={() => undefined} theme="light" onToggleTheme={() => undefined} />)
    expect(await accessibilityViolations(container)).toEqual([])
  })

  it('lets the password be shown, with a name for the button', () => {
    render(<Login onLogin={() => undefined} theme="light" onToggleTheme={() => undefined} />)
    const toggle = screen.getByRole('button', { name: 'Afficher le mot de passe' })
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
  })
})

describe('the forced password change', () => {
  it('has no accessibility violation', async () => {
    const { container } = render(
      <ForcedPasswordChange user={{ username: 'amina', email: 'amina@ansi.ne' }} onDone={() => undefined}
                            onLogout={() => undefined} />,
    )
    expect(await accessibilityViolations(container)).toEqual([])
  })

  it('refuses two different new passwords before calling the server', async () => {
    const calls = scriptFetch({})
    render(<ForcedPasswordChange user={{ username: 'amina' }} onDone={() => undefined} onLogout={() => undefined} />)
    fireEvent.change(screen.getByLabelText('Mot de passe provisoire', { selector: 'input' }), { target: { value: 'Provisoire-2026-A' } })
    fireEvent.change(screen.getByLabelText('Nouveau mot de passe', { selector: 'input' }), { target: { value: 'Nouveau-2026-AAAA' } })
    fireEvent.change(screen.getByLabelText('Confirmer le nouveau mot de passe', { selector: 'input' }), { target: { value: 'Autre-2026-BBBBBB' } })
    fireEvent.click(screen.getByText('Enregistrer et continuer →'))
    await screen.findByRole('alert')
    expect(calls).toHaveLength(0)
  })
})

describe('accounts', () => {
  function adminRoutes(extra = {}) {
    return {
      'GET /admin/users': { body: [{ id: 2, username: 'ancien', email: null, role: 'user', department: null,
                                     department_label: 'Non rattaché', is_active: true, status: 'active',
                                     must_change_password: false }] },
      'GET /admin/registrations': { body: [] },
      'GET /admin/password-resets': { body: [] },
      ...extra,
    }
  }

  it('sends the address and the department when creating an account', async () => {
    // The department used to be omitted: every account created here was born unattached.
    const calls = scriptFetch(adminRoutes({ 'POST /admin/users': { status: 201, body: {} } }))
    render(<Users user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('ancien')
    fireEvent.change(screen.getAllByPlaceholderText('prenom.nom@ansi.ne')[0], { target: { value: 'amina@ansi.ne' } })
    fireEvent.change(screen.getByLabelText('Mot de passe provisoire'), { target: { value: 'Provisoire-2026-ABCD' } })
    fireEvent.change(screen.getByLabelText('Service de rattachement'), { target: { value: 'rh' } })
    fireEvent.click(screen.getByText('Créer le compte'))
    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true))
    const sent = JSON.parse(calls.find((call) => call.method === 'POST').body)
    expect(sent).toMatchObject({ email: 'amina@ansi.ne', department: 'rh', role: 'user' })
  })

  it('says how many accounts still sign in without an address', async () => {
    scriptFetch(adminRoutes())
    render(<Users user={ADMIN} onToast={() => undefined} />)
    expect(await screen.findByText(/1 compte\(s\) sans adresse professionnelle/)).toBeTruthy()
  })

  it('has no accessibility violation', async () => {
    scriptFetch(adminRoutes())
    const { container } = render(<Users user={ADMIN} onToast={() => undefined} />)
    await screen.findByText('ancien')
    expect(await accessibilityViolations(container)).toEqual([])
  })
})

describe('validated answers', () => {
  it('sends one phrasing per line', async () => {
    const calls = scriptFetch({ 'POST /admin/validated-answers': { status: 201, body: {} } })
    render(<ValidatedAnswerEditor initial={{ question: 'Durée des congés annuels ?', answer: 'Trente jours ouvrables.' }}
                                  onClose={() => undefined} onSaved={() => undefined} onToast={() => undefined} />)
    fireEvent.change(screen.getByLabelText(/Autres formulations/), {
      target: { value: 'Combien de jours de congés ?\n\n  Congés annuels : combien ?  ' },
    })
    fireEvent.click(screen.getByText('Publier'))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(JSON.parse(calls[0].body).phrasings).toEqual(['Combien de jours de congés ?', 'Congés annuels : combien ?'])
  })

  it('is a labelled dialog with no accessibility violation', async () => {
    const { container } = render(
      <ValidatedAnswerEditor initial={{}} onClose={() => undefined} onSaved={() => undefined} onToast={() => undefined} />,
    )
    expect(screen.getByRole('dialog', { name: 'Publier une réponse validée' })).toBeTruthy()
    expect(await accessibilityViolations(container)).toEqual([])
  })
})
