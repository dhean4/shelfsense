# shelfsense-simulator

Publishes fake fridge temperature and GPS telemetry over MQTT so the ingest path,
anomaly rule and dashboard can be exercised without hardware.

**Status: P0 skeleton.** `shelfsense-simulator run` exits with code 2 and a message until
P5 implements the publisher. It does not silently no-op.
