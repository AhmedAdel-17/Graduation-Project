"""
EGX Trading-Signal Sentiment Pipeline v2.

v2 turns the v1 "scrape + classify relevant + sentiment" flow into a
layered trading-signal engine:

    scrape -> relevance gate -> entity extraction -> intent detection
           -> content-type classification -> sentiment -> weighted aggregation
           -> per-stock signal + market signal

Each stage is a small, independently-testable module. Sources are pluggable.
"""
