# Database Timeouts and Lock Contention

## Symptoms
Requests hang or fail with query timeouts. Database CPU may look moderate while wait events show lock waits.

## Common causes
- Long-running transactions holding row or table locks
- Missing indexes causing full scans under concurrency
- Deadlocks between competing transactions
- Statement or idle-in-transaction timeouts set too low for batch jobs
- Blocking migrations running during peak traffic

## Troubleshooting steps
1. Identify blocking sessions and the statements they hold.
2. Kill or roll back stuck transactions when safe.
3. Add or verify indexes for hot query paths.
4. Split large transactions into smaller units of work.
5. Align application statement timeouts with expected query latency.

## Related notes
Timeouts at the application layer often appear as HTTP 504s when a proxy waits on a blocked request.
