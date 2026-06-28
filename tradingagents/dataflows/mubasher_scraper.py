import requests
from bs4 import BeautifulSoup
import random
import time
import re
from typing import Optional, Dict, Any, Tuple

class MubasherScraper:
    """
    Scraper for real-time EGX stock data from english.mubasher.info
    """
    
    BASE_URL = "https://english.mubasher.info/markets/EGX/stocks"
    
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36'
    ]
    
    @classmethod
    def get_live_data(cls, symbol: str) -> Dict[str, Any]:
        """
        Fetch live price, change, and volume for a given EGX symbol.
        
        Args:
            symbol: Ticker symbol (e.g., 'COMI' or 'COMI.CA')
            
        Returns:
            Dict containing:
            - price: Current price (float)
            - change: Price change (float)
            - change_pct: Percentage change (float)
            - volume: Volume (int)
            - last_updated: Timestamp of fetch
            - source: "Mubasher"
        """
        # Clean symbol: COMI.CA -> COMI
        ticker = symbol.upper().replace(".CA", "").strip()
        url = f"{cls.BASE_URL}/{ticker}"
        
        headers = {
            'User-Agent': random.choice(cls.USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://english.mubasher.info/markets/EGX',
            'Cache-Control': 'max-age=0'
        }
        
        max_retries = 1
        for attempt in range(max_retries):
            try:
                # Add slight delay between retries
                if attempt > 0:
                    time.sleep(1)
                
                response = requests.get(url, headers=headers, timeout=5)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # --- SCRAPING STRATEGY ---
                
                # 1. Price
                price = cls._extract_price(soup)
                
                # 2. Change
                change, change_pct = cls._extract_change(soup)
                
                # 3. Volume
                volume = cls._extract_volume(soup)
                
                if price is not None:
                     return {
                        "symbol": f"{ticker}.CA",
                        "price": price,
                        "change": change,
                        "change_pct": change_pct,
                        "volume": volume,
                        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "Mubasher (Live)"
                    }
            except Exception as e:
                if attempt == max_retries - 1:
                    return {"error": f"Failed after {max_retries} attempts. Last error: {str(e)}"}
                continue
                
        return {"error": f"Could not extract price for {ticker} after retries"}

    @staticmethod
    def _extract_price(soup) -> Optional[float]:
        """Extract price looking for large numbers or specific classes"""
        # Strategy 1: Look for number with 2 decimal places in prominent classes
        candidates = []
        
        # Specific classes found on Mubasher (based on typical structure)
        # Note: Classes often change hash, simplified selectors are better
        # Looking for 'market-summary__last-price' or similar
        potential_classes = [
            'market-summary__last-price', 
            'stock-price', 
            'current-price', 
            'value'
        ]
        
        for cls in potential_classes:
            elements = soup.find_all(class_=lambda x: x and cls in x)
            for el in elements:
                try:
                    text = el.get_text(strip=True).replace(',', '')
                    val = float(text)
                    if 0 < val < 10000: # Reasonable stock price range
                        candidates.append(val)
                except ValueError:
                    continue
        
        if candidates:
            return candidates[0] # Return first valid candidate
            
        # Strategy 2: Search for "Last Price" label parent's sibling
        labels = soup.find_all(string=re.compile("Last Price|Close", re.I))
        for label in labels:
            try:
                # Value is often in a sibling or parent's sibling
                container = label.find_parent('div') or label.find_parent('td')
                if container:
                    # Look for number in container text
                    text = container.get_text(strip=True)
                    match = re.search(r'(\d+\.\d{2})', text)
                    if match:
                        return float(match.group(1).replace(',', ''))
            except Exception:
                pass
                
        # Strategy 3: Brute force search for "121." etc (if we know approx price)
        # Not reliable for generic tool
        
        return None

    @staticmethod
    def _extract_change(soup) -> Tuple[Optional[float], Optional[float]]:
        try:
            # Look for colored change percentage
            # Often has class 'up' (green) or 'down' (red)
            elements = soup.find_all(class_=lambda x: x and ('up' in x or 'down' in x or 'change' in x))
            for el in elements:
                text = el.get_text(strip=True)
                if '%' in text:
                    # Found percentage
                    pct_match = re.search(r'([+-]?\d+\.\d+)%', text)
                    if pct_match:
                        # Value usually nearby
                        return 0.0, float(pct_match.group(1)) # Placeholder for absolute change
        except Exception:
            pass
        return None, None

    @staticmethod
    def _extract_volume(soup) -> float:
        try:
            labels = soup.find_all(string=re.compile("Volume", re.I))
            for label in labels:
                container = label.find_parent('div') or label.find_parent('td')
                if container:
                    text = container.get_text(strip=True)
                    # Extract large integer
                    match = re.search(r'(\d{1,3}(,\d{3})*)', text) # Matches 1,000,000
                    if match:
                        return float(match.group(1).replace(',', ''))
        except Exception:
            pass
        return 0.0

if __name__ == "__main__":
    # Test block
    print("Testing MubasherScraper...")
    import re
    result = MubasherScraper.get_live_data("COMI")
    print(result)
