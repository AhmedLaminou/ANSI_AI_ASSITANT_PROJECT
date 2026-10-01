/* Administration: supervision, audit trail, feedback.
 *
 * Built around one idea: an administrator needs to see the agency service by
 * service, not as a total. A corpus of 40 documents looks healthy until you notice
 * that 38 of them are transverse and the logistics service has none — which a
 * global figure hides completely. Every table here is therefore per perimeter, and
 * the gaps are stated rather than left to be inferred from a zero.
 *
 * Every signal leads somewhere: « 3 documents ont dépassé leur date de révision »
 * comes with the button that opens exactly those three in the library. */

import { useCallback, useEffect, useState } from 'react'

import { Contacts, Gaps, ValidatedAnswers } from './Knowledge.jsx'
import { Icon, formatDate, request, roleLabel } from './shared.jsx'
import {
  Bar,
  EmptyState,
  ListFooter,
  PageHeader,
  PageSizeSelect,
  PageSummary,
  Pagination,
  ReviewBadge,
  SectionNav,
  SkeletonRows,
  StatCard,
  agree,
  countOf,
  downloadCsv,
  useClientPages,
} from './ui.jsx'

export { ReviewBadge }

const SECTIONS = [
  { id: 'supervision', label: 'Supervision', icon: 'grid' },
  { id: 'lacunes', label: 'Lacunes du corpus', icon: 'search' },
  { id: 'reponses', label: 'Réponses validées', icon: 'check' },
  { id: 'contacts', label: 'Contacts', icon: 'users' },
  { id: 'journal', label: "Journal d'audit", icon: 'shield' },
  { id: 'retours', label: 'Retours', icon: 'chat' },
]

const AUDIT_FILTERS = [
  { value: '', label: 'Tous les événements' },
  { value: 'document', label: 'Documents' },
  { value: 'user', label: 'Comptes' },
  { value: 'registration', label: "Demandes d'accès" },
  { value: 'tool_invoked', label: 'Outils' },
  { value: 'feedback', label: 'Retours' },
  { value: 'login_failed', label: 'Connexions échouées' },
]

const ACTIVITY_LABELS = {
  document_uploaded: 'Documents importés',
  document_deleted: 'Documents supprimés',
  document_opened: 'Documents consultés',
  document_scope_updated: 'Périmètres modifiés',
  document_question_answered: 'Questions répondues',
  validated_answer_served: 'Réponses validées servies',
  validated_answer_created: 'Réponses validées publiées',
  login_failed: 'Connexions échouées',
  user_created: 'Comptes créés',
  user_updated: 'Comptes modifiés',
  registration_approved: 'Demandes accordées',
  registration_refused: 'Demandes refusées',
  password_reset_requested: 'Mots de passe oubliés',
  user_password_reset: 'Mots de passe provisoires',
  user_password_changed: 'Mots de passe changés',
  sessions_revoked: 'Sessions fermées',
  gap_resolved: 'Lacunes traitées',
  contact_updated: 'Contacts modifiés',
}

function activityLabel(kind) {
  const base = kind.split(':')[0]
  return ACTIVITY_LABELS[base] ?? (base === 'tool_invoked' ? `Outil : ${kind.split(':')[1] ?? ''}` : base.replace(/_/g, ' '))
}

/** What the figures mean operationally, said outright rather than left to inference —
 * each with the place where it is dealt with. */
