# DNS Resolution Failures

## Symptoms
Applications or browsers fail to reach a host by name. Errors may include `NXDOMAIN`, `SERVFAIL`, or timeouts while resolving hostnames. IP-based access sometimes still works.

## Common causes
- Incorrect DNS server configured on the client or DHCP options
- Stale DNS cache after a record change (TTL not yet expired)
- Missing or outdated A/AAAA records in the authoritative zone
- Split-horizon DNS returning different answers inside vs outside the network
- Firewall or security appliance blocking UDP/TCP port 53

## Troubleshooting steps
1. Confirm the hostname with `nslookup` or `dig` against the configured resolver.
2. Compare results from a public resolver (for example 8.8.8.8) and the corporate resolver.
3. Flush the local DNS cache after intentional record updates.
4. Verify the zone file or DNS provider UI shows the expected records and TTL.
5. Check whether IPv6 (AAAA) answers are broken while IPv4 (A) still works.

## Related notes
DNS problems often look like “the network is down” even when connectivity and routing are healthy.
