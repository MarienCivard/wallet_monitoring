# streamlit_app.py — CLEAN v2
# Minimal, testable skeleton to monitor yield/borrow/costs across multiple wallets
# Demo defaults to the sample address provided by the user: 0xCCeE77e74C4466DF0dA0ec85F2D3505956fD6Fa7
# -----------------------------------------------------------------------------
# What works out-of-the-box:
# - Multi-wallet input (regex-based parsing, robust to line breaks)
# - Morpho Blue positions (GraphQL Blue API, no API key): supply/borrow/collateral by market (token + USD)
# - Zapper (optional API key): recent signer transactions + gas cost (native); graceful error messages
# - Clean error handling & no-crash warnings
# - Pendle panel present as a stub (wire the REST endpoint later)
#
# Run locally:
#   1) pip install streamlit requests pandas python-dateutil python-dotenv
#   2) export ZAPPER_API_KEY=your_key   (optional)
#   3) streamlit run streamlit_app.py
# -----------------------------------------------------------------------------

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
import pandas as pd
from dateutil import tz
import streamlit as st

# Optional dotenv support (no hard dependency)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
DEFAULT_WALLETS = [
    "0xCCeE77e74C4466DF0dA0ec85F2D3505956fD6Fa7",
]
TIMEZONE = "Europe/Paris"
MORPHO_GRAPHQL = "https://blue-api.morpho.org/graphql"  # Blue API endpoint
ZAPPER_GQL = "https://public.zapper.xyz/graphql"
ZAPPER_API_KEY = os.getenv("ZAPPER_API_KEY") or (st.secrets["ZAPPER_API_KEY"] if "ZAPPER_API_KEY" in st.secrets else None)

# We check these chains for Morpho Blue support (you can extend)
CHAIN_IDS = [1, 8453, 42161]  # Ethereum, Base, Arbitrum

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def to_local(ts_ms: int, tzname: str = TIMEZONE) -> str:
    dt = datetime.utcfromtimestamp(ts_ms / 1000)
    return dt.replace(tzinfo=tz.UTC).astimezone(tz.gettz(tzname)).strftime("%Y-%m-%d %H:%M")

@st.cache_data(ttl=300)
def morpho_user_overview(address: str, chain_id: int = 1) -> Dict[str, Any]:
    """Query Morpho Blue user data on a given chain.
    - Uses Blue API endpoint
    - `state{}` returns user-level amounts (supply/borrow/collateral) per market
    - Treats NOT_FOUND gracefully as empty
    """
    q = f"""
    query {{
      userByAddress(chainId: {chain_id}, address: \"{address}\") {{
        address
        marketPositions {{
          market {{ uniqueKey loanAsset {{ symbol }} collateralAsset {{ symbol }} }}
          state {{
            supplyAssets
            supplyAssetsUsd
            borrowAssets
            borrowAssetsUsd
            collateral
            collateralUsd
          }}
        }}
        transactions {{ hash timestamp type }}
      }}
    }}
    """
    r = requests.post(MORPHO_GRAPHQL, json={"query": q}, timeout=30)
    # Parse
    try:
        payload = r.json()
    except Exception:
        raise RuntimeError(f"Morpho API error [{r.status_code}]: {r.text[:200]}")
    # Errors (e.g., NOT_FOUND)
    if "errors" in payload:
        msgs = ", ".join([e.get("message", "") for e in payload["errors"]])
        if "NOT_FOUND" in msgs or "No results matching" in msgs:
            return {"address": address, "marketPositions": [], "transactions": []}
        raise RuntimeError(f"Morpho API error [{r.status_code}]: {payload['errors']}")
    data = payload.get("data", {}).get("userByAddress")
    return data or {"address": address, "marketPositions": [], "transactions": []}

