/* Accounts: requests to handle, accounts to create, accounts to maintain.
 *
 * Two queues come first because they are the only things here that someone is
 * waiting on: an access request, and a forgotten password. Everything below them
 * is maintenance.
 *
 * A password the administrator types is temporary by construction: the account
 * must replace it at the next sign-in. An administrator should never know a
 * password that is still in use. */

import { useCallback, useEffect, useState } from 'react'

import { DEPARTMENTS, Icon, ROLES, formatDate, request } from './shared.jsx'

function RegistrationRow({ entry, onDecide }) {
  const [department, setDepartment] = useState(entry.requested_department ?? 'technique')
  return (
    <article className="registration-row">
      <div className="registration-meta">
        <strong>{entry.email ?? entry.username}</strong>
        <span>
          demande : {entry.requested_department_label}
          {entry.reason ? ` — « ${entry.reason} »` : ''}
        </span>
      </div>
      <select
        value={department}
        onChange={(event) => setDepartment(event.target.value)}
        aria-label={`Service à accorder à ${entry.email ?? entry.username}`}
      >
        {DEPARTMENTS.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </select>
      <button className="text-button" onClick={() => onDecide(entry, 'approve', department)}>
        <Icon name="check" /> Accorder
      </button>
      <button
        className="icon-button"
        title="Refuser"
        aria-label={`Refuser la demande de ${entry.email ?? entry.username}`}
        onClick={() => onDecide(entry, 'refuse')}
      >
        <Icon name="close" />
      </button>
    </article>
  )
}

function askTemporaryPassword(who) {
  const value = window.prompt(
    `Mot de passe provisoire pour « ${who} » (12 caractères minimum).\n` +
      "Communiquez-le par un autre canal : l'agent devra le remplacer à sa connexion.",
  )
  if (value === null) return null
  if (value.length < 12) {
    window.alert('Le mot de passe doit contenir au moins 12 caractères.')
    return null
  }
  return value
}

function AccountRow({ account, self, onUpdate, onResetPassword, onRevoke }) {
  const [email, setEmail] = useState('')

  return (
    <article className={account.is_active ? '' : 'inactive'}>
      <div className="avatar small" aria-hidden="true">
        {account.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="user-name">
        <strong>{account.username}</strong>
        {account.email ? (
          <span className="user-email">{account.email}</span>
        ) : (
          <span className="tag-department none">sans adresse</span>
        )}
        <span className={`role-pill ${account.role}`}>{account.role}</span>
        <span className={`tag-department ${account.department || 'none'}`}>
          {account.department_label || 'Non rattaché'}
        </span>
        {account.must_change_password && <span className="tag-department none">mot de passe provisoire</span>}
        {self && <span className="role-pill self">vous</span>}
      </div>

      {!account.email && (
        // Recording an address makes it the account's only sign-in identifier.
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            onUpdate(account.id, { email }, `« ${account.username} » se connectera désormais avec ${email}.`)
          }}
        >
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="prenom.nom@ansi.ne"
            aria-label={`Adresse professionnelle de ${account.username}`}
            required
          />
          <button className="text-button">Enregistrer l'adresse</button>
        </form>
      )}

      <div className="user-actions">
        <select
          value={account.role}
          aria-label={`Rôle de ${account.username}`}
          disabled={self}
          onChange={(event) => onUpdate(account.id, { role: event.target.value })}
        >
          {ROLES.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <select
          value={account.department || ''}
          aria-label={`Service de ${account.username}`}
          onChange={(event) => onUpdate(account.id, { department: event.target.value })}
        >
          {!account.department && <option value="">Non rattaché</option>}
          {DEPARTMENTS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <button
          className="text-button"
          disabled={self}
          onClick={() => onUpdate(account.id, { is_active: !account.is_active })}
        >
          {account.is_active ? 'Désactiver' : 'Réactiver'}
        </button>
        <button className="text-button" onClick={() => onResetPassword(account)}>
          Mot de passe
        </button>
        <button
          className="text-button"
          title="Ferme toutes les sessions ouvertes de ce compte, sans le désactiver"
          onClick={() => onRevoke(account)}
        >
          Fermer les sessions
        </button>
      </div>
      <small className={account.is_active ? 'active-state' : ''}>{account.is_active ? 'Actif' : 'Inactif'}</small>
    </article>
  )
}

export default function Users({ user, onToast }) {
  const [users, setUsers] = useState([])
  const [registrations, setRegistrations] = useState([])
  const [resets, setResets] = useState([])
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('user')
  // A department is not optional in the design: an account without one reads only
  // transverse documents and lands as « Non rattaché ».
  const [department, setDepartment] = useState(DEPARTMENTS[0].value)
  const [error, setError] = useState('')

  const loadAll = useCallback(async () => {
    const [accounts, pending, requests] = await Promise.all([
      request('/admin/users'),
      request('/admin/registrations'),
      request('/admin/password-resets'),
    ])
    setUsers(accounts)
    setRegistrations(pending)
    setResets(requests)
  }, [])

  useEffect(() => {
    if (user.role !== 'admin') return
    loadAll().catch((requestError) => setError(requestError.message))
  }, [user.role, loadAll])

  if (user.role !== 'admin') {
    return (
      <section className="workspace">
        <div className="empty-state">
          <h3>Accès administrateur requis</h3>
          <p>La gestion des comptes est réservée aux administrateurs.</p>
        </div>
      </section>
    )
  }

  async function run(action, success) {
    try {
      await action()
      await loadAll()
      if (success) onToast(success, 'success')
    } catch (requestError) {
      onToast(requestError.message, 'error')
    }
  }

  function decide(entry, action, grantedDepartment) {
    const who = entry.email ?? entry.username
    return action === 'approve'
      ? run(
          () =>
            request(`/admin/registrations/${entry.id}/approve`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role: 'user', department: grantedDepartment }),
            }),
          `Accès accordé à « ${who} ».`,
        )
      : run(
          () =>
            request(`/admin/registrations/${entry.id}/refuse`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ reason: '' }),
            }),
          `Demande de « ${who} » refusée.`,
        )
  }

  function resolveReset(entry) {
    const temporary = askTemporaryPassword(entry.email ?? entry.username)
    if (!temporary) return
    run(
      () =>
        request(`/admin/password-resets/${entry.id}/resolve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: temporary }),
        }),
      `Mot de passe provisoire défini pour « ${entry.email ?? entry.username} ».`,
    )
  }

  function dismissReset(entry) {
    run(
      () => request(`/admin/password-resets/${entry.id}/dismiss`, { method: 'POST' }),
      'Demande écartée.',
    )
  }

  function updateAccount(accountId, changes, success = 'Compte mis à jour.') {
    run(
      () =>
        request(`/admin/users/${accountId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(changes),
        }),
      success,
    )
  }

  function resetPassword(account) {
    const temporary = askTemporaryPassword(account.email ?? account.username)
    if (!temporary) return
    run(
      () =>
        request(`/admin/users/${account.id}/password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: temporary }),
        }),
      `Mot de passe provisoire défini pour « ${account.username} ». Ses sessions ont été fermées.`,
    )
  }

  function revokeSessions(account) {
    if (!window.confirm(`Fermer toutes les sessions ouvertes de « ${account.username} » ?`)) return
    run(
      () => request(`/admin/users/${account.id}/revoke-sessions`, { method: 'POST' }),
      `Sessions de « ${account.username} » fermées.`,
    )
  }

  async function create(event) {
    event.preventDefault()
    setError('')
    try {
      await request('/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, role, department }),
      })
      onToast(`Compte « ${email} » créé. Le mot de passe devra être changé à la première connexion.`, 'success')
      setEmail('')
      setPassword('')
      setRole('user')
      setDepartment(DEPARTMENTS[0].value)
      await loadAll()
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  const withoutAddress = users.filter((account) => !account.email).length

  return (
    <section className="workspace">
      <div className="workspace-head">
        <div>
          <p className="overline">ADMINISTRATION</p>
          <h1>Comptes et accès</h1>
          <p>
            Le rôle dit ce qu'un compte peut faire, le service ce qu'il peut lire. L'adresse
            professionnelle est l'identifiant de connexion.
          </p>
        </div>
      </div>

      {resets.length > 0 && (
        <div className="registration-queue">
          <p className="overline">MOTS DE PASSE OUBLIÉS ({resets.length})</p>
          <p className="queue-hint">
            Définissez un mot de passe provisoire et communiquez-le par un autre canal. L'agent
            devra le remplacer à sa connexion ; ses sessions ouvertes sont fermées.
          </p>
          {resets.map((entry) => (
            <article key={entry.id} className="registration-row">
              <div className="registration-meta">
                <strong>{entry.email ?? entry.username}</strong>
                <span>
                  {entry.department_label} · demandé le {formatDate(entry.created_at)}
                </span>
              </div>
              <button className="text-button" onClick={() => resolveReset(entry)}>
                <Icon name="check" /> Définir un mot de passe provisoire
              </button>
              <button
                className="icon-button"
                title="Écarter"
                aria-label={`Écarter la demande de ${entry.email ?? entry.username}`}
                onClick={() => dismissReset(entry)}
              >
                <Icon name="close" />
              </button>
            </article>
          ))}
        </div>
      )}

      {registrations.length > 0 && (
        <div className="registration-queue">
          <p className="overline">DEMANDES D'ACCÈS EN ATTENTE ({registrations.length})</p>
          <p className="queue-hint">
            Le service demandé n'est qu'une indication : vous choisissez celui qui est réellement accordé.
          </p>
          {registrations.map((entry) => (
            <RegistrationRow key={entry.id} entry={entry} onDecide={decide} />
          ))}
        </div>
      )}

      {withoutAddress > 0 && (
        <p className="signal warn">
          <Icon name="shield" />
          {withoutAddress} compte(s) sans adresse professionnelle. Ils se connectent encore avec
          leur identifiant ; enregistrez une adresse pour qu'elle devienne leur seul identifiant.
        </p>
      )}

      <div className="admin-grid">
        <form className="upload-card" onSubmit={create}>
          <h3>Créer un compte</h3>
          <label>
            Adresse professionnelle
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="prenom.nom@ansi.ne"
              required
            />
          </label>
          <label>
            Mot de passe provisoire
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength="12"
              autoComplete="new-password"
              required
            />
            <span className="field-hint">L'agent devra le remplacer à sa première connexion.</span>
          </label>
          <label>
            Rôle
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {ROLES.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Service de rattachement
            <select value={department} onChange={(event) => setDepartment(event.target.value)}>
              {DEPARTMENTS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary">Créer le compte</button>
        </form>
        <div className="user-list">
          {users.map((account) => (
            <AccountRow
              key={account.id}
              account={account}
              self={account.id === user.id}
              onUpdate={updateAccount}
              onResetPassword={resetPassword}
              onRevoke={revokeSessions}
            />
          ))}
        </div>
      </div>
    </section>
  )
}
