# HTTP 502 Bad Gateway

## Symptoms
Clients see **502 Bad Gateway**. The edge proxy could talk to something, but the upstream response was invalid or the connection was reset.

## Common causes
- Upstream process crashed mid-request
- Upstream returned an empty or malformed response
- Protocol mismatch (HTTP vs HTTPS) between proxy and origin
- Keep-alive / idle connection reuse hitting a closed socket
- Container or pod not ready despite being in the load-balancer pool

## Troubleshooting steps
1. Check upstream process status and recent crash logs.
2. Verify proxy upstream URL, scheme, and port.
3. Disable connection reuse briefly to test stale keep-alive issues.
4. Confirm readiness probes before traffic is routed to new instances.
5. Capture proxy error logs that often include the upstream reset reason.

## Related notes
Frequent 502 spikes after deploys usually point to readiness or startup timing problems.
