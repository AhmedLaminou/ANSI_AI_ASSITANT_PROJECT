/* The library: finding a document among thousands.
 *
 * The server sends one page at a time, with the count behind every filter, so the
 * screen stays fast whatever the size of the corpus and a filter can be chosen
 * knowing what it will give. Search, filters, sort and page live in the address:
 * /documents?service=rh&statut=overdue is a view a supervisor can send, and the
 * supervision screen links to it.
 *
 * Two searches, two costs. Titles and metadata are searched as you type, on the
 * server, without the model. The content of the documents is searched on demand —
 * an embedding, a few seconds — and answers with passages and their page. */

import { useCallback, useEffect, useId, useMemo, useState } from 'react'

import { API_URL, CLASSIFICATIONS, DOCUMENT_DEPARTMENTS, Icon, ROLES, formatDate, request, roleLabel } from './shared.jsx'
import {
  Chip,
  Drawer,
  EmptyState,
  FormatBadge,
  PageHeader,
  PageSizeSelect,
  PageSummary,
  Pagination,
  ReviewBadge,
  SearchInput,
  SkeletonRows,
  agree,
  countOf,
  downloadCsv,
  timeAgo,
  useDebounced,
  useFavorites,
  usePersistentState,
} from './ui.jsx'

export const SORTS = [
  { value: 'recent', label: 'Plus récents' },
  { value: 'oldest', label: 'Plus anciens' },
  { value: 'title', label: 'Titre (A → Z)' },
  { value: 'department', label: 'Service' },
  { value: 'review', label: 'Date de révision' },
]

export const STATUSES = [
  { value: 'overdue', label: 'Révision dépassée', tone: 'danger' },
  { value: 'due_soon', label: 'Révision proche', tone: 'warn' },
  { value: 'expired', label: 'Périmés', tone: 'danger' },
  { value: 'unowned', label: 'Sans responsable', tone: 'warn' },
  { value: 'mine', label: 'Dont je suis responsable' },
]

const FORMATS = [
  { value: 'pdf', label: 'PDF' },
  { value: 'docx', label: 'Word' },
  { value: 'txt', label: 'Texte' },
  { value: 'md', label: 'Markdown' },
]

const SIZES = [10, 25, 50]
const EMPTY_PAGE = { items: [], total: 0, page: 1, pages: 1, first: 0, last: 0, readable_total: 0,
                     facets: { department: {}, classification: {}, format: {}, status: {} } }
const SHORT_DEPARTMENT = {
  technique: 'Technique',
  finance: 'Finances',
  logistique: 'Logistique',
  rh: 'Ressources humaines',
  transverse: 'Transverse',
}

/** The address, read as the screen's state. Unknown values fall back to defaults. */
export function readLibraryQuery(query = {}) {
  return {
    q: query.q ?? '',
    service: query.service ?? '',
    classification: query.classification ?? '',
    format: query.format ?? '',
    statut: STATUSES.some((item) => item.value === query.statut) ? query.statut : '',
    tri: SORTS.some((item) => item.value === query.tri) ? query.tri : 'recent',
    page: Math.max(1, Number.parseInt(query.page, 10) || 1),
    taille: SIZES.includes(Number(query.taille)) ? Number(query.taille) : 10,
    favoris: query.favoris === '1',
    versions: query.versions === '1',
    mode: query.mode === 'contenu' ? 'contenu' : 'titres',
  }
}

/** The screen's state, written back without its defaults: the address stays short. */
export function writeLibraryQuery(state) {
  return {
    q: state.q || undefined,
    service: state.service || undefined,
    classification: state.classification || undefined,
    format: state.format || undefined,
    statut: state.statut || undefined,
    tri: state.tri !== 'recent' ? state.tri : undefined,
    page: state.page > 1 ? state.page : undefined,
    taille: state.taille !== 10 ? state.taille : undefined,
    favoris: state.favoris ? '1' : undefined,
    versions: state.versions ? '1' : undefined,
    mode: state.mode === 'contenu' ? 'contenu' : undefined,
  }
}

function apiParams(state, favoriteIds, overrides = {}) {
  const params = new URLSearchParams({ page: String(state.page), size: String(state.taille), sort: state.tri })
  if (state.q) params.set('q', state.q)
  if (state.service) params.set('department', state.service)
  if (state.classification) params.set('classification', state.classification)
  if (state.format) params.set('format', state.format)
  if (state.statut) params.set('status', state.statut)
  if (state.versions) params.set('superseded', 'true')
  if (state.favoris) params.set('ids', favoriteIds.join(','))
  for (const [key, value] of Object.entries(overrides)) params.set(key, String(value))
  return params
}

// ---------------------------------------------------------------------------
// Editing a document's perimeter
// ---------------------------------------------------------------------------

/** Le périmètre d'un document se révise : une note classée « interne » ne concerne
 * finalement qu'un service, ou un service est réorganisé. Avant, la seule façon de
 * le changer était de supprimer et réimporter — ce qui perd l'historique des
 * versions et coûte une réindexation complète. */
