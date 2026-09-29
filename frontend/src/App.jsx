import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'

import Administration from './Administration.jsx'
import { DocumentDrawer, DocumentsView } from './Documents.jsx'
import { ValidatedAnswerEditor } from './Knowledge.jsx'
import { ForcedPasswordChange, Login } from './Login.jsx'
import Profile from './Profile.jsx'
import Users from './Users.jsx'
import { useRoute } from './routing.js'
import { DOCUMENT_DEPARTMENTS, Icon, request, roleLabel, streamChat } from './shared.jsx'
import { Bar, EmptyState, FormatBadge, SearchInput, StatCard, countOf, timeAgo, useFavorites } from './ui.jsx'

const TABS = [
  { id: 'overview', label: 'Accueil', icon: 'grid', group: 'ESPACE' },
  { id: 'chat', label: 'Assistant', icon: 'chat', group: 'ESPACE' },
  { id: 'documents', label: 'Documents', icon: 'folder', group: 'ESPACE' },
  { id: 'users', label: 'Comptes', icon: 'users', admin: true, group: 'ADMINISTRATION' },
  { id: 'admin', label: 'Supervision', icon: 'shield', admin: true, group: 'ADMINISTRATION' },
]

const ADMIN_SECTION_LABELS = {
  supervision: 'Supervision',
  lacunes: 'Lacunes du corpus',
  reponses: 'Réponses validées',
  contacts: 'Contacts',
  journal: "Journal d'audit",
  retours: 'Retours',
}

const EMPTY_LIBRARY = { total: 0, readable_total: 0, items: [], facets: { department: {} } }

function getInitialTheme() {
  try {
    const stored = window.localStorage.getItem('ansi-theme')
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // Private browsing or storage disabled: fall through to system preference.
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function useTheme() {
  const [theme, setTheme] = useState(getInitialTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      window.localStorage.setItem('ansi-theme', theme)
    } catch {
      // Nothing to persist to; the toggle still works for this session.
    }
  }, [theme])
  return [theme, () => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))]
}

export function useToasts() {
  const [toasts, setToasts] = useState([])
  // Memoised on purpose: `push` is passed down as `onToast` and read by effect
  // dependency lists. A fresh function on every render made those effects refire,
  // which is what produced nine identical GET /documents in a row.
  const push = useCallback((message, tone = 'info') => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    setToasts((current) => [...current, { id, message, tone }])
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 3600)
  }, [])
  return { toasts, push }
}

function ToastStack({ toasts }) {
  if (!toasts.length) return null
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div className={`toast ${toast.tone}`} key={toast.id}>
          {toast.tone === 'success' && <Icon name="check" />}
          {toast.tone === 'error' && <Icon name="close" />}
          <span>{toast.message}</span>
        </div>
      ))}
    </div>
  )
}

/** Minimal, dependency-free renderer: bold, inline code, and lists only.
 * Builds React elements directly (never dangerouslySetInnerHTML) because
 * assistant answers are derived from untrusted document content. */
function renderInline(text, keyPrefix) {
  const nodes = []
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let lastIndex = 0
  let match
  let index = 0
  while ((match = pattern.exec(text))) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const token = match[0]
    if (token.startsWith('**')) {
      nodes.push(<strong key={`${keyPrefix}-b${index}`}>{token.slice(2, -2)}</strong>)
    } else {
      nodes.push(<code key={`${keyPrefix}-c${index}`}>{token.slice(1, -1)}</code>)
    }
    lastIndex = pattern.lastIndex
    index += 1
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

function renderRichText(content) {
  const lines = content.split('\n')
  const blocks = []
  let listBuffer = []
  let listType = null

  function flushList() {
    if (!listBuffer.length) return
    const ListTag = listType === 'ol' ? 'ol' : 'ul'
    blocks.push(
      <ListTag key={`list-${blocks.length}`}>
        {listBuffer.map((item, itemIndex) => (
          <li key={itemIndex}>{renderInline(item, `li-${blocks.length}-${itemIndex}`)}</li>
        ))}
      </ListTag>,
    )
    listBuffer = []
    listType = null
  }

  lines.forEach((rawLine, lineIndex) => {
    const line = rawLine.trim()
    if (!line) {
      flushList()
      return
    }
    const bulletMatch = line.match(/^[-*]\s+(.*)/)
    const orderedMatch = line.match(/^\d+[.)]\s+(.*)/)
    if (bulletMatch) {
      if (listType !== 'ul') flushList()
      listType = 'ul'
      listBuffer.push(bulletMatch[1])
      return
    }
    if (orderedMatch) {
      if (listType !== 'ol') flushList()
      listType = 'ol'
      listBuffer.push(orderedMatch[1])
      return
    }
    flushList()
    blocks.push(<p key={`p-${blocks.length}-${lineIndex}`}>{renderInline(line, `p-${blocks.length}-${lineIndex}`)}</p>)
  })
  flushList()
  return blocks
}

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const helper = document.createElement('textarea')
  helper.value = text
  helper.style.position = 'fixed'
  helper.style.opacity = '0'
  document.body.appendChild(helper)
  helper.select()
  document.execCommand('copy')
  document.body.removeChild(helper)
}

