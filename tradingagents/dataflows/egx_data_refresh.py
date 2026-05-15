import os
import sys
import datetime
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
import json

# Try to import bs4 (required for scraping EGX page)
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Ensure the root path is accessible to import the parsing script
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.append(project_root)


def is_fundamentals_stale(trade_date: str, max_age_days: int = 90) -> bool:
    """
    Checks if the local fundamentals data is stale compared to trade_date.
    Fast execution: just checks the filesystem modification time of the key_ratios CSVs.
    """
    try:
        # Use existing data cache path
        ratios_dir = Path(project_root) / "tradingagents" / "dataflows" / "data_cache" / "egx_fundamentals" / "key_ratios"
        
        if not ratios_dir.exists():
            return True
            
        csv_files = list(ratios_dir.glob("*.csv"))
        if not csv_files:
            return True
        
        # Parse trade_date
        current_dt = datetime.datetime.strptime(trade_date, "%Y-%m-%d")
        
        # Find the most recently updated file among the key ratios
        newest_mod_timestamp = max(f.stat().st_mtime for f in csv_files)
        newest_mod_dt = datetime.datetime.fromtimestamp(newest_mod_timestamp)
        
        # Check staleness: difference between trade date and CSV modified time
        age_days = (current_dt - newest_mod_dt).days
        
        # If age is positive and exceeds max_age_days, it's stale
        if age_days > max_age_days:
            return True
            
        return False
        
    except Exception as e:
        print(f"Warning: Failed to check fundamentals staleness: {e}")
        return True  # If unable to verify, lean towards refreshing


def fetch_and_refresh_egx_data(quarter_end_date: str) -> None:
    """
    Scrapes the EGX website for the latest Annex 5 PDF in English,
    downloads it, and triggers the parse_egx_annex5 script to update local CSVs.
    """
    print(f"\n📥 Fundamentals data is stale. Initiating EGX statistics report download for '{quarter_end_date}'...")
    
    if not BeautifulSoup:
        print("Error: BeautifulSoup4 (bs4) is not installed. Cannot scrape EGX.")
        print("Please run: pip install beautifulsoup4")
        return
        
    try:
        # Step 1: Scrape the EGX Statistics page for the latest English disclosure PDF
        # Note: the website structure might vary, we look for "Statistical Bulletin" or similar PDF link.
        # Often it's behind a specific search/archive. For simplicity, if we don't find it dynamically,
        # we can provide manual fallback or warn the user.
        url = "https://www.egx.com.eg/en/EGX_Statistics.aspx"
        
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        )
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read()
        except urllib.error.URLError as e:
            print(f"Failed to fetch EGX Statistics page: {e}. Network is unreachable.")
            return
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find any PDF links that look like our statistical disclosure report
        pdf_urls = []
        for anchor in soup.find_all('a', href=True):
            href = anchor['href']
            # Based on known pattern DiscDoc_IDxxx_xxx_ENG.pdf
            if '.pdf' in href.lower() and ('DiscDoc_ID' in href or 'Statistics' in href or 'ENG' in href.upper()):
                # Resolve relative url
                full_url = urllib.parse.urljoin(url, href)
                pdf_urls.append(full_url)
                
        if not pdf_urls:
            print("Could not automatically locate the English PDF report on EGX Statistics page.")
            print("Format might have changed. Please manually download and run scripts/parse_egx_annex5.py")
            return
            
        # Prioritize English ('ENG') reports if multiple exist, sort for newest
        eng_pdfs = [p for p in pdf_urls if 'ENG' in p.upper()]
        target_pdf_url = eng_pdfs[0] if eng_pdfs else pdf_urls[0]
        
        print(f"Found latest PDF report: {target_pdf_url}")
        
        # Step 2: Download the PDF to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
            temp_pdf_path = tmp_pdf.name
            
        print(f"Downloading to temporary file...")
        pdf_req = urllib.request.Request(
            target_pdf_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
        )
        with urllib.request.urlopen(pdf_req, timeout=30) as response:
            with open(temp_pdf_path, 'wb') as out_file:
                out_file.write(response.read())
                
        print(f"Download complete.")
        
        # Step 3: Call the parser script directly
        try:
            from scripts.parse_egx_annex5 import extract_annex5_data, process_and_update
            
            raw_data = extract_annex5_data(temp_pdf_path)
            if raw_data:
                process_and_update(raw_data, quarter_end_date, project_root, dry_run=False)
                
                # Log success
                log_path = Path(project_root) / "tradingagents" / "dataflows" / "data_cache" / "egx_fundamentals" / "refresh_log.json"
                log_data = {"last_refresh": str(datetime.datetime.now()), "trade_date": quarter_end_date, "status": "success", "rows_extracted": len(raw_data)}
                with open(log_path, 'w') as f:
                    json.dump(log_data, f)
            else:
                print("No Annex 5 data was found inside the downloaded PDF.")
        except ImportError as e:
            print(f"Failed to import parser logic: {e}")
            
        # Cleanup
        try:
            os.remove(temp_pdf_path)
        except OSError:
            pass
            
    except Exception as e:
        print(f"Error during EGX data refresh process: {e}")