function readSignals(overview) {
  const signals = []
  const emptyServices = overview.departments.filter(
    (item) => item.value !== 'transverse' && item.documents === 0,
  )
  if (emptyServices.length) {
    signals.push({
      tone: 'warn',
      text: `${countOf(emptyServices.length, 'service')} sans aucun document : ${emptyServices
        .map((item) => item.label)
        .join(', ')}. ${agree(emptyServices.length, 'Ses', 'Leurs')} agents ne peuvent lire que les documents transverses.`,
      action: { label: 'Importer des documents', go: ['documents'] },
    })
  }
  const staffedButEmpty = overview.departments.filter(
    (item) => item.value !== 'transverse' && item.accounts > 0 && item.documents === 0,
  )
  if (staffedButEmpty.length) {
    signals.push({
      tone: 'warn',
      text: `${staffedButEmpty.map((item) => item.label).join(', ')} : des comptes sont rattachés à un service qui n'a pas de corpus.`,
    })
  }
  if (overview.accounts.unattached > 0) {
    signals.push({
      tone: 'warn',
      text: `${countOf(overview.accounts.unattached, 'compte actif', 'comptes actifs')} sans service. ${agree(overview.accounts.unattached, 'Il ne lit', 'Ils ne lisent')} que les documents transverses — c'est presque toujours un oubli.`,
      action: { label: 'Voir les comptes', go: ['users'] },
    })
  }
  if (overview.accounts.pending > 0) {
    signals.push({
      tone: 'info',
      text: `${countOf(overview.accounts.pending, 'demande')} d'accès en attente de décision.`,
      action: { label: 'Traiter les demandes', go: ['users'] },
    })
  }
  if (overview.documents.expired > 0) {
    signals.push({
      tone: 'warn',
      text: `${countOf(overview.documents.expired, 'document')} ${agree(overview.documents.expired, 'dépasse sa', 'dépassent leur')} date de validité. ${agree(overview.documents.expired, 'Il reste consultable et est signalé comme périmé', 'Ils restent consultables et sont signalés comme périmés')} dans les réponses.`,
      action: { label: 'Voir les documents périmés', go: ['documents', null, { statut: 'expired' }] },
    })
  }
  if (overview.gaps?.unanswered > 0) {
    signals.push({
      tone: 'info',
      text: `${countOf(overview.gaps.unanswered, 'question')} sans réponse à examiner dans « Lacunes du corpus » : autant de documents manquants ou de mots que le corpus n'emploie pas.`,
      action: { label: 'Voir les lacunes', section: 'lacunes' },
    })
  }
  if (overview.reviews?.overdue > 0) {
    signals.push({
      tone: 'warn',
      text: `${countOf(overview.reviews.overdue, 'document')} ${agree(overview.reviews.overdue, 'a dépassé sa', 'ont dépassé leur')} date de révision. ${agree(overview.reviews.overdue, "Son responsable doit confirmer qu'il est toujours exact.", "Leurs responsables doivent confirmer qu'ils sont toujours exacts.")}`,
      action: { label: 'Voir ces documents', go: ['documents', null, { statut: 'overdue' }] },
    })
  }
  if (overview.reviews?.without_owner > 0) {
    signals.push({
      tone: 'warn',
      text: `${countOf(overview.reviews.without_owner, 'document')} sans responsable. Un document dont personne ne répond n'est jamais révisé.`,
      action: { label: 'Voir ces documents', go: ['documents', null, { statut: 'unowned' }] },
    })
  }
  if (overview.contacts_missing?.length) {
    signals.push({
      tone: 'info',
      text: `Aucun contact pour : ${overview.contacts_missing.join(', ')}. Sans contact, un refus de l'assistant ne renvoie vers personne.`,
      action: { label: 'Désigner un contact', section: 'contacts' },
    })
  }
  if (overview.accounts.without_email > 0) {
    signals.push({
      tone: 'info',
      text: `${countOf(overview.accounts.without_email, 'compte actif', 'comptes actifs')} sans adresse professionnelle : ${agree(overview.accounts.without_email, 'il se connecte encore avec son identifiant', 'ils se connectent encore avec leur identifiant')}.`,
      action: { label: 'Voir les comptes', go: ['users'] },
    })
  }
  if (overview.accounts.password_resets_pending > 0) {
    signals.push({
      tone: 'warn',
      text: `${countOf(overview.accounts.password_resets_pending, 'agent')} ${agree(overview.accounts.password_resets_pending, 'attend', 'attendent')} un mot de passe provisoire (écran Comptes).`,
      action: { label: 'Traiter', go: ['users'] },
    })
  }
  if (overview.model.retention_days === 0) {
    signals.push({
      tone: 'warn',
      text: "Durée de rétention non fixée : l'historique des conversations est conservé indéfiniment. Cet arbitrage doit être rendu avant toute donnée réelle.",
    })
  }
  if (!signals.length) {
    signals.push({ tone: 'ok', text: 'Aucun point d’attention détecté sur le périmètre supervisé.' })
  }
  return signals
}

