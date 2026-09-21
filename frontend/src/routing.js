/* Addressable sections, without a routing library.
 *
 * The application used to keep its section in React state alone, so every screen
 * lived at "/". Typing /admin/feedback in the address bar showed the home screen,
 * which is what an administrator will naturally try. Nothing here needs a router
 * dependency: the History API and one popstate listener are enough, and an offline
 * deployment is happier with one fewer package to vendor.
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
]

// English paths an administrator is likely to guess, and the ones the API itself
// uses. They redirect rather than duplicating a route.
const ALIASES = {
  '/admin': '/administration',
  '/admin/overview': '/administration',
  '/admin/audit': '/administration/journal',
  '/admin/feedback': '/administration/retours',
  '/admin/users': '/utilisateurs',
  '/chat': '/assistant',
  '/users': '/utilisateurs',
  '/profile': '/profil',
  '/compte': '/profil',
}

function normalise(pathname) {
  const trimmed = pathname.replace(/\/+$/, '') || '/'
  return ALIASES[trimmed] ?? trimmed
}

export function locationToRoute(pathname) {
  const target = normalise(pathname)
  return ROUTES.find((route) => route.path === target) ?? ROUTES[0]
}

export function routeToPath(tab, section) {
  const match = ROUTES.find((route) => route.tab === tab && (!section || route.section === section))
  return match?.path ?? ROUTES.find((route) => route.tab === tab)?.path ?? '/'
}

export function useRoute() {
  const [route, setRoute] = useState(() => locationToRoute(window.location.pathname))

  useEffect(() => {
    function onPop() {
      setRoute(locationToRoute(window.location.pathname))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // Rewrites an alias to its canonical path on first load, so the address bar
  // shows what a bookmark should contain.
  useEffect(() => {
    const canonical = routeToPath(route.tab, route.section)
    if (window.location.pathname !== canonical) {
      window.history.replaceState({}, '', canonical)
    }
    // Only on mount: later navigation goes through navigate() below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const navigate = useCallback((tab, section) => {
    const path = routeToPath(tab, section)
    if (window.location.pathname !== path) window.history.pushState({}, '', path)
    setRoute(locationToRoute(path))
  }, [])

  return [route, navigate]
}
