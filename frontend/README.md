# Support console (frontend)

A React + TypeScript single-page app for the FastAPI agent in the repository root. It sends a
ticket to `POST /tickets/analyze` and shows the outcome the backend returns: a grounded answer
with its documentation sources, clarifying questions, or the fallback notice. It holds no
credentials; Gemini and the document index are used only by the backend.

## Requirements

- Node.js 20.19+ or 22.12+ (required by Vite 8) and npm
- The backend running locally (see the root `README.md` for its setup and document ingestion)

## Run locally

Start the backend from the repository root (Windows PowerShell shown):

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend:

```powershell
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. The backend does not send CORS headers, so the browser calls
`/api/...` on the Vite dev server, which forwards them to `API_PROXY_TARGET` with the `/api`
prefix removed. The system status in the sidebar reflects real `GET /health` checks; it shows
"Offline" when the backend is not running.

## Configuration

All variables are optional. To override them, copy the example file and edit the copy
(`.env.local` is ignored by git):

```powershell
Copy-Item .env.example .env.local
```

| Variable                   | Default                      | Used by                                   |
| -------------------------- | ---------------------------- | ----------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | `/api`                       | Browser: base URL for API requests        |
| `NEXT_PUBLIC_API_DOCS_URL` | `http://127.0.0.1:8000/docs` | Browser: "API docs" link                  |
| `API_PROXY_TARGET`         | `http://127.0.0.1:8000`      | Dev and preview server: where `/api` goes |

Only `NEXT_PUBLIC_*` values are compiled into the browser bundle. Do not put secrets in any
of them; `GOOGLE_API_KEY` belongs in the backend's `.env` only.

## Checks

```powershell
npm test          # Vitest unit and component tests
npm run lint      # oxlint
npx tsc -b        # type-check
npm run build     # type-check and production build into dist/
npm run preview   # serve dist/ on http://localhost:4173 with the same /api proxy
```

## Deploying the build

`dist/` is static. In production, serve it behind a reverse proxy that forwards `/api` to the
FastAPI service, or set `NEXT_PUBLIC_API_BASE_URL` to the API's URL at build time and enable
CORS for the frontend's origin on the backend.
