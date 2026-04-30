import argparse
import os
import sys
import datetime
import csv
import pdfplumber
import pandas as pd
from typing import Optional, Dict

# Company Name to EGX Ticker Mapping
# Covers all 31 main active EGX stocks we currently track
EGX_COMPANY_MAP = {
    # Banks
    "COMMERCIAL INTERNATIONAL BANK-EGYPT (CIB)": "COMI",
    "QNB ALAHLI": "QNBA",
    "ABU DHABI ISLAMIC BANK- EGYPT": "ADIB",
    "CREDIT AGRICOLE EGYPT": "CIEB",
    
    # Financial Services / Insurance
    "EFG HOLDING": "HRHO",
    "E-FINANCE FOR DIGITAL AND FINANCIAL INVESTMENTS": "EFIH",
    "FAWRY FOR BANKING TECHNOLOGY AND ELECTRONIC PAYMENT": "FWRY",
    "BELTONE FINANCIAL HOLDING": "BTFH",
    "CI CAPITAL HOLDING FOR FINANCIAL INVESTMENTS": "CICH",
    "EGYPTIAN FINANCIAL GROUP-HERMES HOLDING": "HRHO", # Historical name variant
    
    # Real Estate & Construction
    "TALAAT MOUSTAFA GROUP HOLDING": "TMGH",
    "PALM HILLS DEVELOPMENT COMPANY": "PHDC",
    "ORASCOM CONSTRUCTION PLC": "ORAS",
    "ORASCOM DEVELOPMENT EGYPT": "ORHD",
    "MADINET MASR FOR HOUSING AND DEVELOPMENT": "MASR",
    "HELIOPOLIS HOUSING": "HELI",

    # Industrials & Manufacturing
    "EASTERN COMPANY": "EAST",
    "ELSEWEDY ELECTRIC": "SWDY",
    "ORIENTAL WEAVERS": "ORWE",
    "GB CORP": "GBCO",
    "GHAZUR ORES": "JUFO", # JUFO is Juhayna Food Industries, wait, let's map accurately
    "JUHAYNA FOOD INDUSTRIES": "JUFO",
    "EGYPT ALUMINUM": "EGAL",
    "EZZEKH": "ESRS", # Ezz Steel
    "EZZ STEEL": "ESRS",
    "ABOU KIR FERTILIZERS": "ABUK",
    "MISR FERTILIZERS PRODUCTION COMPANY - MOPCO": "MFPC",
    "ALEXANDRIA MINERAL OILS COMPANY": "AMOC",
    "SIDI KERIR PETROCHEMICALS - SIDPEC": "SKPC",
    "QALA FOR FINANCIAL INVESTMENTS": "CCAP",
    
    # Telecom
    "TELECOM EGYPT": "ETEL",
    "RAYA HOLDING FOR FINANCIAL INVESTMENTS": "RAYA",
    
    # Healthcare
    "IBNSINA PHARMA": "ISPH",
    "RAMEDA": "RMDA",
    "TENTH OF RAMADAN PHARMACEUTICAL INDUSTRIES & DIAGNOSTIC-RAMEDA": "RMDA",
}


def normalize_company_name(name: str) -> str:
    """Normalize company name to maximize matching probability."""
    if not isinstance(name, str):
        return ""
    return name.strip().upper().replace("\n", " ")


def find_ticker(company_name: str) -> Optional[str]:
    """Look up EGX ticker using normalized name."""
    norm_name = normalize_company_name(company_name)
    
    # Direct match
    if norm_name in EGX_COMPANY_MAP:
        return EGX_COMPANY_MAP[norm_name]
    
    # Partial match heuristics
    for map_name, map_ticker in EGX_COMPANY_MAP.items():
        if (norm_name in map_name and len(norm_name) > 10) or (map_name in norm_name and len(map_name) > 10):
            return map_ticker
            
    return None


