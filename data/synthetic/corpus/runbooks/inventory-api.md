> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: inventory-api

## Overview
- **Service:** `inventory-api` (Python 3.12, FastAPI)  
- **Team:** Fulfilment  
- **Purpose:** Serves stock‑level availability queries and processes reservation events.  
- **Key SLOs:**  
  - API p95 latency < 150 ms  
  - Reservation lag < 60 s  
- **Dependencies:**  
  - Calls `catalog-api` (internal)  
  - Consumed by `checkout-api` and `order-worker`  
  - Datastores: `inventory-db` (PostgreSQL 15) and Kafka topic `inventory-reservations` (consumer group `inventory-reserver`, 12 partitions)  
- **Deployment:** Helm values in `platform-config` → Argo CD → `prod-eu1` cluster (48 nodes)  

## Key metrics and dashboards
| Metric | Source | Target / Alert Threshold |
|--------|--------|---------------------------|
| API latency p95 ( `/v2/availability` ) | OpenSearch dashboard | < 150 ms |
| Reservation processing lag (current offset – latest offset) | Kafka consumer group lag (Prometheus `kafka_consumer_group_lag{group="inventory-reserver"}`) | < 60 s |
| Consumer poll duration | Prometheus `kafka_consumer_poll_time_ms` (max) | < 300 000 ms (5 min) |
| Consumer rebalance count | Prometheus `kafka_consumer_rebalances_total` | < 5 per hour |
| PostgreSQL connection pool usage (PgBouncer) | PgBouncer stats (`SHOW POOLS;`) | < 80 % |
| Pod CPU / memory | Prometheus `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes` | Within pod limits |
| Error rate (5xx) | OpenSearch `status:5xx` | < 1 % |

Dashboards are available in Grafana under **Fulfilment → inventory‑api**.

## Alerts and first response
| Alert | Condition | Initial Action |
|-------|-----------|----------------|
| `InventoryAPIHighLatency` | API p95 latency > 150 ms for 5 min | Verify pod health, check recent deployments |
| `InventoryReservationLag` | Kafka consumer lag > 60 s for 2 min | Inspect consumer poll metrics, check Kafka broker health |
| `InventoryConsumerRebalanceLoop` | Rebalance count > 5 in 10 min | Look for long poll batches, verify consumer config |
| `InventoryDBConnectionHigh` | PgBouncer pool usage > 80 % for 5 min | Check DB query performance, connection leaks |
| `InventoryPodCrashLoop` | Pod restarts > 3 in 5 min | Examine pod logs, restart pod if needed |

**First response steps (common to all alerts):**
1. Acknowledge the alert in PagerDuty.  
2. Pull latest logs:  
   ```bash
   kubectl -n fulfilment logs -l app=inventory-api -c inventory-api --tail=200 -f
   ```  
3. Verify pod status:  
   ```bash
   kubectl -n fulfilment get pods -l app=inventory-api -o wide
   ```

## Diagnosis

### 1. Slow per‑message processing causing rebalance loops and growing consumer lag
**Checks:**
1. **Consumer poll duration** – confirm if any poll exceeds `max.poll.interval.ms` (300 000 ms):  
   ```bash
   curl -s http://prometheus-prod/api/v1/query?query=max_over_time(kafka_consumer_poll_time_ms{group="inventory-reserver"}[5m])
   ```
2. **Consumer lag** – compare current offset vs. latest offset:  
   ```bash
   kafka-consumer-groups.sh --bootstrap-server kafka-prod:9092 \
     --describe --group inventory-reserver
   ```
3. **Processing time per message** – sample logs for `reservation processed` timestamps and compute delta.  
4. **Pod resource pressure** – check CPU/Memory:  
   ```bash
   kubectl top pods -n fulfilment -l app=inventory-api
   ```
5. **Thread pool / async worker saturation** – if the service uses a worker pool, look for queue length metrics (if exposed).

### 2. Breaking API changes affect checkout‑api
**Checks:**
1. **OpenAPI schema diff** – compare deployed Helm values (`values.yaml`) with previous version in Git (`git diff HEAD~1..HEAD platform-config/helm/inventory-api/values.yaml`).  
2. **Health of downstream callers** – verify `checkout-api` pod logs for 4xx/5xx errors targeting `/v2/availability`.  
   ```bash
   kubectl -n fulfilment logs -l app=checkout-api -c checkout-api --since=15m | grep "/v2/availability"
   ```
3. **Contract test results** – if CI publishes contract test status, check latest run in Argo CD UI.  
4. **Response schema validation** – send a test request and compare fields:  
   ```bash
   curl -s http://inventory-api.prod.svc.cluster.local/v2/availability?sku=TEST123 | jq .
   ```

## Mitigation

### For slow processing / rebalance loops
1. **Scale consumer pods** (horizontal pod autoscaler or manual):  
   ```bash
   kubectl -n fulfilment scale deployment inventory-api --replicas=4
   ```
2. **Reduce batch size** – edit Helm values and redeploy:  
   ```yaml
   consumer:
     maxPollRecords: 200   # from 500
   ```  
   Then run `argocd app sync inventory-api`.
3. **Increase `max.poll.interval.ms`** temporarily (e.g., to 600 000 ms) if batch size cannot be reduced.  
4. **Optimize processing code** – prioritize fixing any blocking I/O identified in logs.  
5. **Restart consumer group** to force a clean rebalance:  
   ```bash
   kubectl -n fulfilment rollout restart deployment inventory-api
   ```

### For breaking API changes
1. **Rollback the offending Helm release**:  
   ```bash
   argocd app rollback inventory-api <previous-version>
   ```
2. **Notify checkout‑api team** to pause deployments until contract is restored.  
3. **Apply a compatibility shim** (e.g., add missing fields) via a quick config change and sync.  

## Escalation
| Escalation Level | Condition | Owner | Contact |
|------------------|-----------|-------|---------|
| L1 (On‑call) | Any alert acknowledged | Fulfilment on‑call | PagerDuty |
| L2 (Team Lead) | Issue not resolved within 30 min or multiple alerts (e.g., both latency and lag) | Fulfilment Team Lead | Slack `#fulfilment-leads` |
| L3 (Platform SRE) | Persistent consumer rebalance loops > 1 hour, or database connection saturation > 90 % | Platform SRE Lead | PagerDuty `platform-sre` |
| L4 (Architecture) | Need for code change to fix processing logic or API contract | Fulfilment Architecture Owner | Email `arch-fulfilment@kestrel.com` |

**Escalation procedure:**
1. After the L1 timeout, page the L2 on‑call via PagerDuty.  
2. If L2 cannot resolve within the next 30 min, create a Zoom bridge and invite Platform SRE.  
3. Document all findings in the incident ticket (Confluence `INC-<date>-inventory-api`).  
4. If a code change is required, open a PR in `platform-config` and tag `#urgent`.  

---  
*All commands assume you have appropriate `kubectl` context set to the `prod-eu1` cluster.*
