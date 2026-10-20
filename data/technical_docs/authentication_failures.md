# Authentication Failures

## Symptoms
Users cannot sign in. Errors may include invalid credentials, account locked, token rejected, or redirect loops between the app and identity provider.

## Common causes
- Wrong username/password or keyboard layout issues
- Account disabled, expired, or locked after failed attempts
- Clock skew breaking token validation (JWT `nbf`/`exp`)
- Misconfigured callback URL / redirect URI with the IdP
- Cookie or session storage blocked by the browser
- MFA challenge not completed or authenticator out of sync

## Troubleshooting steps
1. Confirm the account status in the identity provider or directory.
2. Reproduce with a known-good test account in the same environment.
3. Check IdP and application auth logs for exact error codes.
4. Verify redirect URIs, client IDs, and allowed origins.
5. Validate server time sync (NTP) on auth-related hosts.

## Related notes
Always distinguish “credentials wrong” from “credentials accepted but session cannot be established.”
