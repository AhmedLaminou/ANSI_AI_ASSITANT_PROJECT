/* Sign-in, account creation, forgotten password — and the forced password change.
 *
 * The professional address is the credential. Accounts created before addresses
 * existed still sign in by username until an administrator records one, which is
 * why the field accepts either — but it is labelled for the address, because that
 * is what every account will end up using.
 *
 * « Mot de passe oublié » cannot send a link: the assistant runs offline, with no
 * mail relay. It files a request to the administrator instead, who hands over a
 * temporary password by another channel. That password must then be replaced —
 * see ForcedPasswordChange below. */

import { useId, useState } from 'react'

import { DEPARTMENTS, Field, Icon, request } from './shared.jsx'

const MODES = {
  login: {
    overline: 'ACCÈS SÉCURISÉ',
    title: 'Bienvenue',
    intro: 'Connectez-vous avec votre adresse professionnelle.',
    submit: "Accéder à l'assistant →",
    busy: 'Connexion…',
  },
  register: {
    overline: 'CRÉER UN COMPTE',
    title: 'Créer mon compte',
    intro:
      "Votre compte est créé immédiatement, puis transmis à l'administrateur pour validation. Il définit le rôle et le service accordés — vous pourrez vous connecter dès son accord.",
    submit: 'Envoyer la demande →',
    busy: 'Envoi…',
  },
  reset: {
    overline: 'MOT DE PASSE OUBLIÉ',
    title: 'Réinitialiser mon mot de passe',
    intro:
      "L'assistant fonctionne hors ligne : aucun lien ne peut vous être envoyé. Votre demande est transmise à l'administrateur, qui vous communiquera un mot de passe provisoire — à changer dès la connexion.",
    submit: 'Prévenir l’administrateur →',
    busy: 'Envoi…',
  },
}

export function Login({ onLogin, theme, onToggleTheme, initialNotice = '' }) {
  const [mode, setMode] = useState('login')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [requestedDepartment, setRequestedDepartment] = useState('technique')
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(initialNotice)
  const [busy, setBusy] = useState(false)
  const copy = MODES[mode]

  function switchTo(next) {
    setMode(next)
    setError('')
    setNotice('')
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (mode === 'register') {
        const response = await request('/auth/register', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email,
            full_name: fullName,
            password,
            requested_department: requestedDepartment,
            reason,
          }),
        })
        setIdentifier(email)
        setPassword('')
        setReason('')
        setFullName('')
        setMode('login')
        setNotice(response.detail)
        return
      }
      if (mode === 'reset') {
        const response = await request('/auth/password-reset-request', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        })
        setIdentifier(email)
        setMode('login')
        setNotice(response.detail)
        return
      }
      await request('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: identifier, password }),
      })
      onLogin(await request('/auth/me'))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-layout" id="contenu">
      <button
        className="theme-toggle floating"
        onClick={onToggleTheme}
        title="Changer de thème"
        aria-label={theme === 'dark' ? 'Passer au thème clair' : 'Passer au thème sombre'}
      >
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
      </button>
      <section className="brand-panel">
        <div className="brand-mark" aria-hidden="true">A</div>
        <p className="overline inverse">ANSI · ASSISTANT INTERNE</p>
        <h1>La connaissance interne, sans quitter votre environnement.</h1>
        <p>
          Un assistant documentaire contrôlé&nbsp;: il cherche dans les documents autorisés, répond avec ses
          sources et garde les données sur l'infrastructure de l'agence.
        </p>
        <div className="security-note">
          <Icon name="shield" />
          Accès réservé aux agents authentifiés
        </div>
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit} aria-labelledby="login-title">
          <p className="overline">{copy.overline}</p>
          <h2 id="login-title">{copy.title}</h2>
          <p className="subtle">{copy.intro}</p>
          {notice && (
            <p className="form-notice" role="status">
              {notice}
            </p>
          )}

          {mode === 'login' && (
            <>
              <Field
                label="Adresse professionnelle"
                hint="Les comptes créés avant l'adresse se connectent avec leur identifiant, jusqu'à ce qu'un administrateur en enregistre une."
              >
                <input
                  value={identifier}
                  onChange={(event) => setIdentifier(event.target.value)}
                  autoComplete="username"
                  inputMode="email"
                  minLength="3"
                  maxLength="160"
                  placeholder="prenom.nom@ansi.ne"
                  required
                />
              </Field>
              <PasswordField value={password} onChange={setPassword} autoComplete="current-password" />
            </>
          )}

          {mode === 'register' && (
            <>
              <Field label="Nom et prénom" hint="C'est ce que verra l'administrateur qui traitera votre demande.">
                <input
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  autoComplete="name"
                  minLength="3"
                  maxLength="120"
                  placeholder="Amina Souley"
                  required
                />
              </Field>
              <EmailField value={email} onChange={setEmail} hint="Elle servira d'identifiant de connexion." />
              <PasswordField
                value={password}
                onChange={setPassword}
                autoComplete="new-password"
                minLength={12}
                hint="12 caractères minimum."
              />
              <label>
                Service souhaité
                <select value={requestedDepartment} onChange={(event) => setRequestedDepartment(event.target.value)}>
                  {DEPARTMENTS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Motif <span className="field-hint">(facultatif)</span>
                <input
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  maxLength={500}
                  placeholder="Ex. stagiaire au service RH"
                />
              </label>
            </>
          )}

          {mode === 'reset' && (
            <EmailField value={email} onChange={setEmail} hint="L'adresse de votre compte." />
          )}

          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary full" disabled={busy}>
            {busy ? copy.busy : copy.submit}
          </button>

          <div className="login-links">
            {mode === 'login' ? (
              <>
                <button type="button" className="link-button" onClick={() => switchTo('reset')}>
                  Mot de passe oublié ?
                </button>
                <button type="button" className="link-button" onClick={() => switchTo('register')}>
                  Pas encore de compte ? En créer un
                </button>
              </>
            ) : (
              <button type="button" className="link-button" onClick={() => switchTo('login')}>
                ← Retour à la connexion
              </button>
            )}
          </div>
          <p className="form-note">
            Ni vos questions ni vos documents ne quittent les serveurs de l'ANSI : aucun service d'IA
            externe n'est appelé.
          </p>
        </form>
      </section>
    </main>
  )
}

