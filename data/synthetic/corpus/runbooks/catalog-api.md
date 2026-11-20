> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: catalog-api

## Overview
- **Service:** `catalog-api` (Kotlin/Ktor)  
- **Team:** Catalog  
- **Purpose:** Serves product, variant, and price data; source of truth for the search index.  
- **Primary datastore:** `catalog-db` (PostgreSQL 15) – table `product_variant` (~42 M rows).  
- **Key query:** Variant lookup by SKU (`WHERE sku = $1`).  
- **SLO:** 95th‑percentile response time < 200 ms.  
- **Deployment pipeline:** Argo CD → Helm values (platform‑config repo).  
- **Schema migrations:** Flyway scripts executed on each deploy.

## Key metrics and dashboards
| Metric | Target | Dashboard / Query |
|--------|--------|-------------------|
| p95 latency (catalog‑api) | < 200 ms | Grafana: `histogram_quantile(0.95, sum(rate(http_server_duration_seconds_bucket{service="catalog-api"}[5m])) by (le))` |
| Error rate (5xx) | < 1 % | Grafana: `sum(rate(http_requests_total{service="catalog-api",code=~"5.."}[5m])) / sum(rate(http_requests_total{service="catalog-api"}[5m]))` |
| DB connection usage (PgBouncer) | < 80 % of pool | Grafana: `pgbouncer_pool_used{database="catalog-db"}` |
| DB query latency (sku lookups) | < 50 ms (p95) | Grafana: `histogram_quantile(0.95, sum(rate(pg_stat_statements_time_seconds_bucket{query~="WHERE sku ="}[5m])) by (le))` |
| Flyway migration duration | < 5 min (typical) | Grafana: `max(flyway_migration_seconds{service="catalog-api"})` |

## Alerts and first response
| Alert | Trigger | Immediate action |
|-------|---------|-------------------|
| **catalog-api latency high** | p95 latency > 200 ms for 5 min | 1️⃣ Verify alert details (region, pod count). 2️⃣ Check recent deploys in Argo CD. 3️⃣ Proceed to **Diagnosis**. |
| **catalog-api error rate** | 5xx error rate > 1 % for 5 min | Same as above. |
| **PgBouncer pool exhaustion** | `pgbouncer_pool_used` > 80 % for 2 min | 1️⃣ Confirm DB health. 2️⃣ Check connection count per pod (`kubectl exec <pod> -- psql -U <user> -d catalog-db -c "SELECT count(*) FROM pg_stat_activity;"`). 3️⃣ Continue to **Diagnosis**. |
| **Flyway migration taking too long** | Migration duration > 10 min | 1️⃣ Identify which migration is running (`kubectl logs -l app=catalog-api -c flyway`). 2️⃣ Move to **Diagnosis**. |

## Diagnosis
### 1. Missing or unusable indexes cause sequential scans on `product_variant`
1. **Check query plan for SKU lookup**  
   ```bash
   kubectl exec -it $(kubectl get pod -l app=catalog-api -o jsonpath='{.items[0].metadata.name}') -- \
   psql -U <user> -d catalog-db -c "EXPLAIN ANALYZE SELECT * FROM product_variant WHERE sku = 'TESTSKU';"
   ```
   - Look for `Seq Scan` on `product_variant`.  
   - Expected: `Index Scan` using an index on `sku`.

2. **Verify index existence**  
   ```sql
   SELECT indexname, indexdef FROM pg_indexes WHERE tablename='product_variant' AND indexdef ILIKE '%sku%';
   ```
   - No rows → missing index.  
   - Index exists but not used → check statistics.

3. **Check table statistics**  
   ```sql
   SELECT relname, n_live_tup, last_analyze FROM pg_stat_user_tables WHERE relname='product_variant';
   ```
   - If `last_analyze` is older than 24 h, run `ANALYZE catalog-db.product_variant;`.

4. **Inspect PgBouncer pool health** (optional, if latency spikes coincide with connection pressure)  
   ```bash
   pgbouncer-cli show pools;
   ```

### 2. Long migrations lock tables
1. **Identify active Flyway migration**  
   ```bash
   kubectl logs -l app=catalog-api -c flyway --since=10m | grep -i "Migrating"
   ```
   - Look for statements like `ALTER TABLE product_variant ADD COLUMN ...`.

2. **Check PostgreSQL lock status**  
   ```sql
   SELECT pid, locktype, mode, granted, query FROM pg_locks l
   JOIN pg_stat_activity a ON l.pid = a.pid
   WHERE relation = 'product_variant'::regclass;
   ```
   - Presence of `AccessExclusiveLock` indicates a migration holding a lock.

3. **Assess impact on traffic**  
   - Verify if read queries are blocked (`SELECT * FROM product_variant WHERE sku = $1` timing out).  
   - Check error rate and latency spikes in Grafana.

## Mitigation
### Missing or unusable indexes
1. **Create missing index** (if absent)  
   ```sql
   CREATE INDEX CONCURRENTLY idx_product_variant_sku ON product_variant (sku);
   ```
   - Use `CONCURRENTLY` to avoid long table locks.

2. **Force planner to use index** (temporary)  
   ```sql
   SET enable_seqscan = off;
   ```
   - Verify improved query plan, then revert.

3. **Refresh statistics** (if index exists but not used)  
   ```sql
   ANALYZE product_variant;
   ```

4. **Roll back recent deploy** (if index removal was part of a migration) via Argo CD:
   ```bash
   argocd app rollback catalog-api --revision <previous-helm-revision>
   ```

### Long migrations lock tables
1. **Cancel the blocking migration** (if safe)  
   ```sql
   SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE query LIKE '%ALTER TABLE product_variant%';
   ```

2. **If migration must complete**, run it manually with reduced impact:  
   - Split large `ALTER TABLE` into smaller steps (e.g., add column without default, then backfill).  
   - Use `ALTER TABLE ... ADD COLUMN ... DEFAULT NULL` to avoid full table rewrite.

3. **Scale up PostgreSQL resources** temporarily (if lock is due to resource contention) via the managed PostgreSQL console.

4. **Redeploy catalog-api** after migration finishes to ensure new schema is recognized:
   ```bash
   argocd app sync catalog-api
   ```

## Escalation
- **Tier 1 (On‑call):** Follow the runbook steps. If unable to restore latency < 200 ms within 15 min, proceed.
- **Tier 2 (Catalog team lead):** Notify via Slack `#catalog-oncall`. Provide:
  - Current alert details.
  - Findings from Diagnosis (index status, migration lock info).
- **Tier 3 (Platform DB/Infra):** If the issue involves PostgreSQL lock contention, index creation failures, or resource exhaustion, page the Platform Database Engineer on-call.
- **Tier 4 (Site Reliability Manager):** If service remains degraded after Tier 3 actions or if a rollback is required that impacts other services, involve the SRE manager.  

All escalations must be logged in the incident ticket (e.g., PagerDuty) with timestamps, commands run, and any temporary work‑arounds applied.
