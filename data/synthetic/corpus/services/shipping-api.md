> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Service: shipping-api

**Team:** Fulfilment  
**Runtime:** Python 3.12 (FastAPI)  
**SLO:** quotes p95 under 400 ms; labels within 2 minutes of request

Delivery quotes for checkout and shipping labels for order-worker, through the CarrierHub API.

## Dependencies

Calls:
- none

Called by:
- checkout-api
- order-worker

## Datastores

- none

## External providers

- CarrierHub carrier API: account limit 50 requests/s shared by quotes and labels; returns HTTP 429 when exceeded

## Configuration notes

- Carrier rates are cached for 15 minutes per (origin, destination, weight band); about 92% of quote requests hit the cache.
- Labels are created with `POST /v1/labels`; the response contains tracking_number, carrier and label_url.

## Known failure modes

- CarrierHub rate limit exceeded, so labels and quotes fail with upstream 429.
- Slow CarrierHub responses make checkout quotes time out.
