> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: web-storefront

## Overview
The **web-storefront** service renders the customer‑facing website (product, search, cart, checkout) using Next.js with server‑side rendering (SSR). It runs in the `prod-eu1` Kubernetes cluster and depends on three internal APIs:

- `catalog-api`
- `search-api`
- `checkout-api`

Static assets and images are served from a CDN whose origin is an object storage bucket. SSR upstream calls have a timeout of **3 s** each. The SLO is **99.9 %** of page renders completing under **1.5 s** (p95).

## Key metrics and dashboards
| Metric | Source | Target / Threshold |
|--------|--------|--------------------|
| Page render latency (p95) | OpenSearch / Grafana dashboard `web-storefront/latency` | ≤ 1.5 s |
| 5xx error rate (incl. 504) | Prometheus `http_server_requests_total{status=~"5.."}` | ≤ 0.1 % |
| Upstream API latency (per call) | Prometheus `upstream_api_duration_seconds` (labels: `api`) | ≤ 3 s |
| CDN cache hit ratio | CDN monitoring endpoint / Grafana `cdn/cache_hit_ratio` | ≥ 95 % |
| Pod restarts / crash loops | Kubernetes `kubectl get pods -n storefront -l app=web-storefront` | 0 |

Dashboards are located in the `kestrel-prod` Grafana folder under **Web Storefront**.

## Alerts and first response
| Alert | Condition | Channel | First response |
|-------|-----------|---------|----------------|
| `WebStorefrontHighLatency` | Render latency p95 > 1.5 s for 5 min | PagerDuty (Storefront on‑call) | Acknowledge, check recent logs (`kubectl logs -l app=web-storefront`) |
| `WebStorefront5xxRate` | 5xx error rate > 0.1 % for 5 min | PagerDuty | Acknowledge, verify if 504s are spiking |
| `WebStorefrontPodCrashLoop` | Pod in CrashLoopBackOff > 2 occurrences | PagerDuty | Acknowledge, `kubectl describe pod` to view events |
| `CDNStaleContent` | CDN cache miss ratio > 5 % for 10 min (or price asset mismatch alerts) | Slack #storefront-alerts | Acknowledge, verify CDN config |

**First response checklist**
1. Acknowledge the alert in PagerDuty.  
2. Pull the latest logs from a representative pod:  
   ```bash
   POD=$(kubectl get pods -n storefront -l app=web-storefront -o jsonpath='{.items[0].metadata.name}')
   kubectl logs -n storefront $POD -c web-storefront --tail=200
   ```
3. Verify the health of dependent APIs:  
   ```bash
   for api in catalog-api search-api checkout-api; do
     curl -s -o /dev/null -w "%{http_code} %{time_total}\\n" http://$api/healthz
   done
   ```
4. Check the current CDN cache status (use the CDN provider’s CLI or API to list cache rules).  

If the alert is clearly due to a known failure mode, proceed to the corresponding mitigation steps.

## Diagnosis

### Slow upstream APIs causing 504 timeouts
1. **Confirm 504 responses**  
   ```bash
   kubectl logs -n storefront $POD -c web-storefront | grep "504"
   ```
2. **Measure upstream latency** (use Prometheus or direct curl):  
   ```bash
   for api in catalog-api search-api checkout-api; do
     curl -s -w "%{time_total}\\n" http://$api/healthz
   done
   ```
   Compare against the 3 s SSR timeout.
3. **Check API pod health**  
   ```bash
   kubectl get pods -n storefront -l app=$api
   kubectl describe pod -n storefront -l app=$api | grep -i "CrashLoopBackOff"
   ```
4. **Inspect recent deployments** – verify that no recent Helm value change lowered timeout or increased load:
   ```bash
   argocd app diff web-storefront --revision HEAD
   ```
5. **Review resource usage** of the upstream API pods (CPU/Memory) to see if they are throttled:
   ```bash
   kubectl top pod -n storefront -l app=$api
   ```

### CDN cache rules misconfiguration leading to stale prices or assets
1. **Validate CDN cache headers** for a known product image or price JSON:
   ```bash
   curl -I https://cdn.example.com/assets/product-123.jpg
   curl -I https://cdn.example.com/api/prices/123
   ```
   Look for `Cache-Control`, `Expires`, and `ETag` values.
2. **Compare origin vs. CDN content** for a sample asset:
   ```bash
   curl -s https://origin-bucket.s3.amazonaws.com/assets/product-123.jpg > /tmp/origin.jpg
   curl -s https://cdn.example.com/assets/product-123.jpg > /tmp/cdn.jpg
   diff /tmp/origin.jpg /tmp/cdn.jpg
   ```
3. **Check CDN configuration** (list cache rules) via the provider’s CLI or API:
   ```bash
   cdn-cli list-rules --service web-storefront
   ```
   Verify that price endpoints have appropriate `max‑age` and that purge rules are enabled.
4. **Review recent CDN invalidation requests** to ensure they were not blocked:
   ```bash
   cdn-cli list-invalidations --service web-storefront --last 24h
   ```

## Mitigation

### Slow upstream APIs
- **Temporary mitigation**: Increase SSR upstream timeout to 5 s (edit Helm values and sync via Argo CD):
  ```yaml
  web-storefront:
    ssr:
      upstreamTimeoutSeconds: 5
  ```
  Commit the change, push to `platform-config`, and let Argo CD apply.
- **Scale affected API**: If a specific API shows high CPU/memory, scale it:
  ```bash
  kubectl scale deployment -n storefront $api --replicas=5
  ```
- **Restart problematic pods** to clear transient issues:
  ```bash
  kubectl rollout restart deployment -n storefront $api
  ```
- **If the API is down**: Route traffic to a fallback stub (if configured) or display a graceful error page.

### CDN cache misconfiguration
- **Purge stale objects** for affected paths:
  ```bash
  cdn-cli purge --paths "/assets/*" "/api/prices/*"
  ```
- **Correct cache rule**: Update the CDN rule to set appropriate `max-age` (e.g., 300 s for price data) and re‑apply:
  ```yaml
  cdn:
    rules:
      - path: "/api/prices/*"
        ttl: 300
        stale-while-revalidate: 60
  ```
  Commit to the CDN config repo and trigger a deployment.
- **Force re‑validation** by adding a version query string to static asset URLs (if possible) until the rule is fixed.
- **Monitor** the CDN hit ratio after changes; ensure it returns to ≥ 95 %.

## Escalation
| Situation | Escalation path | Contact |
|-----------|----------------|---------|
| Persistent 504s after mitigation (≥ 15 min) | Escalate to **Platform Reliability** (on‑call) | Slack `#platform-oncall` |
| CDN rule changes do not propagate (≥ 30 min) | Escalate to **CDN Provider Support** via ticket (include purge IDs) | Email `cdn-support@kestrel.com` |
| Multiple upstream APIs down simultaneously | Escalate to **API Owner Teams** (catalog, search, checkout) via PagerDuty escalation chain | PagerDuty `api-owners` |
| Incident impacting SLO (> 5 min of degraded latency) | Trigger **Incident Commander** (Storefront lead) and open a post‑mortem issue in the `storefront-incidents` repo | Slack `#storefront-incidents` |

All escalations must include:
- Alert ID and timestamp
- Summary of diagnostics performed
- Mitigation steps already applied
- Links to relevant dashboards and logs

---
