# MNQ Trading Bot

Skeleton for an automated strategy on Micro E-mini Nasdaq-100 futures (MNQ).

- Tick size 0.25 = $0.50; point value $2.
- Default mode is **paper/backtest**. Live trading requires implementing a broker adapter and setting `mode: live`.
- Not financial advice. Futures trading carries substantial risk of loss.

## Layout
- `config.yaml` – settings
- `bot/strategy.py` – EMA crossover + ATR stops
- `bot/risk.py` – position sizing and daily loss limit
- `bot/broker.py` – broker interface and paper broker
- `bot/backtest.py` – CSV backtester
- `main.py` – entry point

## Usage
```
pip install -r requirements.txt
python main.py --csv data/mnq_1m.csv
```
CSV columns: `timestamp,open,high,low,close,volume`.
