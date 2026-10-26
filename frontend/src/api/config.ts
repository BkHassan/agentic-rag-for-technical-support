function withoutTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '')
}

/**
 * The backend does not send CORS headers, so the default "/api" goes to the Vite
 * dev server, which proxies to FastAPI (see vite.config.ts). An absolute URL only
 * works if the backend is served from the same origin or allows this origin.
 */
export const API_BASE_URL = withoutTrailingSlash(import.meta.env.NEXT_PUBLIC_API_BASE_URL || '/api')

/**
 * Swagger UI loads /openapi.json from the backend's own origin, so it is linked
 * directly rather than through the dev proxy.
 */
export const API_DOCS_URL = import.meta.env.NEXT_PUBLIC_API_DOCS_URL || 'http://127.0.0.1:8000/docs'

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}
