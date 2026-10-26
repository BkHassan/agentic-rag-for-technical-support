/**
 * Only NEXT_PUBLIC_* variables reach the browser bundle (see `envPrefix` in
 * vite.config.ts). Never put secrets in them; Gemini credentials stay in the backend's .env.
 */
interface ImportMetaEnv {
  /** Base URL for API requests. Defaults to "/api", which the Vite dev server proxies to FastAPI. */
  readonly NEXT_PUBLIC_API_BASE_URL?: string
  /** Link target for the API documentation (FastAPI Swagger UI). */
  readonly NEXT_PUBLIC_API_DOCS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
