# Network Connectivity Basics

## Symptoms
Users report intermittent or total loss of access to internal or external services. Ping may fail, or only some destinations are unreachable.

## Common causes
- Local NIC disabled, wrong VLAN, or cable/Wi-Fi association issues
- Default gateway missing or incorrect
- Upstream link or ISP outage
- Firewall rules or NAC policies blocking traffic
- MTU mismatches causing large packets to drop

## Troubleshooting steps
1. Verify link status and IP assignment (`ipconfig` / `ip addr`).
2. Ping the default gateway, then a known external IP (for example 1.1.1.1).
3. If IP works but names fail, treat it as a DNS issue rather than routing.
4. Traceroute to locate where packets stop.
5. Review recent network change windows (firewall, VPN, routing updates).

## Related notes
Separate Layer 2/3 connectivity problems from application-layer failures early to avoid chasing the wrong system.
