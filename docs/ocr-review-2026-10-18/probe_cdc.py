from app.services.cdc_agent import CDCEventProducer as P
for b in ("memory", "kafka"):
    try:
        P(backend=b)
        print(b, "construct OK")
    except Exception as e:
        print(b, "->", type(e).__name__, e)
