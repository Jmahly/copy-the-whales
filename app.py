
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://data-api.polymarket.com"
TIMEOUT = 30

st.set_page_config(
    page_title="Copy the Whales",
    page_icon="🐋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .stApp { max-width: 1200px; margin: 0 auto; }
      .block-container { padding-top: 1rem; padding-bottom: 4rem; }
      h1 { font-size: clamp(2rem, 8vw, 4rem) !important; letter-spacing: -0.04em; }
      h2 { font-size: clamp(1.35rem, 5vw, 2rem) !important; }
      [data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 18px;
        padding: 14px;
        background: rgba(128,128,128,.06);
      }
      [data-testid="stMetricValue"] { font-size: clamp(1.45rem, 6vw, 2.15rem); }
      .pick-card {
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 20px;
        padding: 18px;
        margin-bottom: 14px;
        background: rgba(128,128,128,.06);
      }
      .pick-rank { opacity: .7; font-size: .9rem; margin-bottom: 4px; }
      .pick-side { font-weight: 800; font-size: 1.35rem; }
      .pick-market { font-size: 1rem; margin: 6px 0 12px; }
      .pick-meta { opacity: .8; font-size: .9rem; }
      .footer-note { opacity: .7; font-size: .85rem; }
      @media (max-width: 640px) {
        .block-container { padding-left: .85rem; padding-right: .85rem; }
        div[data-testid="stHorizontalBlock"] { gap: .65rem; }
        button[kind="primary"] { min-height: 52px; font-size: 1.05rem; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@dataclass(frozen=True)
class Config:
    top_n: int = 20
    category: str = "OVERALL"
    time_period: str = "MONTH"
    order_by: str = "PNL"
    minimum_position_value: float = 100.0
    minimum_traders: int = 2
    include_redeemable: bool = False


def api_get(path: str, params: dict[str, Any], retries: int = 3) -> list[dict[str, Any]]:
    url = f"{BASE_URL}{path}"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=TIMEOUT,
                headers={"User-Agent": "CopyTheWhales/2.0"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("Unexpected API response.")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.25 * (attempt + 1))
    raise RuntimeError(f"Polymarket request failed: {last_error}")


@st.cache_data(ttl=300, show_spinner=False)
def get_leaderboard(top_n: int, category: str, time_period: str, order_by: str):
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < top_n:
        limit = min(50, top_n - len(rows))
        batch = api_get(
            "/v1/leaderboard",
            {
                "category": category,
                "timePeriod": time_period,
                "orderBy": order_by,
                "limit": limit,
                "offset": offset,
            },
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < limit:
            break
    return rows[:top_n]


@st.cache_data(ttl=180, show_spinner=False)
def get_positions(wallet: str, include_redeemable: bool):
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 500
    while True:
        batch = api_get(
            "/positions",
            {
                "user": wallet,
                "sizeThreshold": 0,
                "redeemable": str(include_redeemable).lower(),
                "limit": limit,
                "offset": offset,
                "sortBy": "CURRENT",
                "sortDirection": "DESC",
            },
        )
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        if offset >= 10000:
            break
    return rows


def normalize(trader: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": int(trader.get("rank") or 0),
        "trader": trader.get("userName") or trader.get("proxyWallet") or "Unknown",
        "wallet": trader.get("proxyWallet") or "",
        "market": position.get("title") or "",
        "outcome": position.get("outcome") or "",
        "condition_id": position.get("conditionId") or "",
        "market_slug": position.get("slug") or "",
        "event_slug": position.get("eventSlug") or "",
        "size": float(position.get("size") or 0),
        "entry_price": float(position.get("avgPrice") or 0),
        "current_price": float(position.get("curPrice") or 0),
        "current_value": float(position.get("currentValue") or 0),
        "cash_pnl": float(position.get("cashPnl") or 0),
        "percent_pnl": float(position.get("percentPnl") or 0),
        "redeemable": bool(position.get("redeemable", False)),
    }


def load_data(config: Config):
    traders = get_leaderboard(
        config.top_n, config.category, config.time_period, config.order_by
    )
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    progress = st.progress(0, text="Reading whale wallets…")
    for idx, trader in enumerate(traders):
        wallet = trader.get("proxyWallet") or ""
        name = trader.get("userName") or wallet
        try:
            for position in get_positions(wallet, config.include_redeemable):
                row = normalize(trader, position)
                if row["current_value"] >= config.minimum_position_value:
                    rows.append(row)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
        progress.progress(
            (idx + 1) / max(len(traders), 1),
            text=f"Wallet {idx + 1} of {len(traders)}",
        )
    progress.empty()
    return pd.DataFrame(traders), pd.DataFrame(rows), failures


def consensus(positions: pd.DataFrame, minimum_traders: int) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame()

    unique = (
        positions.sort_values("current_value", ascending=False)
        .drop_duplicates(["wallet", "condition_id", "outcome"])
    )

    result = (
        unique.groupby(
            ["condition_id", "market", "outcome", "market_slug", "event_slug"],
            dropna=False,
        )
        .agg(
            whales=("wallet", "nunique"),
            combined_value=("current_value", "sum"),
            median_value=("current_value", "median"),
            average_entry=("entry_price", "mean"),
            current_price=("current_price", "mean"),
            combined_pnl=("cash_pnl", "sum"),
            holders=("trader", lambda s: ", ".join(sorted(set(map(str, s))))),
        )
        .reset_index()
    )
    result = result[result["whales"] >= minimum_traders]
    result = result.sort_values(
        ["whales", "combined_value"], ascending=[False, False]
    ).reset_index(drop=True)
    result.insert(0, "rank", range(1, len(result) + 1))
    return result


st.title("🐋 Copy the Whales")
st.write("Live consensus among Polymarket’s top-performing traders.")

with st.expander("Filters", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        top_n = st.number_input("Top traders", 1, 100, 20, 1)
        category = st.selectbox(
            "Category",
            ["OVERALL", "SPORTS", "POLITICS", "CRYPTO", "CULTURE",
             "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE"],
        )
        min_value = st.number_input(
            "Minimum position value", 0.0, value=100.0, step=100.0
        )
    with c2:
        period = st.selectbox("Leaderboard period", ["DAY", "WEEK", "MONTH", "ALL"], index=2)
        min_traders = st.number_input("Minimum shared holders", 1, int(top_n), 2, 1)
        include_redeemable = st.checkbox(
            "Include resolved/redeemable holdings", value=False
        )

refresh = st.button("Refresh live picks", type="primary", use_container_width=True)

if refresh or "results" not in st.session_state:
    cfg = Config(
        top_n=int(top_n),
        category=category,
        time_period=period,
        minimum_position_value=float(min_value),
        minimum_traders=int(min_traders),
        include_redeemable=include_redeemable,
    )
    try:
        with st.spinner("Building live whale consensus…"):
            traders_df, positions_df, failures = load_data(cfg)
            results_df = consensus(positions_df, cfg.minimum_traders)
        st.session_state["results"] = results_df
        st.session_state["positions"] = positions_df
        st.session_state["traders"] = traders_df
        st.session_state["failures"] = failures
        st.session_state["cfg"] = cfg
    except Exception as exc:
        st.error(str(exc))
        st.stop()

results = st.session_state.get("results", pd.DataFrame())
positions = st.session_state.get("positions", pd.DataFrame())
traders = st.session_state.get("traders", pd.DataFrame())
failures = st.session_state.get("failures", [])
cfg = st.session_state.get("cfg", Config())

if results.empty:
    st.warning("No shared active picks matched these filters.")
else:
    st.subheader("Top 3 live bets")
    for _, row in results.head(3).iterrows():
        st.markdown(
            f"""
            <div class="pick-card">
              <div class="pick-rank">#{int(row['rank'])} consensus pick</div>
              <div class="pick-side">{row['outcome']}</div>
              <div class="pick-market">{row['market']}</div>
              <div class="pick-meta">
                Held by <b>{int(row['whales'])}/{cfg.top_n}</b> whales ·
                ${row['combined_value']:,.0f} combined ·
                Current price {row['current_price']:.3f}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    m1, m2, m3 = st.columns(3)
    m1.metric("Whales checked", cfg.top_n)
    m2.metric("Active positions", f"{len(positions):,}")
    m3.metric("Shared picks", f"{len(results):,}")

    st.subheader("Full consensus")
    table = results[
        [
            "rank", "market", "outcome", "whales", "combined_value",
            "average_entry", "current_price", "combined_pnl", "holders"
        ]
    ].copy()
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "combined_value": st.column_config.NumberColumn("Combined value", format="$%,.0f"),
            "average_entry": st.column_config.NumberColumn("Avg entry", format="%.3f"),
            "current_price": st.column_config.NumberColumn("Current", format="%.3f"),
            "combined_pnl": st.column_config.NumberColumn("Combined P&L", format="$%,.0f"),
        },
    )

    st.download_button(
        "Download consensus CSV",
        results.to_csv(index=False).encode(),
        "whale_consensus.csv",
        "text/csv",
        use_container_width=True,
    )

    with st.expander("See every active position"):
        st.dataframe(positions, use_container_width=True, hide_index=True)

    with st.expander("See leaderboard wallets"):
        st.dataframe(traders, use_container_width=True, hide_index=True)

if failures:
    with st.expander(f"Wallet warnings ({len(failures)})"):
        for item in failures:
            st.warning(item)

st.markdown(
    """
    <div class="footer-note">
      Current positions only. Resolved/redeemable holdings are excluded unless enabled.
      Consensus does not guarantee a winning trade; whales may hedge elsewhere or copy one another.
    </div>
    """,
    unsafe_allow_html=True,
)
