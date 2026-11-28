#!/usr/bin/env python3
"""Direct Testnet close patch helper is a retired patch helper and is intentionally disabled.

It could re-enable direct exchange order/close routes outside TITAN's supervised
worker. Do not run it. Current execution must remain behind the durable
TITAN worker, risk checks, order log, idempotency, and reconciliation path.
"""

raise SystemExit(
    "DISABLED: would bypass durable TITAN position lifecycle. Direct Testnet order/close routes must remain disabled; "
    "use the supervised TITAN worker and reviewed source changes only."
)
