# Retrieval sufficiency calibration

- Generated at: `2026-10-25T14:45:24.971717+00:00`
- Dataset: `evals/calibration_tickets.json` (53 labeled tickets, separate from the 20-ticket evaluation)
- Embedding model: `gemini-embedding-001`; collection chunks: 14
- Cost: unsafe answer = 2, missed answer = 1

## Selected rule

- Family: **distance+contrast**
- `relevance_max_distance` = **0.3687**
- `relevance_min_contrast` = **0.0442** (contrast rank 5)
- On the calibration set: 0 unsafe answer(s), 0 missed answer(s)
- Leave-one-out: 0 unsafe, 0 missed (accuracy 100%)
- Previous rule (distance <= 0.45): 18 unsafe, 0 missed

## Best rule per family

In-sample counts use thresholds fitted on all tickets. Leave-one-out counts refit without each ticket and score it, so they are the fairer comparison between families.

| Family | Rank | Max distance | Min contrast | In-sample unsafe | In-sample missed | LOO unsafe | LOO missed | LOO cost | Margin |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| distance | — | 0.3232 | — | 0 | 4 | 1 | 5 | 7 | 0.01 |
| contrast | 2 | — | 0.0289 | 0 | 3 | 1 | 4 | 6 | 0.01 |
| distance+contrast | 2 | 0.3687 | 0.0275 | 0 | 3 | 1 | 3 | 5 | 0.03 |
| contrast | 3 | — | 0.0520 | 0 | 2 | 1 | 2 | 4 | 0.19 |
| distance+contrast | 3 | 0.3687 | 0.0358 | 0 | 0 | 1 | 0 | 2 | 0.04 |
| contrast | 4 | — | 0.0458 | 0 | 1 | 1 | 2 | 4 | 0.02 |
| distance+contrast | 4 | 0.3687 | 0.0395 | 0 | 0 | 1 | 0 | 2 | 0.03 |
| contrast | 5 | — | 0.0490 | 0 | 0 | 1 | 0 | 2 | 0.04 |
| distance+contrast | 5 | 0.3687 | 0.0442 | 0 | 0 | 0 | 0 | 0 | 0.07 |

## Selected rule by group

| Group | Label | Tickets | Accepted | Fallback |
|---|---|---:|---:|---:|
| answerable | answer | 29 | 29 | 0 |
| out_of_corpus | fallback | 14 | 0 | 14 |
| vague | fallback | 10 | 0 | 10 |

Top-1 document matched the labeled document for 26/29 answerable tickets.

## Limits

- 53 hand-labeled tickets over a 14-chunk corpus. Thresholds carry real sampling error; leave-one-out is the better estimate than the in-sample counts.
- The thresholds depend on the corpus, chunking, and embedding model. Re-run after changing any of them.
- Contrast compares the best document with the background. If several documents legitimately cover one topic, contrast drops for good tickets and the rank may need re-fitting.

## Examples

