# app_fixed.py — DeFi Multi‑Wallet Monitor (Per‑wallet + USD recompute, tokenization‑safe)
# Streamlit dashboard to monitor per‑wallet supply / borrow / collateral on Morpho Blue
# Recomputes USD using DefiLlama prices; normalizes token units with decimals.
# -----------------------------------------------------------------------------

import os
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

import requests
import pandas as pd
from dateutil import tz
import streamlit as st
from decimal import Decimal, InvalidOperation, getcontext

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
MORPHO_GRAPHQL = "https://api.morpho.org/graphql"
ZAPPER_GQL = "https://public.zapper.xyz/graphql"
ZAPPER_API_KEY = os.getenv("ZAPPER_API_KEY") or (st.secrets["ZAPPER_API_KEY"] if "ZAPPER_API_KEY" in st.secrets else None)

CHAIN_OPTIONS = [1, 8453, 42161]  # Ethereum, Base, Arbitrum
HTTP_HEADERS = {"Content-Type": "application/json", "User-Agent": "DeFiWalletMonitor/1.2"}
CHAIN_SLUG = {1: "ethereum", 8453: "base", 42161: "arbitrum"}

# High precision for money math
getcontext().prec = 50

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

def parse_chain_from_market_key(mk: str) -> Optional[int]:
    # Many Morpho uniqueKeys start with the chain id followed by '-', e.g. '1-0x...-0x-...'
    try:
        m = re.match(r"^(\d+)[-:]", mk or "")
        return int(m.group(1)) if m else None
    except Exception:
        return None

# -----------------------------------------------------------------------------
# Morpho — STRICT per-user positions via filtered list query (no pool totals)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def morpho_user_positions(address: str, chain_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    # Return ONLY the positions for the given wallet; user-level amounts in state{...}.
    chains_clause = ""
    if chain_ids:
        uniq = ",".join(str(int(c)) for c in sorted(set(chain_ids)))
        chains_clause = f", chainId_in: [{uniq}]"

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
    items = [it for it in items if (it.get("user") or {}).get("address", "").lower() == address.lower()]

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
# UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="DeFi Multi‑Wallet Monitor", layout="wide")
st.title("🧭 DeFi Multi‑Wallet Monitor — Per‑wallet + USD recompute (fixed)")

with st.sidebar:
    st.header("⚙️ Settings")
    default_wallets_value = "\n".join(DEFAULT_WALLETS)
    wallets_text = st.text_area("Wallets (one per line)", value=default_wallets_value, height=120)
    wallets = re.findall(r"0x[a-fA-F0-9]{40}", wallets_text)

    use_morpho = st.checkbox("Fetch Morpho positions", value=True)
    morpho_chain_sel = st.multiselect("Morpho chains", options=CHAIN_OPTIONS, default=[1])
    recompute_usd = st.checkbox("Recompute USD from DefiLlama prices", value=True)

    st.markdown("—")
    st.write("Timezone:", TIMEZONE)

