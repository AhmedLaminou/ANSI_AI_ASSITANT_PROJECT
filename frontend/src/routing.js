/* Addressable sections, without a routing library.
 *
 * The application used to keep its section in React state alone, so every screen
 * lived at "/". Typing /admin/feedback in the address bar showed the home screen,
 * which is what an administrator will naturally try. Nothing here needs a router
 * dependency: the History API and one popstate listener are enough, and an offline
 * deployment is happier with one fewer package to vendor.
 *
 * The query string carries a screen's state when that state is worth sharing: a
 * filtered page of the library — /documents?service=rh&statut=overdue&page=2 — is an
 * address a supervisor can send, and one the supervision screen can link to.
 *
 * nginx already serves index.html for unknown paths (try_files … /index.html), so a
 * deep link works on a fresh page load too. */

import { useCallback, useEffect, useState } from 'react'

export const ROUTES = [
  { tab: 'overview', path: '/' },
  { tab: 'chat', path: '/assistant' },
  { tab: 'documents', path: '/documents' },
  { tab: 'users', path: '/utilisateurs' },
  { tab: 'profile', path: '/profil' },
  { tab: 'admin', path: '/administration', section: 'supervision' },
  { tab: 'admin', path: '/administration/journal', section: 'journal' },
  { tab: 'admin', path: '/administration/retours', section: 'retours' },
  { tab: 'admin', path: '/administration/lacunes', section: 'lacunes' },
  { tab: 'admin', path: '/administration/reponses-validees', section: 'reponses' },
  { tab: 'admin', path: '/administration/contacts', section: 'contacts' },
]

// English paths an administrator is likely to guess, and the ones the API itself
// uses. They redirect rather than duplicating a route.
const ALIASES = {
  '/admin': '/administration',
  '/admin/overview': '/administration',
  '/admin/audit': '/administration/journal',
  '/admin/feedback': '/administration/retours',
  '/admin/gaps': '/administration/lacunes',
  '/admin/validated-answers': '/administration/reponses-validees',
  '/admin/contacts': '/administration/contacts',
  '/admin/users': '/utilisateurs',
  '/chat': '/assistant',
  '/users': '/utilisateurs',
  '/profile': '/profil',
  '/compte': '/profil',
  '/bibliotheque': '/documents',
}

function normalise(pathname) {
  const trimmed = pathname.replace(/\/+$/, '') || '/'
  return ALIASES[trimmed] ?? trimmed
}

/** The query string as a plain object, without the empty values. */
export function parseQuery(search = '') {
  const query = {}
  for (const [key, value] of new URLSearchParams(search)) {
    if (value !== '') query[key] = value
  }
  return query
}

/** A plain object back into a query string, empty values left out. */
export function toSearch(query = {}) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '' && value !== false) params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export function locationToRoute(pathname, search = '') {
  const target = normalise(pathname)
  const route = ROUTES.find((item) => item.path === target) ?? ROUTES[0]
  return { ...route, query: parseQuery(search) }
}

export function routeToPath(tab, section) {
  const match = ROUTES.find((route) => route.tab === tab && (!section || route.section === section))
  return match?.path ?? ROUTES.find((route) => route.tab === tab)?.path ?? '/'
}

export function useRoute() {
  const [route, setRoute] = useState(() => locationToRoute(window.location.pathname, window.location.search))

  useEffect(() => {
    function onPop() {
      setRoute(locationToRoute(window.location.pathname, window.location.search))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // Rewrites an alias to its canonical path on first load, so the address bar
  // shows what a bookmark should contain. The query string is kept.
  useEffect(() => {
    const canonical = routeToPath(route.tab, route.section)
    if (window.location.pathname !== canonical) {
      window.history.replaceState({}, '', canonical + window.location.search)
    }
    // Only on mount: later navigation goes through navigate() below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** A new place: a history entry, so the back button returns here. */
  const navigate = useCallback((tab, section, query) => {
    const target = routeToPath(tab, section) + toSearch(query)
    if (window.location.pathname + window.location.search !== target) window.history.pushState({}, '', target)
    const [path, search = ''] = target.split('?')
    setRoute(locationToRoute(path, search ? `?${search}` : ''))
  }, [])

  /** The same place, refined: typing in a filter must not fill the history. */
  const setQuery = useCallback((query) => {
    const search = toSearch(query)
    window.history.replaceState({}, '', window.location.pathname + search)
    setRoute((current) => ({ ...current, query: parseQuery(search) }))
  }, [])

  return [route, navigate, setQuery]
}
