"""Inspect what's stored in ChromaDB agent-memory collections.

Usage:
    python scripts/inspect_memory.py                          # list all collections + counts
    python scripts/inspect_memory.py --collection bull_memory # dump all rows in a collection
    python scripts/inspect_memory.py --query "EGX bank earnings beat" --collection bull_memory
    python scripts/inspect_memory.py --query "..." --top 5    # semantic search across one collection
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure repo root is importable when run from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import chromadb
from dotenv import load_dotenv

load_dotenv()

COLLECTIONS = [
    "bull_memory",
    "bear_memory",
    "trader_memory",
    "invest_judge_memory",
    "risk_manager_memory",
]


def truncate(s, n=80):
    if not s:
        return ""
    s = str(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def list_collections(client, persist_dir):
    print(f"\nChroma dir: {persist_dir}\n")
    print(f"{'COLLECTION':<22} {'ROWS':>6}")
    print("-" * 30)
    for name in COLLECTIONS:
        try:
            col = client.get_or_create_collection(name)
            print(f"{name:<22} {col.count():>6}")
        except Exception as e:
            print(f"{name:<22} ERROR: {e}")
    print()


def dump_collection(client, name, limit=20):
    col = client.get_or_create_collection(name)
    n = col.count()
    print(f"\n=== {name}  ({n} rows) ===\n")
    if n == 0:
        print("(empty)\n")
        return
    data = col.get(limit=limit, include=["documents", "metadatas"])
    for i, (doc_id, doc, meta) in enumerate(
        zip(data["ids"], data["documents"], data["metadatas"])
    ):
        meta = meta or {}
        print(f"[{i+1}] id={doc_id}")
        print(f"    ticker     : {meta.get('ticker', '-')}")
        print(f"    trade_date : {meta.get('trade_date', '-')}")
        print(f"    memory_type: {meta.get('memory_type', '-')}")
        print(f"    confidence : {meta.get('confidence', '-')}")
        print(f"    outcome    : {truncate(meta.get('outcome'), 90)}")
        print(f"    situation  : {truncate(doc, 120)}")
        print(f"    recommend  : {truncate(meta.get('recommendation'), 120)}")
        print()
    if n > limit:
        print(f"... ({n - limit} more rows not shown — pass --limit {n} to see all)\n")


def query_collection(client, name, query, top=3):
    """Use the project's FinancialSituationMemory so we exercise the real
    embedding path (Ollama) and similarity scoring."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.agents.utils.memory import FinancialSituationMemory

    mem = FinancialSituationMemory(name, DEFAULT_CONFIG)
    print(f"\n=== query '{query}' in {name} (top {top}) ===\n")
    print(f"embeddings_enabled = {mem.embeddings_enabled}, model = {mem.embedding}\n")
    results = mem.get_memories(query, n_matches=top, min_similarity=0.0)
    if not results:
        print("(no matches)\n")
        return
    for i, r in enumerate(results, 1):
        print(f"[{i}] similarity={r['similarity_score']:.3f}")
        print(f"    situation : {truncate(r['matched_situation'], 140)}")
        print(f"    recommend : {truncate(r['recommendation'], 140)}")
        meta = r.get("metadata", {})
        if meta:
            print(f"    metadata  : {meta}")
        print()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--collection", "-c", help="Name of collection to dump or query")
    p.add_argument("--query", "-q", help="Semantic search query (requires --collection)")
    p.add_argument("--top", "-k", type=int, default=3, help="Top-K matches for --query")
    p.add_argument("--limit", "-l", type=int, default=20, help="Row limit for dump mode")
    p.add_argument(
        "--persist-dir",
        default=os.environ.get("CHROMA_PERSIST_DIR", "./chroma_db"),
        help="Path to ChromaDB directory (default: ./chroma_db)",
    )
    args = p.parse_args()

    client = chromadb.PersistentClient(path=args.persist_dir)

    if args.query:
        if not args.collection:
            print("--query requires --collection (e.g. bull_memory)")
            sys.exit(2)
        query_collection(client, args.collection, args.query, top=args.top)
    elif args.collection:
        dump_collection(client, args.collection, limit=args.limit)
    else:
        list_collections(client, args.persist_dir)


if __name__ == "__main__":
    main()