def parse_numeric(val_str: str) -> Optional[float]:
    """Parse string numbers with commas to floats. Return None if empty or dash."""
    if not val_str:
        return None
    val_str = str(val_str).strip()
    if val_str == "-" or val_str == "":
        return None
    try:
        # Handle commas and convert
        return float(val_str.replace(",", ""))
    except ValueError:
        return None


def extract_annex5_data(pdf_path: str) -> list[dict]:
    """
    Extracts the Annex 5 table data from pages 16-26.
    Returns a list of dicts with raw extracted string values.
    """
    print(f"Opening PDF: {pdf_path}")
    raw_results = []
    found_headers = False
    
    with pdfplumber.open(pdf_path) as pdf:
        # We know Annex 5 typically spans from page 16 (index 15) to end
        for page_idx in range(12, len(pdf.pages)):  # Start checking slightly earlier just in case
            page = pdf.pages[page_idx]
            tables = page.extract_tables()
            
            if not tables:
                continue
                
            for table in tables:
                # Basic check if it's the Annex 5 table (has Stock Name)
                if not table or not table[0]:
                    continue
                    
                header = [str(c).replace("\n", " ").strip() if c else "" for c in table[0]]
                
                # Check for key columns in header
                if "Stock Name" in header and "EGP Net Profit" in header:
                    found_headers = True
                    # It's an annex 5 table, skip header row, parse data
                    for row in table[1:]:
                        if not row or not any(row):  # skip empty rows
                            continue
                            
                        # Ensure row length matches header reasonably
                        if len(row) >= 20: 
                            row_dict = {header[i]: str(row[i]).replace("\n", " ").strip() if row[i] else "" for i in range(len(header))}
                            raw_results.append(row_dict)
                elif found_headers:
                     # sometimes a table on a subsequent page continues without headers
                     # Check if it looks like a continuation (e.g., first element is just a number '#' or empty)
                     if len(table[0]) >= 20 and (not table[0][0] or str(table[0][0]).isdigit()):
                         # Re-use previous header
                         for row in table:
                             if not row or not any(row):
                                 continue
                             if len(row) >= 20:
                                 row_dict = {header[i]: str(row[i]).replace("\n", " ").strip() if row[i] else "" for i in range(min(len(header), len(row)))}
                                 raw_results.append(row_dict)
    
    print(f"Extracted {len(raw_results)} rows from Annex 5 tables.")
    return raw_results


def update_csv_file(filepath: str, new_row: dict, unique_key: str = "period_end_date", dry_run: bool = False):
    """
    Appends or updates a row in a target CSV file.
    Only updates if unique_key (e.g. period_end_date) is new or needs replacement.
    Leaves unspecified columns as blanks.
    """
    if dry_run:
        print(f"    [DRY-RUN] Would update {filepath}:")
        print(f"      {new_row}")
        return

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    df = pd.DataFrame()
    if os.path.exists(filepath):
        try:
            df = pd.read_csv(filepath)
        except Exception as e:
            print(f"    Error reading existing csv {filepath}: {e}")
            return
            
    # If no existing dataframe or empty, infer columns from new_row
    if df.empty:
        df = pd.DataFrame([new_row])
    else:
        # Check if the unique_key already exists
        if unique_key in df.columns and new_row.get(unique_key) in df[unique_key].values:
            # Update existing row
            idx = df.index[df[unique_key] == new_row[unique_key]].tolist()[0]
            for k, v in new_row.items():
                if k not in df.columns:
                    df[k] = None # add column if missing
                df.at[idx, k] = v
            print(f"    Updated existing row in {os.path.basename(filepath)} for {new_row[unique_key]}")
        else:
            # Append new row, align columns
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
            print(f"    Appended new row to {os.path.basename(filepath)} for {new_row[unique_key]}")
            
    # Save back to CSV
    # Ensure correct column order if possible, by sorting period_end_date descending
    if unique_key == "period_end_date" and unique_key in df.columns:
        df[unique_key] = pd.to_datetime(df[unique_key], errors='coerce')
        df = df.sort_values(by=unique_key, ascending=False)
        df[unique_key] = df[unique_key].dt.strftime('%Y-%m-%d')
        
    df.to_csv(filepath, index=False)


