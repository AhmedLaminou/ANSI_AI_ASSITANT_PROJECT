/* The pieces every screen is built from: pages, headers, figures, empty states,
 * search boxes, side panels, avatars, filter chips.
 *
 * Kept apart from shared.jsx, which holds plumbing (requests, constants): this file
 * is presentation only, and imports nothing but that plumbing. */

import { forwardRef, useCallback, useEffect, useRef, useState } from 'react'

import { Icon, formatDate } from './shared.jsx'

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

function range(from, to) {
  return Array.from({ length: to - from + 1 }, (_, index) => from + index)
}

/** The page numbers to show: always the first and the last, the current one with its
 * neighbours, and an ellipsis for the rest — seven slots, so the control never jumps
 * in width while paging. */
export function pageWindow(page, pages) {
  if (pages <= 7) return range(1, pages)
  if (page <= 4) return [...range(1, 5), '…', pages]
  if (page >= pages - 3) return [1, '…', ...range(pages - 4, pages)]
  return [1, '…', page - 1, page, page + 1, '…', pages]
}

export function Pagination({ page, pages, onPage, label = 'Pagination' }) {
  if (pages <= 1) return null
  return (
    <nav className="pagination" aria-label={label}>
      <button type="button" className="page-step" onClick={() => onPage(page - 1)} disabled={page <= 1}
              aria-label="Page précédente">
        <Icon name="chevron-left" /> <span>Précédente</span>
      </button>
      <ul>
        {pageWindow(page, pages).map((item, index) =>
          item === '…' ? (
            <li key={`gap-${index}`} className="page-gap" aria-hidden="true">…</li>
          ) : (
            <li key={item}>
              <button
                type="button"
                className={item === page ? 'active' : ''}
                aria-current={item === page ? 'page' : undefined}
                aria-label={`Page ${item}`}
                onClick={() => onPage(item)}
              >
                {item}
              </button>
            </li>
          ),
        )}
      </ul>
      <button type="button" className="page-step" onClick={() => onPage(page + 1)} disabled={page >= pages}
              aria-label="Page suivante">
        <span>Suivante</span> <Icon name="chevron-right" />
      </button>
    </nav>
  )
}

export function PageSizeSelect({ value, onChange, options = [10, 25, 50], label = 'Par page' }) {
  return (
    <label className="page-size">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(Number(event.target.value))}>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </label>
  )
}

/** "11–20 sur 134", or nothing when there is nothing. */
export function PageSummary({ first, last, total, noun = 'élément' }) {
  if (!total) return null
  const plural = total > 1 ? `${noun}s` : noun
  return (
    <p className="page-summary">
      <strong>{first}–{last}</strong> sur <strong>{total.toLocaleString('fr-FR')}</strong> {plural}
    </p>
  )
}

/** Paging a list that is already in memory: accounts, gaps, feedback. `resetKey`
 * returns to the first page when a filter changes, and only then — reloading after
 * an edit keeps the reader where they were. */
export function useClientPages(items, initialSize = 10, resetKey = '') {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(initialSize)
  // Adjusted while rendering rather than in an effect: an effect would first draw the
  // filtered list at the old page, then jump back to the first one.
  const [pageKey, setPageKey] = useState(resetKey)
  if (pageKey !== resetKey) {
    setPageKey(resetKey)
    setPage(1)
  }
  const pages = Math.max(1, Math.ceil(items.length / size))
  const current = Math.min(page, pages)
  const slice = items.slice((current - 1) * size, current * size)
  return {
    page: current,
    pages,
    size,
    slice,
    total: items.length,
    first: items.length ? (current - 1) * size + 1 : 0,
    last: (current - 1) * size + slice.length,
    setPage,
    setSize: (value) => {
      setSize(value)
      setPage(1)
    },
  }
}

