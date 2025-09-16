# app.py — DeFi Multi‑Wallet Monitor (full clean script)
# Streamlit dashboard to monitor yield/borrow/collateral across multiple wallets
# Supports: Morpho Blue (user positions), Zapper (optional tx + gas), Pendle (stub)
# -----------------------------------------------------------------------------
# Quick start (local):
#   pip install streamlit requests pandas python-dateutil python-dotenv
#   export ZAPPER_API_KEY=your_key   # optional
#   streamlit run app.py
# -----------------------------------------------------------------------------

import os
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
MORPHO_GRAPHQL = "https://blue-api.morpho.org/graphql"   # Morpho Blue API
ZAPPER_GQL = "https://public.zapper.xyz/graphql"
ZAPPER_API_KEY = os.getenv("ZAPPER_API_KEY") or (st.secrets["ZAPPER_API_KEY"] if "ZAPPER_API_KEY" in st.secrets else None)

# Chains to check on Morpho Blue (extend as needed)
CHAIN_IDS = [1, 8453, 42161]  # Ethereum, Base, Arbitrum

# HTTP defaults
HTTP_HEADERS = {"Content-Type": "application/json", "User-Agent": "DeFiWalletMonitor/1.0"}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def to_local(ts_ms: int, tzname: str = TIMEZONE) -> str:
    try:
        dt = datetime.utcfromtimestamp(ts_ms / 1000)
        return dt.replace(tzinfo=tz.UTC).astimezone(tz.gettz(tzname)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts_ms)

@st.cache_data(ttl=300)
def _run_morpho_query(query: str) -> Dict[str, Any]:
    r = requests.post(MORPHO_GRAPHQL, json={"query": query}, headers=HTTP_HEADERS, timeout=30)
    try:
        payload = r.json()
    except Exception:
        raise RuntimeError(f"Morpho API error [{r.status_code}]: {r.text[:200]}")
    return payload

@st.cache_data(ttl=300)
@st.cache_data(ttl=300)
def morpho_user_positions(address: str, chain_id: int) -> Dict[str, Any]:
    """
    User positions from Morpho Blue (par utilisateur, pas totaux de pool).
    On interroge userByAddress et on lit directement les champs user-level.
    """
    q1 = f"""
    query {{
      userByAddress(chainId: {chain_id}, address: "{address}") {{
        address
        marketPositions {{
          market {{
            uniqueKey
            whitelisted
            loanAsset {{ symbol decimals }}
            collateralAsset {{ symbol decimals }}
          }}
          borrowAssets
          borrowAssetsUsd
          supplyAssets
          supplyAssetsUsd
        }}
      }}
    }}
    """
    payload = _run_morpho_query(q1)

    # Gestion propre des erreurs
    if "errors" in payload:
        msgs = ", ".join(e.get("message", "") for e in payload.get("errors", []))
        if "NOT_FOUND" in msgs or "No results matching" in msgs:
            return {"address": address, "marketPositions": []}
        # remonte l'erreur lisiblement
        raise RuntimeError(f"Morpho API error: {payload['errors']}")

    data = (payload.get("data") or {}).get("userByAddress") or {}
    return data or {"address": address, "marketPositions": []}


@st.cache_data(ttl=300)
def morpho_collateral_map(address: str, chain_id: int) -> Dict[str, Dict[str, float]]:
    """Fetch per-market collateral for a given user via marketPositions list query.
    Returns { uniqueKey: { collateral, collateralUsd } }.
    If chain filter is unsupported by API version, we fallback without it.
    """
    where_filters = f"where: {{ userAddress_in: [\"{address}\"], chainId_in: [{chain_id}] }}"
    q = f"""
    query {{
      marketPositions(first: 300, {where_filters}) {{
        items {{ market {{ uniqueKey }} state {{ collateral collateralUsd }} }}
      }}
    }}
    """
    payload = _run_morpho_query(q)
    if "errors" in payload:
        # Fallback: remove chain filter
        q2 = f"""
        query {{
          marketPositions(first: 300, where: {{ userAddress_in: [\"{address}\"] }}) {{
            items {{ market {{ uniqueKey }} state {{ collateral collateralUsd }} }}
          }}
        }}
        """
        payload = _run_morpho_query(q2)
    items = (((payload or {}).get("data") or {}).get("marketPositions") or {}).get("items", [])
    out: Dict[str, Dict[str, float]] = {}
    for it in items:
        mk = (it.get("market") or {}).get("uniqueKey")
        stt = it.get("state") or {}
        if mk:
            out[mk] = {
                "collateral": float(stt.get("collateral") or 0),
                "collateralUsd": float(stt.get("collateralUsd") or 0),
            }
    return out

