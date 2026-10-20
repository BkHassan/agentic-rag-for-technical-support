# HTTP 503 Service Unavailable

## Symptoms
Users intermittently or consistently receive **503 Service Unavailable**. Maintenance pages or retry-after headers may appear.

## Common causes
- Application intentionally marked unavailable during maintenance
- Autoscaling lag under sudden load
- Upstream capacity exhausted (worker/thread/connection pools)
- Circuit breakers opened after repeated failures
- Dependency outage causing the service to refuse new work

## Troubleshooting steps
1. Check whether a maintenance flag or drain mode is enabled.
2. Review CPU, memory, and request queue depth on the service.
3. Inspect circuit-breaker / rate-limiter metrics.
4. Confirm dependent services (cache, DB, auth) are healthy.
5. Scale out or shed load if saturation is confirmed.

## Related notes
Unlike 504, a 503 often means the service knowingly cannot accept the request right now.
