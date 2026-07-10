
from __future__ import annotations

import re
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
    .block-container {max-width: 1200px; padding-top: 1rem; padding-bottom: 4rem;}
    .pick-card {
        border: 1px solid rgba(128,128,128,.3);
        border-radius: 20px;
        padding: 18px;
        margin-bottom: 14px;
        background: rgba(128,128,128,.07);
    }
    .pick-rank {opacity:.72;font-size:.9rem}
    .pick-side {font-size:1.4rem;font-weight:800;margin-top:4px}
    .pick-market {font-size:1rem;margin:6px 0 10px}
    .pick-meta {font-size:.92rem;line-height:1.55;opacity:.9}
    @media(max-width:640px){
      .block-container{padding-left:.85rem;padding-right:.85rem}
      button[kind="primary"]{min-height:52px;font-size:1.05rem}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

SPORT_PATTERNS = {
    "Baseball": [
        r"\bMLB\b", r"\bbaseball\b", r"\bworld series\b", r"\binnings?\b",
        r"\bruns?\b", r"\bstrikeouts?\b", r"\bhome runs?\b",
    ],
    "Basketball": [
        r"\bNBA\b", r"\bWNBA\b", r"\bNCAA basketball\b", r"\bbasketball\b",
        r"\brebounds?\b", r"\bassists?\b",
    ],
    "Football": [
        r"\bNFL\b", r"\bNCAAF\b", r"\bcollege football\b",
        r"\btouchdowns?\b", r"\bpassing yards?\b", r"\brushing yards?\b",
    ],
    "Soccer": [
        r"\bsoccer\b", r"\bpremier league\b", r"\bla liga\b", r"\bserie a\b",
        r"\bbundesliga\b", r"\bligue 1\b", r"\bMLS\b",
        r"\bchampions league\b", r"\bworld cup\b", r"\bcorner kicks?\b",
    ],
    "Hockey": [r"\bNHL\b", r"\bhockey\b", r"\bstanley cup\b"],
    "Tennis": [r"\bATP\b", r"\bWTA\b", r"\btennis\b"],
    "Golf": [r"\bPGA\b", r"\bLPGA\b", r"\bgolf\b", r"\bmasters\b"],
    "MMA / UFC": [r"\bUFC\b", r"\bMMA\b", r"\bsubmission\b"],
    "Boxing": [r"\bboxing\b", r"\bby decision\b", r"\bby knockout\b"],
    "Cricket": [r"\bIPL\b", r"\bcricket\b", r"\bwickets?\b"],
    "Esports": [
        r"\besports?\b", r"\bLeague of Legends\b", r"\bCounter-Strike\b",
        r"\bCS2\b", r"\bDota\b", r"\bValorant\b",
    ],
}


@dataclass(frozen=True)
class Config:
    top_n: int
    category: str
    period: str
    min_position_value: float
    history_limit: int
    include_redeemable: bool


def api_get(path: str, params: dict[str, Any], retries: int = 3) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                f"{BASE_URL}{path}",
                params=params,
                timeout=TIMEOUT,
                headers={"User-Agent": "CopyTheWhales/3.0"},
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
    raise RuntimeError(f"Polymarket API request failed: {last_error}")


@st.cache_data(ttl=300, show_spinner=False)
def get_leaderboard(top_n: int, category: str, period: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < top_n:
        limit = min(50, top_n - len(rows))
        batch = api_get(
            "/v1/leaderboard",
            {
                "category": category,
                "timePeriod": period,
                "orderBy": "PNL",
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
def get_open_positions(wallet: str, include_redeemable: bool) -> list[dict[str, Any]]:
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
        offset += len(batch)
        if offset >= 10000:
            break
    return rows


@st.cache_data(ttl=900, show_spinner=False)
def get_closed_positions(wallet: str, max_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < max_rows:
        limit = min(50, max_rows - len(rows))
        batch = api_get(
            "/closed-positions",
            {
                "user": wallet,
                "limit": limit,
                "offset": offset,
                "sortBy": "REALIZEDPNL",
                "sortDirection": "DESC",
            },
        )
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += len(batch)
    return rows[:max_rows]


def classify_sport(text: str) -> str:
    value = str(text or "")
    for sport, patterns in SPORT_PATTERNS.items():
        if any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns):
            return sport
    return "Other"


def parse_date(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def format_date(value: Any) -> str:
    dt = parse_date(value)
    if pd.isna(dt):
        return "Date unavailable"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def normalize_open(trader: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    market = p.get("title") or ""
    searchable = " ".join([market, p.get("slug") or "", p.get("eventSlug") or ""])
    return {
        "rank": int(trader.get("rank") or 0),
        "trader": trader.get("userName") or trader.get("proxyWallet") or "Unknown",
        "wallet": trader.get("proxyWallet") or "",
        "market": market,
        "outcome": p.get("outcome") or "",
        "condition_id": p.get("conditionId") or "",
        "sport": classify_sport(searchable),
        "end_date": p.get("endDate") or "",
        "event_date": parse_date(p.get("endDate")),
        "entry_price": float(p.get("avgPrice") or 0),
        "current_price": float(p.get("curPrice") or 0),
        "current_value": float(p.get("currentValue") or 0),
        "cash_pnl": float(p.get("cashPnl") or 0),
        "redeemable": bool(p.get("redeemable", False)),
    }


def normalize_closed(trader: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    market = p.get("title") or ""
    searchable = " ".join([market, p.get("slug") or "", p.get("eventSlug") or ""])
    realized = float(p.get("realizedPnl") or 0)
    bought = float(p.get("totalBought") or 0)
    result = "Win" if realized > 0 else "Loss" if realized < 0 else "Push"
    return {
        "trader": trader.get("userName") or trader.get("proxyWallet") or "Unknown",
        "wallet": trader.get("proxyWallet") or "",
        "sport": classify_sport(searchable),
        "result": result,
        "realized_pnl": realized,
        "total_bought": bought,
    }


def load_all(config: Config):
    traders = get_leaderboard(config.top_n, config.category, config.period)
    open_rows: list[dict[str, Any]] = []
    closed_rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    progress = st.progress(0, text="Reading whale wallets…")
    for i, trader in enumerate(traders):
        wallet = trader.get("proxyWallet") or ""
        name = trader.get("userName") or wallet
        try:
            for p in get_open_positions(wallet, config.include_redeemable):
                row = normalize_open(trader, p)
                if row["current_value"] >= config.min_position_value:
                    open_rows.append(row)
        except Exception as exc:
            warnings.append(f"Open positions — {name}: {exc}")

        try:
            for p in get_closed_positions(wallet, config.history_limit):
                closed_rows.append(normalize_closed(trader, p))
        except Exception as exc:
            warnings.append(f"Closed positions — {name}: {exc}")

        progress.progress((i + 1) / max(len(traders), 1), text=f"Wallet {i+1} of {len(traders)}")
    progress.empty()

    return pd.DataFrame(traders), pd.DataFrame(open_rows), pd.DataFrame(closed_rows), warnings


def wallet_stats(closed: pd.DataFrame, sport: str) -> pd.DataFrame:
    if closed.empty:
        return pd.DataFrame()
    df = closed.copy()
    if sport != "All Sports":
        df = df[df["sport"] == sport]
    if df.empty:
        return pd.DataFrame()

    stats = (
        df.groupby(["wallet", "trader"], dropna=False)
        .agg(
            wins=("result", lambda s: int((s == "Win").sum())),
            losses=("result", lambda s: int((s == "Loss").sum())),
            pushes=("result", lambda s: int((s == "Push").sum())),
            realized_pnl=("realized_pnl", "sum"),
            amount_bought=("total_bought", "sum"),
        )
        .reset_index()
    )
    stats["graded_plays"] = stats["wins"] + stats["losses"]
    stats["win_rate"] = stats.apply(
        lambda r: 100 * r["wins"] / r["graded_plays"] if r["graded_plays"] else 0.0,
        axis=1,
    )
    stats["roi"] = stats.apply(
        lambda r: 100 * r["realized_pnl"] / r["amount_bought"] if r["amount_bought"] else 0.0,
        axis=1,
    )
    return stats


def filter_event_window(df: pd.DataFrame, days_ahead: int) -> pd.DataFrame:
    if df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC")
    lower = now - pd.Timedelta(hours=6)
    upper = now + pd.Timedelta(days=days_ahead)
    return df[
        df["event_date"].notna()
        & (df["event_date"] >= lower)
        & (df["event_date"] <= upper)
    ].copy()


def build_consensus(open_df: pd.DataFrame, stats: pd.DataFrame, selected_wallet_count: int, min_wallets: int):
    if open_df.empty:
        return pd.DataFrame()

    unique = (
        open_df.sort_values("current_value", ascending=False)
        .drop_duplicates(["wallet", "condition_id", "outcome"])
    )

    if stats.empty:
        merged = unique.copy()
        for col in ["wins", "losses", "graded_plays", "win_rate", "roi"]:
            merged[col] = 0.0
    else:
        merged = unique.merge(
            stats[["wallet", "wins", "losses", "graded_plays", "win_rate", "roi"]],
            on="wallet",
            how="left",
        )
        for col in ["wins", "losses", "graded_plays", "win_rate", "roi"]:
            merged[col] = merged[col].fillna(0)

    result = (
        merged.groupby(
            ["condition_id", "market", "outcome", "sport"],
            dropna=False,
        )
        .agg(
            event_date=("event_date", "min"),
            whales=("wallet", "nunique"),
            combined_value=("current_value", "sum"),
            average_entry=("entry_price", "mean"),
            current_price=("current_price", "mean"),
            open_pnl=("cash_pnl", "sum"),
            holder_wins=("wins", "sum"),
            holder_losses=("losses", "sum"),
            holder_win_rate=("win_rate", "mean"),
            holder_roi=("roi", "mean"),
            holders=("trader", lambda s: ", ".join(sorted(set(map(str, s))))),
        )
        .reset_index()
    )

    result = result[result["whales"] >= min_wallets].copy()
    result["consensus_pct"] = 100 * result["whales"] / max(selected_wallet_count, 1)

    roi_score = ((result["holder_roi"].clip(-25, 25) + 25) / 50) * 100
    result["confidence_score"] = (
        0.50 * result["consensus_pct"]
        + 0.35 * result["holder_win_rate"]
        + 0.15 * roi_score
    ).clip(0, 100)

    result = result.sort_values(
        ["confidence_score", "whales", "combined_value"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    result.insert(0, "rank", range(1, len(result) + 1))
    return result


st.title("🐋 Copy the Whales")
st.caption("Current positions, upcoming events, wallet records, ROI, and confidence.")

with st.expander("Filters", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        top_n = st.number_input("Top wallets", 1, 100, 20, 1)
        category = st.selectbox(
            "Leaderboard category",
            ["OVERALL", "SPORTS", "POLITICS", "CRYPTO", "CULTURE", "MENTIONS",
             "WEATHER", "ECONOMICS", "TECH", "FINANCE"],
        )
        sport = st.selectbox("Sport", ["All Sports"] + list(SPORT_PATTERNS.keys()))
        game_window = st.selectbox(
            "Upcoming event window",
            [1, 2, 3, 7, 14, 30, 60],
            index=2,
            format_func=lambda d: f"Next {d} day" if d == 1 else f"Next {d} days",
        )
    with c2:
        period = st.selectbox("Leaderboard period", ["DAY", "WEEK", "MONTH", "ALL"], index=2)
        min_position = st.number_input("Minimum active position value", 0.0, value=100.0, step=100.0)
        min_wallets = st.number_input("Minimum wallets sharing a pick", 1, int(top_n), 2, 1)
        history_limit = st.selectbox("Closed plays used per wallet", [50, 100, 250, 500, 1000], index=3)

    st.markdown("#### Wallet-performance filters")
    f1, f2, f3 = st.columns(3)
    with f1:
        min_win_rate = st.slider("Minimum win rate", 0, 100, 0)
    with f2:
        min_roi = st.slider("Minimum ROI", -100, 100, -100)
    with f3:
        min_plays = st.number_input("Minimum graded plays", 0, 1000, 0, 5)

refresh = st.button("Refresh live picks", type="primary", use_container_width=True)

if refresh or "open_df" not in st.session_state:
    cfg = Config(
        top_n=int(top_n),
        category=category,
        period=period,
        min_position_value=float(min_position),
        history_limit=int(history_limit),
        include_redeemable=False,
    )
    try:
        with st.spinner("Building live whale consensus…"):
            traders_df, open_df, closed_df, warnings = load_all(cfg)
        st.session_state["cfg"] = cfg
        st.session_state["traders_df"] = traders_df
        st.session_state["open_df"] = open_df
        st.session_state["closed_df"] = closed_df
        st.session_state["warnings"] = warnings
    except Exception as exc:
        st.error(str(exc))
        st.stop()

cfg = st.session_state["cfg"]
traders_df = st.session_state["traders_df"]
open_df = st.session_state["open_df"]
closed_df = st.session_state["closed_df"]
warnings = st.session_state.get("warnings", [])

stats = wallet_stats(closed_df, sport)

filtered = open_df.copy()
if sport != "All Sports":
    filtered = filtered[filtered["sport"] == sport]
    filtered = filter_event_window(filtered, int(game_window))

if not stats.empty:
    qualified_wallets = stats[
        (stats["win_rate"] >= min_win_rate)
        & (stats["roi"] >= min_roi)
        & (stats["graded_plays"] >= min_plays)
    ]["wallet"]
    filtered = filtered[filtered["wallet"].isin(qualified_wallets)]

selected_wallet_count = int(filtered["wallet"].nunique()) if not filtered.empty else 0
results = build_consensus(filtered, stats, selected_wallet_count, int(min_wallets))

if results.empty:
    st.warning("No shared upcoming picks matched the current filters.")
else:
    st.subheader("Top 3 upcoming live picks")
    for _, row in results.head(3).iterrows():
        st.markdown(
            f"""
            <div class="pick-card">
              <div class="pick-rank">#{int(row['rank'])} · {row['sport']} · {format_date(row['event_date'])}</div>
              <div class="pick-side">{row['outcome']}</div>
              <div class="pick-market">{row['market']}</div>
              <div class="pick-meta">
                Held by <b>{int(row['whales'])}/{max(selected_wallet_count,1)}</b> qualifying wallets ·
                ${row['combined_value']:,.0f} combined<br>
                Holder record: <b>{int(row['holder_wins'])}-{int(row['holder_losses'])}</b> ·
                Win rate: <b>{row['holder_win_rate']:.1f}%</b> ·
                ROI: <b>{row['holder_roi']:.1f}%</b><br>
                Confidence: <b>{row['confidence_score']:.1f}/100</b>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    table = results.copy()
    table["event_date"] = table["event_date"].map(format_date)
    st.subheader("Full consensus")
    st.dataframe(
        table[
            [
                "rank", "event_date", "sport", "market", "outcome", "whales",
                "holder_wins", "holder_losses", "holder_win_rate", "holder_roi",
                "confidence_score", "combined_value", "average_entry",
                "current_price", "open_pnl", "holders",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "holder_win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
            "holder_roi": st.column_config.NumberColumn("ROI", format="%.1f%%"),
            "confidence_score": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%.1f"),
            "combined_value": st.column_config.NumberColumn("Combined value", format="$%,.0f"),
            "average_entry": st.column_config.NumberColumn("Avg entry", format="%.3f"),
            "current_price": st.column_config.NumberColumn("Current", format="%.3f"),
            "open_pnl": st.column_config.NumberColumn("Open P&L", format="$%,.0f"),
        },
    )
    st.download_button(
        "Download consensus CSV",
        table.to_csv(index=False).encode("utf-8"),
        "whale_consensus.csv",
        "text/csv",
        use_container_width=True,
    )

st.subheader("Wallet win/loss and ROI")
if stats.empty:
    st.info("No closed-position history was available.")
else:
    st.dataframe(
        stats.sort_values(["roi", "win_rate"], ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
            "roi": st.column_config.NumberColumn("ROI", format="%.1f%%"),
            "realized_pnl": st.column_config.NumberColumn("Realized P&L", format="$%,.0f"),
            "amount_bought": st.column_config.NumberColumn("Amount bought", format="$%,.0f"),
        },
    )

with st.expander("How confidence is calculated"):
    st.write(
        "Confidence = 50% consensus share + 35% average holder win rate + "
        "15% normalized holder ROI. ROI is capped between -25% and +25% in the score."
    )

with st.expander("Every qualifying active position"):
    st.dataframe(filtered, use_container_width=True, hide_index=True)

with st.expander("Leaderboard wallets"):
    st.dataframe(traders_df, use_container_width=True, hide_index=True)

if warnings:
    with st.expander(f"API warnings ({len(warnings)})"):
        for warning in warnings:
            st.warning(warning)
