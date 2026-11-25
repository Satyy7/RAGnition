# EVAL-112: Sign-ins failing

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-19T21:00:00Z

## Reporter note

Customers cannot sign in since about 20:30; existing sessions still work. auth-service only changed email text yesterday.

## Alerts

- SignInFailureRate: auth-service 64%
- RedisMemoryHigh: session-store used_memory 100% of maxmemory

## Logs

```
2026-12-19T20:41:10Z auth-service level=error msg="session save failed" err="OOM command not allowed when used memory > 'maxmemory'."
2026-12-19T20:41:30Z session-store INFO keyspace: db0 keys=2281903 expires=391022
```
