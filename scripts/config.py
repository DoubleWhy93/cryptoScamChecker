"""
Central configuration for all scripts.
Put your Etherscan API key in ETHERSCAN_API_KEY.
Get a free key at: https://etherscan.io/register  (5 calls/sec, no other limits)
"""

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
DATA_DIR    = ROOT / "data"
CATS_DIR    = DATA_DIR   # CB.tsv / CE.tsv live in data/ in this repo
OUTPUT_DIR  = ROOT / "output"

BTC_ADDR_DIR = DATA_DIR / "btc" / "addresses"
BTC_TX_DIR   = DATA_DIR / "btc" / "txs"
ETH_ADDR_DIR = DATA_DIR / "eth" / "addresses"
ETH_TX_DIR   = DATA_DIR / "eth" / "txs"

GRAPHS_DIR  = OUTPUT_DIR / "graphs"
REPORTS_DIR = OUTPUT_DIR / "reports"

# ── Source files ───────────────────────────────────────────────────────────────
CB_TSV = CATS_DIR / "CB.tsv"   # criminal Bitcoin
CE_TSV = CATS_DIR / "CE.tsv"   # criminal Ethereum
BB_TSV = CATS_DIR / "BB.tsv"   # benign Bitcoin
BE_TSV = CATS_DIR / "BE.tsv"   # benign Ethereum

# ── API ────────────────────────────────────────────────────────────────────────
MEMPOOL_BASE    = "https://mempool.space/api"
ETHERSCAN_BASE  = "https://api.etherscan.io/api"
ETHERSCAN_API_KEY = ""          # paste your free key here — https://etherscan.io/register

# Google AI Studio — get a free key at https://aistudio.google.com/apikey
# Can also be set via GOOGLE_API_KEY environment variable (preferred)
GOOGLE_API_KEY = ""
GEMMA_MODEL    = "gemma-3-27b-it"   # or "gemma-3-4b-it" (faster) / "gemini-2.0-flash"

# ── Rate limits (seconds between requests) ────────────────────────────────────
BTC_DELAY = 0.5   # mempool.space: generous, but be polite
ETH_DELAY = 0.25  # Etherscan free: 5 req/sec max → 0.2s; use 0.25 for safety

# ── Fetch limits per address ───────────────────────────────────────────────────
MAX_TX_PAGES = 10   # max mempool.space pagination pages (25 tx/page = 250 txs)
ETH_TX_OFFSET = 200 # max txs per Etherscan call

# ── Flow analysis thresholds ───────────────────────────────────────────────────
# Amounts in satoshis for BTC, wei for ETH
LARGE_TX_SATOSHI  = 10_000       # 0.0001 BTC ≈ $6  — "large" for a criminal wallet
LARGE_TX_WEI      = 10**16       # 0.01 ETH ≈ $20

# Min USD value to flag a transaction as "significant"
SIGNIFICANT_USD = 50.0
