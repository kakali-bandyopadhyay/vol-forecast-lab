# Data sources

## SPY daily prices

- URL: <https://api.tiingo.com/tiingo/daily/SPY/prices>
- Fields kept: date, raw and adjusted OHLC, volume, adjusted volume, cash dividends, and split factor.
- Check the provider’s terms; raw vendor data is not committed.
- Adjusted fields only are used for analysis. Within-day ratios are unaffected by the adjustment factor; overnight returns use `adjOpen_t / adjClose_(t−1)`. No absolute adjusted level is used (PREREGISTRATION §2.3).

## Cboe VIX history

- URL: <https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv>
- Fields kept: the complete downloaded CSV, including date and OHLC columns.
- Check the provider’s terms; raw vendor data is not committed.

## FRED VIXCLS cross-check

- URL: <https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS>
- Fields kept: the complete downloaded CSV (`observation_date`, `VIXCLS`).
- Check the provider’s terms; raw vendor data is not committed.
