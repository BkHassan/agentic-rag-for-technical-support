# Session Tokens and SSO Issues

## Symptoms
Users authenticate once but lose access quickly, or SSO login succeeds at the IdP then fails when returning to the application.

## Common causes
- Short-lived access tokens without working refresh tokens
- SameSite / Secure cookie attributes blocking session cookies
- Cross-domain cookie configuration mistakes
- Audience or issuer claims not matching the API expectations
- Load balancers sticky-session requirements with in-memory sessions

## Troubleshooting steps
1. Inspect Set-Cookie headers and browser storage after login.
2. Validate JWT claims (`iss`, `aud`, `exp`) against API configuration.
3. Confirm HTTPS and cookie Secure/SameSite settings for the domain layout.
4. Prefer shared session stores over sticky in-memory sessions.
5. Reproduce with SSO disabled to isolate IdP vs application session bugs.

## Related notes
SSO callback success does not guarantee the application successfully created a local session.