export function ScopeEditor({ document, onClose, onSaved, onToast }) {
  const [owners, setOwners] = useState([])
  const [ownerId, setOwnerId] = useState(document.owner_id ?? '')
  const [reviewDue, setReviewDue] = useState(document.review_due ? document.review_due.slice(0, 10) : '')
  const [classification, setClassification] = useState(document.classification)
  const [department, setDepartment] = useState(document.department)
  const [roles, setRoles] = useState(document.allowed_roles)
  const [title, setTitle] = useState(document.title)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    // A reader cannot answer for a document: the owner must be able to replace it.
    request('/admin/users')
      .then((accounts) => setOwners(accounts.filter(
        (account) => account.is_active && ['admin', 'document_manager'].includes(account.role),
      )))
      .catch(() => setOwners([]))
  }, [])

  function toggleRole(role) {
    setRoles((current) => (current.includes(role) ? current.filter((item) => item !== role) : [...current, role]))
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await request(`/documents/${document.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          classification,
          department,
          allowed_roles: roles.join(','),
          review_due: reviewDue,
          ...(ownerId ? { owner_id: Number(ownerId) } : {}),
        }),
      })
      onToast(`Périmètre de « ${title} » mis à jour.`, 'success')
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
        className="modal scope-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="scope-title"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === 'Escape' && onClose()}
      >
        <form className="modal-form" onSubmit={submit}>
          <div className="modal-head">
            <h2 id="scope-title" className="block-title">Modifier le périmètre</h2>
            <button type="button" className="icon-button" onClick={onClose} aria-label="Fermer">
              <Icon name="close" />
            </button>
          </div>
          <p className="muted">
            Ces champs décident qui peut lire le document. Le changement prend effet immédiatement,
            sans réindexation : le contenu n'est pas touché.
          </p>
          <label>
            Titre
            <input value={title} onChange={(event) => setTitle(event.target.value)} required autoFocus />
          </label>
          <label>
            Service
            <select value={department} onChange={(event) => setDepartment(event.target.value)}>
              {DOCUMENT_DEPARTMENTS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>
            Classification
            <select value={classification} onChange={(event) => setClassification(event.target.value)}>
              {CLASSIFICATIONS.map((value) => <option key={value}>{value}</option>)}
            </select>
            <span className="field-hint">Indicative seulement : ce sont les rôles ci-dessous qui filtrent réellement.</span>
          </label>
          <label>
            Responsable
            <select value={ownerId} onChange={(event) => setOwnerId(event.target.value)}>
              {!document.owner_id && <option value="">Aucun</option>}
              {owners.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.username} ({account.role})
                </option>
              ))}
            </select>
            <span className="field-hint">C'est à lui que la révision est demandée.</span>
          </label>
          <label>
            À réviser le
            <input type="date" value={reviewDue} onChange={(event) => setReviewDue(event.target.value)} />
            <span className="field-hint">Vide : aucune révision planifiée.</span>
          </label>
          <fieldset className="role-grid">
            <legend>Rôles autorisés</legend>
            {ROLES.map((role) => (
              <label key={role} className="checkbox">
                <input type="checkbox" checked={roles.includes(role)} disabled={role === 'admin'}
                       onChange={() => toggleRole(role)} />
                {roleLabel(role)}
                {role === 'admin' && <span className="field-hint">toujours autorisé</span>}
              </label>
            ))}
          </fieldset>
          {error && <p className="error">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="text-button" onClick={onClose}>Annuler</button>
            <button className="primary" disabled={busy}>{busy ? 'Enregistrement…' : 'Enregistrer'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// One document, in detail
// ---------------------------------------------------------------------------

/** Everything about one document, beside the list rather than instead of it. Also
 * opened from the assistant, when an agent clicks a cited source. */
export function DocumentDrawer({ documentId, user, favorites, onClose, onChanged, onAsk, onToast }) {
  const [loaded, setLoaded] = useState(null)
  const [editing, setEditing] = useState(false)
  const [version, setVersion] = useState(0)
  const titleId = useId()
  const isAdmin = user.role === 'admin'
  // What was loaded belongs to one document and one edit: anything else is still loading.
  const previewKey = `${documentId}:${version}`
  const preview = loaded?.key === previewKey ? loaded.preview : null

  useEffect(() => {
    if (!documentId) return undefined
    let cancelled = false
    request(`/documents/${documentId}/preview`)
      .then((data) => !cancelled && setLoaded({ key: `${documentId}:${version}`, preview: data }))
      .catch((requestError) => {
        if (cancelled) return
        onToast(requestError.message, 'error')
        onClose()
      })
    return () => {
      cancelled = true
    }
  }, [documentId, version, onToast, onClose])

  async function remove() {
    const document = preview.document
    if (!window.confirm(`Supprimer « ${document.title} » et son index local ? Cette action est définitive.`)) return
    try {
      await request(`/documents/${document.id}`, { method: 'DELETE' })
      onToast(`« ${document.title} » supprimé.`, 'success')
      onChanged?.()
      onClose()
    } catch (requestError) {
      onToast(requestError.message, 'error')
    }
  }

  const document = preview?.document
  const favourite = document ? favorites?.has(document.id) : false

  return (
    <>
      <Drawer open={Boolean(documentId)} onClose={onClose} labelledBy={titleId}>
        <header className="drawer-head">
          {document && <FormatBadge filename={document.filename} />}
          <div className="drawer-title">
            <p className="overline">{document ? document.department_label : 'DOCUMENT'}</p>
            <h2 id={titleId}>{document ? document.title : 'Chargement…'}</h2>
            {document && <p className="muted">{document.filename}</p>}
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Fermer le détail">
            <Icon name="close" />
          </button>
        </header>
        {!preview ? (
          <div className="drawer-body"><SkeletonRows rows={4} /></div>
        ) : (
          <div className="drawer-body">
            <div className="drawer-actions">
              <OriginalFileLink document={document} />
              {onAsk && (
                <button type="button" className="primary" onClick={() => onAsk(document)}>
                  <Icon name="chat" /> Poser une question
                </button>
              )}
              {favorites && (
                <button type="button" className={`ghost-button ${favourite ? 'on' : ''}`} aria-pressed={favourite}
                        onClick={() => favorites.toggle(document.id)}>
                  <Icon name={favourite ? 'star-filled' : 'star'} /> {favourite ? 'Dans mes favoris' : 'Ajouter aux favoris'}
                </button>
              )}
              {isAdmin && (
                <>
                  <button type="button" className="ghost-button" onClick={() => setEditing(true)}>
                    <Icon name="pencil" /> Périmètre
                  </button>
                  <button type="button" className="ghost-button danger" onClick={remove}>
                    <Icon name="trash" /> Supprimer
                  </button>
                </>
              )}
            </div>
            {(!document.is_current || document.is_expired || ['overdue', 'due_soon'].includes(document.review_status)) && (
              <div className="drawer-alerts">
                {!document.is_current && <p className="signal info"><Icon name="layers" />Version remplacée : l'assistant ne l'interroge plus.</p>}
                {document.is_expired && <p className="signal warn"><Icon name="alert" />Périmé depuis le {formatDate(document.valid_until, false)} : signalé comme tel dans les réponses.</p>}
                {document.review_status === 'overdue' && <p className="signal warn"><Icon name="clock" />Révision dépassée depuis le {formatDate(document.review_due, false)}.</p>}
                {document.review_status === 'due_soon' && <p className="signal info"><Icon name="clock" />Révision prévue le {formatDate(document.review_due, false)}.</p>}
              </div>
            )}
            <dl className="meta-grid">
              <div><dt>Service</dt><dd><span className={`dept-badge ${document.department}`}>{document.department_label}</span></dd></div>
              <div><dt>Classification</dt><dd><span className={`tag-classification ${document.classification}`}>{document.classification}</span></dd></div>
              <div><dt>Rôles autorisés</dt><dd>{document.allowed_roles.map(roleLabel).join(', ')}</dd></div>
              <div><dt>Version</dt><dd>v{document.version}{document.is_current ? ' · en vigueur' : ' · remplacée'}</dd></div>
              <div><dt>Importé</dt><dd>{formatDate(document.created_at)}</dd></div>
              <div><dt>Valide jusqu'au</dt><dd>{document.valid_until ? formatDate(document.valid_until, false) : 'sans limite'}</dd></div>
              <div><dt>Responsable</dt><dd>{document.owner ?? <span className="warn-text">aucun</span>}</dd></div>
              <div><dt>Révision</dt><dd><ReviewBadge status={document.review_status} due={document.review_due} /></dd></div>
              <div><dt>Extraits indexés</dt><dd>{preview.chunks_total ?? preview.chunks.length}</dd></div>
            </dl>
            <h3 className="drawer-section">
              {preview.chunks.length < (preview.chunks_total ?? 0)
                ? `Aperçu : ${preview.chunks.length === 1 ? 'le premier extrait' : `les ${preview.chunks.length} premiers extraits`} sur ${preview.chunks_total}`
                : 'Aperçu : extraits indexés'}
            </h3>
            {preview.chunks.length ? (
              <ol className="excerpt-list">
                {preview.chunks.map((chunk, index) => (
                  <li key={`${chunk.page}-${index}`}>
                    <span className="excerpt-page">Page {chunk.page}</span>
                    <p>{chunk.content}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">Aucun extrait indexé.</p>
            )}
          </div>
        )}
      </Drawer>
      {editing && document && (
        <ScopeEditor
          document={document}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setVersion((value) => value + 1)
            onChanged?.()
          }}
          onToast={onToast}
        />
      )}
    </>
  )
}

/** The whole document as imported. A plain link rather than a fetch: the browser's own
 * PDF viewer opens it, with its real file name, and the session cookie goes with it. A
 * DOCX cannot be shown by a browser, so it is offered as a download. */
function OriginalFileLink({ document }) {
  const href = `${API_URL}/documents/${document.id}/file`
  if (document.filename.toLowerCase().endsWith('.docx')) {
    return (
      <a className="primary button-link" href={`${href}?download=true`}>
        <Icon name="download" /> Télécharger le document
      </a>
    )
  }
  return (
    <a className="primary button-link" href={href} target="_blank" rel="noopener noreferrer">
      <Icon name="external" /> Ouvrir le document complet
    </a>
  )
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

function UploadPanel({ onUploaded, onClose, onToast }) {
  // Several files go through the batch endpoint, each titled after its filename; a
  // single file keeps the title field. Importing a corpus one form at a time does not
  // happen, so the batch path is the one that matters for real use.
  const [files, setFiles] = useState([])
  const file = files.length === 1 ? files[0] : null
  const [reviewDue, setReviewDue] = useState('')
  const [batchReport, setBatchReport] = useState(null)
  const [title, setTitle] = useState('')
  const [classification, setClassification] = useState('interne')
  const [department, setDepartment] = useState('transverse')
  const [validUntil, setValidUntil] = useState('')
  const [allowedRoles, setAllowedRoles] = useState(['admin', 'document_manager', 'user'])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function toggleRole(role) {
    setAllowedRoles((current) => (current.includes(role) ? current.filter((value) => value !== role) : [...current, role]))
  }

  async function submit(event) {
    event.preventDefault()
    if (!files.length) return setError('Sélectionnez au moins un document.')
    if (!allowedRoles.length) return setError('Choisissez au moins un rôle autorisé.')
    setBusy(true)
    setError('')
    setBatchReport(null)
    const form = new FormData()
    form.append('classification', classification)
    form.append('allowed_roles', allowedRoles.join(','))
    form.append('valid_until', validUntil)
    form.append('review_due', reviewDue)
    form.append('department', department)
    try {
      if (file) {
        form.append('file', file)
        form.append('title', title || file.name.replace(/\.[^.]+$/, ''))
        const uploaded = await request('/documents/upload', { method: 'POST', body: form })
        onToast(`« ${uploaded.title} » importé et indexé (${uploaded.chunks_indexed} extraits).`, 'success')
      } else {
        files.forEach((item) => form.append('files', item))
        const report = await request('/documents/upload-batch', { method: 'POST', body: form })
        setBatchReport(report)
        onToast(`${countOf(report.imported, 'document importé', 'documents importés')}, ${countOf(report.failed, 'échec')}.`, report.failed ? 'error' : 'success')
      }
      setFiles([])
      setTitle('')
      setValidUntil('')
      setReviewDue('')
      onUploaded()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="upload-card upload-panel" onSubmit={submit}>
      <div className="upload-header">
        <div>
          <h2 className="block-title">Importer des documents</h2>
          <p>Le texte est conservé localement, découpé puis indexé par <strong>embeddinggemma</strong>.</p>
        </div>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Fermer l'import">
          <Icon name="close" />
        </button>
      </div>
      <label className="drop-zone">
        <input type="file" multiple accept=".pdf,.docx,.txt,.md"
               onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
        <Icon name="upload" />
        <span className="drop-zone-title">
          {files.length === 0 && 'Choisir un ou plusieurs fichiers'}
          {files.length === 1 && files[0].name}
          {files.length > 1 && `${files.length} fichiers sélectionnés`}
        </span>
        <span className="drop-zone-hint">PDF, Word, texte ou Markdown · 20 Mo par fichier · 50 fichiers à la fois</span>
      </label>
      <div className="form-grid">
        <label>
          Titre du document
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder={files.length > 1 ? 'Chaque document prend le nom de son fichier' : 'Ex. Rapport trimestriel'}
            disabled={files.length > 1}
          />
        </label>
        <label>
          Service concerné
          <select value={department} onChange={(event) => setDepartment(event.target.value)}>
            {DOCUMENT_DEPARTMENTS.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <label>
          Classification
          <select value={classification} onChange={(event) => setClassification(event.target.value)}>
            <option value="interne">Interne</option>
            <option value="direction">Direction</option>
            <option value="confidentiel">Confidentiel</option>
          </select>
        </label>
        <label>
          Valide jusqu'au <span className="field-hint">(facultatif)</span>
          <input type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} />
        </label>
        <label>
          À réviser le <span className="field-hint">(par défaut : dans douze mois)</span>
          <input type="date" value={reviewDue} onChange={(event) => setReviewDue(event.target.value)} />
        </label>
      </div>
      <fieldset>
        <legend>Rôles autorisés</legend>
        {ROLES.map((role) => (
          <label className="role-check" key={role}>
            <input type="checkbox" checked={allowedRoles.includes(role)} onChange={() => toggleRole(role)} />
            {roleLabel(role)}
          </label>
        ))}
      </fieldset>
      <p className="field-hint">
        Vous devenez responsable des documents importés : c'est à vous que la révision sera demandée.
        Pour un dossier entier, l'import depuis le serveur (app.import_folder) est plus adapté.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>
        <Icon name="upload" />{' '}
        {busy ? 'Indexation locale…' : files.length > 1 ? `Importer ${files.length} documents` : 'Importer et indexer'}
      </button>
      {batchReport && (
        <ul className="batch-report" aria-label="Résultat de l'import">
          {batchReport.results.map((row) => (
            <li key={row.filename} className={row.status}>
              <Icon name={row.status === 'ok' ? 'check' : 'close'} />
              <strong>{row.filename}</strong>
              <span>{row.status === 'ok' ? `importé${row.version > 1 ? ` (version ${row.version})` : ''}` : row.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </form>
  )
}

// ---------------------------------------------------------------------------
// The library
// ---------------------------------------------------------------------------

/** A column title that sorts. `descending` is optional: title, service and review
 * sort one way only, the date both ways. */
function SortHeader({ label, ascending, descending, current, onSort }) {
  const active = current === ascending || (descending && current === descending)
  const next = current === ascending && descending ? descending : ascending
  return (
    <button type="button" className={`sort-header ${active ? 'active' : ''}`} onClick={() => onSort(next)}>
      {label}
      <Icon name={active ? 'chevron-down' : 'sort'} className={active && current === ascending ? 'flip' : ''} />
    </button>
  )
}

function ariaSort(current, ascending, descending) {
  if (current === ascending) return 'ascending'
  if (descending && current === descending) return 'descending'
  return undefined
}

export function DocumentsView({ user, query, onQuery, onToast, onAsk, onLibraryChanged }) {
  // Standalone use (tests) keeps its own query; in the application it is the address.
  const [localQuery, setLocalQuery] = useState(query ?? {})
  const currentQuery = useMemo(() => (onQuery ? query ?? {} : localQuery), [onQuery, query, localQuery])
  const setQuery = onQuery ?? setLocalQuery
  const state = readLibraryQuery(currentQuery)

  const canManage = user.role === 'admin' || user.role === 'document_manager'
  const isAdmin = user.role === 'admin'
  const favorites = useFavorites(user.id)
  const favoriteKey = favorites.ids.join(',')
  // A table on a phone scrolls sideways: cards are the first view there, until chosen otherwise.
  const [view, setView] = usePersistentState(
    'ansi-vue-documents',
    window.matchMedia?.('(max-width: 800px)').matches ? 'cartes' : 'liste',
  )
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [openId, setOpenId] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [bulkTarget, setBulkTarget] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const [contentResults, setContentResults] = useState(null)
  const [contentBusy, setContentBusy] = useState(false)
  const [text, setText] = useState(state.q)
  const debounced = useDebounced(text, 300)

  const update = useCallback(
    (changes) => setQuery(writeLibraryQuery({ ...readLibraryQuery(currentQuery), page: 1, ...changes })),
    [currentQuery, setQuery],
  )

  useEffect(() => setText(state.q), [state.q])

  useEffect(() => {
    if (state.mode === 'titres' && debounced !== state.q) update({ q: debounced })
    // Only the debounced text drives this; the query is read, not watched.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced])

  useEffect(() => {
    if (state.mode === 'contenu') return undefined
    if (state.favoris && !favorites.ids.length) {
      setData(EMPTY_PAGE)
      return undefined
    }
    let cancelled = false
    setLoading(true)
    request(`/documents/page?${apiParams(state, favorites.ids)}`)
      .then((page) => {
        if (cancelled) return
        setData(page)
        setSelected(new Set())
        // A deletion can empty the last page: the server answers with the new last one.
        if (page.page !== state.page) setQuery(writeLibraryQuery({ ...state, page: page.page }))
      })
      .catch((requestError) => !cancelled && onToast(requestError.message, 'error'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // The state is derived from these fields; listing them keeps the fetch exact.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.page, state.taille, state.tri, state.q, state.service, state.classification, state.format,
      state.statut, state.versions, state.favoris, state.mode, favoriteKey, reload, onToast])

  const refresh = useCallback(() => {
    setReload((value) => value + 1)
    onLibraryChanged?.()
  }, [onLibraryChanged])

  async function searchContent() {
    const question = text.trim()
    if (question.length < 2) return
    setContentBusy(true)
    try {
      const response = await request('/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: question, limit: 12 }),
      })
      setContentResults({ query: question, items: response.results })
    } catch (requestError) {
      onToast(requestError.message, 'error')
    } finally {
      setContentBusy(false)
    }
  }

  async function exportList() {
    try {
      const rows = [['Titre', 'Fichier', 'Service', 'Classification', 'Rôles', 'Responsable', 'Révision',
                     'Valide jusqu\'au', 'Version', 'Importé le']]
      for (let page = 1; ; page += 1) {
        const batch = await request(`/documents/page?${apiParams({ ...state, page }, favorites.ids, { size: 100 })}`)
        batch.items.forEach((document) => rows.push([
          document.title, document.filename, document.department_label, document.classification,
          document.allowed_roles.join(' '), document.owner ?? '', document.review_due?.slice(0, 10) ?? '',
          document.valid_until?.slice(0, 10) ?? '', document.version, document.created_at.slice(0, 10),
        ]))
        if (page >= batch.pages) break
      }
      downloadCsv(`documents-${new Date().toISOString().slice(0, 10)}.csv`, rows)
      onToast(`${countOf(rows.length - 1, 'document exporté', 'documents exportés')}.`, 'success')
    } catch (requestError) {
      onToast(requestError.message, 'error')
    }
  }

  async function runBulk(kind) {
    const ids = [...selected]
    if (!ids.length) return
    const noun = countOf(ids.length, 'document')
    if (kind === 'delete' && !window.confirm(`Supprimer ${noun} et leur index local ? Cette action est définitive.`)) return
    if (kind === 'move' && !bulkTarget) return
    setBulkBusy(true)
    let failed = 0
    for (const id of ids) {
      try {
        await request(`/documents/${id}`, kind === 'delete'
          ? { method: 'DELETE' }
          : { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ department: bulkTarget }) })
      } catch {
        failed += 1
      }
    }
    setBulkBusy(false)
    setSelected(new Set())
    setBulkTarget('')
    const done = ids.length - failed
    onToast(
      kind === 'delete' ? `${countOf(done, 'document supprimé', 'documents supprimés')}${failed ? `, ${countOf(failed, 'échec')}` : ''}.`
        : `${countOf(done, 'document déplacé', 'documents déplacés')}${failed ? `, ${countOf(failed, 'échec')}` : ''}.`,
      failed ? 'error' : 'success',
    )
    refresh()
  }

  const page = data ?? EMPTY_PAGE
  const facets = page.facets
  const items = page.items
  const departmentTotal = Object.values(facets.department).reduce((sum, count) => sum + count, 0)
  const filtered = Boolean(state.q || state.service || state.classification || state.format || state.statut
                           || state.favoris || state.versions)
  const allSelected = items.length > 0 && items.every((document) => selected.has(document.id))

  function toggleSelected(id) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const openDocument = useCallback((id) => setOpenId(id), [])
  const closeDocument = useCallback(() => setOpenId(null), [])

  return (
    <section className="workspace library">
      <PageHeader
        icon="folder"
        overline="BIBLIOTHÈQUE DOCUMENTAIRE"
        title="Documents"
        actions={(
          <>
            {isAdmin && (
              <button type="button" className="ghost-button" onClick={exportList} disabled={!page.total}>
                <Icon name="download" /> Exporter
              </button>
            )}
            {canManage && (
              <button type="button" className="primary" onClick={() => setUploading((value) => !value)}
                      aria-expanded={uploading}>
                <Icon name="upload" /> Importer
              </button>
            )}
          </>
        )}
      >
        {page.readable_total
          ? `${page.readable_total.toLocaleString('fr-FR')} document${page.readable_total > 1 ? 's' : ''} lisible${page.readable_total > 1 ? 's' : ''} par votre compte. Les droits sont appliqués avant toute recherche.`
          : 'Les droits sont appliqués avant toute recherche.'}
      </PageHeader>

      {uploading && canManage && (
        <UploadPanel onUploaded={refresh} onClose={() => setUploading(false)} onToast={onToast} />
      )}

      <div className="library-search">
        <SearchInput
          value={text}
          onChange={setText}
          onSubmit={() => (state.mode === 'contenu' ? searchContent() : update({ q: text }))}
          label={state.mode === 'contenu' ? 'Rechercher dans le contenu des documents' : 'Rechercher un document'}
          placeholder={state.mode === 'contenu'
            ? 'Une question ou quelques mots, puis Entrée…'
            : 'Titre, fichier, service ou responsable…'}
        />
        <div className="mode-toggle" role="group" aria-label="Portée de la recherche">
          <button type="button" aria-pressed={state.mode === 'titres'} onClick={() => update({ mode: 'titres' })}>
            <Icon name="tag" /> Titres
          </button>
          <button type="button" aria-pressed={state.mode === 'contenu'}
                  onClick={() => { setContentResults(null); update({ mode: 'contenu' }) }}>
            <Icon name="doc-text" /> Contenu
          </button>
        </div>
      </div>

      {state.mode === 'contenu' ? (
        <div className="content-search">
          {contentBusy ? (
            <SkeletonRows rows={4} />
          ) : contentResults === null ? (
            <EmptyState icon="search" title="Chercher dans le texte des documents">
              Tapez une question ou quelques mots, puis Entrée : les passages les plus proches s'affichent, avec
              leur page — quelques secondes, sans rédaction de réponse. La recherche porte sur tous vos documents.
            </EmptyState>
          ) : contentResults.items.length === 0 ? (
            <EmptyState icon="search" title="Aucun passage correspondant">
              Essayez d'autres mots, ou posez la question à l'assistant.
            </EmptyState>
          ) : (
            <>
              <p className="page-summary">
                <strong>{contentResults.items.length}</strong> {agree(contentResults.items.length, 'passage', 'passages')} pour « {contentResults.query} »
              </p>
              <ol className="passage-list">
                {contentResults.items.map((hit, index) => (
                  <li key={`${hit.document_id}-${hit.page}-${index}`}>
                    <button type="button" className="passage" onClick={() => openDocument(hit.document_id)}>
                      <span className="passage-head">
                        <strong>{hit.title}</strong>
                        <span className="passage-page">page {hit.page}</span>
                        <span className="passage-score">{Math.round(hit.score * 100)} %</span>
                      </span>
                      <span className="passage-text">{hit.excerpt}…</span>
                    </button>
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      ) : (
        <>
          {data && (
          <>
          <div className="facet-row" role="group" aria-label="Filtrer par service">
            <Chip active={!state.service} count={departmentTotal} onClick={() => update({ service: '' })}>
              Tous les services
            </Chip>
            {DOCUMENT_DEPARTMENTS.filter((item) => facets.department[item.value] || state.service === item.value)
              .map((item) => (
                <Chip key={item.value} tone={`dept ${item.value}`} active={state.service === item.value}
                      count={facets.department[item.value] ?? 0}
                      onClick={() => update({ service: state.service === item.value ? '' : item.value })}>
                  {SHORT_DEPARTMENT[item.value]}
                </Chip>
              ))}
          </div>
          <div className="facet-row secondary" role="group" aria-label="Filtres rapides">
            <Chip active={state.favoris} count={favorites.ids.length} onClick={() => update({ favoris: !state.favoris })}>
              <Icon name={state.favoris ? 'star-filled' : 'star'} /> Favoris
            </Chip>
            {canManage && STATUSES.map((item) => (
              <Chip key={item.value} tone={item.tone} active={state.statut === item.value}
                    count={facets.status[item.value] ?? 0}
                    onClick={() => update({ statut: state.statut === item.value ? '' : item.value })}>
                {item.label}
              </Chip>
            ))}
            {!canManage && (
              <Chip tone="danger" active={state.statut === 'expired'} count={facets.status.expired ?? 0}
                    onClick={() => update({ statut: state.statut === 'expired' ? '' : 'expired' })}>
                Périmés
              </Chip>
            )}
          </div>
          </>
          )}

          <div className="library-toolbar">
            <label className="compact-field">
              <span>Classification</span>
              <select value={state.classification} onChange={(event) => update({ classification: event.target.value })}>
                <option value="">Toutes</option>
                {CLASSIFICATIONS.map((value) => (
                  <option key={value} value={value}>
                    {value[0].toUpperCase() + value.slice(1)}{facets.classification[value] !== undefined ? ` (${facets.classification[value]})` : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="compact-field">
              <span>Format</span>
              <select value={state.format} onChange={(event) => update({ format: event.target.value })}>
                <option value="">Tous</option>
                {FORMATS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}{facets.format[item.value] !== undefined ? ` (${facets.format[item.value]})` : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="compact-field">
              <span>Trier par</span>
              <select value={state.tri} onChange={(event) => update({ tri: event.target.value })}>
                {SORTS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="toggle-field" title="Les versions remplacées ne sont plus interrogées par l'assistant">
              <input type="checkbox" checked={state.versions} onChange={(event) => update({ versions: event.target.checked })} />
              Versions remplacées
            </label>
            <div className="view-toggle" role="group" aria-label="Affichage">
              <button type="button" aria-pressed={view === 'liste'} onClick={() => setView('liste')} title="Liste">
                <Icon name="list" /><span className="visually-hidden">Liste</span>
              </button>
              <button type="button" aria-pressed={view === 'cartes'} onClick={() => setView('cartes')} title="Cartes">
                <Icon name="cards" /><span className="visually-hidden">Cartes</span>
              </button>
            </div>
          </div>

          <div className="results-bar">
            <PageSummary first={page.first} last={page.last} total={page.total} noun="document" />
            {loading && <span className="loading-dot" role="status">Mise à jour…</span>}
            {filtered && (
              <button type="button" className="text-button" onClick={() => {
                setText('')
                setQuery(writeLibraryQuery({ ...readLibraryQuery({}), taille: state.taille, tri: state.tri }))
              }}>
                <Icon name="close" /> Réinitialiser les filtres
              </button>
            )}
          </div>

          {isAdmin && selected.size > 0 && (
            <div className="bulk-bar" role="region" aria-label="Actions sur la sélection">
              <strong>{selected.size} sélectionné{selected.size > 1 ? 's' : ''}</strong>
              <label className="compact-field inline">
                <span>Déplacer vers</span>
                <select value={bulkTarget} onChange={(event) => setBulkTarget(event.target.value)}>
                  <option value="">Choisir un service…</option>
                  {DOCUMENT_DEPARTMENTS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <button type="button" className="ghost-button" disabled={!bulkTarget || bulkBusy} onClick={() => runBulk('move')}>
                <Icon name="arrow-right" /> Déplacer
              </button>
              <button type="button" className="ghost-button danger" disabled={bulkBusy} onClick={() => runBulk('delete')}>
                <Icon name="trash" /> Supprimer
              </button>
              <button type="button" className="text-button" onClick={() => setSelected(new Set())}>Annuler</button>
            </div>
          )}

          <h2 className="visually-hidden">Liste des documents</h2>
          {!data ? (
            <SkeletonRows rows={6} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={filtered ? 'filter' : 'folder'}
              title={filtered ? 'Aucun document ne correspond' : page.readable_total ? 'Aucun document' : 'La bibliothèque est vide'}
              action={filtered ? null : canManage && (
                <button type="button" className="primary" onClick={() => setUploading(true)}>
                  <Icon name="upload" /> Importer des documents
                </button>
              )}
            >
              {filtered
                ? state.favoris && !favorites.ids.length
                  ? 'Ajoutez un document à vos favoris avec l’étoile, il apparaîtra ici.'
                  : 'Modifiez la recherche ou retirez un filtre.'
                : 'Importez les procédures d’un service pour que ses agents puissent les interroger.'}
            </EmptyState>
          ) : view === 'cartes' ? (
            <ul className="doc-grid">
              {items.map((document) => {
                const favourite = favorites.has(document.id)
                return (
                  <li key={document.id} className={`doc-card ${document.is_current ? '' : 'superseded'}`}>
                    <div className="doc-card-top">
                      <FormatBadge filename={document.filename} />
                      <button type="button" className={`star ${favourite ? 'on' : ''}`} aria-pressed={favourite}
                              aria-label={favourite ? `Retirer « ${document.title} » des favoris` : `Ajouter « ${document.title} » aux favoris`}
                              onClick={() => favorites.toggle(document.id)}>
                        <Icon name={favourite ? 'star-filled' : 'star'} />
                      </button>
                    </div>
                    <button type="button" className="doc-title" onClick={() => openDocument(document.id)}>
                      {document.title}
                    </button>
                    <span className="doc-file"><span className="doc-filename" title={document.filename}>{document.filename}</span></span>
                    <div className="doc-badges">
                      <span className={`dept-badge ${document.department}`}>{SHORT_DEPARTMENT[document.department] ?? document.department_label}</span>
                      <span className={`tag-classification ${document.classification}`}>{document.classification}</span>
                      {document.version > 1 && <span className="version-badge">v{document.version}</span>}
                      {!document.is_current && <span className="version-badge muted">remplacée</span>}
                      {document.is_expired && <span className="version-badge danger">périmée</span>}
                      {canManage && !document.owner && <span className="tag-department none">sans responsable</span>}
                      {canManage && ['overdue', 'due_soon'].includes(document.review_status) && (
                        <ReviewBadge status={document.review_status} due={document.review_due} />
                      )}
                    </div>
                    <span className="doc-card-foot" title={formatDate(document.created_at)}>
                      <Icon name="clock" /> {timeAgo(document.created_at)}
                    </span>
                  </li>
                )
              })}
            </ul>
          ) : (
            <div className="table-scroll">
              <table className="data-table doc-table">
                <caption className="visually-hidden">Documents, page {page.page} sur {page.pages}</caption>
                <thead>
                  <tr>
                    {isAdmin && (
                      <th scope="col" className="col-check">
                        <input type="checkbox" checked={allSelected} aria-label="Sélectionner tous les documents de la page"
                               onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((document) => document.id)))} />
                      </th>
                    )}
                    <th scope="col" aria-sort={ariaSort(state.tri, 'title')}>
                      <SortHeader label="Document" ascending="title" current={state.tri} onSort={(tri) => update({ tri })} />
                    </th>
                    <th scope="col" aria-sort={ariaSort(state.tri, 'department')}>
                      <SortHeader label="Service, classification" ascending="department" current={state.tri}
                                  onSort={(tri) => update({ tri })} />
                    </th>
                    {canManage && (
                      <th scope="col" aria-sort={ariaSort(state.tri, 'review')}>
                        <SortHeader label="Responsable, révision" ascending="review" current={state.tri}
                                    onSort={(tri) => update({ tri })} />
                      </th>
                    )}
                    <th scope="col" className="col-added" aria-sort={ariaSort(state.tri, 'oldest', 'recent')}>
                      <SortHeader label="Ajouté" ascending="oldest" descending="recent" current={state.tri}
                                  onSort={(tri) => update({ tri })} />
                    </th>
                    <th scope="col"><span className="visually-hidden">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((document) => {
                    const favourite = favorites.has(document.id)
                    return (
                      <tr key={document.id} className={`${document.is_current ? '' : 'row-muted'} ${selected.has(document.id) ? 'row-selected' : ''}`}>
                        {isAdmin && (
                          <td className="col-check">
                            <input type="checkbox" checked={selected.has(document.id)}
                                   aria-label={`Sélectionner « ${document.title} »`}
                                   onChange={() => toggleSelected(document.id)} />
                          </td>
                        )}
                        <td>
                          <div className="doc-cell">
                            <FormatBadge filename={document.filename} />
                            <div className="doc-cell-text">
                              <button type="button" className="doc-title" onClick={() => openDocument(document.id)}>
                                {document.title}
                              </button>
                              <span className="doc-file">
                                <span className="doc-filename" title={document.filename}>{document.filename}</span>
                                {document.version > 1 && <span className="version-badge">v{document.version}</span>}
                                {!document.is_current && <span className="version-badge muted">remplacée</span>}
                                {document.is_expired && <span className="version-badge danger">périmée</span>}
                              </span>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="stack-cell">
                            <span className={`dept-badge ${document.department}`}>
                              {SHORT_DEPARTMENT[document.department] ?? document.department_label}
                            </span>
                            <span className={`tag-classification ${document.classification}`}>{document.classification}</span>
                          </div>
                        </td>
                        {canManage && (
                          <td>
                            <div className="owner-cell">
                              {document.owner ? <span>{document.owner}</span> : <span className="tag-department none">sans responsable</span>}
                              {document.review_status !== 'none' && (
                                <ReviewBadge status={document.review_status} due={document.review_due} />
                              )}
                            </div>
                          </td>
                        )}
                        <td className="nowrap muted col-added" title={formatDate(document.created_at)}>{timeAgo(document.created_at)}</td>
                        <td className="row-actions">
                          <button type="button" className={`star ${favourite ? 'on' : ''}`} aria-pressed={favourite}
                                  aria-label={favourite ? `Retirer « ${document.title} » des favoris` : `Ajouter « ${document.title} » aux favoris`}
                                  onClick={() => favorites.toggle(document.id)}>
                            <Icon name={favourite ? 'star-filled' : 'star'} />
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {data && page.total > 0 && (
            <div className="list-footer">
              <PageSummary first={page.first} last={page.last} total={page.total} noun="document" />
              <Pagination page={page.page} pages={page.pages} onPage={(number) => update({ page: number })}
                          label="Pages de documents" />
              <PageSizeSelect value={state.taille} onChange={(taille) => update({ taille })} options={SIZES} />
            </div>
          )}
        </>
      )}

      <DocumentDrawer
        documentId={openId}
        user={user}
        favorites={favorites}
        onClose={closeDocument}
        onChanged={refresh}
        onAsk={onAsk}
        onToast={onToast}
      />
    </section>
  )
}