export function ListFooter({ pager, noun, label }) {
  if (!pager.total) return null
  return (
    <div className="list-footer">
      <PageSummary first={pager.first} last={pager.last} total={pager.total} noun={noun} />
      <Pagination page={pager.page} pages={pager.pages} onPage={pager.setPage} label={label} />
      <PageSizeSelect value={pager.size} onChange={pager.setSize} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Structure
// ---------------------------------------------------------------------------

export function PageHeader({ icon, overline, title, children, actions }) {
  return (
    <header className="page-header">
      {icon && (
        <span className="page-header-icon" aria-hidden="true">
          <Icon name={icon} />
        </span>
      )}
      <div className="page-header-text">
        {overline && <p className="overline">{overline}</p>}
        <h1>{title}</h1>
        {children && <p className="page-header-sub">{children}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  )
}

export function StatCard({ icon, label, value, hint, tone = '', onClick }) {
  const body = (
    <>
      <span className="stat-icon" aria-hidden="true"><Icon name={icon} /></span>
      <span className="stat-text">
        <span className="stat-value">{value}</span>
        <span className="stat-label">{label}</span>
        {hint && <span className="stat-hint">{hint}</span>}
      </span>
      {onClick && <Icon name="arrow-right" className="stat-go" />}
    </>
  )
  return onClick ? (
    <button type="button" className={`stat-card ${tone} clickable`} onClick={onClick}>{body}</button>
  ) : (
    <div className={`stat-card ${tone}`}>{body}</div>
  )
}

export function EmptyState({ icon = 'inbox', title, children, action }) {
  return (
    <div className="empty-panel">
      <span className="empty-panel-icon" aria-hidden="true"><Icon name={icon} /></span>
      <p className="empty-panel-title">{title}</p>
      {children && <p className="empty-panel-text">{children}</p>}
      {action}
    </div>
  )
}

export function SkeletonRows({ rows = 5 }) {
  return (
    <>
      <p className="visually-hidden" role="status">Chargement…</p>
      <div className="skeleton-list" aria-hidden="true">
        {range(1, rows).map((row) => (
          <div key={row} className="skeleton-row">
            <span />
            <span />
            <span />
          </div>
        ))}
      </div>
    </>
  )
}

/** Navigation between the sections of a screen. Each section has its own address,
 * so these are navigation buttons marked with aria-current, not tabs. */
export function SectionNav({ items, active, onChange, label }) {
  return (
    <nav className="section-nav" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={item.id === active ? 'active' : ''}
          aria-current={item.id === active ? 'page' : undefined}
          onClick={() => onChange(item.id)}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
          {item.count ? <span className={`nav-count ${item.tone ?? ''}`}>{item.count}</span> : null}
        </button>
      ))}
    </nav>
  )
}

export function Drawer({ open, onClose, labelledBy, children }) {
  const panel = useRef(null)
  useEffect(() => {
    if (open) panel.current?.focus()
  }, [open])
  if (!open) return null
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      {/* A div, not an aside: an aside is a landmark, and a landmark cannot be a dialog. */}
      <div
        ref={panel}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === 'Escape' && onClose()}
      >
        {children}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

export const SearchInput = forwardRef(function SearchInput(
  { value, onChange, onSubmit, placeholder, label, hint, className = '' },
  ref,
) {
  return (
    <form
      role="search"
      className={`search-box ${className}`}
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit?.()
      }}
    >
      <Icon name="search" />
      <input
        ref={ref}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
      />
      {value && (
        <button type="button" className="search-clear" aria-label="Effacer la recherche" onClick={() => onChange('')}>
          <Icon name="close" />
        </button>
      )}
      {hint && !value && <kbd className="search-hint" aria-hidden="true">{hint}</kbd>}
    </form>
  )
})

export function Chip({ active = false, count, onClick, children, tone = '' }) {
  return (
    <button type="button" className={`chip ${tone} ${active ? 'active' : ''}`} aria-pressed={active} onClick={onClick}>
      {children}
      {count !== undefined && <span className="chip-count">{count}</span>}
    </button>
  )
}

export function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

/** State kept in this browser only: a view mode, favourites. Never something another
 * agent or the server needs — storage can be empty, blocked or cleared at any time. */