function fold(text) {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

/** Keyboard navigation. Alt+1..5 switch section, Ctrl/Cmd+K focuses the question
 * field, "/" the document search, "?" opens the shortcut list. Alt is used rather
 * than Ctrl for sections so browser tab-switching keeps working. */
function useShortcuts({ onSection, onFocusComposer, onFocusSearch, onToggleHelp, enabled }) {
  useEffect(() => {
    if (!enabled) return undefined
    function handle(event) {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        onFocusComposer()
        return
      }
      if (event.altKey && ['1', '2', '3', '4', '5'].includes(event.key)) {
        event.preventDefault()
        onSection(Number(event.key) - 1)
        return
      }
      if (event.key === '/' && !typing) {
        event.preventDefault()
        onFocusSearch()
        return
      }
      if (event.key === '?' && !typing) {
        event.preventDefault()
        onToggleHelp()
      }
      if (event.key === 'Escape') onToggleHelp(false)
    }
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  }, [enabled, onSection, onFocusComposer, onFocusSearch, onToggleHelp])
}

function ShortcutHelp({ open, onClose }) {
  if (!open) return null
  const rows = [
    ['Alt + 1 … 5', 'Changer de section'],
    ['/', 'Rechercher un document'],
    ['Ctrl / Cmd + K', 'Aller au champ de question'],
    ['Entrée', 'Envoyer la question'],
    ['Maj + Entrée', 'Saut de ligne'],
    ['?', 'Afficher ou masquer cette aide'],
    ['Échap', 'Fermer'],
  ]
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="shortcut-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <p className="overline">RACCOURCIS CLAVIER</p>
            <h2 id="shortcuts-title">Navigation rapide</h2>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Fermer l'aide" autoFocus>
            <Icon name="close" />
          </button>
        </header>
        <div className="shortcut-list">
          {rows.map(([keys, what]) => (
            <div key={keys}>
              <kbd>{keys}</kbd>
              <span>{what}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

function TopBar({ trail, system, searchRef, onSearch, showSearch }) {
  const [text, setText] = useState('')
  const ready = system?.chat_model_ready && system?.embedding_model_ready
  return (
    <header className="top-bar">
      <nav className="breadcrumb" aria-label="Fil d'Ariane">
        <ol>
          {trail.map((item, index) => (
            <li key={item} aria-current={index === trail.length - 1 ? 'page' : undefined}>{item}</li>
          ))}
        </ol>
      </nav>
      {showSearch && (
        <SearchInput
          ref={searchRef}
          className="global-search"
          value={text}
          onChange={setText}
          onSubmit={() => {
            if (!text.trim()) return
            onSearch(text.trim())
            setText('')
          }}
          label="Rechercher un document"
          placeholder="Rechercher un document…"
          hint="/"
        />
      )}
      <span className={`status-pill ${ready ? 'ready' : 'warning'}`} title={ready ? `${system.chat_model} + ${system.embedding_model}` : 'Ollama ou un modèle ne répond pas'}>
        <span className="status-dot" aria-hidden="true" />
        {ready ? 'IA locale disponible' : system ? 'IA locale indisponible' : 'Vérification…'}
      </span>
    </header>
  )
}

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------

export function Overview({ user, system, library, onNavigate, onOpenDocument }) {
  const [profile, setProfile] = useState(null)
  useEffect(() => {
    request('/auth/profile').then(setProfile).catch(() => setProfile(null))
  }, [])

  const ready = system?.chat_model_ready && system?.embedding_model_ready
  const hour = new Date().getHours()
  const greeting = hour < 18 ? 'Bonjour' : 'Bonsoir'
  const today = new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
  const services = DOCUMENT_DEPARTMENTS
    .map((item) => ({ ...item, count: library.facets?.department?.[item.value] ?? 0 }))
    .filter((item) => item.count > 0)
  const largest = Math.max(1, ...services.map((item) => item.count))

  return (
    <section className="workspace overview">
      <div className="hero">
        <div className="hero-text">
          <p className="overline">{today.toUpperCase()}</p>
          <h1>{greeting}, {user.username}.</h1>
          <p>
            Interrogez les documents de l'agence : chaque réponse cite ses sources, et ne porte que sur ce
            que votre compte a le droit de lire.
          </p>
          <div className="hero-actions">
            <button type="button" className="primary" onClick={() => onNavigate('chat')}>
              <Icon name="chat" /> Poser une question
            </button>
            <button type="button" className="ghost-button" onClick={() => onNavigate('documents', null, { mode: 'contenu' })}>
              <Icon name="search" /> Chercher dans les documents
            </button>
          </div>
        </div>
        <div className={`readiness-card ${ready ? 'ready' : 'warning'}`}>
          <span className="readiness-icon" aria-hidden="true"><Icon name={ready ? 'check' : 'alert'} /></span>
          <div>
            <strong>{ready ? 'Services locaux disponibles' : 'Vérification requise'}</strong>
            <small>{ready ? `${system.chat_model} · ${system.embedding_model}` : 'Ollama ou un modèle est indisponible'}</small>
            <small>Aucune donnée ne quitte le serveur.</small>
          </div>
        </div>
      </div>

      <div className="stat-grid">
        <StatCard icon="folder" label="Documents lisibles" value={(library.readable_total ?? library.total).toLocaleString('fr-FR')}
                  onClick={() => onNavigate('documents')} />
        <StatCard icon="chat" label="Conversations" value={profile ? profile.activity.conversations : '—'}
                  onClick={() => onNavigate('chat')} />
        <StatCard icon="spark" label="Questions posées" value={profile ? profile.activity.questions : '—'} />
        <StatCard icon="lock" label="Exécution de l'IA" value="Locale" hint="Aucune API externe" tone="accent" />
      </div>

      <div className="overview-grid">
        <article className="panel">
          <div className="panel-head">
            <h2 className="block-title">Derniers documents</h2>
            <button type="button" className="text-button" onClick={() => onNavigate('documents')}>
              Toute la bibliothèque <Icon name="arrow-right" />
            </button>
          </div>
          {library.items.length ? (
            <ul className="recent-docs">
              {library.items.slice(0, 5).map((document) => (
                <li key={document.id}>
                  <button type="button" onClick={() => onOpenDocument(document.id)}>
                    <FormatBadge filename={document.filename} />
                    <span className="recent-doc-text">
                      <strong>{document.title}</strong>
                      <small>{document.department_label} · {timeAgo(document.created_at)}</small>
                    </span>
                    <Icon name="arrow-right" className="recent-doc-go" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon="folder" title="Aucun document accessible">
              Un administrateur ou gestionnaire documentaire doit importer un document de votre service.
            </EmptyState>
          )}
        </article>

        <article className="panel">
          <div className="panel-head">
            <h2 className="block-title">Par service</h2>
          </div>
          {services.length ? (
            <ul className="service-bars">
              {services.map((item) => (
                <li key={item.value}>
                  <button type="button" onClick={() => onNavigate('documents', null, { service: item.value })}>
                    <span className="service-name">{item.label}</span>
                    <Bar value={item.count} max={largest} tone={`dept ${item.value}`} />
                    <strong>{item.count}</strong>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">Aucun document pour l'instant.</p>
          )}
        </article>
      </div>

      <div className="overview-grid">
        <article className="panel tinted">
          <p className="overline">GARANTIES DE L'ASSISTANT</p>
          <ul className="check-list">
            <li><Icon name="check" />Une source indisponible produit un refus, jamais une invention.</li>
            <li><Icon name="check" />Rôle et service filtrent les documents avant la recherche, dans le code.</li>
            <li><Icon name="check" />Les PDF scannés sont lus sur place, sans service en ligne.</li>
            <li><Icon name="check" />Une réponse administrative importante reste validée par un agent.</li>
          </ul>
        </article>
        <article className="panel">
          <p className="overline">BIEN POSER UNE QUESTION</p>
          <ol className="tips">
            <li>Une question précise : « délai de préavis d'un agent titulaire » plutôt que « préavis ».</li>
            <li>Vérifiez la source citée sous la réponse : un clic l'ouvre.</li>
            <li>Une question qui suit la précédente peut rester courte : « et pour un stagiaire ? ».</li>
          </ol>
          <p className="muted small">Raccourcis : <kbd>/</kbd> rechercher un document, <kbd>?</kbd> toute l'aide clavier.</p>
        </article>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Assistant
// ---------------------------------------------------------------------------

function ConversationRow({ conversation, isActive, onSelect, onRename, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(conversation.title)
  const inputRef = useRef(null)

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  function startEditing(event) {
    event.stopPropagation()
    setValue(conversation.title)
    setEditing(true)
  }

  function commit() {
    const trimmed = value.trim()
    setEditing(false)
    if (trimmed && trimmed !== conversation.title) onRename(conversation.id, trimmed)
  }

  return (
    <div className={`conversation-row ${isActive ? 'selected' : ''}`}>
      {editing ? (
        <input
          ref={inputRef}
          className="conversation-rename"
          value={value}
          maxLength={160}
          aria-label="Nouveau titre de la conversation"
          onChange={(event) => setValue(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); commit() }
            if (event.key === 'Escape') { event.preventDefault(); setEditing(false) }
          }}
        />
      ) : (
        <button className="conversation-title" onClick={() => onSelect(conversation.id)} title={conversation.title}>
          {conversation.title}
        </button>
      )}
      <div className="conversation-actions">
        <button title="Renommer" aria-label={`Renommer la conversation « ${conversation.title} »`} onClick={startEditing}>
          <Icon name="pencil" />
        </button>
        <button
          title="Supprimer cette conversation"
          aria-label={`Supprimer la conversation « ${conversation.title} »`}
          onClick={(event) => { event.stopPropagation(); onDelete(conversation.id) }}
        >
          <Icon name="close" />
        </button>
      </div>
    </div>
  )
}

/** The answer text as it should be validated: without the referral appended to a
 * refusal, and without a previous validation's signature. */
function answerForValidation(content) {
  return content
    .split('\n\nPour cette question, vous pouvez vous adresser à')[0]
    .split('\n\n*Réponse validée par')[0]
    .trim()
}

function SourcePill({ source, onOpenDocument }) {
  const label = `${source.id} · ${source.title} · p. ${source.page}${source.is_expired ? ' · PÉRIMÉ' : ''}`
  if (!source.document_id) {
    return <span className={`source-pill ${source.is_expired ? 'expired' : ''}`}>{label}</span>
  }
  return (
    <button type="button" className={`source-pill ${source.is_expired ? 'expired' : ''}`}
            title="Ouvrir le document" onClick={() => onOpenDocument(source.document_id)}>
      <Icon name="doc-text" /> {label}
    </button>
  )
}

function ChatView({
  user,
  library,
  system,
  conversations,
  activeConversation,
  messages,
  streaming,
  draft,
  onDraftUsed,
  onNewConversation,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  onSend,
  onOpenDocument,
  onToast,
}) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState('assistant')
  const [results, setResults] = useState(null)
  const [rated, setRated] = useState({})
  const [publishing, setPublishing] = useState(null)
  const [filter, setFilter] = useState('')
  const composer = useRef(null)
  const ready = system?.chat_model_ready && system?.embedding_model_ready
  const available = library.readable_total ?? library.total

  useEffect(() => {
    if (!draft) return
    setMode('assistant')
    setMessage(draft)
    onDraftUsed()
    window.requestAnimationFrame(() => {
      composer.current?.focus()
      composer.current?.setSelectionRange(draft.length, draft.length)
    })
  }, [draft, onDraftUsed])

  const shownConversations = filter
    ? conversations.filter((conversation) => fold(conversation.title).includes(fold(filter)))
    : conversations

  async function submit(event) {
    event.preventDefault()
    const question = message.trim()
    if (!question || busy) return
    setBusy(true)
    setError('')
    try {
      if (mode === 'search') {
        // Retrieval only: no generation, so seconds instead of a minute.
        const response = await request('/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: question, limit: 8 }),
        })
        setResults({ query: question, items: response.results })
      } else {
        await onSend(question)
        setResults(null)
      }
      setMessage('')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  async function rate(messageId, verdict) {
    if (!messageId || rated[messageId]) return
    try {
      await request('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: messageId, verdict }),
      })
      setRated((current) => ({ ...current, [messageId]: verdict }))
      onToast(verdict === 'useful' ? 'Merci, réponse marquée utile.' : 'Merci, réponse signalée comme incorrecte.', 'success')
    } catch (requestError) {
      onToast(requestError.message, 'error')
    }
  }

  async function copyMessage(content) {
    try {
      await copyToClipboard(content)
      onToast('Réponse copiée.', 'success')
    } catch {
      onToast('Impossible de copier automatiquement.', 'error')
    }
  }

  return (
    <section className="assistant-workspace">
      {publishing && (
        <ValidatedAnswerEditor
          initial={publishing}
          onClose={() => setPublishing(null)}
          onSaved={() => undefined}
          onToast={onToast}
        />
      )}
      <aside className="conversation-sidebar" aria-label="Conversations">
        <button className="new-conversation" onClick={onNewConversation}>
          <Icon name="plus" /> Nouvelle conversation
        </button>
        {conversations.length > 4 && (
          <div className="conversation-filter">
            <Icon name="search" />
            <input value={filter} onChange={(event) => setFilter(event.target.value)}
                   placeholder="Filtrer les conversations…" aria-label="Filtrer les conversations" />
          </div>
        )}
        <p className="conversation-label">VOS CONVERSATIONS</p>
        <div className="conversation-list">
          {conversations.length === 0 ? (
            <p className="empty-list">Vos échanges documentaires apparaîtront ici.</p>
          ) : shownConversations.length === 0 ? (
            <p className="empty-list">Aucune conversation ne correspond.</p>
          ) : (
            shownConversations.map((conversation) => (
              <ConversationRow
                key={conversation.id}
                conversation={conversation}
                isActive={activeConversation?.id === conversation.id}
                onSelect={onSelectConversation}
                onRename={onRenameConversation}
                onDelete={onDeleteConversation}
              />
            ))
          )}
        </div>
        <div className="local-note">
          <Icon name="shield" />
          <p>Historique stocké localement et visible seulement par votre compte.</p>
        </div>
      </aside>
      <main className="chat-main">
        <header className="chat-head">
          <div>
            <p className="overline">ASSISTANT DOCUMENTAIRE</p>
            <h2>{activeConversation?.title ?? 'Nouvelle conversation'}</h2>
          </div>
          <span className={`model-status ${ready ? 'ready' : 'warning'}`}>
            {ready ? '● IA locale disponible' : '! Vérifier Ollama'}
          </span>
        </header>
        {available === 0 ? (
          <div className="empty-state">
            <div className="empty-icon"><Icon name="folder" /></div>
            <h3>Aucun document accessible</h3>
            <p>Un administrateur ou gestionnaire documentaire doit importer un document et vous autoriser à y accéder avant toute recherche.</p>
          </div>
        ) : (
          <div className="conversation-content">
            {messages.length === 0 ? (
              <div className="welcome">
                <p className="overline">PRÊT À RECHERCHER</p>
                <h3>Que souhaitez-vous savoir&nbsp;?</h3>
                <p>
                  L'assistant répond <strong>uniquement</strong> à partir des documents ci-dessous, et cite le document
                  et la page utilisés. Si l'information ne s'y trouve pas, il le dit au lieu de l'inventer.
                </p>
                <div className="corpus">
                  <p className="corpus-label">
                    {available} document{available > 1 ? 's' : ''} interrogeable{available > 1 ? 's' : ''} par votre compte
                  </p>
                  <div className="corpus-items">
                    {library.items.slice(0, 6).map((document) => (
                      <button type="button" className="corpus-item" key={document.id} onClick={() => onOpenDocument(document.id)}>
                        <Icon name="doc-text" />
                        {document.title}
                      </button>
                    ))}
                    {available > 6 && <span className="corpus-item more">+ {available - 6} autres</span>}
                  </div>
                </div>
                <div className="suggestions">
                  <button onClick={() => setMessage('Résume les principaux objectifs présentés dans les documents.')}>
                    Résumer les objectifs
                  </button>
                  <button onClick={() => setMessage('Quelles sont les échéances mentionnées dans les documents ?')}>
                    Identifier les échéances
                  </button>
                  <button onClick={() => setMessage('Quels responsables sont nommés dans les documents ?')}>
                    Retrouver un responsable
                  </button>
                </div>
              </div>
            ) : (
              messages.map((entry, entryIndex) => (
                <article className={`message ${entry.role}`} key={entry.id ?? `${entry.role}-${entryIndex}`}>
                  <div className="message-head">
                    <p className="message-label">{entry.role === 'user' ? 'VOUS' : 'ASSISTANT ANSI'}</p>
                    {entry.role === 'assistant' && (
                      <div className="message-tools">
                        {user.role === 'admin' && entry.id && (
                          <button
                            className="text-button"
                            title="Servir cette réponse telle quelle, sans le modèle, aux questions qui ont le même sens"
                            onClick={() => setPublishing({
                              question: messages[entryIndex - 1]?.role === 'user' ? messages[entryIndex - 1].content : '',
                              answer: answerForValidation(entry.content),
                            })}
                          >
                            <Icon name="check" /> Publier comme réponse validée
                          </button>
                        )}
                        <button
                          className="copy-button"
                          title="Copier la réponse"
                          aria-label="Copier la réponse"
                          onClick={() => copyMessage(entry.content)}
                        >
                          <Icon name="copy" />
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="message-body">{renderRichText(entry.content)}</div>
                  {entry.sources?.length > 0 && (
                    <div className="source-list">
                      {entry.sources.map((source) => (
                        <SourcePill key={source.id} source={source} onOpenDocument={onOpenDocument} />
                      ))}
                    </div>
                  )}
                  {entry.role === 'assistant' && entry.id && (
                    <div className="rating">
                      {rated[entry.id] ? (
                        <span className="rating-done">
                          <Icon name="check" />
                          {rated[entry.id] === 'useful' ? 'Marquée utile' : 'Signalée comme incorrecte'}
                        </span>
                      ) : (
                        <>
                          <span className="rating-label">Cette réponse vous a-t-elle aidé&nbsp;?</span>
                          <button onClick={() => rate(entry.id, 'useful')}>Utile</button>
                          <button onClick={() => rate(entry.id, 'wrong')}>Incorrecte</button>
                        </>
                      )}
                    </div>
                  )}
                </article>
              ))
            )}
            <p className="visually-hidden" role="status" aria-live="polite">
              {streaming
                ? streaming.phase === 'thinking'
                  ? "L'assistant recherche dans les documents."
                  : "L'assistant rédige sa réponse."
                : ''}
            </p>
            {streaming && (
              <article className="message assistant streaming" aria-busy="true">
                <div className="message-head">
                  <p className="message-label">ASSISTANT ANSI</p>
                </div>
                {streaming.phase === 'thinking' ? (
                  <p className="thinking-line">
                    <span className="thinking-dots">
                      <span></span>
                      <span></span>
                      <span></span>
                    </span>
                    Raisonnement local… {streaming.chars > 0 && `${streaming.chars} caractères analysés`}
                  </p>
                ) : (
                  <div className="message-body">
                    {renderRichText(streaming.text)}
                    <span className="stream-caret" />
                  </div>
                )}
                {streaming.sources?.length > 0 && (
                  <div className="source-list">
                    {streaming.sources.map((source) => (
                      <SourcePill key={source.id} source={source} onOpenDocument={onOpenDocument} />
                    ))}
                  </div>
                )}
              </article>
            )}
          </div>
        )}
        {results && (
          <div className="search-results">
            <div className="search-head">
              <p className="overline">RÉSULTATS POUR « {results.query} »</p>
              <button className="text-button" onClick={() => setResults(null)}>
                <Icon name="close" /> Fermer
              </button>
            </div>
            {results.items.length === 0 ? (
              <p className="empty-list">Aucun passage correspondant dans vos documents autorisés.</p>
            ) : (
              results.items.map((item, index) => (
                <button type="button" className="search-hit" key={`${item.document_id}-${item.page}-${index}`}
                        onClick={() => onOpenDocument(item.document_id)}>
                  <span className="search-hit-head">
                    <strong>{item.title}</strong>
                    <span className="search-score">p. {item.page} · {Math.round(item.score * 100)}%</span>
                  </span>
                  <span className="search-hit-text">{item.excerpt}…</span>
                </button>
              ))
            )}
          </div>
        )}
        <form className="composer" onSubmit={submit}>
          <div className="mode-switch" role="group" aria-label="Mode de recherche">
            <button
              type="button"
              className={mode === 'assistant' ? 'active' : ''}
              aria-pressed={mode === 'assistant'}
              onClick={() => setMode('assistant')}
            >
              <Icon name="chat" /> Assistant
            </button>
            <button type="button" className={mode === 'search' ? 'active' : ''} aria-pressed={mode === 'search'}
                    onClick={() => setMode('search')}>
              <Icon name="search" /> Recherche rapide
            </button>
          </div>
          <div className="composer-input">
            <textarea
              ref={composer}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
              rows="3"
              maxLength="4000"
              aria-label="Votre question"
              placeholder={available ? 'Posez une question précise sur les documents autorisés…' : 'Aucun document accessible…'}
              disabled={!available || busy}
            />
            <span className="char-count">{message.length}/4000</span>
          </div>
          {error && <p className="error">{error}</p>}
          <div className="composer-footer">
            <span>
              {mode === 'search'
                ? 'Recherche seule : retrouve les passages, sans rédiger de réponse — quelques secondes.'
                : `${available} document${available > 1 ? 's' : ''} accessible${available > 1 ? 's' : ''} · Entrée pour envoyer, Maj+Entrée pour un saut de ligne`}
            </span>
            <button className="primary" disabled={!available || busy}>
              <Icon name={mode === 'search' ? 'search' : 'send'} />{' '}
              {busy ? (mode === 'search' ? 'Recherche…' : 'Analyse locale…') : mode === 'search' ? 'Rechercher' : 'Envoyer'}
            </button>
          </div>
        </form>
      </main>
    </section>
  )
}

/** The document panel opened from outside the library: a cited source, the home page. */
function StandaloneDrawer({ user, documentId, onClose, onChanged, onAsk, onToast }) {
  const favorites = useFavorites(user.id)
  return (
    <DocumentDrawer documentId={documentId} user={user} favorites={favorites} onClose={onClose}
                    onChanged={onChanged} onAsk={onAsk} onToast={onToast} />
  )
}

// ---------------------------------------------------------------------------
// Application
// ---------------------------------------------------------------------------

function App() {
  const [user, setUser] = useState(null)
  // Until /auth/me answers, nobody knows whether a session exists: showing the sign-in
  // form meanwhile made it flash on every page load for agents already signed in.
  const [checkingSession, setCheckingSession] = useState(true)
  const [library, setLibrary] = useState(EMPTY_LIBRARY)
  const [system, setSystem] = useState(null)
  const [conversations, setConversations] = useState([])
  const [activeConversation, setActiveConversation] = useState(null)
  const [messages, setMessages] = useState([])
  const [streaming, setStreaming] = useState(null)
  const [attention, setAttention] = useState({})
  const [openDocument, setOpenDocument] = useState(null)
  const [draft, setDraft] = useState('')
  // The section lives in the URL, so /administration/retours is a real address
  // an administrator can bookmark or share — and so does a filtered library.
  const [route, navigate, setQuery] = useRoute()
  const tab = route.tab
  const [loadError, setLoadError] = useState('')
  const [theme, toggleTheme] = useTheme()
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [sessionNotice, setSessionNotice] = useState('')
  const { toasts, push: pushToast } = useToasts()
  const globalSearch = useRef(null)

  const refreshLibrary = useCallback(async () => {
    try {
      setLibrary(await request('/documents/page?size=6'))
    } catch (requestError) {
      pushToast(requestError.message, 'error')
    }
  }, [pushToast])

  const refreshAttention = useCallback(async (account) => {
    if (account?.role !== 'admin') return
    try {
      const overview = await request('/admin/overview')
      setAttention({
        users: (overview.accounts.pending ?? 0) + (overview.accounts.password_resets_pending ?? 0),
        admin: overview.gaps?.unanswered ?? 0,
      })
    } catch {
      setAttention({})
    }
  }, [])

  async function initialize(currentUser) {
    setUser(currentUser)
    setSessionNotice('')
    // Every other endpoint answers 403 until the agent chooses their own password,
    // so loading the workspace now would only produce errors.
    if (currentUser.must_change_password) return
    try {
      const [loadedLibrary, loadedSystem, loadedConversations] = await Promise.all([
        request('/documents/page?size=6'),
        request('/system/status'),
        request('/conversations'),
      ])
      setLibrary(loadedLibrary)
      setSystem(loadedSystem)
      setConversations(loadedConversations)
      refreshAttention(currentUser)
    } catch (requestError) {
      setLoadError(requestError.message)
    }
  }

  // The badges in the navigation follow what the administrator just handled.
  useEffect(() => {
    if (user) refreshAttention(user)
  }, [tab, user, refreshAttention])

  async function selectConversation(id) {
    const response = await request(`/conversations/${id}/messages`)
    setActiveConversation(response.conversation)
    setMessages(response.messages)
    navigate('chat')
  }

  async function newConversation() {
    const conversation = await request('/conversations', { method: 'POST' })
    setConversations((current) => [conversation, ...current])
    setActiveConversation(conversation)
    setMessages([])
    navigate('chat')
  }

  async function renameConversation(id, title) {
    try {
      const updated = await request(`/conversations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      setConversations((current) => current.map((item) => (item.id === id ? updated : item)))
      if (activeConversation?.id === id) setActiveConversation(updated)
    } catch (requestError) {
      pushToast(requestError.message, 'error')
    }
  }

  async function deleteConversation(id) {
    if (!window.confirm('Supprimer cette conversation locale ?')) return
    await request(`/conversations/${id}`, { method: 'DELETE' })
    setConversations((current) => current.filter((conversation) => conversation.id !== id))
    if (activeConversation?.id === id) {
      setActiveConversation(null)
      setMessages([])
    }
    pushToast('Conversation supprimée.', 'success')
  }

  async function sendMessage(question) {
    setMessages((current) => [...current, { role: 'user', content: question }])
    setStreaming({ phase: 'thinking', text: '', sources: [], chars: 0 })

    let answer = ''
    let sources = []
    let failure = null
    let messageId = null
    try {
      await streamChat({ message: question, conversation_id: activeConversation?.id ?? null }, (event) => {
        if (event.type === 'meta') {
          sources = event.sources
          setStreaming((current) => ({ ...current, sources, phase: event.reasoning_expected ? 'thinking' : 'answer' }))
        } else if (event.type === 'thinking') {
          setStreaming((current) => ({ ...current, chars: event.chars }))
        } else if (event.type === 'answer_start') {
          answer = ''
          setStreaming((current) => ({ ...current, phase: 'answer', text: '' }))
        } else if (event.type === 'token') {
          answer += event.value
          setStreaming((current) => ({ ...current, text: answer }))
        } else if (event.type === 'error') {
          failure = event.detail
        } else if (event.type === 'done') {
          const conversation = event.conversation
          messageId = event.message_id ?? null
          setActiveConversation(conversation)
          setConversations((current) => [conversation, ...current.filter((item) => item.id !== conversation.id)])
        }
      })
    } finally {
      setStreaming(null)
    }

    if (failure) throw new Error(failure)
    setMessages((current) => [...current, { id: messageId, role: 'assistant', content: answer.trim(), sources }])
  }

  const askAbout = useCallback((document) => {
    setOpenDocument(null)
    setDraft(`À propos du document « ${document.title} » : `)
    navigate('chat')
  }, [navigate])

  const clearDraft = useCallback(() => setDraft(''), [])
  const closeDocument = useCallback(() => setOpenDocument(null), [])

  useEffect(() => {
    request('/auth/me')
      .then(initialize)
      .catch(() => undefined)
      .finally(() => setCheckingSession(false))
    // Once, on load: initialize() is not a dependency that changes meaning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A session can be closed from elsewhere: a password changed on another machine,
  // an administrator revoking it. The next request answers 401; without this the
  // interface would stay on screen, failing on every click.
  useEffect(() => {
    function onSessionLost(event) {
      setUser(null)
      setLibrary(EMPTY_LIBRARY)
      setConversations([])
      setActiveConversation(null)
      setMessages([])
      setStreaming(null)
      setOpenDocument(null)
      setSessionNotice(event.detail || 'Votre session a été fermée. Reconnectez-vous.')
    }
    window.addEventListener('ansi:session-lost', onSessionLost)
    return () => window.removeEventListener('ansi:session-lost', onSessionLost)
  }, [])

  const focusSearch = useCallback(() => {
    if (tab === 'documents') {
      document.querySelector('.library-search input')?.focus()
    } else {
      globalSearch.current?.focus()
    }
  }, [tab])

  useShortcuts({
    enabled: Boolean(user),
    onSection: (index) => {
      const available = TABS.filter((item) => !item.admin || user?.role === 'admin')
      if (available[index]) navigate(available[index].id)
    },
    onFocusComposer: () => {
      navigate('chat')
      // The composer belongs to ChatView; querying the DOM avoids threading a ref
      // through three components for a single focus call.
      window.requestAnimationFrame(() => document.querySelector('.composer textarea')?.focus())
    },
    onFocusSearch: focusSearch,
    onToggleHelp: (next) => setShowShortcuts((current) => (next === false ? false : !current)),
  })

  if (!user && checkingSession) {
    return (
      <main className="boot-screen" aria-busy="true">
        <div className="brand-mark" aria-hidden="true">A</div>
        <p role="status">Ouverture de votre espace…</p>
      </main>
    )
  }

  if (!user) {
    return (
      <Login onLogin={initialize} theme={theme} onToggleTheme={toggleTheme} initialNotice={sessionNotice} />
    )
  }

  async function logout() {
    await request('/auth/logout', { method: 'POST' })
    setUser(null)
    setLibrary(EMPTY_LIBRARY)
    setConversations([])
    setActiveConversation(null)
    setMessages([])
    setStreaming(null)
    setOpenDocument(null)
    navigate('overview')
  }

  if (user.must_change_password) {
    return <ForcedPasswordChange user={user} onDone={initialize} onLogout={logout} />
  }

  const visibleTabs = TABS.filter((item) => !item.admin || user.role === 'admin')
  const groups = [...new Set(visibleTabs.map((item) => item.group))]
  const trail = {
    overview: ['Accueil'],
    chat: ['Assistant'],
    documents: ['Documents'],
    users: ['Administration', 'Comptes'],
    profile: ['Mon profil'],
    admin: ['Administration', ADMIN_SECTION_LABELS[route.section ?? 'supervision'] ?? 'Supervision'],
  }[tab] ?? ['Accueil']
  const attentionTitle = {
    users: (count) => `${countOf(count, 'demande')} en attente`,
    admin: (count) => `${countOf(count, 'question')} sans réponse`,
  }

  return (
    <main className="app-shell">
      <a className="skip-link" href="#contenu">Aller au contenu</a>
      <aside className="main-sidebar" aria-label="Navigation principale">
        <div className="side-brand">
          <div className="brand-mark">A</div>
          <div>
            <strong>ANSI</strong>
            <span>Assistant documentaire</span>
          </div>
        </div>
        <nav aria-label="Sections">
          {groups.map((group) => (
            <div className="nav-group" key={group}>
              <p className="nav-label">{group}</p>
              {visibleTabs.filter((item) => item.group === group).map((item) => {
                const index = visibleTabs.indexOf(item)
                const count = attention[item.id]
                return (
                  <button
                    key={item.id}
                    className={tab === item.id ? 'active' : ''}
                    aria-current={tab === item.id ? 'page' : undefined}
                    onClick={() => navigate(item.id)}
                    title={`${item.label} (Alt+${index + 1})`}
                  >
                    <Icon name={item.icon} />
                    {item.label}
                    {count > 0 ? (
                      <span className="nav-badge" title={attentionTitle[item.id]?.(count)}>
                        {count}
                        <span className="visually-hidden"> — {attentionTitle[item.id]?.(count)}</span>
                      </span>
                    ) : (
                      <span className="nav-key">Alt{index + 1}</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>
        <div className="side-footer">
          {/* On a phone the footer is one row: the labels stay for screen readers only. */}
          <button className="theme-toggle" onClick={toggleTheme} title="Changer de thème">
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            <span className="side-label">{theme === 'dark' ? 'Thème clair' : 'Thème sombre'}</span>
          </button>
          <button
            className={`account${tab === 'profile' ? ' active' : ''}`}
            onClick={() => navigate('profile')}
            title="Mon profil"
          >
            <div className="avatar">{user.username.slice(0, 1).toUpperCase()}</div>
            <div>
              <strong>{user.username}</strong>
              <span>
                {roleLabel(user.role)} · {user.sees_every_department ? 'tous services' : user.department_label}
              </span>
            </div>
            <Icon name="pencil" className="account-go" />
          </button>
          <button className="logout shortcuts" onClick={() => setShowShortcuts(true)}>
            <Icon name="spark" /> Raccourcis clavier <span className="nav-key">?</span>
          </button>
          <button className="logout" onClick={logout} title="Déconnexion">
            <Icon name="logout" /> <span className="side-label">Déconnexion</span>
          </button>
        </div>
      </aside>
      <section className="main-content" id="contenu" tabIndex={-1}>
        {tab !== 'chat' && (
          <TopBar
            trail={trail}
            system={system}
            searchRef={globalSearch}
            showSearch={tab !== 'documents'}
            onSearch={(text) => navigate('documents', null, { q: text })}
          />
        )}
        {loadError && <div className="notice">{loadError}</div>}
        {tab === 'overview' && (
          <Overview user={user} system={system} library={library} onNavigate={navigate}
                    onOpenDocument={setOpenDocument} />
        )}
        {tab === 'chat' && (
          <ChatView
            user={user}
            library={library}
            system={system}
            conversations={conversations}
            activeConversation={activeConversation}
            messages={messages}
            streaming={streaming}
            draft={draft}
            onDraftUsed={clearDraft}
            onNewConversation={newConversation}
            onSelectConversation={selectConversation}
            onRenameConversation={renameConversation}
            onDeleteConversation={deleteConversation}
            onSend={sendMessage}
            onOpenDocument={setOpenDocument}
            onToast={pushToast}
          />
        )}
        {tab === 'documents' && (
          <DocumentsView
            user={user}
            query={route.query}
            onQuery={setQuery}
            onToast={pushToast}
            onAsk={askAbout}
            onLibraryChanged={refreshLibrary}
          />
        )}
        {tab === 'users' && <Users user={user} onToast={pushToast} />}
        {tab === 'profile' && <Profile user={user} onToast={pushToast} onNavigate={navigate} />}
        {tab === 'admin' && (
          <Administration
            user={user}
            section={route.section ?? 'supervision'}
            onSection={(section) => navigate('admin', section)}
            onNavigate={navigate}
            onToast={pushToast}
          />
        )}
      </section>
      {tab !== 'documents' && (
        <StandaloneDrawer user={user} documentId={openDocument} onClose={closeDocument}
                          onChanged={refreshLibrary} onAsk={askAbout} onToast={pushToast} />
      )}
      <ShortcutHelp open={showShortcuts} onClose={() => setShowShortcuts(false)} />
      <ToastStack toasts={toasts} />
    </main>
  )
}

export default App
