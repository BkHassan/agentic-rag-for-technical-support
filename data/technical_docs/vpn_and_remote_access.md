# VPN and Remote Access

## Symptoms
Remote users cannot reach internal applications. VPN connects but routes or DNS inside the tunnel do not work.

## Common causes
- Split-tunnel routes missing the application subnet
- VPN DNS suffix/search list not applied
- MFA or certificate authentication failing intermittently
- Client firewall blocking the VPN adapter
- Overlapping home-LAN subnets with corporate ranges

## Troubleshooting steps
1. Confirm the VPN session is fully established and assigned an internal IP.
2. Check pushed routes and attempt to ping an internal gateway.
3. Test name resolution for internal hostnames while connected.
4. Look for overlapping RFC1918 ranges between home and corporate networks.
5. Collect VPN client logs covering the authentication and route-install phases.

## Related notes
“VPN connected” is not enough—validate routes and DNS separately for remote-access tickets.
