# app_multi.py — DeFi Multi-Wallet Monitor (Multi-wallet: Consolidated + Per-wallet)
# Features:
#  • Strict per-wallet Morpho positions (no pool totals)
#  • Borrow-only toggle
#  • Supply = Collateral (display convention)
#  • Borrow Rate (APY) per market
#  • Optional USD recompute via DefiLlama prices with decimals normalization
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
HTTP_HEADERS = {"Content-Type": "application/json", "User-Agent": "DeFiWalletMonitor/2.0"}

CHAIN_OPTIONS = [1, 8453, 42161]  # Ethereum, Base, Arbitrum
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
    # Many Morpho uniqueKeys start with the chain id followed by '-' (or ':')
    try:
        m = re.match(r"^(\d+)[-:]", mk or "")
        return int(m.group(1)) if m else None
    except Exception:
        return None

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

# Borrow APY per market
@st.cache_data(ttl=300)
def morpho_market_borrow_apys(unique_keys: List[str]) -> Dict[str, float]:
    keys = [k for k in dict.fromkeys(unique_keys) if k]
    if not keys:
        return {}

    variants = [
        """
        query($keys:[String!]) {
          markets(first:300, where:{ uniqueKey_in: $keys }) {
            items { uniqueKey rates { borrowApy } }
          }
        }
        """,
        """
        query($keys:[String!]) {
          markets(first:300, where:{ uniqueKey_in: $keys }) {
            items { uniqueKey apy { borrowApy } }
          }
        }
        """,
        """
        query($keys:[String!]) {
          markets(first:300, where:{ uniqueKey_in: $keys }) {
            items { uniqueKey state { borrowRate } }
          }
        }
        """,
    ]

    for gql in variants:
        try:
            data = _run_graphql(MORPHO_GRAPHQL, gql, {"keys": keys})
            if "errors" in data:
                continue
            items = (((data.get("data") or {}).get("markets") or {}).get("items") or [])
            apys: Dict[str, float] = {}
            for it in items:
                uk = it.get("uniqueKey")
                rates = it.get("rates") or it.get("apy")
                if isinstance(rates, dict) and ("borrowApy" in rates):
                    apys[uk] = float(rates.get("borrowApy") or 0.0)
                else:
                    stt = it.get("state") or {}
                    br = stt.get("borrowRate")
                    if br is not None:
                        apys[uk] = float(br)
            if apys:
                return apys
        except Exception:
            pass
    return {}

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="DeFi Multi-Wallet Monitor", layout="wide")
st.title("🧭 DeFi Multi-Wallet Monitor — Multi-wallet (Consolidated + Detail)")

with st.sidebar:
    st.header("⚙️ Settings")
    default_wallets_value = "\n".join(DEFAULT_WALLETS)
    wallets_text = st.text_area("Wallets (one per line)", value=default_wallets_value, height=140)
    wallets = re.findall(r"0x[a-fA-F0-9]{40}", wallets_text)

    morpho_chain_sel = st.multiselect("Morpho chains", options=CHAIN_OPTIONS, default=[1])
    recompute_usd = st.checkbox("Recompute USD via DefiLlama", value=True)
    include_untrusted = st.checkbox("Show non-whitelisted markets", value=False)

    st.markdown("—")
    st.write("Timezone:", TIMEZONE)