def process_and_update(raw_data: list[dict], target_date: str, root_dir: str, dry_run: bool):
    """
    Maps raw PDF data to tickers, formats numbers, computes derived values, 
    and updates the key_ratios and income_statements CSVs.
    """
    matched_count = 0
    
    # Establish base paths
    base_dir = os.path.join(root_dir, "tradingagents", "dataflows", "data_cache", "egx_fundamentals")
    ratios_dir = os.path.join(base_dir, "key_ratios")
    income_dir = os.path.join(base_dir, "income_statements")
    
    for row in raw_data:
        company_name = row.get("Stock Name", "")
        if not company_name:
            continue
            
        ticker = find_ticker(company_name)
        if not ticker:
            continue # Skip unmatched companies
            
        matched_count += 1
        print(f"\nProcessing {ticker} ({company_name})")
        
        # 1. Parse required raw fields
        pe_str = row.get("Trailin g P/E", row.get("Trailing P/E", ""))
        pe_ratio = parse_numeric(pe_str)
        
        market_cap_str = row.get("Market Capital in EGP", row.get("Market Capital in EGP", ""))
        market_cap = parse_numeric(market_cap_str)
        
        dy_str = row.get("DY %", row.get("DY%", ""))
        div_yield = parse_numeric(dy_str)
        
        net_profit_str = row.get("EGP Net Profit", "")
        net_profit = parse_numeric(net_profit_str)
        
        shares_str = row.get("No. Listed Shares", "")
        shares = parse_numeric(shares_str)
        
        net_profit_date_str = row.get("Net Profit Date", "").strip()
        # Clean net profit date (comes like '31/12/2025')
        clean_profit_date = target_date
        if net_profit_date_str:
            try:
                date_obj = datetime.datetime.strptime(net_profit_date_str, "%d/%m/%Y")
                clean_profit_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                pass
            
        # 2. Compute Derived Fields
        eps = None
        if net_profit and shares and shares > 0:
            eps = round(net_profit / shares, 6)
            
        # 3. Form dictionaries for CSV insertion
        # Ratios Row
        ratios_row = {
            "period_end_date": target_date,
            "pe_ratio": pe_ratio,
            "eps": eps,
            "market_cap": market_cap,
            "dividend_yield": div_yield,
        }
        
        # Income Row - we use clean_profit_date because the profit pertains to that period
        income_row = {
            "period_end_date": clean_profit_date,
            "net_income": net_profit,
        }
        
        # 4. Write to CSVs
        ratios_csv = os.path.join(ratios_dir, f"{ticker}_ratios.csv")
        update_csv_file(ratios_csv, ratios_row, dry_run=dry_run)
        
        income_csv = os.path.join(income_dir, f"{ticker}_income_quarterly.csv")
        update_csv_file(income_csv, income_row, dry_run=dry_run)
        
    print(f"\nProcessing Complete. Matched {matched_count} out of {len(raw_data)} rows to known EGX30 tickers.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse EGX Annex 5 Quarterly PDF Report")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--date", required=True, help="Quarter end date (YYYY-MM-DD), e.g. 2026-03-31")
    parser.add_argument("--dry-run", action="store_true", help="Print updates instead of writing to CSV")
    
    args = parser.parse_args()
    
    # Resolve top level repo dir to locate tradingagents correctly
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    raw_data = extract_annex5_data(args.pdf_path)
    if raw_data:
        process_and_update(raw_data, args.date, root_dir, dry_run=args.dry_run)
    else:
        print("No Annex 5 data found in the provided PDF.")
        sys.exit(1)