# KPIs
col1, col2, col3 = st.columns(3)
col1.metric("Wallets", len(wallets))
now_str = datetime.now(tz.gettz(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
col2.metric("Now", now_str)
col3.metric("Chains (Morpho)", ", ".join(map(str, morpho_chain_sel)) if morpho_chain_sel else "all")

st.divider()

def _to_dec(x) -> Decimal:
    try:
        if x is None:
            return Decimal(0)
        if isinstance(x, (int, float)):
            return Decimal(str(x))
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return Decimal(0)

def _norm(raw: Decimal, decimals: int) -> Decimal:
    # If clearly base units (much larger than 10**decimals), scale down
    threshold = Decimal(10) ** (decimals + 2)
    if raw > threshold:
        return raw / (Decimal(10) ** decimals)
    return raw

def _fetch_prices(price_keys: List[str]) -> Dict[str, Any]:
    if not price_keys:
        return {}
    try:
        url = "https://coins.llama.fi/prices/current/" + ",".join(sorted(set(price_keys)))
        resp = requests.get(url, timeout=15)
        return (resp.json() or {}).get("coins", {}) or {}
    except Exception:
        return {}

def _price_from_llama(prices: Dict[str, Any], chain_id: Optional[int], token_addr: str) -> Optional[Decimal]:
    if chain_id not in CHAIN_SLUG or not token_addr:
        return None
    key = f"{CHAIN_SLUG[chain_id]}:{token_addr.lower()}"
    p = (prices.get(key) or {}).get("price")
    try:
        return Decimal(str(p)) if p is not None else None
    except Exception:
        return None

# Per-wallet sections
for addr in wallets:
    st.subheader(f"👛 {addr}")

    if not use_morpho:
        st.info("Morpho disabled.")
        continue

    # Fetch positions
    try:
        items = morpho_user_positions(addr, morpho_chain_sel)
    except Exception as e:
        st.error(f"Morpho query failed: {e}")
        continue

    # Build price map if recompute
    prices = {}
    price_keys: List[str] = []
    if recompute_usd:
        for it in items:
            m = it.get("market") or {}
            mk = m.get("uniqueKey") or ""
            cid = parse_chain_from_market_key(mk)
            loan = (m.get("loanAsset") or {})
            coll = (m.get("collateralAsset") or {})
            la = (loan.get("address") or "").lower()
            ca = (coll.get("address") or "").lower()
            if cid in CHAIN_SLUG and la:
                price_keys.append(f"{CHAIN_SLUG[cid]}:{la}")
            if cid in CHAIN_SLUG and ca:
                price_keys.append(f"{CHAIN_SLUG[cid]}:{ca}")
        prices = _fetch_prices(price_keys)

    # Build table
    rows: List[Dict[str, Any]] = []
    total_s_usd = Decimal(0)
    total_b_usd = Decimal(0)
    total_c_usd = Decimal(0)
    debug_msgs: List[str] = []

    for it in items:
        m = it.get("market") or {}
        stt = it.get("state") or {}
        if m.get("whitelisted") is False:
            continue

        mk = m.get("uniqueKey") or ""
        cid = parse_chain_from_market_key(mk)
        loan = (m.get("loanAsset") or {})
        coll = (m.get("collateralAsset") or {})
        loan_dec = int(loan.get("decimals") or 18)
        coll_dec = int(coll.get("decimals") or 18)
        loan_addr = (loan.get("address") or "").lower()
        coll_addr = (coll.get("address") or "").lower()

        s_raw = _to_dec(stt.get("supplyAssets"))
        b_raw = _to_dec(stt.get("borrowAssets"))
        c_raw = _to_dec(stt.get("collateral"))
        s = _norm(s_raw, loan_dec)
        b = _norm(b_raw, loan_dec)
        c = _norm(c_raw, coll_dec)

        if recompute_usd:
            p_loan = _price_from_llama(prices, cid, loan_addr) or Decimal(0)
            p_coll = _price_from_llama(prices, cid, coll_addr) or Decimal(0)
            s_usd = s * p_loan
            b_usd = b * p_loan
            c_usd = c * p_coll
            # If price missing, fall back to API USD
            if p_loan == 0:
                s_usd = _to_dec(stt.get("supplyAssetsUsd"))
                b_usd = _to_dec(stt.get("borrowAssetsUsd"))
            if p_coll == 0:
                c_usd = _to_dec(stt.get("collateralUsd"))
        else:
            s_usd = _to_dec(stt.get("supplyAssetsUsd"))
            b_usd = _to_dec(stt.get("borrowAssetsUsd"))
            c_usd = _to_dec(stt.get("collateralUsd"))

        if max(s_usd, b_usd, c_usd) > Decimal(1e11):
            debug_msgs.append(f"Skipped {mk} due to abnormal USD value")
            continue

        rows.append({
            "marketKey": mk,
            "loan": loan.get("symbol"),
            "collateralAsset": coll.get("symbol"),
            "supplyAssets": float(s),
            "supplyUsd": float(s_usd),
            "borrowAssets": float(b),
            "borrowUsd": float(b_usd),
            "collateralAmt": float(c),
            "collateralUsd": float(c_usd),
        })
        total_s_usd += s_usd
        total_b_usd += b_usd
        total_c_usd += c_usd

    left, right = st.columns([2, 1])
    with left:
        if rows:
            df = pd.DataFrame(rows)
            cols = ["marketKey", "loan", "collateralAsset",
                    "supplyAssets", "supplyUsd", "borrowAssets", "borrowUsd",
                    "collateralAmt", "collateralUsd"]
            df = df[[c for c in cols if c in df.columns]]
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No Morpho positions detected for this wallet (or all filtered).")
        if debug_msgs:
            with st.expander("Debug log (Morpho)"):
                for m in debug_msgs:
                    st.code(m)
                st.caption("USD recomputed via DefiLlama when available; otherwise API USD used. Amounts normalized by token decimals if needed.")

    with right:
        st.metric("Supply USD", f"{total_s_usd:,.2f}")
        st.metric("Borrow USD", f"{total_b_usd:,.2f}")
        st.metric("Collateral USD", f"{total_c_usd:,.2f}")
        st.metric("Net (Supply−Borrow)", f"{(total_s_usd - total_b_usd):,.2f}")
