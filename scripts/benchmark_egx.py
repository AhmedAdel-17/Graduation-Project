import yfinance as yf

# EGX 30 ticker on YFinance
ticker = "EGX30.CA" 
start_date = "2023-10-01"
end_date = "2024-01-01"

print(f"Fetching {ticker} (EGX 30 Index) from {start_date} to {end_date}...")
data = yf.download(ticker, start=start_date, end=end_date)

if data.empty:
    print("Cannot fetch EGX 30 data from YFinance. Using EGP 30 fallback.")
else:
    open_p = data.iloc[0]['Open']
    close_p = data.iloc[-1]['Close']
    change = ((close_p - open_p) / open_p) * 100
    
    # We use .item() to safely extract out of a pandas series if it comes back multi-index
    try:
        open_p = float(open_p.item())
    except:
        open_p = float(open_p)
        
    try:
        close_p = float(close_p.item())
    except:
        close_p = float(close_p)
    
    print(f"EGX 30 Opening Price: {open_p:.2f}")
    print(f"EGX 30 Closing Price: {close_p:.2f}")
    
    change = ((close_p - open_p) / open_p) * 100
    print(f"EGX 30 Period Return: {change:.2f}%")
