# TITAN AI Premium — Demo Starter

This is a safe development starter, not a proven profitable trading system.
It runs in PAPER_ONLY mode and does not connect to Binance or place orders.

## Google Colab
1. Extract this ZIP.
2. Run `pip install -r requirements.txt`.
3. Run `python -m unittest discover -s tests -v`.
4. Run `python main.py`.

The sample candles in main.py are synthetic, not live market data.
No API keys are required. Do not paste secrets into Colab or chat.
Binance Futures Demo integration must be implemented and tested separately
before any exchange orders are enabled. The AI/LLM decision layer is not yet
implemented; this is the verified foundation only.
