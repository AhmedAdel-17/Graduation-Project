"""
EGX ticker catalogue used by:
  - scripts/test_random_egx.py (CLI)
  - server/api_server.py /api/test/random-egx and /api/test/egx-tickers

Kept minimal and dependency-free so it can be imported from anywhere in the
project without pulling in heavy analysis modules at import time.
"""

EGX_TICKERS = [
    "COMI.CA",   # Commercial International Bank
    "EAST.CA",   # Eastern Company
    "FWRY.CA",   # Fawry
    "TMGH.CA",   # Talaat Moustafa Group
    "HRHO.CA",   # EFG Hermes Holding
    "ETEL.CA",   # Telecom Egypt
    "ABUK.CA",   # Abou Kir Fertilizers
    "ADIB.CA",   # Abu Dhabi Islamic Bank (Egypt)
    "EFIH.CA",   # EFG Finance Holding
    "EGAL.CA",   # Egypt Aluminum
    "MFPC.CA",   # Misr Fertilizers (MOPCO)
    "CCAP.CA",   # Citadel Capital
    "SKPC.CA",   # Sidi Kerir Petrochemicals
    "AMOC.CA",   # Alexandria Mineral Oils
    "ESRS.CA",   # Ezz Steel
    "ORWE.CA",   # Oriental Weavers
    "HELI.CA",   # Heliopolis Housing
    "GBCO.CA",   # GB Corp
    "SWDY.CA",   # Elsewedy Electric
    "ORAS.CA",   # Orascom Construction
    "PHDC.CA",   # Palm Hills Developments
    "CIEB.CA",   # Credit Agricole Egypt
    "ISPH.CA",   # Ibnsina Pharma
    "DSCW.CA",   # Dice Sport & Casual Wear
    "RMDA.CA",   # Tenth of Ramadan Pharmaceutical
    "ARCC.CA",   # Arabia Investments Holding
    "BTFH.CA",   # Beltone Financial Holding
    "JUFO.CA",   # Juhayna Food Industries
    "ORHD.CA",   # Orascom Development Egypt
    "RAYA.CA",   # Raya Holding
    "VLMR.CA",   # Valoria
]
