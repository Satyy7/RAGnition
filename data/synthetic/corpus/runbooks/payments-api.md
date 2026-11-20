> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: payments-api

## Overview
- **Service:** `payments-api`  
- **Team:** Payments  
- **Runtime:** Go 1.23  
- **SLO:** 99.95 % successful authorizations; p99 latency < 2 s  
- **Function:** Authorizes and captures card payments via the Paygate PSP (mutual TLS).  
- **Dependencies:**  
  - **Called by:** `checkout-api`  
  - **Datastore:** `payments-db` – PostgreSQL 15 accessed through PgBouncer (transaction pooling).  
  - **External provider:** Paygate PSP (mTLS with client cert + pinned trust bundle).  
- **Deployment:** 6 replicas, each with a DB pool max 20 connections (120 client connections total). PgBouncer `default_pool_size` defines the number of real server connections per DB/user pair.  

## Key metrics and dashboards
| Metric | Description | Typical Threshold | Dashboard |
|--------|-------------|-------------------|-----------|
| `payments_api_success_rate` | % of successful authorizations | < 99.95 % (5 min) → alert | Grafana “Payments API – SLO” |
| `payments_api_latency_p99` | p99 request latency (seconds) | > 2 s (5 min) → alert | Grafana “Payments API – Latency” |
| `pgbouncer_client_conn` | Current client connections to PgBouncer | > 120 → warning | Grafana “PgBouncer – Connections” |
| `pgbouncer_server_conn` | Current server connections (real DB sockets) | Approaching `default_pool_size` → warning | Grafana “PgBouncer – Server Pool” |
| `pgbouncer_client_wait_seconds_total` | Total time clients spend waiting for a server connection | > 5 s (5 min) → alert | Grafana “PgBouncer – Wait Time” |
| `payments_api_tls_errors_total` | TLS handshake failures to Paygate | > 0 (5 min) → alert | Grafana “Payments API – TLS Errors” |
| `payments_api_paygate_latency_seconds` | Latency of outbound calls to Paygate | > 2 s (5 min) → alert | Grafana “Payments API – Paygate Latency” |

All dashboards are located in the `platform-config` Grafana folder *Payments*.

## Alerts and first response
| Alert | Condition | PagerDuty Service | First‑response actions |
|-------|-----------|-------------------|------------------------|
| `PaymentsAPISuccessRateLow` | success_rate < 99.95 % for 5 min | Payments | 1️⃣ Acknowledge. 2️⃣ Verify alert details in Grafana. |
| `PaymentsAPILatencyHigh` | latency_p99 > 2 s for 5 min | Payments | Same as above. |
| `PgBouncerClientWaitHigh` | client_wait_seconds_total > 5 s for 5 min | Payments | Same as above. |
| `PaymentsAPITLSError` | tls_errors_total > 0 for 5 min | Payments | Same as above. |
| `PaygateLatencyHigh` | paygate_latency_seconds > 2 s for 5 min | Payments | Same as above. |

After acknowledgment, proceed to **Diagnosis**.

## Diagnosis

### Connection waits at PgBouncer when client demand exceeds the server pool
1. **Check PgBouncer pool stats**  
   ```bash
   kubectl exec -n payments-api -c pgbouncer $(kubectl get pod -n payments-api -l app=payments-api -o jsonpath='{.items[0].metadata.name}') -- psql -h localhost -p 6432 -U pgbouncer -c "SHOW POOLS;"
   ```  
   Look for `cl_active`, `cl_waiting`, `sv_active`, `sv_idle`, and `maxwait` values.
2. **Verify total client connections**  
   ```bash
   kubectl exec -n payments-api -c pgbouncer $(kubectl get pod -n payments-api -l app=payments-api -o jsonpath='{.items[0].metadata.name}') -- psql -h localhost -p 6432 -U pgbouncer -c "SELECT sum(cl_active) FROM pg_stat_activity;"
   ```
3. **Inspect pod logs for wait‑timeout messages**  
   ```bash
   kubectl logs -n payments-api -l app=payments-api --since=10m | grep -i "client_wait_timeout"
   ```
4. **Confirm PgBouncer `default_pool_size`** (ConfigMap `pgbouncer-config` in `platform-config` repo).  
5. **Check node resource pressure** (CPU/Memory) that could limit pod scaling:  
   ```bash
   kubectl top nodes | grep prod-eu1
   ```

