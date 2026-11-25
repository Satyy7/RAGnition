# EVAL-116: Orders not handed to the warehouse

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-05T16:00:00Z

## Reporter note

Since early afternoon no orders reach the warehouse. Labels seem to be created fine. order-worker concurrency was raised yesterday.

## Alerts

- OrderHandoffDelay: oldest order waiting 2h10m

## Logs

```
2027-01-05T13:44:02Z order-worker ERROR handoff failed order=K-9010021: KeyError: 'tracking_number'
2027-01-05T13:44:02Z order-worker INFO label created status=201 body_keys=[tracking_id, carrier, label_url]
```
