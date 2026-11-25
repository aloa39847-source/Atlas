# TITAN SUPER AI PREMIUM

This repository contains the current TITAN SUPER AI PREMIUM source and its verification workflows.

## Project source

The application source is maintained under:

`titan_project/titan-ai-premium-78d9310021b58c510c8362e2acb34eba6353eebc/`

The project directory contains the backend, frontend, tests, documentation, and deployment files. The reference ZIP at the repository root is preserved as an immutable reference and is not included inside the final release archive.

## Verification and release

- Backend and frontend CI run against the current source.
- The offline stability soak exercises the 15-timeframe analysis path using labeled synthetic data; it does not contact Binance or submit orders.
- The Spot execution lifecycle regression uses a deterministic fake adapter.
- The USD-M Futures Testnet preflight is read-only and requires dedicated Futures Testnet credentials.
- The release workflow creates one clean ZIP with one project root, then the published-ZIP deep audit validates and tests that exact release.

No Binance Live trading is authorized by these workflows. External Binance Testnet order execution, Android runtime compatibility, and local LLM installation must be validated separately and are not implied by passing offline CI.

See the project README and Arabic audit/status reports inside the project directory for implementation scope and known limitations.
