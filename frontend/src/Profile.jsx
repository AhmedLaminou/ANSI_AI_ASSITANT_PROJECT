/* Profil : ce qu'un agent peut légitimement savoir sur son propre compte.
 *
 * Volontairement limité à soi-même — il n'existe pas d'écran de profil d'un autre
 * agent. Le périmètre d'un compte est le sien, et laisser un compte consulter la
 * fiche d'un autre serait une façon discrète d'apprendre la forme de l'agence. */

import { useEffect, useState } from 'react'

import { Field, Icon, formatDate, request, roleLabel } from './shared.jsx'
import { Avatar, Bar, EmptyState, ReviewBadge, SkeletonRows, StatCard } from './ui.jsx'

/** How far a password is from the rules, said as rules — not as a colour alone. */
export function passwordChecks(value) {
  return [
    { ok: value.length >= 12, label: '12 caractères au moins' },
    { ok: /[a-z]/.test(value) && /[A-Z]/.test(value), label: 'Majuscules et minuscules' },
    { ok: /\d/.test(value), label: 'Un chiffre' },
    { ok: /[^A-Za-z0-9]/.test(value), label: 'Un caractère spécial' },
  ]
}

function StrengthMeter({ value }) {
  if (!value) return null
  const checks = passwordChecks(value)
  const score = checks.filter((check) => check.ok).length
  const level = ['faible', 'faible', 'moyen', 'bon', 'solide'][score]
  return (
    <div className="strength">
      <span className={`strength-bar level-${score}`} aria-hidden="true"><span /></span>
      <span className="strength-label">Robustesse : {level}</span>
      <ul className="strength-checks">
        {checks.map((check) => (
          <li key={check.label} className={check.ok ? 'ok' : ''}>
            <Icon name={check.ok ? 'check' : 'minus'} /> {check.label}
          </li>
        ))}
      </ul>
    </div>
  )
}

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
    <form className="panel form-panel" onSubmit={submit}>
      <div className="panel-head">
        <h2 className="block-title"><Icon name="lock" /> Changer mon mot de passe</h2>
      </div>
      <p className="muted">
        En cas d'oubli, la demande se fait depuis l'écran de connexion : l'assistant fonctionne
        hors ligne, elle est donc transmise à l'administrateur, qui vous communique un mot de
        passe provisoire à remplacer.
      </p>
      <label>
        Mot de passe actuel
        <input type="password" value={current} autoComplete="current-password"
               onChange={(event) => setCurrent(event.target.value)} required />
      </label>
      <Field label="Nouveau mot de passe" hint="12 caractères minimum.">
        <input type="password" value={next} autoComplete="new-password" minLength={12}
               onChange={(event) => setNext(event.target.value)} required />
      </Field>
      <StrengthMeter value={next} />
      <label>
        Confirmer le nouveau mot de passe
        <input type="password" value={confirmation} autoComplete="new-password" minLength={12}
               onChange={(event) => setConfirmation(event.target.value)} required />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>
        {busy ? 'Enregistrement…' : 'Changer le mot de passe'}
      </button>
      <p className="field-hint">
        Changer votre mot de passe ferme toutes vos autres sessions ; celle-ci reste ouverte.
      </p>
    </form>
  )
}

