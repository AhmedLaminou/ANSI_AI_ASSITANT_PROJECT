/* What the corpus lacks, what was validated, who to ask.
 *
 * Three administration views that turn the assistant from something that answers
 * into something that improves: gaps show what people asked and nobody documented,
 * validated answers let a person vouch for the frequent ones, contacts give every
 * refusal somewhere to go. */

import { useCallback, useEffect, useState } from 'react'

import { DOCUMENT_DEPARTMENTS, Field, Icon, formatDate, request } from './shared.jsx'

// ---------------------------------------------------------------------------
// Gaps
// ---------------------------------------------------------------------------

export function Gaps({ onToast }) {
  const [data, setData] = useState(null)
  const [showResolved, setShowResolved] = useState(false)
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await request(`/admin/gaps?include_resolved=${showResolved}`))
    } catch (error) {
      onToast(error.message, 'error')
    }
  }, [showResolved, onToast])

  useEffect(() => {
    load()
  }, [load])

  async function resolve(gap) {
    try {
      await request('/admin/gaps/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: gap.ids }),
      })
      onToast('Lacune marquée comme traitée.', 'success')
      await load()
    } catch (error) {
      onToast(error.message, 'error')
    }
  }

  return (
    <>
      <p className="muted">
        Les questions auxquelles l'assistant n'a pas su répondre, regroupées par sens. Chaque groupe
        est un document manquant ou un vocabulaire que le corpus n'emploie pas. Le nombre de
        demandes est ce qui le rend actionnable : une question est une anecdote, quatorze sont un
        document à écrire.
      </p>
      <p className="muted">
        La plupart de ces refus viennent du modèle, qui a trouvé des extraits et les a jugés
        insuffisants. Le regroupement est approximatif : deux questions voisines peuvent rester
        séparées, et c'est plus sûr que l'inverse.
      </p>
      <div className="filter-row">
        <label className="checkbox-line">
          <input type="checkbox" checked={showResolved} onChange={(event) => setShowResolved(event.target.checked)} />
          Inclure les lacunes déjà traitées
        </label>
        <span className="muted">
          {data ? `${data.gaps.length} lacune(s) · ${data.total_questions} question(s)` : 'Chargement…'}
        </span>
      </div>

      {data && !data.gaps.length && (
        <p className="signal ok">
          <Icon name="check" />
          Aucune question sans réponse à traiter.
        </p>
      )}

      <div className="gap-list">
        {data?.gaps.map((gap, index) => (
          <article key={gap.ids[0]} className="gap-card">
            <div className="gap-head">
              <span className="gap-count" aria-label={`${gap.count} demande(s)`}>{gap.count}</span>
              <div className="gap-title">
                <strong>{gap.representative}</strong>
                <span className="muted">
                  {Object.entries(gap.departments).map(([label, count]) => `${label} (${count})`).join(' · ')}
                  {' — '}dernière demande le {formatDate(gap.last_seen)}
                </span>
              </div>
              <button
                className="text-button"
                aria-expanded={open === index}
                onClick={() => setOpen(open === index ? null : index)}
              >
                {open === index ? 'Masquer' : 'Détail'}
              </button>
              <button className="text-button" onClick={() => resolve(gap)}>
                <Icon name="check" /> Traitée
              </button>
            </div>
            {open === index && (
              <div className="gap-detail">
                <p className="overline">FORMULATIONS REÇUES</p>
                <ul>
                  {gap.questions.map((question, position) => (
                    <li key={position}>{question}</li>
                  ))}
                </ul>
                <p className="muted">
                  Motifs : {Object.entries(gap.reasons).map(([label, count]) => `${label} (${count})`).join(', ')}.
                  Première demande le {formatDate(gap.first_seen)}.
                </p>
              </div>
            )}
          </article>
        ))}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Validated answers
// ---------------------------------------------------------------------------

function splitPhrasings(text) {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

export function ValidatedAnswerEditor({ initial, onClose, onSaved, onToast }) {
  const [question, setQuestion] = useState(initial?.question ?? '')
  const [answer, setAnswer] = useState(initial?.answer ?? '')
  const [phrasings, setPhrasings] = useState((initial?.phrasings ?? []).join('\n'))
  const [department, setDepartment] = useState(initial?.department ?? 'transverse')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const editing = Boolean(initial?.id)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const body = JSON.stringify({ question, answer, phrasings: splitPhrasings(phrasings), department })
      await request(editing ? `/admin/validated-answers/${initial.id}` : '/admin/validated-answers', {
        method: editing ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      })
      onToast(editing ? 'Réponse validée mise à jour.' : 'Réponse publiée : elle sera servie sans passer par le modèle.', 'success')
      onSaved()
      onClose()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal scope-modal wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="validated-title"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === 'Escape' && onClose()}
      >
      <form className="modal-form" onSubmit={submit}>
        <div className="modal-head">
          <h2 id="validated-title" className="block-title">{editing ? 'Modifier la réponse validée' : 'Publier une réponse validée'}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Fermer">
            <Icon name="close" />
          </button>
        </div>
        <p className="muted">
          Servie telle quelle, sans passer par le modèle, avec votre nom et la date. Elle ne répond
          qu'aux questions qui emploient les mêmes mots porteurs de sens qu'une des formulations
          ci-dessous — ajoutez-en autant que nécessaire.
        </p>
        <label>
          Question
          <input value={question} onChange={(event) => setQuestion(event.target.value)} minLength={5} required />
        </label>
        <label>
          Autres formulations <span className="field-hint">(une par ligne)</span>
          <textarea
            rows={3}
            value={phrasings}
            onChange={(event) => setPhrasings(event.target.value)}
            placeholder={'Combien de jours de congés par an ?\nDurée des congés annuels'}
          />
        </label>
        <label>
          Réponse
          <textarea rows={7} value={answer} onChange={(event) => setAnswer(event.target.value)} minLength={5} required />
        </label>
        <Field label="Visible par" hint="Même règle qu'un document : un service, ou tous avec « Transverse ».">
          <select value={department} onChange={(event) => setDepartment(event.target.value)}>
            {DOCUMENT_DEPARTMENTS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="text-button" onClick={onClose}>
            Annuler
          </button>
          <button className="primary" disabled={busy}>
            {busy ? 'Enregistrement…' : editing ? 'Enregistrer' : 'Publier'}
          </button>
        </div>
      </form>
      </div>
    </div>
  )
}

export function ValidatedAnswers({ onToast }) {
  const [answers, setAnswers] = useState(null)
  const [editing, setEditing] = useState(null)

  const load = useCallback(async () => {
    try {
      setAnswers(await request('/admin/validated-answers'))
    } catch (error) {
      onToast(error.message, 'error')
    }
  }, [onToast])

  useEffect(() => {
    load()
  }, [load])

  async function toggle(entry) {
    try {
      await request(`/admin/validated-answers/${entry.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: !entry.active }),
      })
      await load()
    } catch (error) {
      onToast(error.message, 'error')
    }
  }

  return (
    <>
      <p className="muted">
        Les questions fréquentes étaient régénérées à chaque fois — trente secondes, et à chaque fois
        une nouvelle occasion de se tromper. Une réponse relue une fois est servie instantanément,
        signée. Elle se publie ici, ou directement depuis une réponse de l'assistant.
      </p>
      <div className="filter-row">
        <button className="primary" onClick={() => setEditing({})}>
          <Icon name="plus" /> Nouvelle réponse validée
        </button>
        <span className="muted">{answers ? `${answers.length} réponse(s)` : 'Chargement…'}</span>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Question</th>
              <th>Visible par</th>
              <th>Validée</th>
              <th>Servie</th>
              <th>État</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {answers?.map((entry) => (
              <tr key={entry.id} className={entry.active ? '' : 'row-muted'}>
                <td>
                  <strong>{entry.question}</strong>
                  {entry.phrasings.length > 0 && (
                    <span className="muted"> · +{entry.phrasings.length} formulation(s)</span>
                  )}
                </td>
                <td>{entry.department_label}</td>
                <td className="nowrap">
                  {entry.validated_by}
                  <br />
                  <span className="muted">{formatDate(entry.validated_at, false)}</span>
                </td>
                <td>{entry.times_served} fois</td>
                <td>{entry.active ? 'Active' : 'Retirée'}</td>
                <td className="nowrap">
                  <button className="text-button" onClick={() => setEditing(entry)}>
                    Modifier
                  </button>
                  <button className="text-button" onClick={() => toggle(entry)}>
                    {entry.active ? 'Retirer' : 'Rétablir'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {answers && !answers.length && <p className="muted">Aucune réponse validée pour l'instant.</p>}
      {editing && (
        <ValidatedAnswerEditor
          initial={editing}
          onClose={() => setEditing(null)}
          onSaved={load}
          onToast={onToast}
        />
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Contacts
// ---------------------------------------------------------------------------

function ContactRow({ contact, onSaved, onToast }) {
  const [name, setName] = useState(contact.name)
  const [email, setEmail] = useState(contact.email)
  const [phone, setPhone] = useState(contact.phone)
  const [note, setNote] = useState(contact.note)
  const [busy, setBusy] = useState(false)

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await request(`/admin/contacts/${contact.department}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, phone, note }),
      })
      onToast(`Contact « ${contact.department_label} » enregistré.`, 'success')
      onSaved()
    } catch (error) {
      onToast(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="contact-row" onSubmit={save}>
      <strong className="contact-service">{contact.department_label}</strong>
      <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Nom"
             aria-label={`Nom du contact ${contact.department_label}`} minLength={2} required />
      <input type="email" value={email} onChange={(event) => setEmail(event.target.value)}
             placeholder="Adresse" aria-label={`Adresse du contact ${contact.department_label}`} />
      <input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="Téléphone"
             aria-label={`Téléphone du contact ${contact.department_label}`} />
      <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Précision (facultatif)"
             aria-label={`Précision pour ${contact.department_label}`} />
      <button className="text-button" disabled={busy}>
        {busy ? '…' : 'Enregistrer'}
      </button>
    </form>
  )
}

export function Contacts({ onToast }) {
  const [contacts, setContacts] = useState(null)

  const load = useCallback(async () => {
    try {
      setContacts(await request('/admin/contacts'))
    } catch (error) {
      onToast(error.message, 'error')
    }
  }, [onToast])

  useEffect(() => {
    load()
  }, [load])

  return (
    <>
      <p className="muted">
        Quand l'assistant ne peut pas répondre, il indique à qui s'adresser : le contact du service de
        l'agent, à défaut le contact général (« Transverse »). « Je ne trouve pas » est honnête ; «
        adressez-vous à Amina, service RH » est utile.
      </p>
      <p className="muted">
        Un agent ne voit que le contact de son service et le contact général.
      </p>
      <div className="contact-list">
        {contacts?.map((contact) => (
          <ContactRow key={contact.department} contact={contact} onSaved={load} onToast={onToast} />
        ))}
      </div>
    </>
  )
}
