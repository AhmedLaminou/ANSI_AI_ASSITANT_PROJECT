/* Test helpers: a scripted fetch, and an accessibility check.
 *
 * axe runs every WCAG rule it can in jsdom. Colour contrast is the exception: jsdom
 * computes no layout or colours, so that rule is disabled here — contrast was
 * measured on the theme tokens directly instead (every pair above 4.5:1). */

import axe from 'axe-core'
import { vi } from 'vitest'

/** Replaces fetch with a router: `routes` maps "METHOD /path" to a response spec.
 * Every call is recorded, so a test can assert what the interface actually sent. */
export function scriptFetch(routes) {
  const calls = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = new URL(url).pathname + new URL(url).search
    const method = (options.method ?? 'GET').toUpperCase()
    calls.push({ method, path, body: options.body })
    const key = `${method} ${path.split('?')[0]}`
    const spec = routes[key] ?? { status: 404, body: { detail: `route non prévue : ${key}` } }
    const status = spec.status ?? 200
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => (typeof spec.body === 'function' ? spec.body(options) : spec.body ?? {}),
    }
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

/** A page of the library as GET /documents/page returns it. */
export function libraryPage(items, extra = {}) {
  const department = {}
  for (const item of items) department[item.department] = (department[item.department] ?? 0) + 1
  return {
    items,
    total: items.length,
    page: 1,
    size: 10,
    pages: 1,
    first: items.length ? 1 : 0,
    last: items.length,
    readable_total: items.length,
    facets: { department, classification: {}, format: {}, status: {} },
    ...extra,
  }
}

export async function accessibilityViolations(container) {
  const result = await axe.run(container, {
    rules: { 'color-contrast': { enabled: false } },
  })
  return result.violations.map((violation) => `${violation.id}: ${violation.help} (${violation.nodes.length})`)
}
