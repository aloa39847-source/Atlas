import pandas as pd
from config import MODE, STARTING_BALANCE, SYMBOLS
from titan.strategy import decide

def sample_candles():
    price = 60000.0
    rows = []
    for i in range(120):
        price *= 1 + (0.00025 if i % 7 else -0.00015)
        rows.append({"open": price*0.999, "high": price*1.001,
                     "low": price*0.998, "close": price, "volume": 10+i%9})
    return pd.DataFrame(rows)

def main():
    print(f"TITAN AI | {MODE} | virtual balance={STARTING_BALANCE:.2f} USDT")
    df = sample_candles()
    for symbol in SYMBOLS:
        signal, info = decide(df)
        print(symbol, signal, info)
    print("Synthetic-data smoke test only. No exchange connection or orders.")

if __name__ == "__main__":
    main()