function SessionsCard({ onToast }) {
  const [busy, setBusy] = useState(false)

  async function revoke() {
    setBusy(true)
    try {
      await request('/auth/sessions/revoke', { method: 'POST' })
      onToast('Toutes vos autres sessions ont été fermées.', 'success')
    } catch (requestError) {
      onToast(requestError.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="block-title"><Icon name="shield" /> Sessions ouvertes</h2>
      </div>
      <p className="muted">
        Une session oubliée sur un poste partagé reste valide huit heures. Vous pouvez la fermer
        d'ici sans changer votre mot de passe — celle que vous utilisez en ce moment reste ouverte.
      </p>
      <button type="button" className="ghost-button" onClick={revoke} disabled={busy}>
        <Icon name="logout" /> {busy ? 'Fermeture…' : 'Me déconnecter partout ailleurs'}
      </button>
    </div>
  )
}

function ContactsCard({ contacts }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="block-title"><Icon name="users" /> Qui contacter</h2>
      </div>
      {contacts.length ? (
        <ul className="contact-cards">
          {contacts.map((contact) => (
            <li key={contact.department}>
              <Avatar name={contact.name} />
              <div>
                <span className="overline">{contact.department_label}</span>
                <strong>{contact.name}</strong>
                {contact.email && <a href={`mailto:${contact.email}`}>{contact.email}</a>}
                {contact.phone && <span>{contact.phone}</span>}
                {contact.note && <span className="muted">{contact.note}</span>}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Aucun contact n'a encore été désigné pour votre service.</p>
      )}
      <p className="field-hint">
        C'est aussi vers eux que l'assistant vous oriente quand il ne trouve pas la réponse.
      </p>
    </div>
  )
}

function OwnedDocuments({ documents, onNavigate }) {
  if (!documents.length) return null
  const late = documents.filter((document) => document.review_status === 'overdue').length
  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="block-title"><Icon name="folder" /> Documents dont je suis responsable</h2>
        {onNavigate && (
          <button type="button" className="text-button"
                  onClick={() => onNavigate('documents', null, { statut: 'mine', tri: 'review' })}>
            Ouvrir dans la bibliothèque <Icon name="arrow-right" />
          </button>
        )}
      </div>
      <p className="muted">
        C'est à vous qu'il revient de confirmer qu'ils sont toujours exacts à leur date de révision
        — ou de les remplacer.{late ? ` ${late} ont dépassé cette date.` : ''}
      </p>
      <ul className="owned-list">
        {documents.map((document) => (
          <li key={document.id}>
            <span>
              <strong>{document.title}</strong>
              <small>{document.department_label}</small>
            </span>
            <ReviewBadge status={document.review_status} due={document.review_due} />
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function Profile({ user, onToast, onNavigate }) {
  const [profile, setProfile] = useState(null)
  const [contacts, setContacts] = useState([])

  useEffect(() => {
    request('/auth/profile').then(setProfile).catch((error) => onToast(error.message, 'error'))
    request('/contacts').then(setContacts).catch(() => setContacts([]))
  }, [onToast])

  const readable = profile ? Object.entries(profile.readable.by_department) : []
  const largest = Math.max(1, ...readable.map(([, count]) => count))

  return (
    <section className="workspace profile-page">
      <div className="profile-hero">
        <Avatar name={user.username} size="xl" />
        <div className="profile-identity">
          <p className="overline">MON COMPTE</p>
          <h1>{user.username}</h1>
          <p className="muted">{user.email ?? 'Aucune adresse enregistrée sur ce compte'}</p>
          <div className="identity-tags">
            <span className={`role-pill ${user.role}`}>{roleLabel(user.role)}</span>
            <span className={`dept-badge ${user.department || 'none'}`}>{user.department_label}</span>
            {user.sees_every_department && <span className="dept-badge transverse">lit tous les services</span>}
          </div>
        </div>
        {profile && (
          <p className="profile-last">
            <Icon name="clock" /> Dernière action : {formatDate(profile.activity.last_action)}
          </p>
        )}
      </div>

      {!profile ? (
        <SkeletonRows rows={6} />
      ) : (
        <>
          <div className="stat-grid">
            <StatCard icon="folder" label="Documents interrogeables" value={profile.readable.total}
                      onClick={onNavigate ? () => onNavigate('documents') : undefined} />
            <StatCard icon="chat" label="Conversations" value={profile.activity.conversations}
                      onClick={onNavigate ? () => onNavigate('chat') : undefined} />
            <StatCard icon="spark" label="Questions posées" value={profile.activity.questions} />
            <StatCard icon="alert" label="Réponses signalées" value={profile.activity.feedback_wrong}
                      hint={`${profile.activity.feedback_useful} jugées utiles`} />
          </div>

          <div className="profile-columns">
            <div className="profile-main">
              <div className="panel">
                <div className="panel-head">
                  <h2 className="block-title"><Icon name="layers" /> Mon périmètre</h2>
                </div>
                <h3 className="panel-sub">Ce que mon rôle me permet</h3>
                <ul className="check-list">
                  {profile.rights.map((right) => (
                    <li key={right}><Icon name="check" />{right}</li>
                  ))}
                </ul>

                <h3 className="panel-sub">Ce que je peux lire</h3>
                {profile.readable.total ? (
                  <ul className="service-bars static">
                    {readable.map(([label, count]) => (
                      <li key={label}>
                        <span className="service-name">{label}</span>
                        <Bar value={count} max={largest} />
                        <strong>{count}</strong>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState icon="folder" title="Aucun document accessible">
                    Aucun document n'est encore accessible à votre compte.
                  </EmptyState>
                )}

                {/* Dire ce qui est hors de portée vaut autant que dire ce qui est lisible :
                    un agent qui ignore qu'un périmètre existe croit le corpus vide. */}
                {profile.readable.unreachable.length > 0 && (
                  <>
                    <h3 className="panel-sub">Ce que je ne vois pas</h3>
                    <p className="muted">
                      Les documents des services {profile.readable.unreachable.join(', ')}. Les
                      documents transverses restent lisibles par tous. Pour un document d'un autre
                      service, adressez-vous à l'administrateur.
                    </p>
                  </>
                )}
              </div>
              <OwnedDocuments documents={profile.owned_documents ?? []} onNavigate={onNavigate} />
            </div>
            <div className="profile-side">
              <ContactsCard contacts={contacts} />
              <PasswordCard onToast={onToast} />
              <SessionsCard onToast={onToast} />
            </div>
          </div>
        </>
      )}
    </section>
  )
}
