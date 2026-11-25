> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: shipping-api

## Overview
**Service:** `shipping-api`  
**Team:** Fulfilment  
**Runtime:** Python 3.12 (FastAPI)  
**Purpose:** Provides delivery quotes for checkout and creates shipping labels for `order-worker` by calling the CarrierHub API.  

Key SLA: Quote latency p95 < 400 ms; shipping labels must be created within 2 minutes of request.

## Key metrics and dashboards
| Metric | Description | Typical Threshold |
|--------|-------------|-------------------|
| `shipping_api_quote_latency_p95` | 95th‑percentile latency of quote endpoint | < 400 ms |
| `shipping_api_label_latency_p95` | 95th‑percentile latency of label creation endpoint | < 2 min |
| `carrierhub_http_429_total` | Counter of 429 responses from CarrierHub | > 0 (alert) |
| `carrierhub_response_time_avg` | Average response time from CarrierHub | < 1 s (adjust as needed) |
| `carrierhub_rate_limit_remaining` | Gauge of remaining requests in the shared 50 req/s bucket (if exposed) | > 0 |

Dashboards:
- **Kestrel‑Commerce → Shipping API** (Grafana) showing the above metrics, request rates, and cache hit ratio (`carrier_rate_cache_hit_ratio` ≈ 92 %).
- **Argo CD → Deployments** for version tracking of `shipping-api`.

## Alerts and first response
| Alert | Trigger | First‑response action |
|-------|---------|-----------------------|
| `ShippingAPIQuoteLatencyHigh` | `shipping_api_quote_latency_p95` > 400 ms for 5 min | Acknowledge; verify alert details; proceed to Diagnosis. |
| `ShippingAPILabelLatencyHigh` | `shipping_api_label_latency_p95` > 2 min for 5 min | Acknowledge; verify alert details; proceed to Diagnosis. |
| `CarrierHub429RateLimit` | `carrierhub_http_429_total` > 0 in 1 min window | Acknowledge; check CarrierHub rate‑limit status; proceed to Diagnosis. |
| `ShippingAPIErrorRate` | HTTP 5xx error rate > 1 % for 5 min | Acknowledge; check pod logs; proceed to Diagnosis. |

All alerts are routed to the Fulfilment on‑call rotation via PagerDuty.

## Diagnosis

### 1. CarrierHub rate limit exceeded (HTTP 429)
**Checks**
1. **Alert details** – confirm `carrierhub_http_429_total` count and time window.
2. **Pod logs** – run:
   ```bash
   kubectl logs -l app=shipping-api -c app --since=5m | grep 429
   ```
   Look for log lines indicating “CarrierHub rate limit exceeded”.
3. **Cache hit ratio** – verify that the 15‑minute carrier rate cache is functioning:
   ```bash
   curl http://prometheus.k8s.svc:9090/api/v1/query?query=carrier_rate_cache_hit_ratio
   ```
   Ratio should be ~0.92; a sudden drop suggests cache misses increasing request volume.
4. **CarrierHub request rate** – if metrics expose `carrierhub_rate_limit_remaining`, ensure it is not zero.
5. **Argo CD deployment version** – confirm no recent change introduced higher request volume:
   ```bash
   argocd app get shipping-api -o yaml | grep image
   ```

### 2. Slow CarrierHub responses causing quote timeouts
**Checks**
1. **Latency metrics** – inspect `carrierhub_response_time_avg` and `shipping_api_quote_latency_p95`:
   ```bash
   curl http://prometheus.k8s.svc:9090/api/v1/query?query=carrierhub_response_time_avg
   ```
2. **Pod logs for timeouts** – search for timeout messages:
   ```bash
   kubectl logs -l app=shipping-api -c app --since=5m | grep -i timeout
   ```
3. **Network connectivity** – test connectivity from a pod to CarrierHub:
   ```bash
   kubectl exec -it $(kubectl get pod -l app=shipping-api -o name | head -n1) -- curl -s -o /dev/null -w "%{http_code} %{time_total}\n" https://api.carrierhub.com/health
   ```
   Verify response code 200 and total time < 1 s.
4. **Cache effectiveness** – confirm that most quote requests are served from cache (should be ~92 %). A drop may increase live calls and expose latency.
5. **Resource usage** – check CPU/Memory of `shipping-api` pods to rule out local throttling:
   ```bash
   kubectl top pods -l app=shipping-api
   ```

## Mitigation
1. **Rate‑limit back‑off**
   - If 429s are observed, enable exponential back‑off in the `shipping-api` client (if configurable via Helm values). Example Helm override:
     ```yaml
     shippingApi:
       carrierHub:
         backoff:
           enabled: true
           baseDelayMs: 100
           maxDelayMs: 2000
     ```
   - Redeploy with `argocd app sync shipping-api`.

2. **Increase cache coverage**
   - Verify that the 15‑minute cache TTL is active. If cache miss ratio is high, consider temporarily extending TTL (e.g., to 30 min) via Helm:
     ```yaml
     shippingApi:
       carrierHub:
         rateCacheTtlMinutes: 30
     ```
   - Sync the change.

3. **Throttle outbound requests**
   - Apply a client‑side rate limiter (e.g., token bucket) to keep calls ≤ 45 req/s, leaving headroom for bursts. Adjust via Helm:
     ```yaml
     shippingApi:
       carrierHub:
         maxRequestsPerSecond: 45
     ```

4. **Restart pods**
   - If pods show high memory/CPU or stuck connections, perform a rolling restart:
     ```bash
     kubectl rollout restart deployment/shipping-api
     ```

5. **Temporary fallback**
   - If CarrierHub remains unresponsive, configure `shipping-api` to return a friendly error to checkout (`503 Service Unavailable`) and alert the fulfilment team to manually process urgent orders.

## Escalation
| Escalation Level | Condition | Contact |
|------------------|-----------|---------|
| **L1** | Alert acknowledged, initial checks performed, issue not resolved within 10 min | Fulfilment on‑call (PagerDuty) |
| **L2** | Persistent 429s or timeouts after mitigation steps, or impact on checkout > 5 % | Platform SRE lead (Slack `#platform-sre`) |
| **L3** | CarrierHub outage confirmed or need for provider‑level changes | Architecture Owner (email `arch@kestrel.com`) and CarrierHub support (account manager) |
| **L4** | Production-wide degradation affecting multiple services | VP of Engineering (escalate via PagerDuty “critical” incident) |

All escalations must include:
- Alert ID and timestamp
- Summary of diagnostics performed
- Mitigation steps already applied
- Current metric values (latency, 429 count, cache hit ratio) 

Document all actions in the incident ticket (Jira `KESTREL-INC`) and update the post‑mortem once resolved.
