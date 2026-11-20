> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: checkout-api

## Overview
- **Service:** `checkout-api`  
- **Team:** Checkout  
- **Runtime:** Java 21 (Spring Boot)  
- **Primary function:** Orchestrates cart handling, stock reservation, payment capture, and order emission.  
- **Key dependencies:** `inventory-api` (GET `/v2/availability`), `payments-api`  
- **Consumers:** `web-storefront`  
- **Datastore:** Redis 7 (`cart-cache`) – maxmemory 4 GB, `volatile-lru` eviction, keys `cart:{session}` with a 7‑day TTL.  
- **SLO:** 99.95 % successful checkout requests; p95 latency < 800 ms.  

## Key metrics and dashboards
| Metric | Description | Typical source |
|--------|-------------|----------------|
| `checkout_api_success_rate` | Percentage of checkout requests returning 2xx | Prometheus / Grafana |
| `checkout_api_p95_latency_ms` | 95th‑percentile request latency | Prometheus / Grafana |
| `redis_memory_used_bytes` | Current Redis memory usage | Redis `INFO memory` |
| `redis_evicted_keys_total` | Total keys evicted due to memory pressure | Redis `INFO stats` |
| `inventory_api_error_rate` | Errors when calling `inventory-api` | Distributed tracing / Prometheus |
| `payments_api_timeout_rate` | Timeouts observed when invoking `payments-api` | Prometheus / logs |
| `checkout_api_pod_restart_count` | Restarts of `checkout-api` pods | Kubernetes metrics |

Dashboards are available in the **Kestrel Commerce Observability** Grafana folder under *checkout‑api*.

## Alerts and first response
| Alert | Condition | Initial action |
|-------|-----------|----------------|
| `CheckoutSuccessRateLow` | Success rate falls below 99.95 % over 5 min | Acknowledge, verify alert details, check recent deployments |
| `CheckoutLatencyHigh` | p95 latency exceeds 800 ms over 5 min | Acknowledge, inspect latency breakdown per dependency |
| `RedisMemoryHigh` | Redis memory usage approaches 4 GB or eviction count rises | Acknowledge, check Redis memory stats |
| `InventorySchemaParseError` | Errors parsing `inventory-api` response (deserialization failures) | Acknowledge, review recent schema changes |
| `PaymentsTimeout` | Payment request timeout rate spikes | Acknowledge, verify connectivity to `payments-api` |

All alerts are routed to the Checkout on‑call Slack channel and PagerDuty service `checkout-api`.

## Diagnosis

### 1. Redis memory pressure
- **Check pod health:** `kubectl -n prod get pods -l app=checkout-api -o wide`  
- **Inspect Redis memory:** `redis-cli -h <redis-host> INFO memory`  
  - Verify `used_memory` vs `maxmemory` (4 GB).  
  - Look at `evicted_keys` and `expired_keys`.  
- **Confirm TTL compliance:** `redis-cli -h <redis-host> KEYS "cart:*" | xargs -I{} redis-cli -h <redis-host> TTL {}` – ensure TTL ≈ 7 days for a sample set.  
- **Review eviction policy:** `redis-cli -h <redis-host> CONFIG GET maxmemory-policy` (should be `volatile-lru`).  

### 2. Downstream schema changes break response parsing
- **Examine recent logs:** `kubectl -n prod logs -l app=checkout-api --since=1h | grep -i "JsonMappingException"`  
- **Validate inventory response:**  
  ```bash
  curl -s http://inventory-api.prod.svc.cluster.local/v2/availability | jq .
  ```  
  - Compare fields against the strict DTO definition in the checkout codebase.  
- **Check Git history:** Look for recent changes to `inventory-api` OpenAPI/JSON schema in the `platform-config` repo.  

### 3. Payment timeouts cause abandoned checkouts
- **Review payment request latency:** `kubectl -n prod logs -l app=checkout-api --since=15m | grep -i "payments-api"`  
- **Check payments‑api health:** `kubectl -n prod get endpoints payments-api` and `curl -s http://payments-api.prod.svc.cluster.local/health`  
- **Inspect network path:** `kubectl exec -n prod -c checkout-api <checkout-pod> -- curl -v http://payments-api.prod.svc.cluster.local/charge` – look for connection timeouts or HTTP 5xx.  

## Mitigation

### Redis memory pressure
1. **Force TTL compliance:** Run a cleanup job to delete keys without TTL:  
   ```bash
   redis-cli -h <redis-host> --scan --pattern "cart:*" | while read key; do
     ttl=$(redis-cli -h <redis-host> TTL "$key")
     if [ "$ttl" -eq -1 ]; then
       redis-cli -h <redis-host> DEL "$key"
     fi
   done
   ```  
2. **Scale Redis:** If memory usage remains high, request a larger Redis node or add a replica (via the platform‑config Helm values).  
3. **Adjust maxmemory‑policy:** If eviction is too aggressive, consider switching to `volatile-lru` with higher maxmemory (requires config change).  

### Downstream schema changes
1. **Rollback inventory‑api change** (if identified) or apply a compatibility shim in `checkout-api` to tolerate missing fields.  
2. **Deploy a hot‑fix** of `checkout-api` with relaxed DTO validation (e.g., `@JsonIgnoreProperties(ignoreUnknown = true)`).  
3. **Coordinate with Inventory team** to align versioning and add integration tests for schema compatibility.  

### Payment timeouts
1. **Increase client timeout** in `checkout-api` configuration (Helm value `payments.timeout`).  
2. **Retry logic:** Enable exponential back‑off for transient failures (if not already enabled).  
3. **If payments‑api is down:** Trigger a fail‑over or switch to a backup payment provider (if configured).  

After any mitigation step, monitor the relevant metrics for at least 10 minutes to confirm recovery.

## Escalation
- **Primary contact:** Checkout on‑call engineer (Slack `#oncall-checkout`).  
- **Secondary contact:** Checkout team lead (email `checkout-lead@kestrel.com`).  
- **Platform escalation:** If Redis or networking issues persist, page the Platform Reliability team (PagerDuty `platform-sre`).  
- **External escalation:** For payment provider outages, contact the provider’s support channel as documented in the payments‑api integration guide.  

Document all actions taken in the incident ticket and update the runbook if new failure modes are discovered.
