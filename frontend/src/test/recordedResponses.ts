// Real AgentResponse bodies recorded from the backend by the evaluation runner
// (evals/results/before_specificity_check.json, serialized with model_dump(mode="json"),
// the same serialization FastAPI uses for the response). Used only as test fixtures.

export const RECORDED_GROUNDED_ANSWER = {
  "ticket_id": "E-REL-001",
  "category": "database",
  "outcome": "grounded_answer",
  "clarifying_questions": [],
  "recommendations": [
    "Verify connection string values against the current environment secrets.",
    "Test connectivity from the app host with a native client (psql, mysql, etc.).",
    "Confirm the database process is running and listening on the expected interface.",
    "Check pool size vs active connections and look for connection leaks.",
    "Review DB server logs for auth failures, TLS errors, and connection limits.",
    "Identify blocking sessions and the statements they hold.",
    "Kill or roll back stuck transactions when safe.",
    "Add or verify indexes for hot query paths.",
    "Split large transactions into smaller units of work.",
    "Align application statement timeouts with expected query latency."
  ],
  "sources": [
    {
      "source": "database_connection_failures.md",
      "doc_id": "database_connection_failures",
      "chunk_index": 0,
      "category": "database",
      "distance": 0.2733328342437744,
      "excerpt": "# Database Connection Failures  ## Symptoms The application cannot connect to the database. Logs may show connection refused, authentication failed, timeout, or “too many connections.”  ## Common causes - Wrong host, port, database name,..."
    },
    {
      "source": "database_timeouts_and_locks.md",
      "doc_id": "database_timeouts_and_locks",
      "chunk_index": 0,
      "category": "database",
      "distance": 0.29380643367767334,
      "excerpt": "# Database Timeouts and Lock Contention  ## Symptoms Requests hang or fail with query timeouts. Database CPU may look moderate while wait events show lock waits.  ## Common causes - Long-running transactions holding row or table locks - ..."
    }
  ],
  "sufficient_evidence": true,
  "fallback_triggered": false,
  "summary": "Documented troubleshooting guidance specifies verifying connection strings, testing native connectivity, checking process status, reviewing connection pool size and leaks, and inspecting DB logs for limits or TLS issues. Additional steps from lock contention documentation include identifying blocking sessions, rolling back stuck transactions, verifying indexes, splitting large transactions, and aligning statement timeouts. Uncertainty remains regarding the exact root cause, as timeouts can stem from network misconfigurations, connection pool exhaustion, or lock contention."
}

export const RECORDED_CLARIFICATION_NEEDED = {
  "ticket_id": "E-REL-004",
  "category": "network",
  "outcome": "clarification_needed",
  "clarifying_questions": [
    "What exactly happens? Please include the exact error message or code, or describe what you see instead of the expected result.",
    "When did it start, and is it affecting one user, a team, or everyone?"
  ],
  "recommendations": [],
  "sources": [],
  "sufficient_evidence": false,
  "fallback_triggered": false,
  "summary": "More detail is needed before documented troubleshooting steps can be recommended. Please answer the questions below."
}

export const RECORDED_FALLBACK = {
  "ticket_id": "E-IRR-001",
  "category": "application",
  "outcome": "fallback",
  "clarifying_questions": [],
  "recommendations": [
    "Insufficient supporting documentation was found to provide reliable troubleshooting recommendations. Please review the ticket manually."
  ],
  "sources": [
    {
      "source": "http_502_bad_gateway.md",
      "doc_id": "http_502_bad_gateway",
      "chunk_index": 0,
      "category": "web",
      "distance": 0.40467309951782227,
      "excerpt": "# HTTP 502 Bad Gateway  ## Symptoms Clients see **502 Bad Gateway**. The edge proxy could talk to something, but the upstream response was invalid or the connection was reset.  ## Common causes - Upstream process crashed mid-request - Up..."
    },
    {
      "source": "http_503_service_unavailable.md",
      "doc_id": "http_503_service_unavailable",
      "chunk_index": 0,
      "category": "web",
      "distance": 0.42326587438583374,
      "excerpt": "# HTTP 503 Service Unavailable  ## Symptoms Users intermittently or consistently receive **503 Service Unavailable**. Maintenance pages or retry-after headers may appear.  ## Common causes - Application intentionally marked unavailable d..."
    },
    {
      "source": "file_upload_failures.md",
      "doc_id": "file_upload_failures",
      "chunk_index": 0,
      "category": "storage",
      "distance": 0.43608421087265015,
      "excerpt": "# File Upload Failures  ## Symptoms Users cannot upload files. The UI may hang, return 413, 415, or a generic “upload failed” message.  ## Common causes - File exceeds application or reverse-proxy max body size limits - Unsupported MIME ..."
    },
    {
      "source": "http_504_gateway_timeout.md",
      "doc_id": "http_504_gateway_timeout",
      "chunk_index": 0,
      "category": "web",
      "distance": 0.4428092837333679,
      "excerpt": "# HTTP 504 Gateway Timeout  ## Symptoms Users receive a **504 Gateway Timeout** when calling an API or loading a web page. Reverse proxies, load balancers, or CDNs typically generate this status.  ## What 504 means A gateway or proxy did..."
    },
    {
      "source": "database_connection_failures.md",
      "doc_id": "database_connection_failures",
      "chunk_index": 0,
      "category": "database",
      "distance": 0.44389986991882324,
      "excerpt": "# Database Connection Failures  ## Symptoms The application cannot connect to the database. Logs may show connection refused, authentication failed, timeout, or “too many connections.”  ## Common causes - Wrong host, port, database name,..."
    }
  ],
  "sufficient_evidence": false,
  "fallback_triggered": true,
  "summary": "Insufficient supporting documentation was found to provide reliable troubleshooting recommendations. Please review the ticket manually."
}
