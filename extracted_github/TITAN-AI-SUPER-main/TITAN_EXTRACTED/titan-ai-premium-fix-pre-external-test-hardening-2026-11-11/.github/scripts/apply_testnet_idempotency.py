#!/usr/bin/env python3
"""Direct Testnet idempotency patch helper is a retired patch helper and is intentionally disabled.

It could re-enable direct exchange order/close routes outside TITAN's supervised
worker. Do not run it. Current execution must remain behind the durable
TITAN worker, risk checks, order log, idempotency, and reconciliation path.
"""

raise SystemExit(
    "DISABLED: would re-enable direct adapter order submission. Direct Testnet order/close routes must remain disabled; "
    "use the supervised TITAN worker and reviewed source changes only."
)
