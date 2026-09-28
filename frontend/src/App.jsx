import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

import Administration, { ReviewBadge } from './Administration.jsx'
import { ValidatedAnswerEditor } from './Knowledge.jsx'
import { ForcedPasswordChange, Login } from './Login.jsx'
import Profile from './Profile.jsx'
import Users from './Users.jsx'
import { useRoute } from './routing.js'
import {
  CLASSIFICATIONS,
  DOCUMENT_DEPARTMENTS,
  Icon,
  ROLES,
  request,
  streamChat,
} from './shared.jsx'

const TABS = [
  { id: 'overview', label: "Vue d'ensemble", icon: 'grid' },
  { id: 'chat', label: 'Assistant', icon: 'chat' },
  { id: 'documents', label: 'Documents', icon: 'folder' },
  { id: 'users', label: 'Utilisateurs', icon: 'users', admin: true },
  { id: 'admin', label: 'Administration', icon: 'shield', admin: true },
]

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

/** Keyboard navigation. Alt+1..4 switch section, Ctrl/Cmd+K focuses the question
 * field, "?" opens the shortcut list. Alt is used rather than Ctrl for sections
 * so browser tab-switching keeps working. */
function useShortcuts({ onSection, onFocusComposer, onToggleHelp, enabled }) {
  useEffect(() => {
    if (!enabled) return undefined
    function handle(event) {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        onFocusComposer()
        return
      }
      if (event.altKey && ['1', '2', '3', '4'].includes(event.key)) {
        event.preventDefault()
        onSection(Number(event.key) - 1)
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
  }, [enabled, onSection, onFocusComposer, onToggleHelp])
}

function ShortcutHelp({ open, onClose }) {
  if (!open) return null
  const rows = [
    ['Alt + 1 … 4', 'Changer de section'],
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
// Screens
// ---------------------------------------------------------------------------

function Overview({ documents, system, user, onNavigate }) {
  const ready = system?.chat_model_ready && system?.embedding_model_ready
  return (
    <section className="workspace overview">
      <div className="page-intro">
        <div>
          <p className="overline">ESPACE DE TRAVAIL</p>
          <h1>Bonjour, {user.username}.</h1>
          <p>Interrogez les informations documentées, avec une réponse traçable et limitée à vos autorisations.</p>
        </div>
        <div className={`readiness ${ready ? 'ready' : 'warning'}`}>
          <Icon name={ready ? 'check' : 'spark'} />
          <div>
            <strong>{ready ? 'Services locaux disponibles' : 'Vérification requise'}</strong>
            <small>{ready ? `${system.chat_model} + ${system.embedding_model}` : 'Ollama ou un modèle est indisponible'}</small>
          </div>
        </div>
      </div>
      <div className="metric-grid">
        <article>
          <span className="metric-icon"><Icon name="folder" /></span>
          <p>Documents accessibles</p>
          <strong>{documents.length}</strong>
          <button onClick={() => onNavigate('documents')}>Consulter la base →</button>
        </article>
        <article>
          <span className="metric-icon"><Icon name="doc-text" /></span>
          <p>Réponses avec sources</p>
          <strong>RAG</strong>
          <small>Documents + pages citées</small>
        </article>
        <article>
          <span className="metric-icon"><Icon name="shield" /></span>
          <p>Exécution IA</p>
          <strong>Locale</strong>
          <small>Aucune API IA externe</small>
        </article>
      </div>
      <div className="two-column">
        <article className="info-card">
          <p className="overline">COMMENT UTILISER L'ASSISTANT</p>
          <h2>Une réponse utile est une réponse vérifiable.</h2>
          <ol>
            <li>Importez un document autorisé depuis la base documentaire.</li>
            <li>Attribuez les rôles qui peuvent le consulter.</li>
            <li>Posez une question précise dans l'Assistant.</li>
            <li>Vérifiez les documents et pages affichés sous la réponse.</li>
          </ol>
          <button className="primary" onClick={() => onNavigate('chat')}>
            Ouvrir l'assistant →
          </button>
        </article>
        <article className="info-card tinted">
          <p className="overline">GARANTIES DE L'ASSISTANT</p>
          <ul className="check-list">
            <li>
              <Icon name="check" />
              Une source indisponible doit produire un refus, pas une invention.
            </li>
            <li>
              <Icon name="check" />
              Rôle et service filtrent les documents avant la recherche, dans le code.
            </li>
            <li>
              <Icon name="check" />
              Les PDF scannés sont reconnus localement, sans service en ligne.
            </li>
            <li>
              <Icon name="check" />
              Toute réponse administrative importante reste validée par un agent.
            </li>
          </ul>
        </article>
      </div>
    </section>
  )
}

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

function ChatView({
  user,
  documents,
  system,
  conversations,
  activeConversation,
  messages,
  streaming,
  onNewConversation,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  onSend,
  onToast,
}) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState('assistant')
  const [results, setResults] = useState(null)
  const [rated, setRated] = useState({})
  const [publishing, setPublishing] = useState(null)
  const ready = system?.chat_model_ready && system?.embedding_model_ready

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
      <aside className="conversation-sidebar">
        <button className="new-conversation" onClick={onNewConversation}>
          <Icon name="plus" /> Nouvelle conversation
        </button>
        <p className="conversation-label">VOS CONVERSATIONS</p>
        <div className="conversation-list">
          {conversations.length === 0 ? (
            <p className="empty-list">Vos échanges documentaires apparaîtront ici.</p>
          ) : (
            conversations.map((conversation) => (
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
        {documents.length === 0 ? (
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
                    {documents.length} document{documents.length > 1 ? 's' : ''} interrogeable
                    {documents.length > 1 ? 's' : ''} par votre compte
                  </p>
                  <div className="corpus-items">
                    {documents.slice(0, 6).map((document) => (
                      <span className="corpus-item" key={document.id}>
                        <Icon name="doc-text" />
                        {document.title}
                      </span>
                    ))}
                    {documents.length > 6 && <span className="corpus-item more">+ {documents.length - 6} autres</span>}
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
                        <span className={`source-pill ${source.is_expired ? 'expired' : ''}`} key={source.id}>
                          {source.id} · {source.title} · p. {source.page}
                          {source.is_expired && ' · PÉRIMÉ'}
                        </span>
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
                      <span className="source-pill" key={source.id}>
                        {source.id} · {source.title} · p. {source.page}
                      </span>
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
                <article className="search-hit" key={`${item.document_id}-${item.page}-${index}`}>
                  <div className="search-hit-head">
                    <strong>{item.title}</strong>
                    <span className="search-score">p. {item.page} · {Math.round(item.score * 100)}%</span>
                  </div>
                  <p>{item.excerpt}…</p>
                </article>
              ))
            )}
          </div>
        )}
        <form className="composer" onSubmit={submit}>
          <div className="mode-switch" role="group" aria-label="Mode de recherche">
            <button
              type="button"
              className={mode === 'assistant' ? 'active' : ''}
              onClick={() => setMode('assistant')}
            >
              <Icon name="chat" /> Assistant
            </button>
            <button type="button" className={mode === 'search' ? 'active' : ''} onClick={() => setMode('search')}>
              <Icon name="search" /> Recherche rapide
            </button>
          </div>
          <div className="composer-input">
            <textarea
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
              placeholder={documents.length ? 'Posez une question précise sur les documents autorisés…' : 'Aucun document accessible…'}
              disabled={!documents.length || busy}
            />
            <span className="char-count">{message.length}/4000</span>
          </div>
          {error && <p className="error">{error}</p>}
          <div className="composer-footer">
            <span>
              {mode === 'search'
                ? 'Recherche seule : retrouve les passages, sans rédiger de réponse — quelques secondes.'
                : `${documents.length} document${documents.length > 1 ? 's' : ''} accessible${documents.length > 1 ? 's' : ''} · Entrée pour envoyer, Maj+Entrée pour un saut de ligne`}
            </span>
            <button className="primary" disabled={!documents.length || busy}>
              <Icon name={mode === 'search' ? 'search' : 'send'} />{' '}
              {busy ? (mode === 'search' ? 'Recherche…' : 'Analyse locale…') : mode === 'search' ? 'Rechercher' : 'Envoyer'}
            </button>
          </div>
        </form>
      </main>
    </section>
  )
}

function DocumentPreview({ preview, onClose }) {
  if (!preview) return null
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="preview-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="preview-title"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === 'Escape' && onClose()}
      >
        <header>
          <div>
            <p className="overline">APERÇU AUTORISÉ</p>
            <h2 id="preview-title">{preview.document.title}</h2>
            <p>{preview.document.filename}</p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Fermer l'aperçu" autoFocus>
            <Icon name="close" />
          </button>
        </header>
        <div className="preview-content">
          {preview.chunks.map((chunk, index) => (
            <article key={`${chunk.page}-${index}`}>
              <span>Page {chunk.page}</span>
              <p>{chunk.content}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}

/** Le périmètre d'un document se révise : une note classée « interne » ne concerne
 * finalement qu'un service, ou un service est réorganisé. Avant, la seule façon de
 * le changer était de supprimer et réimporter — ce qui perd l'historique des
 * versions et coûte une réindexation complète. */
function ScopeEditor({ document, onClose, onSaved, onToast }) {
  const [owners, setOwners] = useState([])
  const [ownerId, setOwnerId] = useState(document.owner_id ?? '')
  const [reviewDue, setReviewDue] = useState(document.review_due ? document.review_due.slice(0, 10) : '')

  useEffect(() => {
    // A reader cannot answer for a document: the owner must be able to replace it.
    request('/admin/users')
      .then((accounts) => setOwners(accounts.filter(
        (account) => account.is_active && ['admin', 'document_manager'].includes(account.role),
      )))
      .catch(() => setOwners([]))
  }, [])
  const [classification, setClassification] = useState(document.classification)
  const [department, setDepartment] = useState(document.department)
  const [roles, setRoles] = useState(document.allowed_roles)
  const [title, setTitle] = useState(document.title)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function toggleRole(role) {
    setRoles((current) =>
      current.includes(role) ? current.filter((item) => item !== role) : [...current, role],
    )
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
          <span className="field-hint">
            Indicative seulement : ce sont les rôles ci-dessous qui filtrent réellement.
          </span>
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
              <input
                type="checkbox"
                checked={roles.includes(role)}
                disabled={role === 'admin'}
                onChange={() => toggleRole(role)}
              />
              {role}
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

export function DocumentsView({ user, documents, onRefresh, onToast }) {
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
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [preview, setPreview] = useState(null)
  const [editing, setEditing] = useState(null)
  const [showSuperseded, setShowSuperseded] = useState(false)
  const [departmentFilter, setDepartmentFilter] = useState('all')
  const [withHistory, setWithHistory] = useState(null)
  const canManage = user.role === 'admin' || user.role === 'document_manager'

  useEffect(() => {
    if (!showSuperseded) return
    request('/documents?include_superseded=true')
      .then(setWithHistory)
      .catch((requestError) => onToast(requestError.message, 'error'))
  }, [showSuperseded, documents, onToast])

  const listed = showSuperseded ? withHistory ?? documents : documents
  const filteredDocuments = useMemo(
    () =>
      listed.filter(
        (document) =>
          (filter === 'all' || document.classification === filter) &&
          (departmentFilter === 'all' || document.department === departmentFilter) &&
          `${document.title} ${document.filename}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [listed, filter, departmentFilter, query],
  )

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
        onToast(`${report.imported} document(s) importé(s), ${report.failed} échec(s).`,
                report.failed ? 'error' : 'success')
      }
      setFiles([])
      setTitle('')
      setValidUntil('')
      setReviewDue('')
      await onRefresh()
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  async function showPreview(id) {
    try {
      setPreview(await request(`/documents/${id}/preview`))
    } catch (requestError) {
      onToast(requestError.message, 'error')
    }
  }

  async function removeDocument(id, name) {
    if (!window.confirm('Supprimer ce document et son index local ?')) return
    try {
      await request(`/documents/${id}`, { method: 'DELETE' })
      await onRefresh()
      onToast(`« ${name} » supprimé.`, 'success')
    } catch (requestError) {
      onToast(requestError.message, 'error')
    }
  }

  return (
    <section className="workspace">
      <div className="workspace-head">
        <div>
          <p className="overline">BASE DOCUMENTAIRE</p>
          <h1>Documents et droits d'accès</h1>
          <p>Les autorisations sont appliquées avant la recherche sémantique.</p>
        </div>
        <span className="count-badge">
          {documents.length} indexé{documents.length > 1 ? 's' : ''}
        </span>
      </div>
      {canManage && (
        <form className="upload-card" onSubmit={submit}>
          <div className="upload-header">
            <div>
              <h2 className="block-title">Importer un document</h2>
              <p>
                Le texte est conservé localement, découpé puis indexé par <strong>embeddinggemma</strong>.
              </p>
            </div>
            <span>PDF · DOCX · TXT · MD</span>
          </div>
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
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
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
            <label className="file-input">
              Fichier(s)
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md"
                onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
              />
              {files.length === 0 && <span>Choisir un ou plusieurs fichiers</span>}
              {files.length === 1 && <span>{files[0].name}</span>}
              {files.length > 1 && <span>{files.length} fichiers sélectionnés</span>}
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
                {role}
              </label>
            ))}
          </fieldset>
          <p className="field-hint">
            Vous devenez responsable des documents importés : c'est à vous que la révision sera
            demandée. Pour un dossier entier, l'import depuis le serveur (app.import_folder) est plus adapté.
          </p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" disabled={busy}>
            <Icon name="upload" />{' '}
            {busy
              ? 'Indexation locale…'
              : files.length > 1
                ? `Importer ${files.length} documents`
                : 'Importer et indexer'}
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
      )}
      <div className="document-toolbar">
        <div className="search-field">
          <Icon name="search" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Rechercher un titre ou fichier…" />
        </div>
        <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filtrer par classification">
          <option value="all">Toutes classifications</option>
          {CLASSIFICATIONS.map((value) => (
            <option key={value} value={value}>
              {value[0].toUpperCase() + value.slice(1)}
            </option>
          ))}
        </select>
        <select value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value)} aria-label="Filtrer par service">
          <option value="all">Tous les services</option>
          {DOCUMENT_DEPARTMENTS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <label className="toggle-field" title="Les versions remplacées ne sont plus interrogées par l'assistant">
          <input
            type="checkbox"
            checked={showSuperseded}
            onChange={(event) => {
              setShowSuperseded(event.target.checked)
              if (!event.target.checked) setWithHistory(null)
            }}
          />
          Versions remplacées
        </label>
      </div>
      <h2 className="visually-hidden">Liste des documents</h2>
      <div className="document-list">
        {filteredDocuments.length === 0 ? (
          <div className="empty-state compact">
            <h3>{documents.length ? 'Aucun résultat' : 'La base est vide'}</h3>
            <p>
              {documents.length
                ? 'Modifiez votre recherche ou votre filtre.'
                : "Importez les procédures du service pour que ses agents puissent les interroger."}
            </p>
          </div>
        ) : (
          filteredDocuments.map((document) => (
            <article
              className={`document-card ${document.is_current ? '' : 'superseded'} ${document.is_expired ? 'expired' : ''}`}
              key={document.id}
            >
              <div className="document-symbol">
                <Icon name="doc-text" />
              </div>
              <div className="document-meta">
                <h3>
                  {document.title}
                  {document.version > 1 && <span className="version-badge">v{document.version}</span>}
                  {!document.is_current && <span className="version-badge muted">remplacée</span>}
                  {document.is_expired && <span className="version-badge danger">périmée</span>}
                </h3>
                <p>{document.filename}</p>
                <div className="tags">
                  <span className={`tag-department ${document.department}`}>{document.department_label}</span>
                  {canManage && document.review_status !== 'ok' && (
                    <ReviewBadge status={document.review_status} due={document.review_due} />
                  )}
                  {canManage && (
                    <span className={document.owner ? '' : 'tag-department none'}>
                      {document.owner ? `responsable : ${document.owner}` : 'sans responsable'}
                    </span>
                  )}
                  <span className={`tag-classification ${document.classification}`}>{document.classification}</span>
                  {document.allowed_roles.map((role) => (
                    <span key={role}>{role}</span>
                  ))}
                </div>
              </div>
              <button className="text-button" onClick={() => showPreview(document.id)}>
                <Icon name="eye" /> Aperçu
              </button>
              {user.role === 'admin' && (
                <>
                  <button className="text-button" onClick={() => setEditing(document)}>
                    <Icon name="pencil" /> Périmètre
                  </button>
                  <button
                    className="icon-button"
                    title="Supprimer"
                    aria-label={`Supprimer le document « ${document.title} »`}
                    onClick={() => removeDocument(document.id, document.title)}
                  >
                    <Icon name="trash" />
                  </button>
                </>
              )}
            </article>
          ))
        )}
      </div>
      <DocumentPreview preview={preview} onClose={() => setPreview(null)} />
      {editing && (
        <ScopeEditor
          document={editing}
          onClose={() => setEditing(null)}
          onSaved={onRefresh}
          onToast={onToast}
        />
      )}
    </section>
  )
}

function App() {
  const [user, setUser] = useState(null)
  const [documents, setDocuments] = useState([])
  const [system, setSystem] = useState(null)
  const [conversations, setConversations] = useState([])
  const [activeConversation, setActiveConversation] = useState(null)
  const [messages, setMessages] = useState([])
  const [streaming, setStreaming] = useState(null)
  // The section lives in the URL, so /administration/retours is a real address
  // an administrator can bookmark or share.
  const [route, navigate] = useRoute()
  const tab = route.tab
  const setTab = navigate
  const [loadError, setLoadError] = useState('')
  const [theme, toggleTheme] = useTheme()
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [sessionNotice, setSessionNotice] = useState('')
  const { toasts, push: pushToast } = useToasts()

  async function refreshDocuments() {
    setDocuments(await request('/documents'))
  }

  async function initialize(currentUser) {
    setUser(currentUser)
    setSessionNotice('')
    // Every other endpoint answers 403 until the agent chooses their own password,
    // so loading the workspace now would only produce errors.
    if (currentUser.must_change_password) return
    try {
      const [loadedDocuments, loadedSystem, loadedConversations] = await Promise.all([
        request('/documents'),
        request('/system/status'),
        request('/conversations'),
      ])
      setDocuments(loadedDocuments)
      setSystem(loadedSystem)
      setConversations(loadedConversations)
    } catch (requestError) {
      setLoadError(requestError.message)
    }
  }

  async function selectConversation(id) {
    const response = await request(`/conversations/${id}/messages`)
    setActiveConversation(response.conversation)
    setMessages(response.messages)
    setTab('chat')
  }

  async function newConversation() {
    const conversation = await request('/conversations', { method: 'POST' })
    setConversations((current) => [conversation, ...current])
    setActiveConversation(conversation)
    setMessages([])
    setTab('chat')
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

  useEffect(() => {
    request('/auth/me').then(initialize).catch(() => undefined)
  }, [])

  // A session can be closed from elsewhere: a password changed on another machine,
  // an administrator revoking it. The next request answers 401; without this the
  // interface would stay on screen, failing on every click.
  useEffect(() => {
    function onSessionLost(event) {
      setUser(null)
      setDocuments([])
      setConversations([])
      setActiveConversation(null)
      setMessages([])
      setStreaming(null)
      setSessionNotice(event.detail || 'Votre session a été fermée. Reconnectez-vous.')
    }
    window.addEventListener('ansi:session-lost', onSessionLost)
    return () => window.removeEventListener('ansi:session-lost', onSessionLost)
  }, [])

  useShortcuts({
    enabled: Boolean(user),
    onSection: (index) => {
      const available = TABS.filter((item) => !item.admin || user?.role === 'admin')
      if (available[index]) setTab(available[index].id)
    },
    onFocusComposer: () => {
      setTab('chat')
      // The composer belongs to ChatView; querying the DOM avoids threading a ref
      // through three components for a single focus call.
      window.requestAnimationFrame(() => document.querySelector('.composer textarea')?.focus())
    },
    onToggleHelp: (next) => setShowShortcuts((current) => (next === false ? false : !current)),
  })

  if (!user) {
    return (
      <Login onLogin={initialize} theme={theme} onToggleTheme={toggleTheme} initialNotice={sessionNotice} />
    )
  }

  async function logout() {
    await request('/auth/logout', { method: 'POST' })
    setUser(null)
    setDocuments([])
    setConversations([])
    setActiveConversation(null)
    setMessages([])
    setStreaming(null)
    setTab('overview')
  }

  if (user.must_change_password) {
    return <ForcedPasswordChange user={user} onDone={initialize} onLogout={logout} />
  }

  const visibleTabs = TABS.filter((item) => !item.admin || user.role === 'admin')

  return (
    <main className="app-shell">
      <a className="skip-link" href="#contenu">Aller au contenu</a>
      <aside className="main-sidebar" aria-label="Navigation principale">
        <div className="side-brand">
          <div className="brand-mark">A</div>
          <div>
            <strong>ANSI</strong>
            <span>Assistant local</span>
          </div>
        </div>
        <nav>
          {visibleTabs.map((item, index) => (
            <button
              key={item.id}
              className={tab === item.id ? 'active' : ''}
              aria-current={tab === item.id ? 'page' : undefined}
              onClick={() => navigate(item.id)}
              title={`${item.label} (Alt+${index + 1})`}
            >
              <Icon name={item.icon} />
              {item.label}
              <span className="nav-key">Alt{index + 1}</span>
            </button>
          ))}
        </nav>
        <div className="side-footer">
          <button className="theme-toggle" onClick={toggleTheme} title="Changer de thème">
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            {theme === 'dark' ? 'Thème clair' : 'Thème sombre'}
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
                {user.role} · {user.sees_every_department ? 'tous services' : user.department_label}
              </span>
            </div>
            <Icon name="pencil" className="account-go" />
          </button>
          <button className="logout" onClick={() => setShowShortcuts(true)}>
            <Icon name="spark" /> Raccourcis clavier <span className="nav-key">?</span>
          </button>
          <button className="logout" onClick={logout}>
            <Icon name="logout" /> Déconnexion
          </button>
        </div>
      </aside>
      <section className="main-content" id="contenu" tabIndex={-1}>
        {loadError && <div className="notice">{loadError}</div>}
        {tab === 'overview' && <Overview documents={documents} system={system} user={user} onNavigate={setTab} />}
        {tab === 'chat' && (
          <ChatView
            user={user}
            documents={documents}
            system={system}
            conversations={conversations}
            activeConversation={activeConversation}
            messages={messages}
            streaming={streaming}
            onNewConversation={newConversation}
            onSelectConversation={selectConversation}
            onRenameConversation={renameConversation}
            onDeleteConversation={deleteConversation}
            onSend={sendMessage}
            onToast={pushToast}
          />
        )}
        {tab === 'documents' && (
          <DocumentsView user={user} documents={documents} onRefresh={refreshDocuments} onToast={pushToast} />
        )}
        {tab === 'users' && <Users user={user} onToast={pushToast} />}
        {tab === 'profile' && <Profile user={user} onToast={pushToast} />}
        {tab === 'admin' && (
          <Administration
            user={user}
            section={route.section ?? 'supervision'}
            onSection={(section) => navigate('admin', section)}
            onToast={pushToast}
          />
        )}
      </section>
      <ShortcutHelp open={showShortcuts} onClose={() => setShowShortcuts(false)} />
      <ToastStack toasts={toasts} />
    </main>
  )
}

export default App