function Supervision({ overview, onNavigate, onSection }) {
  if (!overview) return <SkeletonRows rows={6} />
  const signals = readSignals(overview)
  const activity = Object.entries(overview.activity_7d).sort((a, b) => b[1] - a[1])
  const busiest = Math.max(1, ...activity.map(([, count]) => count))
  const largest = Math.max(1, ...overview.departments.map((item) => item.documents))
  const go = (action) => (action.section ? onSection(action.section) : onNavigate?.(...action.go))

  return (
    <>
      <div className="stat-grid six">
        <StatCard icon="folder" label="Documents indexés" value={overview.documents.total}
                  hint={`${overview.documents.chunks} extraits`} onClick={() => onNavigate?.('documents')} />
        <StatCard icon="users" label="Comptes actifs" value={overview.accounts.active}
                  hint={`${overview.accounts.total} au total`} onClick={() => onNavigate?.('users')} />
        <StatCard icon="inbox" label="Demandes en attente" value={overview.accounts.pending}
                  tone={overview.accounts.pending ? 'warn' : ''} onClick={() => onNavigate?.('users')} />
        <StatCard icon="search" label="Questions sans réponse" value={overview.gaps?.unanswered ?? 0}
                  tone={overview.gaps?.unanswered ? 'info' : ''} onClick={() => onSection('lacunes')} />
        <StatCard icon="clock" label="Révisions dépassées" value={overview.reviews?.overdue ?? 0}
                  tone={overview.reviews?.overdue ? 'danger' : ''}
                  onClick={() => onNavigate?.('documents', null, { statut: 'overdue' })} />
        <StatCard icon="chat" label="Réponses signalées" value={overview.feedback.wrong}
                  hint={`${overview.feedback.useful} jugées utiles`}
                  tone={overview.feedback.wrong ? 'warn' : ''} onClick={() => onSection('retours')} />
      </div>

      <article className="panel">
        <div className="panel-head">
          <h2 className="block-title">Points d'attention</h2>
          <span className="muted small">{signals.filter((signal) => signal.tone !== 'ok').length} à examiner</span>
        </div>
        <div className="signal-list">
          {signals.map((signal, index) => (
            <div key={index} className={`signal ${signal.tone}`}>
              <Icon name={signal.tone === 'ok' ? 'check' : signal.tone === 'warn' ? 'alert' : 'info'} />
              <p>{signal.text}</p>
              {signal.action && (
                <button type="button" className="signal-action" onClick={() => go(signal.action)}>
                  {signal.action.label} <Icon name="arrow-right" />
                </button>
              )}
            </div>
          ))}
        </div>
      </article>

      <article className="panel">
        <div className="panel-head">
          <h2 className="block-title">Répartition par service</h2>
        </div>
        <p className="muted">
          Le cloisonnement se lit ici : un service sans document n'a rien à offrir à ses agents,
          un service sans compte n'a personne pour l'interroger.
        </p>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Service</th>
                <th scope="col">Documents</th>
                <th scope="col">Extraits indexés</th>
                <th scope="col">Comptes rattachés</th>
                <th scope="col">Dernier import</th>
              </tr>
            </thead>
            <tbody>
              {overview.departments.map((item) => (
                <tr key={item.value} className={item.documents === 0 && item.value !== 'transverse' ? 'row-warn' : ''}>
                  <td>
                    <button type="button" className="cell-link"
                            onClick={() => onNavigate?.('documents', null, { service: item.value })}>
                      <strong>{item.label}</strong>
                    </button>
                    {item.value === 'transverse' && <span className="muted"> — lisible par tous</span>}
                  </td>
                  <td>
                    <span className="bar-cell">
                      <Bar value={item.documents} max={largest} tone={`dept ${item.value}`} />
                      <strong>{item.documents}</strong>
                    </span>
                  </td>
                  <td>{item.chunks.toLocaleString('fr-FR')}</td>
                  <td>{item.accounts === null ? <span className="muted">s. o.</span> : item.accounts}</td>
                  <td>{formatDate(item.last_import, false)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      {overview.reviews?.items.length > 0 && (
        <article className="panel">
          <div className="panel-head">
            <h2 className="block-title">Documents à réviser</h2>
            <button type="button" className="text-button"
                    onClick={() => onNavigate?.('documents', null, { tri: 'review' })}>
              Tout voir par date de révision <Icon name="arrow-right" />
            </button>
          </div>
          <p className="muted">
            Date de révision dépassée ou proche, responsable absent ou inactif. Distinct de la date
            de validité : la révision est le moment où quelqu'un vérifie que le texte est encore vrai.
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Service</th>
                  <th scope="col">Responsable</th>
                  <th scope="col">Révision</th>
                </tr>
              </thead>
              <tbody>
                {overview.reviews.items.map((item) => (
                  <tr key={item.id} className={item.review_status === 'overdue' ? 'row-warn' : ''}>
                    <td><strong>{item.title}</strong></td>
                    <td>{item.department_label}</td>
                    <td>
                      {item.owner ?? <span className="warn-text">aucun</span>}
                      {item.owner_inactive && <span className="warn-text"> (compte inactif)</span>}
                    </td>
                    <td className="nowrap">
                      <ReviewBadge status={item.review_status} due={item.review_due} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      )}

      <div className="admin-columns">
        <article className="panel">
          <h2 className="block-title">Activité des sept derniers jours</h2>
          {activity.length ? (
            <ul className="activity-bars">
              {activity.map(([kind, count]) => (
                <li key={kind}>
                  <span>{activityLabel(kind)}</span>
                  <Bar value={count} max={busiest} />
                  <strong>{count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Aucune activité enregistrée sur la période.</p>
          )}
        </article>
        <article className="panel">
          <h2 className="block-title">Configuration en vigueur</h2>
          <ul className="stat-list">
            <li><span>Modèle de conversation</span><strong>{overview.model.chat}</strong></li>
            <li><span>Modèle d'embeddings</span><strong>{overview.model.embedding}</strong></li>
            <li><span>Base de données</span><strong>{overview.model.database}</strong></li>
            <li><span>Reconnaissance de caractères</span><strong>{overview.model.ocr ? 'active' : 'désactivée'}</strong></li>
            <li>
              <span>Rétention des conversations</span>
              <strong className={overview.model.retention_days === 0 ? 'warn-text' : ''}>
                {overview.model.retention_days === 0 ? 'illimitée' : `${overview.model.retention_days} jours`}
              </strong>
            </li>
          </ul>
          <h2 className="block-title section-title">Comptes par rôle</h2>
          <ul className="stat-list">
            {Object.entries(overview.accounts.by_role).map(([role, count]) => (
              <li key={role}><span>{roleLabel(role)}</span><strong>{count}</strong></li>
            ))}
          </ul>
        </article>
      </div>
    </>
  )
}

function Journal({ onToast }) {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(25)

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: String(size), offset: String((page - 1) * size) })
      if (filter) query.set('event_type', filter)
      setData(await request(`/admin/audit?${query}`))
    } catch (error) {
      onToast(error.message, 'error')
    }
  }, [filter, page, size, onToast])

  useEffect(() => { load() }, [load])

  const matching = data?.matching ?? data?.total ?? 0
  const pages = Math.max(1, Math.ceil(matching / size))
  const first = matching ? (page - 1) * size + 1 : 0
  const last = (page - 1) * size + (data?.events.length ?? 0)

  function exportPage() {
    downloadCsv(`journal-${new Date().toISOString().slice(0, 10)}.csv`, [
      ['Date', 'Compte', 'Service', 'Événement', 'Document'],
      ...data.events.map((event) => [event.created_at, event.actor, event.actor_department ?? '', event.label,
                                     event.document ?? '']),
    ])
  }

  return (
    <>
      <p className="muted">
        Chaque import, suppression, question répondue, appel d'outil, modification de compte et
        tentative de connexion échouée laisse une trace. Le journal est conservé aussi longtemps que
        la base : sa durée de rétention fait partie des arbitrages à rendre.
      </p>
      <div className="toolbar-row">
        <label className="compact-field">
          <span>Type d'événement</span>
          <select value={filter} onChange={(event) => { setFilter(event.target.value); setPage(1) }}>
            {AUDIT_FILTERS.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <span className="muted small">
          {data ? `${countOf(matching, 'événement')} · ${data.total.toLocaleString('fr-FR')} au total` : 'Chargement…'}
        </span>
        <button type="button" className="ghost-button push-right" onClick={exportPage} disabled={!data?.events.length}>
          <Icon name="download" /> Exporter la page
        </button>
      </div>
      {!data ? (
        <SkeletonRows rows={8} />
      ) : data.events.length === 0 ? (
        <EmptyState icon="shield" title="Aucun événement pour ce filtre" />
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Compte</th>
                <th scope="col">Service</th>
                <th scope="col">Événement</th>
                <th scope="col">Document</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((event) => (
                <tr key={event.id} className={event.event_type === 'login_failed' ? 'row-warn' : ''}>
                  <td className="nowrap">{formatDate(event.created_at)}</td>
                  <td><strong>{event.actor}</strong></td>
                  <td className="muted">{event.actor_department ?? '—'}</td>
                  <td>{event.label}</td>
                  <td className="muted">{event.document ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && matching > 0 && (
        <div className="list-footer">
          <PageSummary first={first} last={last} total={matching} noun="événement" />
          <Pagination page={page} pages={pages} onPage={setPage} label="Pages du journal" />
          <PageSizeSelect value={size} onChange={(value) => { setSize(value); setPage(1) }} options={[25, 50, 100]} />
        </div>
      )}
    </>
  )
}

function Retours({ onToast }) {
  const [entries, setEntries] = useState(null)
  const [only, setOnly] = useState('all')

  useEffect(() => {
    request('/admin/feedback').then(setEntries).catch((error) => onToast(error.message, 'error'))
  }, [onToast])

  const shown = (entries ?? []).filter((entry) => only === 'all' || entry.verdict === only)
  const wrong = (entries ?? []).filter((entry) => entry.verdict === 'wrong').length
  const pager = useClientPages(shown, 10, only)

  return (
    <>
      <p className="muted">
        Un avis n'entraîne <strong>aucun apprentissage</strong> : ni ré-entraînement, ni
        repondération de la recherche. Marquer une réponse incorrecte ne rend pas la suivante
        meilleure. C'est délibéré — un système qui s'ajusterait seul sur des clics serait
        inauditable, ce qui est inacceptable sur un corpus réglementaire.
      </p>
      <p className="muted">
        Chaque signalement est en revanche un <strong>cas à ajouter au jeu d'évaluation</strong>,
        qui sert ensuite à mesurer si un changement de modèle ou de découpage améliore ou dégrade
        les réponses. C'est là que ces lignes servent.
      </p>
      <div className="toolbar-row">
        <label className="compact-field">
          <span>Afficher</span>
          <select value={only} onChange={(event) => setOnly(event.target.value)}>
            <option value="all">Tous les avis</option>
            <option value="wrong">Signalés incorrects</option>
            <option value="useful">Jugés utiles</option>
          </select>
        </label>
        <span className="muted small">{entries ? `${shown.length} affichés · ${wrong} à traiter` : 'Chargement…'}</span>
        <button type="button" className="ghost-button push-right" disabled={!shown.length}
                onClick={() => downloadCsv(`retours-${new Date().toISOString().slice(0, 10)}.csv`, [
                  ['Date', 'Compte', 'Avis', 'Question', 'Commentaire'],
                  ...shown.map((entry) => [entry.created_at, entry.author, entry.verdict === 'wrong' ? 'Incorrecte' : 'Utile',
                                           entry.question ?? '', entry.comment ?? '']),
                ])}>
          <Icon name="download" /> Exporter
        </button>
      </div>
      {!entries ? (
        <SkeletonRows rows={5} />
      ) : shown.length === 0 ? (
        <EmptyState icon="chat" title="Aucun avis pour ce filtre" />
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Compte</th>
                <th scope="col">Avis</th>
                <th scope="col">Question posée</th>
                <th scope="col">Commentaire</th>
              </tr>
            </thead>
            <tbody>
              {pager.slice.map((entry) => (
                <tr key={entry.id} className={entry.verdict === 'wrong' ? 'row-warn' : ''}>
                  <td className="nowrap">{formatDate(entry.created_at)}</td>
                  <td><strong>{entry.author}</strong></td>
                  <td>
                    <span className={`verdict-pill ${entry.verdict}`}>
                      {entry.verdict === 'wrong' ? 'Incorrecte' : 'Utile'}
                    </span>
                  </td>
                  <td>{entry.question || <span className="muted">question non retrouvée</span>}</td>
                  <td className="muted">{entry.comment || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ListFooter pager={pager} noun="avis" label="Pages des retours" />
    </>
  )
}

export default function Administration({ user, section, onSection, onNavigate, onToast }) {
  const [overview, setOverview] = useState(null)

  const load = useCallback(() => {
    request('/admin/overview').then(setOverview).catch((error) => onToast(error.message, 'error'))
  }, [onToast])

  useEffect(() => {
    if (user.role !== 'admin') return
    load()
  }, [user.role, load])

  if (user.role !== 'admin') {
    return (
      <section className="workspace">
        <EmptyState icon="lock" title="Accès administrateur requis">
          La supervision de l'agence est réservée aux administrateurs.
        </EmptyState>
      </section>
    )
  }

  const items = SECTIONS.map((item) => ({
    ...item,
    count: {
      lacunes: overview?.gaps?.unanswered,
      retours: overview?.feedback?.wrong,
      contacts: overview?.contacts_missing?.length,
    }[item.id],
    tone: item.id === 'contacts' ? 'warn' : '',
  }))

  return (
    <section className="workspace">
      <PageHeader
        icon="shield"
        overline="ADMINISTRATION"
        title="Supervision de l'agence"
        actions={(
          <button type="button" className="ghost-button" onClick={load}>
            <Icon name="refresh" /> Actualiser
          </button>
        )}
      >
        L'état du corpus, des comptes et de l'activité, service par service.
      </PageHeader>
      <SectionNav items={items} active={section} onChange={onSection} label="Sections de l'administration" />
      <div className="admin-panel">
        {section === 'supervision' && <Supervision overview={overview} onNavigate={onNavigate} onSection={onSection} />}
        {section === 'journal' && <Journal onToast={onToast} />}
        {section === 'retours' && <Retours onToast={onToast} />}
        {section === 'lacunes' && <Gaps onToast={onToast} />}
        {section === 'reponses' && <ValidatedAnswers onToast={onToast} />}
        {section === 'contacts' && <Contacts onToast={onToast} />}
      </div>
    </section>
  )
}
