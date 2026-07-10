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

st.set_page_config(page_title="Copy the Whales", page_icon="🐋", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
.stApp { max-width: 1250px; margin: 0 auto; }
.block-container { padding-top: 1rem; padding-bottom: 4rem; }
h1 { font-size: clamp(2rem, 8vw, 4rem) !important; letter-spacing: -0.04em; }
[data-testid="stMetric"] { border: 1px solid rgba(128,128,128,.25); border-radius: 18px; padding: 14px; background: rgba(128,128,128,.06); }
.pick-card { border: 1px solid rgba(128,128,128,.25); border-radius: 20px; padding: 18px; margin-bottom: 14px; background: rgba(128,128,128,.06); }
.pick-rank { opacity: .7; font-size: .9rem; margin-bottom: 4px; }
.pick-side { font-weight: 800; font-size: 1.35rem; }
.pick-market { font-size: 1rem; margin: 6px 0 12px; }
.pick-meta { opacity: .85; font-size: .9rem; line-height: 1.55; }
.high { color: #4ade80; font-weight: 800; }
.medium { color: #fbbf24; font-weight: 800; }
.low { color: #fb7185; font-weight: 800; }
.footer-note { opacity: .7; font-size: .85rem; }
@media (max-width: 640px) { .block-container { padding-left: .85rem; padding-right: .85rem; } button[kind="primary"] { min-height: 52px; font-size: 1.05rem; } }
</style>
""", unsafe_allow_html=True)

SPORT_PATTERNS = {
    "Baseball": [r"\bMLB\b", r"\bbaseball\b", r"\bworld series\b", r"\binnings?\b", r"\bhome runs?\b", r"\bstrikeouts?\b"],
    "Basketball": [r"\bNBA\b", r"\bWNBA\b", r"\bNCAA basketball\b", r"\bbasketball\b", r"\brebounds?\b", r"\bassists?\b"],
    "Football": [r"\bNFL\b", r"\bNCAAF\b", r"\bcollege football\b", r"\btouchdowns?\b", r"\bpassing yards?\b", r"\brushing yards?\b"],
    "Soccer": [r"\bsoccer\b", r"\bpremier league\b", r"\bla liga\b", r"\bserie a\b", r"\bbundesliga\b", r"\bligue 1\b", r"\bMLS\b", r"\bchampions league\b", r"\bworld cup\b", r"\bgoals?\b", r"\bcorner kicks?\b"],
    "Hockey": [r"\bNHL\b", r"\bhockey\b", r"\bstanley cup\b"],
    "Tennis": [r"\bATP\b", r"\bWTA\b", r"\btennis\b", r"\bgames handicap\b"],
    "Golf": [r"\bPGA\b", r"\bLPGA\b", r"\bgolf\b", r"\bmasters\b"],
    "MMA / UFC": [r"\bUFC\b", r"\bMMA\b", r"\bsubmission\b"],
    "Boxing": [r"\bboxing\b", r"\bby knockout\b", r"\bby decision\b"],
    "Cricket": [r"\bIPL\b", r"\bcricket\b", r"\bwickets?\b"],
    "Esports": [r"\besports?\b", r"\bLeague of Legends\b", r"\bCounter-Strike\b", r"\bCS2\b", r"\bDota\b", r"\bValorant\b"],
}

@dataclass(frozen=True)
class Config:
    top_n: int = 20
    category: str = "OVERALL"
    time_period: str = "MONTH"
    order_by: str = "PNL"
    minimum_position_value: float = 100.0
    minimum_traders: int = 2
    include_redeemable: bool = False
    closed_history_limit: int = 500


def api_get(path: str, params: dict[str, Any], retries: int = 3) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT, headers={"User-Agent": "CopyTheWhales/3.0"})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("Unexpected API response")
            return payload
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.25 * (attempt + 1))
    raise RuntimeError(f"Polymarket request failed: {last_error}")


@st.cache_data(ttl=300, show_spinner=False)
def get_leaderboard(top_n: int, category: str, time_period: str, order_by: str):
    rows, offset = [], 0
    while len(rows) < top_n:
        limit = min(50, top_n - len(rows))
        batch = api_get("/v1/leaderboard", {"category": category, "timePeriod": time_period, "orderBy": order_by, "limit": limit, "offset": offset})
        if not batch: break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < limit: break
    return rows[:top_n]


@st.cache_data(ttl=180, show_spinner=False)
def get_current_positions(wallet: str, include_redeemable: bool):
    rows, offset, limit = [], 0, 500
    while True:
        batch = api_get("/positions", {"user": wallet, "sizeThreshold": 0, "redeemable": str(include_redeemable).lower(), "limit": limit, "offset": offset, "sortBy": "CURRENT", "sortDirection": "DESC"})
        rows.extend(batch)
        if len(batch) < limit: break
        offset += limit
        if offset >= 10000: break
    return rows


@st.cache_data(ttl=900, show_spinner=False)
def get_closed_positions(wallet: str, max_rows: int):
    rows, offset = [], 0
    while len(rows) < max_rows:
        limit = min(50, max_rows - len(rows))
        batch = api_get("/closed-positions", {"user": wallet, "limit": limit, "offset": offset, "sortBy": "TIMESTAMP", "sortDirection": "DESC"})
        rows.extend(batch)
        if len(batch) < limit: break
        offset += len(batch)
    return rows[:max_rows]


def classify_sport(text: str) -> str:
    value = str(text or "")
    for sport, patterns in SPORT_PATTERNS.items():
        if any(re.search(p, value, flags=re.I) for p in patterns):
            return sport
    return "Other"


def normalize_current(trader, position):
    market = position.get("title") or ""
    sport = classify_sport(" ".join([market, position.get("slug") or "", position.get("eventSlug") or ""]))
    return {
        "rank": int(trader.get("rank") or 0), "trader": trader.get("userName") or trader.get("proxyWallet") or "Unknown", "wallet": trader.get("proxyWallet") or "",
        "market": market, "outcome": position.get("outcome") or "", "condition_id": position.get("conditionId") or "", "market_slug": position.get("slug") or "", "event_slug": position.get("eventSlug") or "", "sport": sport,
        "size": float(position.get("size") or 0), "entry_price": float(position.get("avgPrice") or 0), "current_price": float(position.get("curPrice") or 0), "current_value": float(position.get("currentValue") or 0), "cash_pnl": float(position.get("cashPnl") or 0), "percent_pnl": float(position.get("percentPnl") or 0), "redeemable": bool(position.get("redeemable", False)),
    }


def normalize_closed(trader, position):
    market = position.get("title") or ""
    sport = classify_sport(" ".join([market, position.get("slug") or "", position.get("eventSlug") or ""]))
    pnl = float(position.get("realizedPnl") or 0)
    bought = float(position.get("totalBought") or 0)
    result = "Win" if pnl > 1e-6 else "Loss" if pnl < -1e-6 else "Push"
    return {"trader": trader.get("userName") or trader.get("proxyWallet") or "Unknown", "wallet": trader.get("proxyWallet") or "", "market": market, "outcome": position.get("outcome") or "", "sport": sport, "realized_pnl": pnl, "total_bought": bought, "result": result, "timestamp": position.get("timestamp") or 0}


def load_data(config: Config):
    traders = get_leaderboard(config.top_n, config.category, config.time_period, config.order_by)
    current_rows, closed_rows, failures = [], [], []
    progress = st.progress(0, text="Reading whale wallets and records…")
    for idx, trader in enumerate(traders):
        wallet = trader.get("proxyWallet") or ""
        name = trader.get("userName") or wallet
        try:
            for p in get_current_positions(wallet, config.include_redeemable):
                row = normalize_current(trader, p)
                if row["current_value"] >= config.minimum_position_value:
                    current_rows.append(row)
        except Exception as exc:
            failures.append(f"Open positions — {name}: {exc}")
        try:
            for p in get_closed_positions(wallet, config.closed_history_limit):
                closed_rows.append(normalize_closed(trader, p))
        except Exception as exc:
            failures.append(f"Closed positions — {name}: {exc}")
        progress.progress((idx + 1) / max(len(traders), 1), text=f"Wallet {idx + 1} of {len(traders)}")
    progress.empty()
    return pd.DataFrame(traders), pd.DataFrame(current_rows), pd.DataFrame(closed_rows), failures


def wallet_stats(closed: pd.DataFrame, sport: str) -> pd.DataFrame:
    if closed.empty: return pd.DataFrame()
    filtered = closed if sport == "All Sports" else closed[closed["sport"] == sport]
    if filtered.empty: return pd.DataFrame()
    stats = filtered.groupby(["wallet", "trader"], dropna=False).agg(
        wins=("result", lambda s: int((s == "Win").sum())), losses=("result", lambda s: int((s == "Loss").sum())), pushes=("result", lambda s: int((s == "Push").sum())), realized_pnl=("realized_pnl", "sum"), amount_bought=("total_bought", "sum")
    ).reset_index()
    stats["decisions"] = stats["wins"] + stats["losses"]
    stats["win_rate"] = stats.apply(lambda r: r["wins"] / r["decisions"] * 100 if r["decisions"] else 0.0, axis=1)
    stats["roi"] = stats.apply(lambda r: r["realized_pnl"] / r["amount_bought"] * 100 if r["amount_bought"] > 0 else 0.0, axis=1)
    return stats


def confidence_label(score: float) -> str:
    return "High" if score >= 75 else "Medium" if score >= 55 else "Low"


def build_consensus(positions, stats, top_n, minimum_traders):
    if positions.empty: return pd.DataFrame()
    unique = positions.sort_values("current_value", ascending=False).drop_duplicates(["wallet", "condition_id", "outcome"])
    if stats.empty:
        merged = unique.copy()
        for c in ["wins", "losses", "decisions", "win_rate", "roi"]: merged[c] = 0.0
    else:
        merged = unique.merge(stats[["wallet", "wins", "losses", "decisions", "win_rate", "roi"]], on="wallet", how="left")
        for c in ["wins", "losses", "decisions", "win_rate", "roi"]: merged[c] = merged[c].fillna(0)
    result = merged.groupby(["condition_id", "market", "outcome", "sport", "market_slug", "event_slug"], dropna=False).agg(
        whales=("wallet", "nunique"), combined_value=("current_value", "sum"), median_value=("current_value", "median"), average_entry=("entry_price", "mean"), current_price=("current_price", "mean"), combined_pnl=("cash_pnl", "sum"), holder_win_rate=("win_rate", "mean"), holder_roi=("roi", "mean"), holder_wins=("wins", "sum"), holder_losses=("losses", "sum"), holder_decisions=("decisions", "sum"), holders=("trader", lambda s: ", ".join(sorted(set(map(str, s)))))
    ).reset_index()
    result = result[result["whales"] >= minimum_traders].copy()
    result["consensus_pct"] = result["whales"] / max(top_n, 1) * 100
    roi_component = ((result["holder_roi"].clip(-25, 25) + 25) / 50) * 100
    result["confidence_score"] = (0.50 * result["consensus_pct"] + 0.35 * result["holder_win_rate"] + 0.15 * roi_component).clip(0, 100)
    result["confidence"] = result["confidence_score"].map(confidence_label)
    result = result.sort_values(["confidence_score", "whales", "combined_value"], ascending=[False, False, False]).reset_index(drop=True)
    result.insert(0, "rank", range(1, len(result) + 1))
    return result


st.title("🐋 Copy the Whales")
st.write("Live Polymarket consensus, wallet records, and ROI.")

with st.expander("Filters", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        top_n = st.number_input("Top wallets", 1, 100, 20, 1)
        category = st.selectbox("Leaderboard category", ["OVERALL", "SPORTS", "POLITICS", "CRYPTO", "CULTURE", "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE"])
        sport = st.selectbox("Sport", ["All Sports"] + list(SPORT_PATTERNS.keys()))
        min_value = st.number_input("Minimum active position value", 0.0, value=100.0, step=100.0)
    with c2:
        period = st.selectbox("Leaderboard period", ["DAY", "WEEK", "MONTH", "ALL"], index=2)
        min_traders = st.number_input("Minimum wallets sharing a pick", 1, int(top_n), 2, 1)
        history_limit = st.selectbox("Closed plays used per wallet", [50, 100, 250, 500, 1000], index=3)
        include_redeemable = st.checkbox("Include resolved/redeemable active holdings", value=False)
    st.markdown("#### Wallet performance filters")
    f1, f2, f3 = st.columns(3)
    with f1: min_wallet_win_rate = st.slider("Minimum wallet win rate", 0, 100, 0, 1)
    with f2: min_wallet_roi = st.slider("Minimum wallet ROI", -100, 100, -100, 1)
    with f3: min_wallet_decisions = st.number_input("Minimum graded plays", 0, 1000, 0, 5)

refresh = st.button("Refresh live picks", type="primary", use_container_width=True)

if refresh or "current" not in st.session_state:
    cfg = Config(top_n=int(top_n), category=category, time_period=period, minimum_position_value=float(min_value), minimum_traders=int(min_traders), include_redeemable=include_redeemable, closed_history_limit=int(history_limit))
    try:
        with st.spinner("Building whale consensus and records…"):
            traders_df, current_df, closed_df, failures = load_data(cfg)
        st.session_state.update({"traders": traders_df, "current": current_df, "closed": closed_df, "failures": failures, "cfg": cfg})
    except Exception as exc:
        st.error(str(exc)); st.stop()

traders = st.session_state.get("traders", pd.DataFrame())
current = st.session_state.get("current", pd.DataFrame())
closed = st.session_state.get("closed", pd.DataFrame())
failures = st.session_state.get("failures", [])
cfg = st.session_state.get("cfg", Config())

stats = wallet_stats(closed, sport)
filtered_current = current.copy() if sport == "All Sports" or current.empty else current[current["sport"] == sport].copy()
if not stats.empty:
    qualifying = stats[(stats["win_rate"] >= min_wallet_win_rate) & (stats["roi"] >= min_wallet_roi) & (stats["decisions"] >= min_wallet_decisions)]["wallet"]
    filtered_current = filtered_current[filtered_current["wallet"].isin(qualifying)]

results = build_consensus(filtered_current, stats, cfg.top_n, int(min_traders))

if results.empty:
    st.warning("No shared live picks matched the selected filters.")
else:
    st.subheader("Top 3 confidence-ranked live bets")
    for _, row in results.head(3).iterrows():
        css = row["confidence"].lower()
        st.markdown(f'''<div class="pick-card"><div class="pick-rank">#{int(row['rank'])} confidence-ranked pick · {row['sport']}</div><div class="pick-side">{row['outcome']}</div><div class="pick-market">{row['market']}</div><div class="pick-meta">Held by <b>{int(row['whales'])}/{cfg.top_n}</b> selected wallets · ${row['combined_value']:,.0f} combined<br>Holder record: <b>{int(row['holder_wins'])}-{int(row['holder_losses'])}</b> · Win rate: <b>{row['holder_win_rate']:.1f}%</b> · ROI: <b>{row['holder_roi']:.1f}%</b><br>Confidence: <span class="{css}">{row['confidence']} — {row['confidence_score']:.1f}/100</span></div></div>''', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Wallets selected", filtered_current["wallet"].nunique())
    m2.metric("Active positions", f"{len(filtered_current):,}")
    m3.metric("Shared picks", f"{len(results):,}")
    m4.metric("Sport", sport)
    st.subheader("Consensus table")
    table = results[["rank", "sport", "market", "outcome", "whales", "holder_wins", "holder_losses", "holder_win_rate", "holder_roi", "confidence_score", "confidence", "combined_value", "average_entry", "current_price", "combined_pnl", "holders"]]
    st.dataframe(table, use_container_width=True, hide_index=True, column_config={
        "holder_win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
        "holder_roi": st.column_config.NumberColumn("ROI", format="%.1f%%"),
        "confidence_score": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%.1f"),
        "combined_value": st.column_config.NumberColumn("Combined value", format="$%,.0f"),
        "average_entry": st.column_config.NumberColumn("Avg entry", format="%.3f"),
        "current_price": st.column_config.NumberColumn("Current", format="%.3f"),
        "combined_pnl": st.column_config.NumberColumn("Open P&L", format="$%,.0f"),
    })
    st.download_button("Download consensus CSV", results.to_csv(index=False).encode(), "whale_consensus.csv", "text/csv", use_container_width=True)

st.subheader("Wallet win/loss and ROI")
if stats.empty:
    st.info("No closed-position history was available for these filters.")
else:
    stats_display = stats.sort_values(["roi", "win_rate", "decisions"], ascending=[False, False, False])
    st.dataframe(stats_display[["trader", "wins", "losses", "pushes", "decisions", "win_rate", "realized_pnl", "amount_bought", "roi"]], use_container_width=True, hide_index=True, column_config={
        "win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
        "realized_pnl": st.column_config.NumberColumn("Realized P&L", format="$%,.0f"),
        "amount_bought": st.column_config.NumberColumn("Amount bought", format="$%,.0f"),
        "roi": st.column_config.NumberColumn("ROI", format="%.1f%%"),
    })
    st.download_button("Download wallet records CSV", stats.to_csv(index=False).encode(), "wallet_records.csv", "text/csv", use_container_width=True)

with st.expander("How confidence is calculated"):
    st.write("**Confidence = 50% consensus + 35% holder win rate + 15% holder ROI.** ROI is capped between -25% and +25% inside the score so one extreme wallet cannot dominate. Win/loss and ROI come from closed positions; consensus comes only from current positions.")
with st.expander("Every qualifying active position"):
    st.dataframe(filtered_current, use_container_width=True, hide_index=True)
with st.expander("Leaderboard wallets"):
    st.dataframe(traders, use_container_width=True, hide_index=True)
if failures:
    with st.expander(f"API warnings ({len(failures)})"):
        for item in failures: st.warning(item)

st.markdown('<div class="footer-note">Win/loss is based on positive or negative realized P&L. ROI equals total realized P&L divided by total amount bought. This is an analytics tool, not a guarantee of profit.</div>', unsafe_allow_html=True)
