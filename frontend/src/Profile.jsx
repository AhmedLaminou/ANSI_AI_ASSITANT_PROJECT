/* Profil : ce qu'un agent peut légitimement savoir sur son propre compte.
 *
 * Volontairement limité à soi-même — il n'existe pas d'écran de profil d'un autre
 * agent. Le périmètre d'un compte est le sien, et laisser un compte consulter la
 * fiche d'un autre serait une façon discrète d'apprendre la forme de l'agence. */

import { useEffect, useState } from 'react'

import { Icon, formatDate, request } from './shared.jsx'

function PasswordCard({ onToast }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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
      setCurrent('')
      setNext('')
      setConfirmation('')
      onToast('Mot de passe modifié.', 'success')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="panel-card" onSubmit={submit}>
      <h3>Changer mon mot de passe</h3>
      <p className="muted">
        Il n'existe pas de procédure « mot de passe oublié » : l'assistant fonctionne hors ligne,
        il n'y a donc aucun relais pour envoyer un lien. Un administrateur peut le réinitialiser
        depuis le serveur.
      </p>
      <label>
        Mot de passe actuel
        <input type="password" value={current} autoComplete="current-password"
               onChange={(event) => setCurrent(event.target.value)} required />
      </label>
      <label>
        Nouveau mot de passe
        <input type="password" value={next} autoComplete="new-password" minLength={12}
               onChange={(event) => setNext(event.target.value)} required />
        <span className="field-hint">12 caractères minimum.</span>
      </label>
      <label>
        Confirmer le nouveau mot de passe
        <input type="password" value={confirmation} autoComplete="new-password" minLength={12}
               onChange={(event) => setConfirmation(event.target.value)} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy}>
        {busy ? 'Enregistrement…' : 'Changer le mot de passe'}
      </button>
      <p className="field-hint">
        À savoir : changer votre mot de passe ne ferme pas les sessions déjà ouvertes ailleurs.
        Une session reste valide huit heures.
      </p>
    </form>
  )
}

export default function Profile({ user, onToast }) {
  const [profile, setProfile] = useState(null)

  useEffect(() => {
    request('/auth/profile').then(setProfile).catch((error) => onToast(error.message, 'error'))
  }, [onToast])

  const initials = user.username.slice(0, 2).toUpperCase()

  return (
    <section className="workspace">
      <div className="workspace-head">
        <div>
          <p className="overline">MON COMPTE</p>
          <h1>Profil</h1>
          <p>Votre identité, votre périmètre de lecture et votre activité.</p>
        </div>
      </div>

      <div className="identity-card">
        <div className="identity-avatar">{initials}</div>
        <div className="identity-body">
          <h2>{user.username}</h2>
          <p className="muted">{user.email ?? 'Aucune adresse enregistrée sur ce compte'}</p>
          <div className="identity-tags">
            <span className={`role-pill ${user.role}`}>{user.role}</span>
            <span className={`tag-department ${user.department || 'none'}`}>
              {user.department_label}
            </span>
            {user.sees_every_department && (
              <span className="tag-department transverse">lit tous les services</span>
            )}
          </div>
        </div>
      </div>

      {!profile ? (
        <p className="muted">Chargement…</p>
      ) : (
        <>
          <div className="figure-grid">
            <Figure label="Documents interrogeables" value={profile.readable.total} />
            <Figure label="Conversations" value={profile.activity.conversations} />
            <Figure label="Questions posées" value={profile.activity.questions} />
            <Figure label="Réponses signalées" value={profile.activity.feedback_wrong}
                    hint={`${profile.activity.feedback_useful} jugées utiles`} />
          </div>

          <div className="admin-columns">
            <div className="panel-card">
              <h3>Ce que mon rôle me permet</h3>
              <ul className="check-list">
                {profile.rights.map((right) => (
                  <li key={right}><Icon name="check" />{right}</li>
                ))}
              </ul>

              <h3>Ce que je peux lire</h3>
              {profile.readable.total ? (
                <ul className="stat-list">
                  {Object.entries(profile.readable.by_department).map(([label, count]) => (
                    <li key={label}><span>{label}</span><strong>{count}</strong></li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Aucun document n'est encore accessible à votre compte.</p>
              )}

              {/* Dire ce qui est hors de portée vaut autant que dire ce qui est lisible :
                  un agent qui ignore qu'un périmètre existe croit le corpus vide. */}
              {profile.readable.unreachable.length > 0 && (
                <>
                  <h3>Ce que je ne vois pas</h3>
                  <p className="muted">
                    Les documents des services {profile.readable.unreachable.join(', ')}. Les
                    documents transverses restent lisibles par tous. Pour un document d'un autre
                    service, adressez-vous à l'administrateur.
                  </p>
                </>
              )}
              <p className="muted">
                Dernière action enregistrée : {formatDate(profile.activity.last_action)}.
              </p>
            </div>
            <PasswordCard onToast={onToast} />
          </div>
        </>
      )}
    </section>
  )
}

function Figure({ label, value, hint }) {
  return (
    <div className="figure">
      <span className="figure-value">{value}</span>
      <span className="figure-label">{label}</span>
      {hint && <span className="figure-hint">{hint}</span>}
    </div>
  )
}
