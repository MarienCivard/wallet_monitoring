# app.py — DeFi Multi‑Wallet Monitor (STRICT per‑wallet + USD recompute)
# Streamlit dashboard to monitor supply / borrow / collateral for one or more wallets
# Morpho Blue (strict per-user via filtered list query), Zapper (optional), Pendle (stub)
# PLUS: robust unit handling + optional USD recompute from token prices (DefiLlama)
# -----------------------------------------------------------------------------
# Quick start:
#   pip install streamlit requests pandas python-dateutil python-dotenv
#   export ZAPPER_API_KEY=your_key   # optional
#   streamlit run app.py
# -----------------------------------------------------------------------------

import os
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
import pandas as pd
from dateutil import tz
from decimal import Decimal, InvalidOperation, getcontext
import streamlit as st

# Optional dotenv support
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
MORPHO_GRAPHQL = "https://api.morpho.org/graphql"   # Official Morpho GraphQL endpoint
ZAPPER_GQL = "https://public.zapper.xyz/graphql"
ZAPPER_API_KEY = os.getenv("ZAPPER_API_KEY") or (st.secrets["ZAPPER_API_KEY"] if "ZAPPER_API_KEY" in st.secrets else None)

CHAIN_OPTIONS = [1, 8453, 42161]  # Ethereum, Base, Arbitrum
HTTP_HEADERS = {"Content-Type": "application/json", "User-Agent": "DeFiWalletMonitor/1.0"}

# Decimal math setup
getcontext().prec = 50
CHAIN_SLUG = {1: "ethereum", 8453: "base", 42161: "arbitrum"}

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
def _run_graphql(url: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(url, json=payload, headers=HTTP_HEADERS, timeout=30)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"GraphQL error [{r.status_code}]: {r.text[:200]}")
    return data