# -------------------------- Zapper (optional) ---------------------------------
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
    headers = {**HTTP_HEADERS, "x-zapper-api-key": ZAPPER_API_KEY}
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
    headers = {**HTTP_HEADERS, "x-zapper-api-key": ZAPPER_API_KEY}
    r = requests.post(ZAPPER_GQL, json={"query": query, "variables": {"hash": tx_hash, "chainId": chain_id}}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", {})
    items = data.get("transactionDetailsV2", [])
    return items[0] if items else {}

# ----------------------------- Pendle (stub) ----------------------------------
@st.cache_data(ttl=300)
def pendle_user_positions_stub(address: str) -> List[Dict[str, Any]]:
    # Wire this to Pendle REST "Portfolio Positions" to fetch PT/YT/LP + implied APY
    return []

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="DeFi Multi‑Wallet Monitor", layout="wide")
st.title("🧭 DeFi Multi‑Wallet Monitor — Full Clean Script")
st.caption("Demo ready. Add your API keys later to unlock more panels.")

with st.sidebar:
    st.header("⚙️ Settings")
    default_wallets_value = "\n".join(DEFAULT_WALLETS)
    wallets_text = st.text_area("Wallets (one per line)", value=default_wallets_value, height=120)
    # Robust parsing: extract any 0x… addresses present
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

# Per-wallet sections
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
            morpho_rows: List[Dict[str, Any]] = []
            total_supply_usd = 0.0
            total_borrow_usd = 0.0
            total_collateral_usd = 0.0
            debug_msgs = []

            include_untrusted = st.toggle("Show non-whitelisted markets (risk of bad pricing)", value=False)

            for chain in CHAIN_IDS:
                try:
                    data = morpho_user_positions(addr, chain)
                    coll_map = {}
                    try:
                        coll_map = morpho_collateral_map(addr, chain)
                    except Exception as e:
                        debug_msgs.append(f"Collateral map failed on chain {chain}: {e}")
                        coll_map = {}

                    for it in data.get("marketPositions", []):
                        m = it.get("market") or {}
                        if not include_untrusted and m.get("whitelisted") is False:
                            continue

                        s_usd = float(it.get("supplyAssetsUsd") or 0)
                        b_usd = float(it.get("borrowAssetsUsd") or 0)
                        mk = m.get("uniqueKey")
                        coll = coll_map.get(mk, {}) if mk else {}
                        c_usd = float(coll.get("collateralUsd") or 0)

                        if max(s_usd, b_usd, c_usd) > 1e11:
                            debug_msgs.append(
                                f"Skipped market {mk} on chain {chain} due to abnormal USD value: "
                                f"borrowUsd={b_usd}, supplyUsd={s_usd}, collateralUsd={c_usd}"
                            )
                            continue

                        row = {
                            "chainId": chain,
                            "marketKey": mk,
                            "loan": (m.get("loanAsset") or {}).get("symbol"),
                            "collateralAsset": (m.get("collateralAsset") or {}).get("symbol"),
                            "supplyAssets": float(it.get("supplyAssets") or 0),
                            "supplyUsd": s_usd,
                            "borrowAssets": float(it.get("borrowAssets") or 0),
                            "borrowUsd": b_usd,
                            "collateralAmt": float(coll.get("collateral") or 0),
                            "collateralUsd": c_usd,
                        }
                        total_supply_usd += row["supplyUsd"]
                        total_borrow_usd += row["borrowUsd"]
                        total_collateral_usd += row["collateralUsd"]
                        morpho_rows.append(row)

                except RuntimeError as e:
                    st.info(f"Morpho: {e}")
                except Exception as e:
                    st.warning(f"Morpho query failed on chain {chain}: {e}")

            left, right = st.columns([2, 1])
            with left:
                if morpho_rows:
                    df = pd.DataFrame(morpho_rows)
                    cols = [
                        "chainId", "marketKey", "loan", "collateralAsset",
                        "supplyAssets", "supplyUsd", "borrowAssets", "borrowUsd",
                        "collateralAmt", "collateralUsd",
                    ]
                    df = df[[c for c in cols if c in df.columns]]
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("No Morpho positions detected (or all filtered as untrusted/aberrant).")

                if debug_msgs:
                    with st.expander("Debug log (Morpho)"):
                        for m in debug_msgs:
                            st.code(m)

            with right:
                st.metric("Supply USD", f"{total_supply_usd:,.2f}")
                st.metric("Borrow USD", f"{total_borrow_usd:,.2f}")
                st.metric("Collateral USD", f"{total_collateral_usd:,.2f}")
                st.metric("Net (Supply−Borrow)", f"{(total_supply_usd - total_borrow_usd):,.2f}")

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
    1. **Pendle**: Replace the stub with a call to the *Portfolio Positions* endpoint (PT/YT/LP + implied APY), then compute **remaining fixed yield** until maturity.
    2. **Net APY**: Accrue **borrow interest** (Morpho) vs **Pendle PT yield**; show **Net APY**.
    3. **Alerts**: HF < 1.30 and **PT maturity < N days** (Slack/Telegram webhooks).
    4. **Prices**: Convert gas/native to USD via a pricing API and add PnL.
    5. **Persistence**: Store snapshots to Postgres (`wallets`, `positions`, `cashflows`, `pnl_daily`) for history charts.
    """
)
