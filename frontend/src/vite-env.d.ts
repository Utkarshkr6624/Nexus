/// <reference types="vite/client" />

// Values come from the repository-root `.env`, not from one beside this file:
// `envDir` in vite.config.ts is anchored to that directory, because
// `scripts/bootstrap.py` writes a single `.env` there for the whole stack.

interface ImportMetaEnv {
  /**
   * Base URL of the backend API including the version prefix, e.g. `/api/v1`.
   * Relative on purpose: the browser then calls the same origin and the Vite
   * dev server (or the production reverse proxy) forwards it, so CORS never
   * applies. An absolute value here makes every call cross-origin.
   */
  readonly VITE_API_BASE_URL?: string
  /** Absolute base URL of the backend, used to link to the OpenAPI docs. */
  readonly VITE_API_SERVER_URL?: string
  readonly VITE_APP_NAME?: string
  readonly VITE_ENABLE_COMMAND_PALETTE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