| Ticket | Label | Best distance | Contrast@2 | Contrast@3 | Contrast@4 | Contrast@5 | Decision | Top document |
|---|---|---:|---:|---:|---:|---:|---|---|
| C-ANS-01 | answer | 0.2965 | 0.0633 | 0.0781 | 0.0916 | 0.1456 | answer | dns_resolution_failures.md |
| C-ANS-02 | answer | 0.3462 | 0.0013 | 0.0404 | 0.0413 | 0.0532 | answer | dns_resolution_failures.md |
| C-ANS-03 | answer | 0.2701 | 0.0615 | 0.0829 | 0.1008 | 0.1404 | answer | http_504_gateway_timeout.md |
| C-ANS-04 | answer | 0.2979 | 0.0605 | 0.0709 | 0.1145 | 0.1225 | answer | http_504_gateway_timeout.md |
| C-ANS-05 | answer | 0.2576 | 0.0661 | 0.0933 | 0.1206 | 0.1418 | answer | http_503_service_unavailable.md |
| C-ANS-06 | answer | 0.3041 | 0.0612 | 0.0706 | 0.1030 | 0.1124 | answer | http_503_service_unavailable.md |
| C-ANS-07 | answer | 0.2797 | 0.0570 | 0.0918 | 0.1074 | 0.1100 | answer | http_502_bad_gateway.md |
| C-ANS-08 | answer | 0.2469 | 0.0564 | 0.0846 | 0.1217 | 0.1516 | answer | http_502_bad_gateway.md |
| C-ANS-09 | answer | 0.2908 | 0.1135 | 0.1158 | 0.1360 | 0.1383 | answer | database_connection_failures.md |
| C-ANS-10 | answer | 0.3113 | 0.0656 | 0.0940 | 0.1047 | 0.1066 | answer | database_connection_failures.md |
| C-ANS-11 | answer | 0.2834 | 0.1086 | 0.1343 | 0.1492 | 0.1562 | answer | database_timeouts_and_locks.md |
| C-ANS-12 | answer | 0.2871 | 0.1038 | 0.1227 | 0.1343 | 0.1403 | answer | database_timeouts_and_locks.md |
| C-ANS-13 | answer | 0.3124 | 0.0305 | 0.0690 | 0.0896 | 0.1010 | answer | session_tokens_and_sso.md |
| C-ANS-14 | answer | 0.3326 | 0.0472 | 0.0865 | 0.1028 | 0.1193 | answer | authentication_failures.md |
| C-ANS-15 | answer | 0.3224 | 0.0105 | 0.1031 | 0.1049 | 0.1069 | answer | password_reset_and_account_access.md |
| C-ANS-16 | answer | 0.3022 | 0.0852 | 0.0977 | 0.1412 | 0.1415 | answer | password_reset_and_account_access.md |
| C-ANS-17 | answer | 0.3005 | 0.0388 | 0.1019 | 0.1189 | 0.1241 | answer | password_reset_and_account_access.md |
| C-ANS-18 | answer | 0.2959 | 0.0469 | 0.0844 | 0.1229 | 0.1318 | answer | session_tokens_and_sso.md |
| C-ANS-19 | answer | 0.3492 | 0.0811 | 0.0959 | 0.0984 | 0.1066 | answer | session_tokens_and_sso.md |
| C-ANS-20 | answer | 0.2749 | 0.1285 | 0.1523 | 0.1672 | 0.1737 | answer | vpn_and_remote_access.md |
| C-ANS-21 | answer | 0.3087 | 0.0689 | 0.0720 | 0.0935 | 0.1447 | answer | vpn_and_remote_access.md |
| C-ANS-22 | answer | 0.2581 | 0.0861 | 0.1313 | 0.1388 | 0.1816 | answer | ip_configuration_troubleshooting.md |
| C-ANS-23 | answer | 0.3168 | 0.0781 | 0.0891 | 0.1035 | 0.1270 | answer | ip_configuration_troubleshooting.md |
| C-ANS-24 | answer | 0.3142 | 0.0100 | 0.0610 | 0.1055 | 0.1236 | answer | ip_configuration_troubleshooting.md |
| C-ANS-25 | answer | 0.3578 | 0.0293 | 0.0376 | 0.0469 | 0.0510 | answer | network_connectivity_basics.md |
| C-ANS-26 | answer | 0.3012 | 0.0842 | 0.1253 | 0.1346 | 0.1491 | answer | file_upload_failures.md |
| C-ANS-27 | answer | 0.2889 | 0.0648 | 0.1119 | 0.1130 | 0.1232 | answer | file_upload_failures.md |
| C-ANS-28 | answer | 0.2320 | 0.1043 | 0.1959 | 0.1969 | 0.2124 | answer | multipart_upload_and_storage.md |
| C-ANS-29 | answer | 0.3098 | 0.0432 | 0.0795 | 0.1126 | 0.1127 | answer | multipart_upload_and_storage.md |
| C-OOC-01 | fallback | 0.4255 | 0.0219 | 0.0339 | 0.0377 | 0.0416 | fallback | http_502_bad_gateway.md |
| C-OOC-02 | fallback | 0.4741 | 0.0026 | 0.0058 | 0.0214 | 0.0265 | fallback | database_connection_failures.md |
| C-OOC-03 | fallback | 0.4630 | 0.0004 | 0.0064 | 0.0071 | 0.0073 | fallback | http_503_service_unavailable.md |
| C-OOC-04 | fallback | 0.4143 | 0.0120 | 0.0236 | 0.0290 | 0.0324 | fallback | http_503_service_unavailable.md |
| C-OOC-05 | fallback | 0.4185 | 0.0285 | 0.0429 | 0.0447 | 0.0469 | fallback | http_502_bad_gateway.md |
| C-OOC-06 | fallback | 0.4797 | 0.0049 | 0.0081 | 0.0243 | 0.0255 | fallback | http_504_gateway_timeout.md |
| C-OOC-07 | fallback | 0.4342 | 0.0142 | 0.0288 | 0.0296 | 0.0383 | fallback | network_connectivity_basics.md |
| C-OOC-08 | fallback | 0.4695 | 0.0015 | 0.0066 | 0.0088 | 0.0114 | fallback | password_reset_and_account_access.md |
| C-OOC-09 | fallback | 0.4474 | 0.0012 | 0.0133 | 0.0134 | 0.0243 | fallback | database_connection_failures.md |
| C-OOC-10 | fallback | 0.4110 | 0.0049 | 0.0095 | 0.0182 | 0.0182 | fallback | authentication_failures.md |
| C-OOC-11 | fallback | 0.4150 | 0.0081 | 0.0164 | 0.0226 | 0.0267 | fallback | database_connection_failures.md |
| C-OOC-12 | fallback | 0.5120 | 0.0046 | 0.0047 | 0.0100 | 0.0173 | fallback | password_reset_and_account_access.md |
| C-OOC-13 | fallback | 0.4703 | 0.0024 | 0.0071 | 0.0126 | 0.0175 | fallback | http_502_bad_gateway.md |
| C-OOC-14 | fallback | 0.4455 | 0.0099 | 0.0113 | 0.0129 | 0.0157 | fallback | http_502_bad_gateway.md |
| C-VAG-01 | fallback | 0.3401 | 0.0212 | 0.0256 | 0.0314 | 0.0321 | fallback | database_timeouts_and_locks.md |
| C-VAG-02 | fallback | 0.3241 | 0.0264 | 0.0326 | 0.0357 | 0.0405 | fallback | http_503_service_unavailable.md |
| C-VAG-03 | fallback | 0.3451 | 0.0054 | 0.0110 | 0.0216 | 0.0221 | fallback | http_502_bad_gateway.md |
| C-VAG-04 | fallback | 0.3796 | 0.0022 | 0.0111 | 0.0115 | 0.0132 | fallback | password_reset_and_account_access.md |
| C-VAG-05 | fallback | 0.3491 | 0.0022 | 0.0192 | 0.0201 | 0.0205 | fallback | network_connectivity_basics.md |
| C-VAG-06 | fallback | 0.3939 | 0.0032 | 0.0122 | 0.0152 | 0.0156 | fallback | http_502_bad_gateway.md |
| C-VAG-07 | fallback | 0.3320 | 0.0082 | 0.0161 | 0.0180 | 0.0330 | fallback | password_reset_and_account_access.md |
| C-VAG-08 | fallback | 0.3310 | 0.0123 | 0.0244 | 0.0273 | 0.0367 | fallback | http_502_bad_gateway.md |
| C-VAG-09 | fallback | 0.4188 | 0.0015 | 0.0043 | 0.0043 | 0.0066 | fallback | database_connection_failures.md |
| C-VAG-10 | fallback | 0.3279 | 0.0114 | 0.0155 | 0.0180 | 0.0199 | fallback | http_502_bad_gateway.md |
