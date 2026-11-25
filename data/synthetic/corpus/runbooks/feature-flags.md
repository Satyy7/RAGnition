> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: feature-flags

## Overview
The **feature-flags** service provides runtime feature toggles to downstream services (web-storefront, checkout‑api, search‑api, order‑worker) via a Go SDK that polls `/api/client/features` every 30 s. The service stores flag definitions in the `flags-db` PostgreSQL 15 instance (accessed through PgBouncer). The SLO requires flag fetch latency p99 < 100 ms. The service runs with 2 replicas in the `prod-eu1` Kubernetes cluster.

## Key metrics and dashboards
| Metric | Description | Typical threshold |
|--------|-------------|-------------------|
| `http_request_duration_seconds{service="feature-flags",le="0.1"}` (p99) | Flag fetch latency | ≤ 0.1 s (100 ms) |
| `http_requests_total{service="feature-flags",code=~"5.."}` | 5xx error count | 0 |
| `process_cpu_seconds_total{service="feature-flags"}` | CPU usage per pod | < 80 % of request CPU limit |
| `process_resident_memory_bytes{service="feature-flags"}` | Memory usage per pod | < 80 % of request memory limit |
| `kube_pod_status_phase{pod=~"feature-flags-.*",phase="Running"}` | Pod availability | 2/2 pods running |
| `postgresql_connections_total{datname="flags-db"}` | DB connections from service | ≤ max‑client‑connections (configured in PgBouncer) |
| `feature_flags_poll_requests_total` | Number of SDK poll requests (should be ~400 × 2 per minute) | ~13 k per minute |

Dashboards (Grafana) are pre‑built in the **Platform → Feature Flags** folder and include the above metrics with alerts overlay.

## Alerts and first response
| Alert | Condition | Immediate action |
|-------|-----------|-------------------|
| `FeatureFlagsHighLatency` | p99 latency > 100 ms for 5 min | Verify pod health, check load |
| `FeatureFlags5xxRate` | 5xx error rate > 5 % over 5 min | Inspect logs, check DB connectivity |
| `FeatureFlagsPodDown` | < 2 pods in Running state for 2 min | Restart missing pods |
| `FeatureFlagsDBConnError` | DB connection errors > 0 for 2 min | Verify PgBouncer and PostgreSQL health |

When any alert fires, acknowledge it in the incident tracker and begin the **Diagnosis** steps.

## Diagnosis

### 1. Flag service unreachable
**Goal:** Determine why downstream services cannot reach `/api/client/features`.

| Check | Command / Query | Expected result |
|-------|-----------------|-----------------|
| Verify pod status | `kubectl get pods -n platform -l app=feature-flags` | 2 pods `Running` |
| Check service endpoint | `kubectl exec -n platform $(kubectl get pod -n platform -l app=feature-flags -o jsonpath="{.items[0].metadata.name}") -- curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/client/features` | `200` |
| Review recent logs | `kubectl logs -n platform -l app=feature-flags --since=5m` | No fatal errors, no “listen: address already in use” |
| Confirm network policy / Service object | `kubectl describe svc -n platform feature-flags` | ClusterIP present, correct port |
| Test DB connectivity | `kubectl exec -n platform $(kubectl get pod -n platform -l app=feature-flags -o jsonpath="{.items[0].metadata.name}") -- psql "host=flags-db port=5432 dbname=flags-db user=readonly" -c "SELECT 1"` | `1` |
| Verify PgBouncer health | `kubectl exec -n platform $(kubectl get pod -n platform pgbouncer-0 -o jsonpath="{.metadata.name}") -- pgbouncer -c show pools` | Pools show active connections from feature‑flags |

If pods are down, restart them (`kubectl rollout restart deployment/feature-flags -n platform`). If the service endpoint returns non‑200, check for resource exhaustion (CPU/Memory) and recent deployments.

### 2. Polling load overloads the service and flag fetches time out
**Goal:** Identify whether the polling frequency or client count is saturating the service.

| Check | Command / Query | Expected result |
|-------|-----------------|-----------------|
| Observe request rate | `promql_query 'sum(rate(http_requests_total{service="feature-flags"}[1m]))'` | ≈ 13 k requests/min (400 clients × 2 replicas × 2 polls/min) |
| Check latency distribution | `promql_query 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{service="feature-flags"}[5m])) by (le))'` | ≤ 0.1 s |
| Look for request timeouts | `kubectl logs -n platform -l app=feature-flags --since=5m | grep timeout` | No timeout entries |
| Review pod resource usage | `kubectl top pod -n platform -l app=feature-flags` | CPU & memory < 80 % of limits |
| Examine recent config changes | `git -C /path/to/platform-config diff HEAD~1 values/feature-flags.yaml` | No unexpected poll interval changes |
| Check PostgreSQL load | `psql -h flags-db -U readonly -c "SELECT count(*) FROM pg_stat_activity WHERE state='active';"` | Should be well below max connections |

If latency spikes or timeouts appear, consider scaling the deployment (`kubectl scale deployment/feature-flags --replicas=3 -n platform`) or reducing client poll frequency (coordinate with SDK owners). Also verify that the PostgreSQL instance is not throttling connections.

## Mitigation
1. **Service unreachable**
   - Restart failing pods: `kubectl rollout restart deployment/feature-flags -n platform`.
   - If pods repeatedly crash, roll back the latest Helm release: `helm rollback feature-flags <previous-version> -n platform`.
   - Temporarily increase replica count to restore availability while investigating root cause.

2. **Polling overload**
   - Scale out the service: `kubectl scale deployment/feature-flags --replicas=3 -n platform`.
   - If scaling is insufficient, throttle client polling by updating the SDK config (e.g., increase interval to 45 s) and redeploy affected services.
   - As a short‑term fix, enable request timeout increase in the service config (if configurable) to avoid immediate failures.

After mitigation, verify that all alerts clear and that the p99 latency returns below 100 ms.

## Escalation
| Situation | Escalation path | Contact |
|-----------|----------------|---------|
| Unable to restore service after pod restarts or rollbacks | Escalate to **Platform Lead** | Slack: `#platform-lead` |
| Persistent DB connectivity issues or PgBouncer failures | Escalate to **Database Reliability Engineer** | Slack: `#db-reliability` |
| Load cannot be mitigated by scaling; requires architectural change | Escalate to **Architecture Review Board** | Email: `arch-board@kestrel.com` |
| All on‑call actions exhausted, alerts remain active > 30 min | Trigger **Page** to **Platform On‑Call Manager** | Phone: +1‑555‑0123 |

Document all actions taken in the incident ticket, include metric snapshots, and add any new findings to the runbook for future reference.
