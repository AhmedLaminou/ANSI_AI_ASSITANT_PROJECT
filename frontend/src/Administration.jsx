/* Administration: supervision, audit trail, feedback.
 *
 * Built around one idea: an administrator needs to see the agency service by
 * service, not as a total. A corpus of 40 documents looks healthy until you notice
 * that 38 of them are transverse and the logistics service has none — which a
 * global figure hides completely. Every table here is therefore per perimeter, and
 * the gaps are stated rather than left to be inferred from a zero. */

import { useCallback, useEffect, useState } from 'react'

import { DEPARTMENTS, Icon, departmentLabel, formatDate, request } from './shared.jsx'

const SECTIONS = [
  { id: 'supervision', label: 'Supervision', icon: 'grid' },
  { id: 'journal', label: "Journal d'audit", icon: 'shield' },
  { id: 'retours', label: 'Retours sur les réponses', icon: 'chat' },
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

function Figure({ label, value, hint, tone = '' }) {
  return (
    <div className={`figure ${tone}`}>
      <span className="figure-value">{value}</span>
      <span className="figure-label">{label}</span>
      {hint && <span className="figure-hint">{hint}</span>}
    </div>
  )
}

/** What the figures mean operationally, said outright rather than left to inference. */
function readSignals(overview) {
  const signals = []
  const emptyServices = overview.departments.filter(
    (item) => item.value !== 'transverse' && item.documents === 0,
  )
  if (emptyServices.length) {
    signals.push({
      tone: 'warn',
      text: `${emptyServices.length} service(s) sans aucun document : ${emptyServices
        .map((item) => item.label)
        .join(', ')}. Leurs agents ne peuvent lire que les documents transverses.`,
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
      text: `${overview.accounts.unattached} compte(s) actif(s) sans service. Ils ne lisent que les documents transverses — c'est presque toujours un oubli.`,
    })
  }
  if (overview.accounts.pending > 0) {
    signals.push({
      tone: 'info',
      text: `${overview.accounts.pending} demande(s) d'accès en attente de décision.`,
    })
  }
  if (overview.documents.expired > 0) {
    signals.push({
      tone: 'warn',
      text: `${overview.documents.expired} document(s) dépassent leur date de validité. Ils restent consultables et sont signalés comme périmés dans les réponses.`,
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

function Supervision({ overview, onToast }) {
  if (!overview) return <p className="muted">Chargement…</p>
  const signals = readSignals(overview)
  const activity = Object.entries(overview.activity_7d).sort((a, b) => b[1] - a[1])

  return (
    <>
      <div className="figure-grid">
        <Figure label="Documents indexés" value={overview.documents.total}
                hint={`${overview.documents.chunks} extraits`} />
        <Figure label="Comptes actifs" value={overview.accounts.active}
                hint={`${overview.accounts.total} au total`} />
        <Figure label="Demandes en attente" value={overview.accounts.pending}
                tone={overview.accounts.pending ? 'warn' : ''} />
        <Figure label="Réponses signalées" value={overview.feedback.wrong}
                hint={`${overview.feedback.useful} jugées utiles`}
                tone={overview.feedback.wrong ? 'warn' : ''} />
      </div>

      <div className="signal-list">
        {signals.map((signal, index) => (
          <p key={index} className={`signal ${signal.tone}`}>
            <Icon name={signal.tone === 'ok' ? 'check' : 'shield'} />
            {signal.text}
          </p>
        ))}
      </div>

      <h3>Répartition par service</h3>
      <p className="muted">
        Le cloisonnement se lit ici : un service sans document n'a rien à offrir à ses agents,
        un service sans compte n'a personne pour l'interroger.
      </p>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Service</th>
              <th>Documents</th>
              <th>Extraits indexés</th>
              <th>Comptes rattachés</th>
              <th>Dernier import</th>
            </tr>
          </thead>
          <tbody>
            {overview.departments.map((item) => (
              <tr key={item.value} className={item.documents === 0 && item.value !== 'transverse' ? 'row-warn' : ''}>
                <td>
                  <strong>{item.label}</strong>
                  {item.value === 'transverse' && <span className="muted"> — lisible par tous</span>}
                </td>
                <td>{item.documents}</td>
                <td>{item.chunks}</td>
                <td>{item.accounts === null ? <span className="muted">s. o.</span> : item.accounts}</td>
                <td>{formatDate(item.last_import, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="admin-columns">
        <div>
          <h3>Activité des sept derniers jours</h3>
          {activity.length ? (
            <ul className="stat-list">
              {activity.map(([kind, count]) => (
                <li key={kind}>
                  <span>{kind.replace(/_/g, ' ')}</span>
                  <strong>{count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Aucune activité enregistrée sur la période.</p>
          )}
        </div>
        <div>
          <h3>Configuration en vigueur</h3>
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
          <h3>Comptes par rôle</h3>
          <ul className="stat-list">
            {Object.entries(overview.accounts.by_role).map(([role, count]) => (
              <li key={role}><span>{role}</span><strong>{count}</strong></li>
            ))}
          </ul>
        </div>
      </div>
    </>
  )
}

function Journal({ onToast }) {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('')
  const [limit, setLimit] = useState(100)

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams({ limit: String(limit) })
      if (filter) query.set('event_type', filter)
      setData(await request(`/admin/audit?${query}`))
    } catch (error) {
      onToast(error.message, 'error')
    }
  }, [filter, limit, onToast])

  useEffect(() => { load() }, [load])

  return (
    <>
      <p className="muted">
        Chaque import, suppression, question répondue, appel d'outil, modification de compte et
        tentative de connexion échouée laisse une trace. Le journal est conservé aussi longtemps que
        la base : sa durée de rétention fait partie des arbitrages à rendre.
      </p>
      <div className="filter-row">
        <label>
          Type d'événement
          <select value={filter} onChange={(event) => setFilter(event.target.value)}>
            {AUDIT_FILTERS.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <label>
          Nombre de lignes
          <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
            {[50, 100, 200, 500].map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        <span className="muted">
          {data ? `${data.events.length} affichés sur ${data.total} enregistrés` : 'Chargement…'}
        </span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Compte</th>
              <th>Service</th>
              <th>Événement</th>
              <th>Document</th>
            </tr>
          </thead>
          <tbody>
            {data?.events.map((event) => (
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
      {data && !data.events.length && <p className="muted">Aucun événement pour ce filtre.</p>}
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
      <div className="filter-row">
        <label>
          Afficher
          <select value={only} onChange={(event) => setOnly(event.target.value)}>
            <option value="all">Tous les avis</option>
            <option value="wrong">Signalés incorrects</option>
            <option value="useful">Jugés utiles</option>
          </select>
        </label>
        <span className="muted">
          {entries ? `${shown.length} affichés · ${wrong} à traiter` : 'Chargement…'}
        </span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Compte</th>
              <th>Avis</th>
              <th>Question posée</th>
              <th>Commentaire</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((entry) => (
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
      {entries && !shown.length && <p className="muted">Aucun avis pour ce filtre.</p>}
    </>
  )
}

export default function Administration({ user, section, onSection, onToast }) {
  const [overview, setOverview] = useState(null)

  useEffect(() => {
    if (user.role !== 'admin') return
    request('/admin/overview').then(setOverview).catch((error) => onToast(error.message, 'error'))
  }, [user.role, onToast])

  if (user.role !== 'admin') {
    return (
      <section className="workspace">
        <div className="empty-state">
          <h3>Accès administrateur requis</h3>
          <p>La supervision de l'agence est réservée aux administrateurs.</p>
        </div>
      </section>
    )
  }

  return (
    <section className="workspace">
      <div className="workspace-head">
        <div>
          <p className="overline">ADMINISTRATION</p>
          <h1>Supervision de l'agence</h1>
          <p>
            L'état du corpus, des comptes et de l'activité, service par service.
          </p>
        </div>
      </div>
      <div className="segmented">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            className={section === item.id ? 'active' : ''}
            onClick={() => onSection(item.id)}
          >
            <Icon name={item.icon} />
            {item.label}
          </button>
        ))}
      </div>
      <div className="admin-panel">
        {section === 'supervision' && <Supervision overview={overview} onToast={onToast} />}
        {section === 'journal' && <Journal onToast={onToast} />}
        {section === 'retours' && <Retours onToast={onToast} />}
      </div>
    </section>
  )
}
