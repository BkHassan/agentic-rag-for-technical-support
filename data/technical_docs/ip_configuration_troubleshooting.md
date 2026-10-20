# IP Configuration Troubleshooting

## Symptoms
A host has no connectivity or can only reach some networks. Address may be missing, duplicate, or in the wrong subnet.

## Common causes
- Static IP typed incorrectly (address, mask, or gateway)
- DHCP scope exhausted or DHCP server unreachable
- Duplicate IP conflict on the LAN
- Wrong subnet mask placing the host off-network
- Multiple NICs with conflicting default routes
- APIPA/link-local address (169.254.x.x) indicating DHCP failure

## Troubleshooting steps
1. Read current addressing with `ipconfig /all` or `ip addr` and `ip route`.
2. Confirm subnet, gateway, and DNS values against the network plan.
3. Detect duplicates (gratuitous ARP conflicts or OS duplicate-address warnings).
4. Renew DHCP (`ipconfig /renew` or `dhclient`) and check lease details.
5. Temporarily set a known-good static configuration to isolate DHCP vs cabling.
6. Ensure only one default route exists unless policy routing is intentional.

## Related notes
An APIPA address is a strong signal that Layer 2 may be up while DHCP/IP configuration failed.
