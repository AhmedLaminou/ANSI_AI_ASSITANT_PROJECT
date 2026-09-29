/* Accounts: requests to handle, accounts to create, accounts to maintain.
 *
 * Two queues come first because they are the only things here that someone is
 * waiting on: an access request, and a forgotten password. Everything below them
 * is maintenance — searchable and paged, because an agency has hundreds of agents.
 *
 * A password the administrator types is temporary by construction: the account
 * must replace it at the next sign-in. An administrator should never know a
 * password that is still in use. */

import { useCallback, useEffect, useState } from 'react'

import { DEPARTMENTS, Field, Icon, ROLES, formatDate, request, roleLabel } from './shared.jsx'
import {
  Avatar,
  EmptyState,
  ListFooter,
  PageHeader,
  SearchInput,
  SkeletonRows,
  StatCard,
  agree,
  countOf,
  downloadCsv,
  useClientPages,
} from './ui.jsx'

function fold(text = '') {
  return String(text).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

function RegistrationRow({ entry, onDecide }) {
  const [department, setDepartment] = useState(entry.requested_department ?? 'technique')
  return (
    <article className="registration-row">
      <Avatar name={entry.email ?? entry.username} />
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
      <button type="button" className="ghost-button" onClick={() => onDecide(entry, 'approve', department)}>
        <Icon name="check" /> Accorder
      </button>
      <button
        type="button"
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
    <article className={`account-row ${account.is_active ? '' : 'inactive'}`}>
      <div className="account-identity">
        <Avatar name={account.username} />
        <div className="user-name">
          <strong>{account.username}</strong>
          {account.email ? (
            <span className="user-email">{account.email}</span>
          ) : (
            <span className="tag-department none">sans adresse</span>
          )}
          <span className="account-tags">
            <span className={`role-pill ${account.role}`}>{roleLabel(account.role)}</span>
            <span className={`dept-badge ${account.department || 'none'}`}>
              {account.department_label || 'Non rattaché'}
            </span>
            {account.must_change_password && <span className="tag-department none">mot de passe provisoire</span>}
            {self && <span className="role-pill self">vous</span>}
          </span>
        </div>
        <span className={`state-pill ${account.is_active ? 'on' : 'off'}`}>{account.is_active ? 'Actif' : 'Inactif'}</span>
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

      {/* Two groups: what the account is, then what can be done to it. On a narrow
          column the second moves below as a whole instead of leaving one button behind. */}
      <div className="user-actions">
        <div className="account-fields">
          <select
            value={account.role}
            aria-label={`Rôle de ${account.username}`}
            disabled={self}
            onChange={(event) => onUpdate(account.id, { role: event.target.value })}
          >
            {ROLES.map((value) => (
              <option key={value} value={value}>{roleLabel(value)}</option>
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
        </div>
        <div className="account-buttons">
          <button
            type="button"
            className="text-button"
            disabled={self}
            onClick={() => onUpdate(account.id, { is_active: !account.is_active })}
          >
            {account.is_active ? 'Désactiver' : 'Réactiver'}
          </button>
          <button type="button" className="text-button" onClick={() => onResetPassword(account)}>
            Mot de passe
          </button>
          <button
            type="button"
            className="text-button"
            title="Ferme toutes les sessions ouvertes de ce compte, sans le désactiver"
            onClick={() => onRevoke(account)}
          >
            Fermer les sessions
          </button>
        </div>
      </div>
    </article>
  )
}

export default function Users({ user, onToast }) {
  const [users, setUsers] = useState(null)
  const [registrations, setRegistrations] = useState([])
  const [resets, setResets] = useState([])
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('user')
  // A department is not optional in the design: an account without one reads only
  // transverse documents and lands as « Non rattaché ».
  const [department, setDepartment] = useState(DEPARTMENTS[0].value)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [departmentFilter, setDepartmentFilter] = useState('')
  const [stateFilter, setStateFilter] = useState('')

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

  const accounts = users ?? []
  const shown = accounts.filter((account) =>
    (!search || fold(`${account.username} ${account.email ?? ''}`).includes(fold(search)))
    && (!roleFilter || account.role === roleFilter)
    && (!departmentFilter || (departmentFilter === 'none' ? !account.department : account.department === departmentFilter))
    && (!stateFilter || (stateFilter === 'active' ? account.is_active : !account.is_active)))
  const pager = useClientPages(shown, 10, `${search}|${roleFilter}|${departmentFilter}|${stateFilter}`)

  if (user.role !== 'admin') {
    return (
      <section className="workspace">
        <EmptyState icon="lock" title="Accès administrateur requis">
          La gestion des comptes est réservée aux administrateurs.
        </EmptyState>
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

  function exportAccounts() {
    downloadCsv(`comptes-${new Date().toISOString().slice(0, 10)}.csv`, [
      ['Identifiant', 'Adresse', 'Rôle', 'Service', 'État', 'Mot de passe provisoire'],
      ...shown.map((account) => [account.username, account.email ?? '', account.role, account.department_label,
                                 account.is_active ? 'actif' : 'inactif', account.must_change_password ? 'oui' : 'non']),
    ])
  }

  const withoutAddress = accounts.filter((account) => !account.email).length
  const active = accounts.filter((account) => account.is_active).length
  const admins = accounts.filter((account) => account.role === 'admin' && account.is_active).length

  return (
    <section className="workspace">
      <PageHeader icon="users" overline="ADMINISTRATION" title="Comptes et accès">
        Le rôle dit ce qu'un compte peut faire, le service ce qu'il peut lire. L'adresse
        professionnelle est l'identifiant de connexion.
      </PageHeader>

      <div className="stat-grid compact">
        <StatCard icon="users" label="Comptes" value={accounts.length} hint={`${active} actifs`} />
        <StatCard icon="shield" label="Administrateurs actifs" value={admins} />
        <StatCard icon="inbox" label="Demandes à traiter" value={registrations.length + resets.length}
                  tone={registrations.length + resets.length ? 'warn' : ''} />
        <StatCard icon="alert" label="Sans adresse" value={withoutAddress} tone={withoutAddress ? 'info' : ''} />
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
              <Avatar name={entry.email ?? entry.username} />
              <div className="registration-meta">
                <strong>{entry.email ?? entry.username}</strong>
                <span>
                  {entry.department_label} · demandé le {formatDate(entry.created_at)}
                </span>
              </div>
              <button type="button" className="ghost-button" onClick={() => resolveReset(entry)}>
                <Icon name="check" /> Définir un mot de passe provisoire
              </button>
              <button
                type="button"
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
          <Icon name="alert" />
          {countOf(withoutAddress, 'compte')} sans adresse professionnelle.{' '}
          {agree(withoutAddress,
                 "Il se connecte encore avec son identifiant ; enregistrez une adresse pour qu'elle devienne son seul identifiant.",
                 "Ils se connectent encore avec leur identifiant ; enregistrez une adresse pour qu'elle devienne leur seul identifiant.")}
        </p>
      )}

      <div className="admin-grid">
        <form className="upload-card create-account" onSubmit={create}>
          <h2 className="block-title"><Icon name="plus" /> Créer un compte</h2>
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
          <Field label="Mot de passe provisoire" hint="L'agent devra le remplacer à sa première connexion.">
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength="12"
              autoComplete="new-password"
              required
            />
          </Field>
          <label>
            Rôle
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {ROLES.map((value) => (
                <option key={value} value={value}>{roleLabel(value)}</option>
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

        <div className="account-panel">
          <div className="toolbar-row">
            <SearchInput value={search} onChange={setSearch} label="Rechercher un compte"
                         placeholder="Nom ou adresse…" className="compact" />
            <button type="button" className="ghost-button" onClick={exportAccounts} disabled={!shown.length}>
              <Icon name="download" /> Exporter
            </button>
            <span className="toolbar-break" aria-hidden="true" />
            <label className="compact-field">
              <span>Rôle</span>
              <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
                <option value="">Tous</option>
                {ROLES.map((value) => <option key={value} value={value}>{roleLabel(value)}</option>)}
              </select>
            </label>
            <label className="compact-field">
              <span>Service</span>
              <select value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value)}>
                <option value="">Tous</option>
                {DEPARTMENTS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                <option value="none">Non rattachés</option>
              </select>
            </label>
            <label className="compact-field">
              <span>État</span>
              <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}>
                <option value="">Tous</option>
                <option value="active">Actifs</option>
                <option value="inactive">Inactifs</option>
              </select>
            </label>
          </div>
          {!users ? (
            <SkeletonRows rows={5} />
          ) : shown.length === 0 ? (
            <EmptyState icon="users" title="Aucun compte ne correspond" />
          ) : (
            <div className="user-list">
              {pager.slice.map((account) => (
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
          )}
          <ListFooter pager={pager} noun="compte" label="Pages des comptes" />
        </div>
      </div>
    </section>
  )
}