export function usePersistentState(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key)
      return stored === null ? initial : JSON.parse(stored)
    } catch {
      return initial
    }
  })
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // Nothing to persist to: the value still holds for this visit.
    }
  }, [key, value])
  return [value, setValue]
}

export function useFavorites(userId) {
  const [ids, setIds] = usePersistentState(`ansi-favoris-${userId}`, [])
  const toggle = useCallback(
    (id) => setIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id])),
    [setIds],
  )
  return { ids, has: (id) => ids.includes(id), toggle }
}

// ---------------------------------------------------------------------------
// Small displays
// ---------------------------------------------------------------------------

const AVATAR_TONES = ['green', 'blue', 'amber', 'violet', 'rose', 'teal']

export function initialsOf(name = '') {
  const parts = String(name).split('@')[0].split(/[\s._-]+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

export function Avatar({ name, size = 'md' }) {
  const tone = AVATAR_TONES[[...String(name)].reduce((sum, character) => sum + character.charCodeAt(0), 0) % AVATAR_TONES.length]
  return (
    <span className={`avatar-chip ${tone} ${size}`} aria-hidden="true">
      {initialsOf(name)}
    </span>
  )
}

/** A proportion drawn next to the number it illustrates — decorative on its own. */
export function Bar({ value, max, tone = '' }) {
  const percent = max ? Math.max(value ? 3 : 0, Math.round((value / max) * 100)) : 0
  return (
    <span className={`bar ${tone}`} aria-hidden="true">
      <span style={{ width: `${percent}%` }} />
    </span>
  )
}

export function ReviewBadge({ status, due }) {
  const label = {
    overdue: 'révision dépassée',
    due_soon: 'révision proche',
    ok: 'à jour',
    none: 'révision non planifiée',
  }[status] ?? status
  return (
    <span className={`review-badge ${status}`}>
      {label}
      {due && ` · ${formatDate(due, false)}`}
    </span>
  )
}

const FORMAT_LABELS = { pdf: 'PDF', docx: 'Word', txt: 'Texte', md: 'Markdown' }

export function formatOf(filename = '') {
  const extension = String(filename).split('.').pop()?.toLowerCase() ?? ''
  return { extension, label: FORMAT_LABELS[extension] ?? extension.toUpperCase() }
}

export function FormatBadge({ filename }) {
  const { extension, label } = formatOf(filename)
  return <span className={`format-badge ${extension}`}>{label}</span>
}

const RELATIVE = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' })

export function timeAgo(value) {
  if (!value) return '—'
  const seconds = (new Date(value).getTime() - Date.now()) / 1000
  if (Number.isNaN(seconds)) return '—'
  const steps = [[60, 'second'], [60, 'minute'], [24, 'hour'], [7, 'day'], [4.35, 'week'], [12, 'month']]
  let amount = seconds
  for (const [size, unit] of steps) {
    if (Math.abs(amount) < size) return RELATIVE.format(Math.round(amount), unit)
    amount /= size
  }
  return RELATIVE.format(Math.round(amount), 'year')
}

/** The form that agrees with a count. French puts 0 and 1 in the singular. */
export function agree(count, singular, plural) {
  return count > 1 ? plural : singular
}

/** « 1 document », « 3 documents » — rather than « 3 document(s) ». */
export function countOf(count, singular, plural = `${singular}s`) {
  return `${count.toLocaleString('fr-FR')} ${agree(count, singular, plural)}`
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

/** A cell a spreadsheet will not execute: a value starting with = + - or @ is a
 * formula to Excel, and these values come from what agents typed. */
function safeCell(cell) {
  let value = cell === null || cell === undefined ? '' : String(cell)
  if (/^[=+\-@\t\r]/.test(value)) value = `'${value}`
  return /[";\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

export function toCsv(rows) {
  return rows.map((row) => row.map(safeCell).join(';')).join('\r\n')
}

/** Semicolons and a byte-order mark: what a French Excel opens without a wizard. */
export function downloadCsv(filename, rows) {
  const blob = new Blob([`﻿${toCsv(rows)}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
