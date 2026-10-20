# HTTP 504 Gateway Timeout

## Symptoms
Users receive a **504 Gateway Timeout** when calling an API or loading a web page. Reverse proxies, load balancers, or CDNs typically generate this status.

## What 504 means
A gateway or proxy did not receive a timely response from an upstream server. The client reached the edge, but the backend did not answer within the configured timeout.

## Common causes
- Upstream application hung, overloaded, or deadlocked
- Backend took longer than the proxy timeout (common with slow DB queries)
- Upstream process crashed or is restarting
- Network path between proxy and origin is degraded
- Incorrect upstream host/port so the proxy waits until timeout

## Troubleshooting steps
1. Confirm whether the origin responds directly when bypassing the proxy.
2. Check application and proxy access logs for matching request IDs and latency.
3. Inspect upstream health checks and recent deployments.
4. Look for slow database queries, lock contention, or thread pool exhaustion.
5. Temporarily increase proxy timeouts only after identifying a legitimate long-running operation.

## Related notes
A 504 is different from a 502 (bad gateway / invalid upstream response) and from a 503 (service unavailable / overloaded or intentionally offline).
