> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: auth-service

## Overview
**Service:** `auth-service`  
**Team:** Identity  
**Runtime:** Go 1.23  

Provides customer sign‑in, session handling, and JWT issuance for the storefront and checkout. Tokens are signed with a rotating key; public keys are exposed at `/.well-known/jwks.json`. Sessions are stored in Redis (`sess:{id}`) with a 1‑day TTL.

## Key metrics and dashboards
| Metric | Description | Typical threshold |
|--------|-------------|-------------------|
| `auth_signin_success_rate` | % of successful sign‑ins (HTTP 200) | ≥ 99.95% |
| `auth_jwt_sign_errors_total` | Counter of JWT signing failures | 0 |
| `redis_memory_used_bytes` (session-store) | Current Redis memory usage | < 2 GB |
| `redis_memory_evicted_keys_total` | Number of evicted keys (should be 0) | 0 |
| `auth_jwks_publish_age_seconds` | Age of the newest published signing key | ≤ 24 h for retired keys |
| `auth_service_pod_restart_count` | Restarts per pod (5‑minute window) | 0 |

Dashboards are built in OpenSearch/Kibana and include:
- Sign‑in success rate time series
- Redis memory usage gauge
- JWKS endpoint response (status & payload)
- Pod health (ready/available, restarts)

## Alerts and first response
| Alert | Trigger | First‑response action |
|-------|---------|-----------------------|
| `AuthSignInFailure` | `auth_signin_success_rate` < 99.95% for 5 min | Acknowledge; verify alert details; proceed to Diagnosis |
| `AuthJWKSKeyMissing` | HTTP 401 from consumers + JWKS endpoint missing a key for > 5 min | Acknowledge; check JWKS publishing |
| `RedisSessionStoreFull` | `redis_memory_used_bytes` ≥ 2 GB (noeviction policy) | Acknowledge; verify Redis health; proceed to Diagnosis |
| `AuthPodCrashLoop` | Pod restart count > 0 in 5 min | Acknowledge; inspect pod logs; proceed to Diagnosis |

All alerts are routed to the on‑call Slack channel `#oncall-auth`.

## Diagnosis

### 1. Signing key rotated without overlap
**Goal:** Confirm that the JWKS endpoint contains the required key for token verification.

1. **Check JWKS endpoint**  
   ```bash
   curl -sSf http://auth-service.prod.svc.cluster.local/.well-known/jwks.json | jq .
   ```  
   Verify that the key ID (`kid`) used in recent failing tokens is present.

2. **Inspect recent token headers** (from a failing request captured in logs)  
   ```bash
   echo "<jwt>" | cut -d. -f1 | base64 -d | jq .
   ```  
   Note the `kid` value.

3. **Confirm key rotation schedule**  
   - Review Helm values in `platform-config` (`auth-service.values.yaml`) for `jwtSigningKeyRotationInterval`.
   - Ensure the previous key’s `notAfter` is still within the 24 h overlap window.

4. **Check consumer cache**  
   - Verify that `web-storefront` and `checkout-api` have refreshed their JWKS cache (e.g., by inspecting their logs for “JWKS cache refreshed”).

5. **Pod logs**  
   ```bash
   kubectl logs -l app=auth-service -c auth-service --since=15m
   ```  
   Look for errors like “failed to sign token” or “key not found”.

### 2. Session‑store full (Redis noeviction)
**Goal:** Determine if Redis memory usage has reached the 2 GB limit and is rejecting writes.

1. **Query Redis memory stats**  
   ```bash
   redis-cli -h <redis-host> -p 6379 INFO memory
   ```  
   - `used_memory` should be < 2 GB (2147483648 bytes).  
   - `maxmemory_policy` must be `noeviction`.

2. **Check for write errors in auth-service logs**  
   ```bash
   kubectl logs -l app=auth-service -c auth-service --since=10m | grep -i "redis.*error"
   ```

3. **Validate session key TTL**  
   ```bash
   redis-cli -h <redis-host> -p 6379 TTL sess:<sample-id>
   ```  
   Should return a positive number (≤ 86400 seconds).

4. **Inspect Redis key count** (optional, to gauge growth)  
   ```bash
   redis-cli -h <redis-host> -p 6379 DBSIZE
   ```

5. **Confirm no eviction events**  
   ```bash
   redis-cli -h <redis-host> -p 6379 INFO stats | grep evicted_keys
   ```  
   Should be `0`.

## Mitigation

### Signing key overlap issue
1. **Publish missing key manually**  
   - Add the retired key to the JWKS JSON in the config map used by `auth-service`.  
   - Reload the config:  
     ```bash
     kubectl rollout restart deployment/auth-service -n prod
     ```
2. **Force consumer cache refresh** (if possible)  
   - Trigger a config reload in `web-storefront` and `checkout-api` via their Argo CD sync or by sending a `POST /admin/refresh-jwks` request if exposed.

3. **Rollback rotation** (temporary)  
   - Revert `jwtSigningKeyRotationInterval` to previous value in Helm values and sync via Argo CD.

### Session‑store full
1. **Increase Redis maxmemory** (if capacity permits)  
   - Edit the Redis StatefulSet/ConfigMap to raise `maxmemory` (e.g., to 3 GB) and apply via Argo CD.  
   - Restart Redis pods:  
     ```bash
     kubectl rollout restart statefulset/redis -n prod
     ```

2. **Clear stale sessions** (short‑term relief)  
   - Identify and delete expired keys:  
     ```bash
     redis-cli -h <redis-host> -p 6379 --scan --pattern "sess:*" | xargs -L1000 redis-cli -h <redis-host> -p 6379 DEL
     ```
   - Note: only delete keys with TTL ≤ 0 to avoid removing active sessions.

3. **Adjust session TTL** (if business permits)  
   - Reduce TTL from 1 day to a shorter period in the auth-service config and redeploy.

4. **Scale Redis** (long‑term)  
   - Add additional Redis replicas or increase node resources via the platform‑config Helm chart.

## Escalation
| Condition | Escalation path |
|-----------|-----------------|
| Unable to restore JWKS overlap within 15 min | Page **Identity Lead** (Slack: @identity-lead) and open a high‑priority incident in PagerDuty. |
| Redis memory remains ≥ 2 GB after mitigation attempts | Page **Platform SRE Lead** (Slack: @platform-sre-lead) and involve the Redis on‑call engineer. |
| Multiple pod crashes or widespread sign‑in failures (> 5 min) | Trigger a **Kestrel Commerce Incident** (severity 1) via the central incident response channel and involve the on‑call Kubernetes/Argo CD team. |
| Any escalation does not resolve within 30 min | Notify the **Engineering Manager** for the Identity team and consider a post‑mortem. |

All escalations must be documented in the incident ticket with timestamps, actions taken, and current status.