function EmailField({ value, onChange, hint }) {
  return (
    <Field label="Adresse professionnelle" hint={hint}>
      <input
        type="email"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="email"
        maxLength="160"
        placeholder="prenom.nom@ansi.ne"
        required
      />
    </Field>
  )
}

function PasswordField({ value, onChange, autoComplete, minLength = 8, hint, label = 'Mot de passe' }) {
  const [visible, setVisible] = useState(false)
  const id = useId()
  return (
    <label>
      {/* Named by the label text alone: without aria-labelledby the name would also
          swallow the hint and the show/hide button's own label. */}
      <span id={`${id}-label`}>{label}</span>
      <span className="password-field">
        <input
          aria-labelledby={`${id}-label`}
          aria-describedby={hint ? `${id}-hint` : undefined}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          minLength={minLength}
          maxLength="128"
          required
        />
        <button
          type="button"
          className="password-toggle"
          onClick={() => setVisible((current) => !current)}
          aria-label={visible ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}
          aria-pressed={visible}
        >
          <Icon name="eye" />
        </button>
      </span>
      {hint && (
        <span id={`${id}-hint`} className="field-hint">
          {hint}
        </span>
      )}
    </label>
  )
}

/** Shown instead of the application while an administrator-chosen password is in
 * force. The server enforces it too — every other endpoint answers 403 — so this
 * screen is the explanation, not the protection. */
export function ForcedPasswordChange({ user, onDone, onLogout }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setError('')
    if (next !== confirmation) {
      setError('Les deux saisies du nouveau mot de passe ne correspondent pas.')
      return
    }
    setBusy(true)
    try {
      await request('/auth/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      onDone(await request('/auth/me'))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-layout single" id="contenu">
      <section className="login-panel">
        <form className="login-card" onSubmit={submit} aria-labelledby="forced-title">
          <p className="overline">SÉCURITÉ DU COMPTE</p>
          <h2 id="forced-title">Choisissez votre mot de passe</h2>
          <p className="subtle">
            Votre mot de passe actuel a été défini par un administrateur. Il est provisoire : un
            administrateur ne doit jamais connaître un mot de passe encore en usage. Choisissez le
            vôtre pour accéder à l'assistant.
          </p>
          <p className="subtle">
            Compte : <strong>{user.email ?? user.username}</strong>
          </p>
          <PasswordField value={current} onChange={setCurrent} autoComplete="current-password"
                         label="Mot de passe provisoire" />
          <PasswordField value={next} onChange={setNext} autoComplete="new-password" minLength={12}
                         label="Nouveau mot de passe" hint="12 caractères minimum." />
          <PasswordField value={confirmation} onChange={setConfirmation} autoComplete="new-password"
                         minLength={12} label="Confirmer le nouveau mot de passe" />
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary full" disabled={busy}>
            {busy ? 'Enregistrement…' : 'Enregistrer et continuer →'}
          </button>
          <button type="button" className="link-button" onClick={onLogout}>
            Me déconnecter
          </button>
        </form>
      </section>
    </main>
  )
}