# -----------------------------------------------------------------------------
# Morpho — STRICT per-user positions via filtered list query (no pool totals)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def morpho_user_positions(address: str, chain_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Return ONLY the positions for the given wallet, with user-level amounts in state{...}.
    Uses marketPositions(where: { userAddress_in: [...], chainId_in: [...] }).
    """
    chains_clause = ""
    if chain_ids:
        uniq = ",".join(str(int(c)) for c in sorted(set(chain_ids)))
        chains_clause = f", chainId_in: [{uniq}]"

    # Use .format with doubled braces to avoid f-string conflicts with GraphQL braces
    query_tpl = """
    query {{
      marketPositions(
        first: 300,
        where: {{ userAddress_in: ["{address}"]{chains_clause} }}
      ) {{
        items {{
          market {{
            uniqueKey
            whitelisted
            chainId
            loanAsset {{ symbol address decimals }}
            collateralAsset {{ symbol address decimals }}
          }}
          user {{ address }}
          state {{
            supplyAssets
            supplyAssetsUsd
            borrowAssets
            borrowAssetsUsd
            collateral
            collateralUsd
          }}
        }}
      }}
    }}
    """
    q = query_tpl.format(address=address, chains_clause=chains_clause)

    payload = _run_graphql(MORPHO_GRAPHQL, q)

    if "errors" in payload:
        msgs = ", ".join([e.get("message", "") for e in payload.get("errors", [])])
        if "NOT_FOUND" in msgs or "No results matching" in msgs:
            return []
        raise RuntimeError(f"Morpho API error: {payload['errors']}")

    items = (((payload or {}).get("data") or {}).get("marketPositions") or {}).get("items", [])
    # Paranoïa : re-filtre par user au cas où l’API ignorerait 'where'
    items = [it for it in items if (it.get("user") or {}).get("address", "").lower() == address.lower()]

    # Dé-dup par marketKey (rare, mais ça évite un double comptage visuel)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        mk = ((it.get("market") or {}).get("uniqueKey"))
        if mk and mk in seen:
            continue
        seen.add(mk)
        deduped.append(it)
    return deduped

# -----------------------------------------------------------------------------
# Zapper (optional)
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Pendle (stub)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def pendle_user_positions_stub(address: str) -> List[Dict[str, Any]]:
    return []

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="DeFi Multi‑Wallet Monitor", layout="wide")
st.title("🧭 DeFi Multi‑Wallet Monitor — STRICT per‑wallet + USD fix")

with st.sidebar:
    st.header("⚙️ Settings")
    default_wallets_value = "\n".join(DEFAULT_WALLETS)
    wallets_text = st.text_area("Wallets (one per line)", value=default_wallets_value, height=120)
    wallets = re.findall(r"0x[a-fA-F0-9]{40}", wallets_text)

    use_morpho = st.checkbox("Fetch Morpho positions", value=True)
    morpho_chain_sel = st.multiselect("Morpho chains", options=CHAIN_OPTIONS, default=[1])
    recompute_usd = st.checkbox("Recompute USD from token prices (DefiLlama)", value=True)

    use_zapper = st.checkbox("Fetch tx & gas via Zapper (API key)", value=bool(ZAPPER_API_KEY))
    tx_chain_sel = st.multiselect("Chains for txs", options=CHAIN_OPTIONS, default=[1, 42161])

    st.markdown("—")
    st.write("Timezone:", TIMEZONE)

# KPIs
col1, col2, col3, col4 = st.columns(4)
col1.metric("Wallets", len(wallets))
col2.metric("Zapper key", "✓" if ZAPPER_API_KEY else "—")
now_str = datetime.now(tz.gettz(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
col3.metric("Now", now_str)
col4.metric("Chains (tx)", ", ".join(map(str, tx_chain_sel)) if tx_chain_sel else "all")

st.divider()

# Per-wallet sections
for addr in wallets:
    st.subheader(f"👛 {addr}")

    if use_morpho:
        tabs = st.tabs(["Morpho", "Transactions", "Pendle (stub)"])
    else:
        tabs = st.tabs(["Transactions", "Pendle (stub)"])

    # Morpho
    if use_morpho:
        with tabs[0]:
            morpho_rows: List[Dict[str, Any]] = []
            total_supply_usd = Decimal(0)
            total_borrow_usd = Decimal(0)
            total_collateral_usd = Decimal(0)
            debug_msgs: List[str] = []

            include_untrusted = st.toggle("Show non‑whitelisted markets (risk of bad pricing)", value=False)

            try:
                items = morpho_user_positions(addr, morpho_chain_sel)

                # Build price query set if recompute_usd
                price_keys = set()
                if recompute_usd:
                    for it in items:
                        m = it.get("market") or {}
                        chain_id = m.get("chainId")
                        for side in ("loanAsset", "collateralAsset"):
                            a = (m.get(side) or {})
                            addr_tok = (a.get("address") or "").lower()
                            if chain_id in CHAIN_SLUG and addr_tok:
                                price_keys.add(f"{CHAIN_SLUG[chain_id]}:{addr_tok}")
                prices = {}
                if recompute_usd and price_keys:
                    try:
                        url = "https://coins.llama.fi/prices/current/" + ",".join(sorted(price_keys))
                        resp = requests.get(url, timeout=15)
                        prices = (resp.json() or {}).get("coins", {})
                    except Exception as e:
                        debug_msgs.append(f"Price fetch failed: {e}")
                        prices = {}

                def _to_dec(x) -> Decimal:
                    try:
                        if x is None:
                            return Decimal(0)
                        if isinstance(x, (int, float)):
                            return Decimal(str(x))
                        return Decimal(str(x))
                    except (InvalidOperation, ValueError):
                        return Decimal(0)

                def _norm_token_amount(raw: Decimal, decimals: int) -> Decimal:
                    # If looks like raw base units (>> 10**decimals), scale down
                    threshold = Decimal(10) ** (decimals + 2)
                    if raw > threshold:
                        return raw / (Decimal(10) ** decimals)
                    return raw

                def _price_usd(chain_id: int, token_addr: str) -> Optional[Decimal]:
                    if not recompute_usd:
                        return None
                    if chain_id not in CHAIN_SLUG or not token_addr:
                        return None
                    key = f"{CHAIN_SLUG[chain_id]}:{token_addr.lower()}"
                    p = (prices.get(key) or {}).get("price")
                    try:
                        return Decimal(str(p)) if p is not None else None
                    except Exception:
                        return None

                for it in items:
                    m = it.get("market") or {}
                    stt = it.get("state") or {}
                    if not include_untrusted and m.get("whitelisted") is False:
                        continue

                    chain_id = m.get("chainId")
                    loan = (m.get("loanAsset") or {})
                    coll = (m.get("collateralAsset") or {})

                    mk = m.get("uniqueKey")
                    loan_dec = int(loan.get("decimals") or 18)
                    coll_dec = int(coll.get("decimals") or 18)
                    loan_addr = (loan.get("address") or "").lower()
                    coll_addr = (coll.get("address") or "").lower()

                    # Raw numbers as Decimal
                    s_raw = _to_dec(stt.get("supplyAssets"))
                    b_raw = _to_dec(stt.get("borrowAssets"))
                    c_raw = _to_dec(stt.get("collateral"))

                    # Normalize token units if needed (divide by 10**decimals if clearly base units)
                    s = _norm_token_amount(s_raw, loan_dec)
                    b = _norm_token_amount(b_raw, loan_dec)
                    c = _norm_token_amount(c_raw, coll_dec)

                    # USD: either recompute via price, or trust API with sanity cap
                    if recompute_usd:
                        p_loan = _price_usd(chain_id, loan_addr) or Decimal(0)
                        p_coll = _price_usd(chain_id, coll_addr) or Decimal(0)
                        s_usd = s * p_loan
                        b_usd = b * p_loan
                        c_usd = c * p_coll
                    else:
                        s_usd = _to_dec(stt.get("supplyAssetsUsd"))
                        b_usd = _to_dec(stt.get("borrowAssetsUsd"))
                        c_usd = _to_dec(stt.get("collateralUsd"))

                    # sanity clamp to drop aberrant oracle values
                    if max(s_usd, b_usd, c_usd) > Decimal(1e11):
                        debug_msgs.append(
                            f"Skipped {mk} due to abnormal USD value: borrowUsd={b_usd}, supplyUsd={s_usd}, collateralUsd={c_usd}")
                        continue

                    row = {
                        "marketKey": mk,
                        "loan": loan.get("symbol"),
                        "collateralAsset": coll.get("symbol"),
                        "supplyAssets": float(s),
                        "supplyUsd": float(s_usd),
                        "borrowAssets": float(b),
                        "borrowUsd": float(b_usd),
                        "collateralAmt": float(c),
                        "collateralUsd": float(c_usd),
                    }
                    total_supply_usd += s_usd
                    total_borrow_usd += b_usd
                    total_collateral_usd += c_usd
                    morpho_rows.append(row)
            except RuntimeError as e:
                st.info(f"Morpho: {e}")
            except Exception as e:
                st.warning(f"Morpho query failed: {e}")

            left, right = st.columns([2, 1])
            with left:
                if morpho_rows:
                    df = pd.DataFrame(morpho_rows)
                    cols = [
                        "marketKey", "loan", "collateralAsset",
                        "supplyAssets", "supplyUsd", "borrowAssets", "borrowUsd",
                        "collateralAmt", "collateralUsd",
                    ]
                    df = df[[c for c in cols if c in df.columns]]
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("No Morpho positions detected for this wallet (or all filtered).")
                if debug_msgs:
                    with st.expander("Debug log (Morpho)"):
                        for m in debug_msgs:
                            st.code(m)
                        st.caption("USD recompute uses DefiLlama prices when enabled; token amounts normalized by decimals when needed.")

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
                data = zapper_tx_history([addr], first=20, chain_ids=tx_chain_sel)
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
                rows: List[Dict[str, Any]] = []
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

st.caption("This build shows ONLY per-wallet positions (no pool totals) and fixes units via decimals/DefiLlama pricing.")
