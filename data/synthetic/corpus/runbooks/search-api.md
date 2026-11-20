> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: search-api

## Overview
- **Service:** `search-api` (Java 21)  
- **Team:** Search  
- **Purpose:** Serves product search requests from `web-storefront` and receives catalog updates from the `search-indexer` job, which writes to OpenSearch.  
- **SLO:** 95th‑percentile search latency ≤ 300 ms; catalog changes must become searchable within 5 minutes.  
- **Dependencies:**  
  - Calls `catalog-api` (internal)  
  - Writes to OpenSearch cluster `search-prod` (3 data nodes, 500 GB each)  
- **Datastores:** OpenSearch indices `products-*` (search data) and `search-querylog-*` (query logs).  

## Key metrics and dashboards
| Metric | Description | Typical threshold |
|--------|-------------|-------------------|
| `search_api_http_request_duration_seconds{quantile="0.95"}` | 95th‑percentile request latency | ≤ 0.300 s |
| `search_indexer_backlog_items` | Number of pending catalog updates awaiting indexing | ≤ 0 (should be near zero) |
| `opensearch_cluster_health_status` | Cluster health (green/yellow/red) | `green` |
| `opensearch_node_disk_used_percent` | Disk usage per OpenSearch data node | < 85 % (low watermark) |
| `opensearch_shards_unassigned_total` | Total unassigned shards | 0 |
| `opensearch_indices_write_blocked` | Count of indices with `index.blocks.read_only_allow_delete` set | 0 |

Dashboards (Grafana):
- **Search API Latency** – shows request latency percentiles and error rates.  
- **OpenSearch Cluster Overview** – node disk usage, shard allocation, index write block status.  
- **Indexer Backlog** – pending catalog updates vs. time.

## Alerts and first response
| Alert | Trigger condition | First‑response action |
|-------|-------------------|-----------------------|
| `SearchAPIHighLatency` | 95th‑pct latency > 300 ms for 5 min | Verify pod health, check recent deployments, look for upstream `catalog-api` latency spikes. |
| `OpenSearchDiskWatermark` | Any node disk usage ≥ 90 % (high watermark) | Inspect OpenSearch node disk usage, confirm watermarks, check for read‑only indices. |
| `OpenSearchReadOnly` | Indexes have `index.blocks.read_only_allow_delete` set | Identify affected indices, assess disk pressure, plan remediation. |
| `SearchIndexerBacklog` | `search_indexer_backlog_items` > 0 for > 5 min | Verify `search-indexer` job status, check OpenSearch write health. |

**First response checklist (run for any alert):**
1. Acknowledge the alert in the incident channel.  
2. Pull up the relevant Grafana dashboard.  
3. Run `kubectl get pods -n search -l app=search-api` to confirm pod status (Ready/Running).  
4. Check recent deployment events: `kubectl rollout status deployment/search-api -n search`.  

## Diagnosis

### 1. Disk watermarks reached (shards unassigned or indices write‑blocked)
**Checks**
1. Verify node disk usage:  
   ```bash
   curl -s -u admin:password https://search-prod/_cat/nodes?v&h=name,heap.percent,disk.used_percent
   ```  
   Look for any node reporting `disk.used_percent` ≥ 85 % (low) or ≥ 90 % (high).  
2. List indices with read‑only block:  
   ```bash
   curl -s -u admin:password https://search-prod/_cat/indices?v&s=index | grep -i read_only_allow_delete
   ```  
3. Check shard allocation status:  
   ```bash
   curl -s -u admin:password https://search-prod/_cluster/health?pretty
   ```  
   Ensure `unassigned_shards` is 0.  
4. Review OpenSearch logs for watermark events:  
   ```bash
   kubectl logs -n search -l app=opensearch-data --tail=100 | grep -i watermark
   ```  

### 2. Indexer backlog makes search results stale
**Checks**
1. Query the backlog metric directly (if exposed via Prometheus):  
   ```bash
   curl -s http://prometheus-operated:9090/api/v1/query?query=search_indexer_backlog_items
   ```  
2. Inspect the `search-indexer` job status:  
   ```bash
   kubectl get jobs -n search -l app=search-indexer -o wide
   kubectl logs -n search -l app=search-indexer --tail=200
   ```  
3. Verify that OpenSearch indices are writable:  
   ```bash
   curl -s -u admin:password https://search-prod/_cat/indices?v | grep products-
   ```  
   Ensure no `read_only_allow_delete` flag appears.  
4. Confirm recent catalog updates are reflected:  
   ```bash
   curl -s -u admin:password https://search-prod/products-*/_search?q=sku:TEST123
   ```  
   Compare the result with the expected catalog state.  

## Mitigation

### Disk watermarks reached
1. **Free disk space**  
   - Delete old query‑log indices if retention is mis‑configured:  
     ```bash
     curl -X DELETE -u admin:password "https://search-prod/search-querylog-*/_delete_by_query" -H 'Content-Type: application/json' -d '{"query":{"range":{"@timestamp":{"lt":"now-30d"}}}}'
     ```  
   - Increase OpenSearch disk size (coordinate with infrastructure) or add a new data node.  
2. **Remove read‑only block** (after confirming sufficient space):  
   ```bash
   curl -X PUT -u admin:password "https://search-prod/_all/_settings" -H 'Content-Type: application/json' -d '{"index.blocks.read_only_allow_delete": null}'
   ```  
3. **Trigger shard reallocation** (if shards remain unassigned):  
   ```bash
   curl -X POST -u admin:password "https://search-prod/_cluster/reroute?retry_failed=true"
   ```  

### Indexer backlog
1. **Restart the `search-indexer` job** (if stuck):  
   ```bash
   kubectl delete job -n search -l app=search-indexer
   kubectl apply -f platform-config/helm/search-indexer/values.yaml
   ```  
2. **Temporarily increase index write capacity** (e.g., raise `index.translog.flush_threshold_size` via index settings) if write throttling is observed.  
3. **Validate catalog source** – ensure `catalog-api` is delivering updates; run a health check:  
   ```bash
   curl -s http://catalog-api.prod.svc.cluster.local/healthz
   ```  

## Escalation
| Situation | Escalation path | When to escalate |
|-----------|----------------|------------------|
| Persistent disk pressure after mitigation attempts (disk usage still ≥ 90 % after 30 min) | Page **Infrastructure Lead** → **Platform SRE** | If freeing space or adding nodes does not reduce usage within the SLA window. |
| Indexer backlog remains > 0 for > 15 min after job restart | Notify **Search Team Lead** → **Data Engineering** | When backlog does not clear and catalog freshness SLO is at risk. |
| Cluster health stays `yellow`/`red` despite shard reallocation | Open a **Critical Incident** with Platform SRE on-call | If search latency degrades or service becomes unavailable. |
| Unable to resolve alerts within 30 min | Escalate to **Site Reliability Manager** | For any alert that threatens the 5‑minute catalog freshness SLO or 300 ms latency SLO. |

All escalations should be logged in the incident tracking system with timestamps, actions taken, and current status.