### TLS failures towards Paygate (expired client certificate, untrusted server chain)
1. **Search logs for TLS errors**  
   ```bash
   kubectl logs -n payments-api -l app=payments-api --since=10m | grep -i "tls"
   ```
2. **Validate client certificate expiration** (mounted at `/etc/certs/paygate-client.crt`):  
   ```bash
   kubectl exec -n payments-api -c payments-api $(kubectl get pod -n payments-api -l app=payments-api -o jsonpath='{.items[0].metadata.name}') -- \
   openssl x509 -noout -dates -in /etc/certs/paygate-client.crt
   ```
3. **Check cert‑manager Certificate status**  
   ```bash
   kubectl get certificate -n payments-api paygate-client -o yaml | grep -E 'status|renewalTime|notAfter'
   ```
4. **Verify the trust bundle ConfigMap**  
   ```bash
   kubectl get configmap -n payments-api paygate-ca-bundle -o yaml | grep -A5 "data:"
   ```
5. **Test a manual TLS handshake** (replace `paygate.example.com` with the real endpoint):  
   ```bash
   kubectl exec -n payments-api -c payments-api $(kubectl get pod -n payments-api -l app=payments-api -o jsonpath='{.items[0].metadata.name}') -- \
   openssl s_client -connect paygate.example.com:443 \
   -CAfile /etc/certs/paygate-ca-bundle.crt \
   -cert /etc/certs/paygate-client.crt \
   -key /etc/certs/paygate-client.key -servername paygate.example.com
   ```

### Paygate latency spikes
1. **Review latency metric** in Grafana for the last 15 min; note any sudden increase.  
2. **Run a direct request from a pod** to measure current response time:  
   ```bash
   START=$(date +%s%3N)
   kubectl exec -n payments-api -c payments-api $(kubectl get pod -n payments-api -l app=payments-api -o jsonpath='{.items[0].metadata.name}') -- \
   curl -s -o /dev/null -w "%{time_total}\n" \
   --cert /etc/certs/paygate-client.crt \
   --key /etc/certs/paygate-client.key \
   --cacert /etc/certs/paygate-ca-bundle.crt \
   https://paygate.example.com/health
   END=$(date +%s%3N)
   echo "Elapsed ms: $((END-START))"
   ```
3. **Check for known Paygate incidents** (internal status page or external status feed).  
4. **Correlate with internal load** – verify that request volume from `checkout-api` is not unusually high (Grafana “Checkout API – Request Rate”).

## Mitigation
- **PgBouncer connection waits**  
  1. If `sv_active` ≈ `default_pool_size`, increase PgBouncer `default_pool_size` in the Helm values and redeploy.  
  2. Scale `payments-api` replicas up (e.g., `kubectl scale deployment/payments-api -n payments-api --replicas=8`).  
  3. If node resources are saturated, consider adding capacity to the `prod-eu1` cluster.  
  4. Restart PgBouncer pods to apply new pool settings: `kubectl rollout restart deployment/pgbouncer -n payments-api`.

- **TLS failures**  
  1. If client cert is expired or near expiry, trigger a manual renewal via cert‑manager: `kubectl cert-manager renew paygate-client -n payments-api`.  
  2. If the trust bundle is outdated, update ConfigMap `paygate-ca-bundle` with the latest CA chain and roll the pods: `kubectl rollout restart deployment/payments-api -n payments-api`.  
  3. Verify after restart that TLS handshake succeeds (repeat manual handshake test).

- **Paygate latency spikes**  
  1. Enable a temporary circuit‑breaker in `payments-api` (adjust feature flag via ConfigMap `payments-api-config`).  
  2. Reduce traffic by throttling calls from `checkout-api` (e.g., lower concurrency limit).  
  3. If latency persists > 5 s, open a ticket with Paygate support and provide observed latency logs.  

All mitigations should be documented in the incident ticket with timestamps and commands used.

## Escalation
1. **Primary on‑call** – Payments on‑call (PagerDuty `payments-api`).  
2. **If unresolved within 15 min** – Page the Payments Team Lead (Slack `#payments-alerts`).  
3. **If the issue impacts the SLO** (e.g., success rate < 99.95 % for > 30 min) – Escalate to Platform Engineering (PagerDuty `platform`).  
4. **For external provider issues** (Paygate latency or TLS problems confirmed on Paygate side) – Contact Paygate support via the established ticketing channel and copy the Payments Team Lead.  

Document all actions, timestamps, and outcomes in the incident post‑mortem.
