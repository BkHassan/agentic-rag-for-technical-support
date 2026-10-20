# Password Reset and Account Access

## Symptoms
A user cannot authenticate after a password reset. They may still see “invalid password,” get locked out, or successfully reset but fail on the next login.

## Common causes
- User typing the old password from a password manager that has not refreshed
- Reset email or SMS delayed; user reused an expired reset link
- Directory replication lag: password updated on one node, login hits another
- Cached credentials on the client (OS keychain, browser autofill, SSO session)
- Password policy rejecting the new password silently in some clients
- Account still locked or MFA still required with an outdated factor

## Troubleshooting steps
1. Confirm the reset completed successfully in the identity system audit log.
2. Ask the user to clear saved passwords and try an incognito/private session.
3. Verify replication health if multiple directory replicas exist.
4. Unlock the account and clear temporary lockouts after verifying identity.
5. Re-enroll MFA if the authenticator was tied to the previous credential state.
6. Have the user wait one replication interval, then retry login against the primary region.

## Related notes
Most “cannot authenticate after password reset” cases are client cache, replication lag, or an unused/expired reset token—not application bugs.