@st.cache_data(ttl=300)
def zapper_tx_history(addresses: List[str], first: int = 20, chain_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    if not ZAPPER_API_KEY:
        return {}
    query = """
    query TransactionHistoryV2($subjects: [Address!]!, $perspective: TransactionHistoryV2Perspective, $first: Int, $filters: TransactionHistoryV2FiltersArgs) {
      transactionHistoryV2(subjects: $subjects, perspective: Signer, first: $first, filters: $filters) {
        edges { node { ... on TimelineEventV2 { transaction { hash network timestamp } } } }
      }
    }
    """
    variables = {"subjects": addresses, "first": first, "filters": {}}
    if chain_ids:
        variables["filters"]["chainIds"] = chain_ids
    headers = {"Content-Type": "application/json", "x-zapper-api-key": ZAPPER_API_KEY, "User-Agent": "WalletMonitor/1.0"}
    r = requests.post(ZAPPER_GQL, json={"query": query, "variables": variables}, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("data", {})

@st.cache_data(ttl=300)
def zapper_tx_details(tx_hash: str, chain_id: int) -> Dict[str, Any]:
    if not ZAPPER_API_KEY:
        return {}
    query = """
    query TransactionDetailsV2($hash: String!, $chainId: Int!) {
      transactionDetailsV2(hash: $hash, chainId: $chainId) {
        transaction { hash gasPrice gas gasUsed blockNumber timestamp }
      }
    }
    """
    headers = {"Content-Type": "application/json", "x-zapper-api-key": ZAPPER_API_KEY, "User-Agent": "WalletMonitor/1.0"}
    r = requests.post(ZAPPER_GQL, json={"query": query, "variables": {"hash": tx_hash, "chainId": chain_id}}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", {})
    items = data.get("transactionDetailsV2", [])
    return items[0] if items else {}

@st.cache_data(ttl=300)
def pendle_user_positions_stub(address: str) -> List[Dict[str, Any]]:
    # Placeholder; wire to Pendle REST "Portfolio Positions" when ready
    return []

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="DeFi Multi‑Wallet Monitor (Skeleton)", layout="wide")
st.title("🧭 DeFi Multi‑Wallet Monitor — Skeleton (CLEAN v2)")
st.caption("Demo ready. Add your API keys later to unlock more panels.")

with st.sidebar:
    st.header("⚙️ Settings")
    wallets_text = st.text_area("Wallets (one per line)", value="\n".join(DEFAULT_WALLETS), height=120)
    # Robust parsing: extract 0x-prefixed 40-hex addresses anywhere in the text
    import re
    wallets = re.findall(r"0x[a-fA-F0-9]{40}", wallets_text)
    use_morpho = st.checkbox("Fetch Morpho positions", value=True)
    use_zapper = st.checkbox("Fetch tx & gas via Zapper (API key)", value=bool(ZAPPER_API_KEY))
    chains_sel = st.multiselect("Chains for txs", options=[1, 8453, 42161], default=[1, 42161])
    st.markdown("—")
    st.write("Timezone:", TIMEZONE)

# Top KPIs
col1, col2, col3, col4 = st.columns(4)
col1.metric("Wallets", len(wallets))
col2.metric("Zapper key", "✓" if ZAPPER_API_KEY else "—")
now_str = datetime.now(tz.gettz(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
col3.metric("Now", now_str)
col4.metric("Chains (tx)", ", ".join(map(str, chains_sel)) if chains_sel else "all")

st.divider()

# Per‑wallet sections
for addr in wallets:
    st.subheader(f"👛 {addr}")

    # Tabs
    if use_morpho:
        tabs = st.tabs(["Morpho", "Transactions", "Pendle (stub)"])
    else:
        tabs = st.tabs(["Transactions", "Pendle (stub)"])

    # Morpho
    if use_morpho:
        with tabs[0]:
            morpho_rows = []
            total_supply_usd = 0.0
            total_borrow_usd = 0.0
            total_collateral_usd = 0.0
            for chain in CHAIN_IDS:
                try:
                    data = morpho_user_overview(addr, chain)
                    for p in data.get("marketPositions", []):
                        stt = p.get("state", {}) or {}
                        row = {
                            "chainId": chain,
                            "marketKey": (p.get("market") or {}).get("uniqueKey"),
                            "loan": (p.get("market") or {}).get("loanAsset", {}).get("symbol"),
                            "collateral": (p.get("market") or {}).get("collateralAsset", {}).get("symbol"),
                            "supplyAssets": float(stt.get("supplyAssets") or 0),
                            "supplyUsd": float(stt.get("supplyAssetsUsd") or 0),
                            "borrowAssets": float(stt.get("borrowAssets") or 0),
                            "borrowUsd": float(stt.get("borrowAssetsUsd") or 0),
                            "collateralAmt": float(stt.get("collateral") or 0),
                            "collateralUsd": float(stt.get("collateralUsd") or 0),
                        }
                        total_supply_usd += row["supplyUsd"]
                        total_borrow_usd += row["borrowUsd"]
                        total_collateral_usd += row["collateralUsd"]
                        morpho_rows.append(row)
                except RuntimeError as e:
                    st.info(f"Morpho: {e}")
                except Exception as e:
                    st.warning(f"Morpho query failed on chain {chain}: {e}")
            left, right = st.columns([2,1])
            with left:
                if morpho_rows:
                    df = pd.DataFrame(morpho_rows)
                    cols = [
                        "chainId", "marketKey", "loan", "collateral",
                        "supplyAssets", "supplyUsd", "borrowAssets", "borrowUsd",
                        "collateralAmt", "collateralUsd",
                    ]
                    df = df[[c for c in cols if c in df.columns]]
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("No Morpho positions detected (or address inactive on selected chains).")
            with right:
                st.metric("Supply USD", f"{total_supply_usd:,.2f}")
                st.metric("Borrow USD", f"{total_borrow_usd:,.2f}")
                st.metric("Collateral USD", f"{total_collateral_usd:,.2f}")
                st.metric("Net (Supply−Borrow)", f"{(total_supply_usd-total_borrow_usd):,.2f}")

    # Transactions + gas (Zapper)
    tx_tab_index = 1 if use_morpho else 0
    with tabs[tx_tab_index]:
        if not ZAPPER_API_KEY:
            st.info("Provide ZAPPER_API_KEY to enable tx & gas computations.")
        else:
            try:
                data = zapper_tx_history([addr], first=20, chain_ids=chains_sel)
                edges = (data.get("transactionHistoryV2", {}) or {}).get("edges", [])
            except requests.HTTPError as he:
                st.warning(f"Zapper API HTTP error: {he}")
                edges = []
            except Exception as e:
                st.warning(f"Zapper API error: {e}")
                edges = []
            if not edges:
                st.info("No recent signer transactions found (or no access on selected chains).")
            else:
                rows = []
                total_gas_native = 0.0
                network_to_chain = {
                    "ETHEREUM_MAINNET": 1,
                    "ARBITRUM_MAINNET": 42161,
                    "BASE_MAINNET": 8453,
                    "POLYGON_POS": 137,
                }
                for e in edges:
                    node = e.get("node", {})
                    tx = node.get("transaction", {})
                    tx_hash = tx.get("hash")
                    network = tx.get("network")
                    ts = tx.get("timestamp")
                    chain_id = network_to_chain.get(network, 1)
                    details = zapper_tx_details(tx_hash, chain_id)
                    t = details.get("transaction", {})
                    gas_price_wei = int(t.get("gasPrice") or 0)
                    gas_used = int(t.get("gasUsed") or t.get("gas") or 0)
                    gas_cost_wei = gas_price_wei * gas_used
                    gas_cost_native = gas_cost_wei / 1e18
                    total_gas_native += gas_cost_native
                    rows.append({
                        "hash": tx_hash,
                        "network": network,
                        "time": to_local(ts) if isinstance(ts, (int, float)) else str(ts),
                        "gas_used": gas_used,
                        "gas_price_wei": gas_price_wei,
                        "gas_cost_native": gas_cost_native,
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
                st.metric("Sum gas (native)", f"{total_gas_native:.6f}")

    # Pendle (stub)
    pendle_tab_index = 2 if use_morpho else 1
    with tabs[pendle_tab_index]:
        pos = pendle_user_positions_stub(addr)
        st.caption("Wire this panel to Pendle's Portfolio Positions REST endpoint.")
        if pos:
            st.dataframe(pd.DataFrame(pos), use_container_width=True)
        else:
            st.info("No Pendle data (stub). Add the API call to fetch PT/YT/LP holdings & implied APY.")

    st.divider()

st.markdown(
    """
    ### Next steps
    1. **Pendle**: Replace the stub with a call to the *User Analytics → Portfolio Positions* endpoint to fetch PT/YT/LP balances + implied APY, then compute **remaining fixed yield** until maturity.
    2. **Net APY**: Accrue **borrow interest** (Morpho) and net it against **Pendle PT yield**; display a **net APY** KPI.
    3. **Alerts**: Add HF < 1.30 and **PT maturity < N days** alerts (Slack/Telegram webhooks).
    4. **Prices**: Convert gas/native to USD via a pricing API and add PnL.
    5. **Persistence**: Push snapshots into Postgres (`wallets`, `positions`, `cashflows`, `pnl_daily`) for history charts.
    """
)
