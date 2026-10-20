# Database Connection Failures

## Symptoms
The application cannot connect to the database. Logs may show connection refused, authentication failed, timeout, or “too many connections.”

## Common causes
- Wrong host, port, database name, or credentials in configuration
- Database server stopped or restarting
- Network/firewall blocking the DB port (for example 5432 or 3306)
- Connection pool exhausted under load
- TLS/SSL mode mismatch between client and server
- Max connections limit reached on the database

## Troubleshooting steps
1. Verify connection string values against the current environment secrets.
2. Test connectivity from the app host with a native client (`psql`, `mysql`, etc.).
3. Confirm the database process is running and listening on the expected interface.
4. Check pool size vs active connections and look for connection leaks.
5. Review DB server logs for auth failures, TLS errors, and connection limits.

## Related notes
Application “cannot connect to the database” tickets are often environment or network misconfiguration rather than query bugs.
