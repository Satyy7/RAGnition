# EVAL-118: Pods slow to start, flag fetch timeouts

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-09T10:00:00Z

## Reporter note

Since yesterday new pods in checkout and search take minutes to become ready, and some features flicker. Checkout deployed saved cards this morning.

## Alerts

- FlagFetchErrors: 4 services reporting flag fetch timeouts
- KubeDeploymentRolloutStuck: checkout-api

## Logs

```
2027-01-09T09:31:12Z checkout-api WARN FlagClient - fetch timed out after 2000ms (attempt 3)
2027-01-09T09:31:40Z feature-flags INFO requests_per_s=412 p99_ms=2840 cpu=98%
```