# KPIs
col1, col2, col3 = st.columns(3)
col1.metric("Wallets", len(wallets))
now_str = datetime.now(tz.gettz(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
col2.metric("Now", now_str)
col3.metric("Chains", ", ".join(map(str, morpho_chain_sel)) if morpho_chain_sel else "all")

st.divider()

# Fetch positions for all wallets
all_rows: List[Dict[str, Any]] = []
debug_msgs: List[str] = []

# Pre-collect price keys if recompute_usd
price_keys: List[str] = []
wallet_items_map: Dict[str, List[Dict[str, Any]]] = {}

for addr in wallets:
    try:
        items = morpho_user_positions(addr, morpho_chain_sel)
        wallet_items_map[addr] = items
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
    except Exception as e:
        debug_msgs.append(f"{addr}: Morpho query failed → {e}")
        wallet_items_map[addr] = []

prices = _fetch_prices(price_keys) if recompute_usd else {}

# Build unified per-wallet rows (with Supply = Collateral convention)
for addr in wallets:
    items = wallet_items_map.get(addr, [])
    for it in items:
        m = it.get("market") or {}
        stt = it.get("state") or {}
        if (not include_untrusted) and m.get("whitelisted") is False:
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

        # USD numbers: recompute if price found; otherwise fallback to API
        if recompute_usd:
            p_loan = _price_from_llama(prices, cid, loan_addr) or Decimal(0)
            p_coll = _price_from_llama(prices, cid, coll_addr) or Decimal(0)
            s_usd = s * p_loan
            b_usd = b * p_loan
            c_usd = c * p_coll
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
            debug_msgs.append(f"{addr} / {mk}: abnormal USD → skipped")
            continue

        # Supply = Collateral (display convention)
        supply_amt = c
        supply_usd = c_usd

        row = {
            "wallet": addr,
            "marketKey": mk,
            "loan": loan.get("symbol"),
            "collateralAsset": coll.get("symbol"),
            "borrowAssets": float(b),
            "borrowUsd": float(b_usd),
            "supplyAssets": float(supply_amt),
            "supplyUsd": float(supply_usd),
            "whitelisted": m.get("whitelisted"),
        }
        all_rows.append(row)

# Borrow APY per market
try:
    mk_list = [r.get("marketKey") for r in all_rows if r.get("marketKey")]
    apy_map = morpho_market_borrow_apys(mk_list) if mk_list else {}
except Exception:
    apy_map = {}

# ---------------- Consolidated tab ----------------
tab_cons, tab_per = st.tabs(["📊 Consolidated", "🧩 Per-wallet detail"])

with tab_cons:
    st.subheader("Consolidated view (all selected wallets)")

    if not all_rows:
        st.info("No Morpho positions detected for selected wallets (or filtered out).")
    else:
        df_all = pd.DataFrame(all_rows)
        df_all["borrowRateRaw"] = df_all["marketKey"].map(apy_map)

        def _fmt_rate(x):
            if pd.isna(x): return None
            x = float(x)
            return f"{x*100:.2f}%" if x <= 1.5 else f"{x:.2f}%"

        df_all["borrowRate"] = df_all["borrowRateRaw"].apply(_fmt_rate)

        # Borrow-only toggle (consolidated)
        only_borrow_cons = st.toggle("Borrow-only (consolidated)", value=True, key="boronly_cons")

        df_show = df_all.copy()
        if only_borrow_cons:
            df_show = df_show[df_show["borrowUsd"].fillna(0) > 0]

        # Aggregate by market (sum across wallets)
        agg_cols = {
            "borrowAssets": "sum",
            "borrowUsd": "sum",
            "supplyAssets": "sum",
            "supplyUsd": "sum",
        }
        df_agg = df_show.groupby(["marketKey","loan","collateralAsset","whitelisted","borrowRate"], dropna=False).agg(agg_cols).reset_index()

        # LTV on aggregated rows
        df_agg["ltv"] = df_agg.apply(lambda r: (r["borrowUsd"]/r["supplyUsd"]) if r["supplyUsd"]>0 else None, axis=1)

        # Totals
        total_supply_usd = float(df_agg["supplyUsd"].fillna(0).sum())
        total_borrow_usd = float(df_agg["borrowUsd"].fillna(0).sum())

        left, right = st.columns([2,1])
        with left:
            show_cols = ["marketKey","loan","collateralAsset","borrowUsd","supplyUsd","borrowRate","ltv","whitelisted"]
            st.dataframe(df_agg[show_cols], use_container_width=True)
        with right:
            st.metric("Supply USD (collateral)", f"{total_supply_usd:,.2f}")
            st.metric("Borrow USD", f"{total_borrow_usd:,.2f}")
            st.metric("Net (Collateral − Borrow)", f"{(total_supply_usd - total_borrow_usd):,.2f}")

        with st.expander("Underlying rows (by wallet)"):
            st.dataframe(df_show[["wallet","marketKey","loan","collateralAsset","borrowUsd","supplyUsd","borrowRate","whitelisted"]], use_container_width=True)

# ---------------- Per-wallet detail tab ----------------
with tab_per:
    if not all_rows:
        st.info("No Morpho positions for the selected wallets.")
    else:
        # Build per-wallet views
        for addr in wallets:
            st.markdown(f"### 👛 {addr}")
            df_w = pd.DataFrame([r for r in all_rows if r["wallet"] == addr])
            if df_w.empty:
                st.info("No positions for this wallet.")
                continue

            df_w["borrowRateRaw"] = df_w["marketKey"].map(apy_map)
            def _fmt_rate(x):
                if pd.isna(x): return None
                x = float(x)
                return f"{x*100:.2f}%" if x <= 1.5 else f"{x:.2f}%"

            df_w["borrowRate"] = df_w["borrowRateRaw"].apply(_fmt_rate)

            only_borrow = st.toggle("Afficher uniquement les emprunts actifs (borrow > 0)", value=True, key=f"boronly_{addr}")
            df_show = df_w[df_w["borrowUsd"].fillna(0) > 0] if only_borrow else df_w.copy()

            # LTV
            df_show["ltv"] = df_show.apply(lambda r: (r["borrowUsd"]/r["supplyUsd"]) if r["supplyUsd"]>0 else None, axis=1)

            # Totaux
            total_supply_usd = float(df_show["supplyUsd"].fillna(0).sum())
            total_borrow_usd = float(df_show["borrowUsd"].fillna(0).sum())

            left, right = st.columns([2,1])
            with left:
                cols = ["marketKey","loan","collateralAsset","borrowAssets","borrowUsd","supplyAssets","supplyUsd","borrowRate","ltv","whitelisted"]
                st.dataframe(df_show[cols], use_container_width=True)
            with right:
                st.metric("Supply USD (collateral)", f"{total_supply_usd:,.2f}")
                st.metric("Borrow USD", f"{total_borrow_usd:,.2f}")
                st.metric("Net (Collateral − Borrow)", f"{(total_supply_usd - total_borrow_usd):,.2f}")

            st.markdown("---")

# Debug log
if debug_msgs:
    with st.expander("Debug log"):
        for m in debug_msgs:
            st.code(m)
        st.caption("Notes: per-wallet positions; Supply=Collateral (display); optional USD recompute via DefiLlama; borrow rate fetched via markets query.")
