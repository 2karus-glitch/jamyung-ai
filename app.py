import re
import html
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="JAMYUNG.AI",
    page_icon="🚨",
    layout="wide",
)

# -----------------------------
# Constants
# -----------------------------
KST = timezone(timedelta(hours=9))

NEGATIVE_WORDS = {
    "논란": -3, "비판": -2, "비난": -2, "사과": -2, "불매": -3,
    "환불": -2, "피해": -3, "소송": -3, "고소": -3, "수사": -4,
    "압수수색": -5, "혐의": -4, "위반": -3, "과징금": -4,
    "벌금": -3, "사망": -5, "부상": -4, "사고": -4, "화재": -4,
    "유출": -5, "해킹": -5, "개인정보": -4, "장애": -3, "먹통": -3,
    "불량": -3, "결함": -4, "조작": -5, "허위": -4, "갑질": -4,
    "퇴출": -4, "폭로": -4, "사퇴": -3, "논쟁": -1,
}

POSITIVE_WORDS = {
    "호평": 2, "칭찬": 2, "성공": 2, "개선": 1, "회복": 2,
    "신뢰": 2, "만족": 2, "추천": 2, "수상": 2, "성장": 1,
}

RISK_KEYWORDS = {
    "법률/수사": ["소송", "고소", "수사", "압수수색", "혐의", "과징금", "벌금", "위반"],
    "소비자/환불": ["환불", "피해", "불매", "보상", "소비자"],
    "서비스/품질": ["장애", "먹통", "불량", "결함", "품질", "서비스"],
    "개인정보/보안": ["유출", "해킹", "개인정보", "보안"],
    "경영/기업": ["사퇴", "갑질", "조작", "허위", "폭로", "경영"],
    "브랜드/여론": ["논란", "비판", "비난", "불매", "퇴출"],
}

SOURCE_URL = "https://news.naver.com/main/rss/search.naver?query={}"


# -----------------------------
# Utilities
# -----------------------------
def clean_text(value):
    if value is None:
        return ""
    return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)


def score_sentiment(text):
    text = str(text or "")
    score = 0
    hits = []

    for word, weight in NEGATIVE_WORDS.items():
        if word in text:
            score += weight
            hits.append(word)

    for word, weight in POSITIVE_WORDS.items():
        if word in text:
            score += weight
            hits.append(word)

    # Light sarcasm adjustment. "ㅋㅋ" alone is not treated as negative.
    if ("참 잘한다" in text or "참 잘하는 짓" in text) and any(
        w in text for w in ["논란", "비판", "사고", "환불", "피해"]
    ):
        score -= 2
        hits.append("sarcasm")

    return score, ", ".join(dict.fromkeys(hits))


def classify_issue(text):
    text = str(text or "")
    counts = {}
    for category, words in RISK_KEYWORDS.items():
        counts[category] = sum(text.count(w) for w in words)
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "기타"


def fetch_naver_rss(keyword, start_date, end_date, timeout=10):
    """Best-effort Naver News RSS collection.

    This does not bypass access controls. If the endpoint is unavailable,
    the UI reports the collection failure instead of fabricating data.
    """
    url = SOURCE_URL.format(quote(keyword))
    headers = {
        "User-Agent": "Mozilla/5.0 JAMYUNG.AI/3.1",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "xml")
    items = soup.find_all("item")
    rows = []

    for item in items:
        title = clean_text(item.title.get_text(" ", strip=True) if item.title else "")
        link = item.link.get_text(strip=True) if item.link else ""
        pub_raw = item.pubDate.get_text(strip=True) if item.pubDate else ""

        if not title:
            continue

        pub = pd.to_datetime(pub_raw, errors="coerce", utc=True)
        if pd.isna(pub):
            continue

        pub_kst = pub.tz_convert(KST)
        d = pub_kst.date()

        if start_date <= d <= end_date:
            score, hits = score_sentiment(title)
            rows.append({
                "published": pub_kst,
                "source": "Naver News",
                "title": title,
                "url": link,
                "sentiment": score,
                "risk_hits": hits,
                "issue": classify_issue(title),
            })

    return pd.DataFrame(rows)


def simulation_data():
    rng = np.random.default_rng(42)
    now = datetime.now(KST)

    rows = []
    for i in range(45):
        if i < 10:
            offset = timedelta(hours=6 * (45 - i))
            negative = False
        else:
            offset = timedelta(hours=6 * (45 - i))
            negative = True

        base = [
            "서비스 업데이트 관련 소식",
            "기업 실적 관련 보도",
            "신제품 출시 관련 기사",
        ][i % 3]

        if negative:
            titles = [
                "서비스 장애 관련 논란과 이용자 불만 확산",
                "소비자 환불 요구와 피해 호소 이어져",
                "개인정보 유출 의혹에 수사 가능성 주목",
                "품질 문제 관련 비판 확산",
                "기업 대응을 둘러싼 논란 지속",
            ]
            title = titles[i % len(titles)]
            score = int(rng.integers(-7, -2))
        else:
            title = base
            score = int(rng.integers(-1, 3))

        rows.append({
            "published": now - offset,
            "source": ["Naver News", "Community", "Blog"][i % 3],
            "title": title,
            "url": "https://example.com",
            "sentiment": score,
            "risk_hits": "",
            "issue": classify_issue(title),
        })

    return pd.DataFrame(rows)


def build_timeseries(df):
    if df.empty:
        return pd.DataFrame()

    x = df.copy()
    x["published"] = pd.to_datetime(x["published"], errors="coerce", utc=True)
    x = x.dropna(subset=["published"]).sort_values("published")

    x["bucket"] = x["published"].dt.floor("6h")
    ts = x.groupby("bucket").agg(
        mentions=("title", "count"),
        negative_count=("sentiment", lambda s: int((s < 0).sum())),
        avg_sentiment=("sentiment", "mean"),
    ).reset_index()

    ts["negative_ratio"] = np.where(
        ts["mentions"] > 0,
        ts["negative_count"] / ts["mentions"],
        0,
    )
    return ts


def calculate_risk(df):
    if df.empty:
        return {
            "risk": 0,
            "level": "NORMAL",
            "z_score": 0,
            "velocity": 0,
            "negative_ratio": 0,
            "mean_sentiment": 0,
            "concentration": 0,
            "confidence": 0,
        }

    ts = build_timeseries(df)
    if ts.empty:
        return {
            "risk": 0, "level": "NORMAL", "z_score": 0, "velocity": 0,
            "negative_ratio": 0, "mean_sentiment": 0, "concentration": 0,
            "confidence": 0,
        }

    current = ts.iloc[-1]
    previous = ts.iloc[:-1]

    if len(previous) >= 2:
        mean = previous["negative_count"].mean()
        std = previous["negative_count"].std(ddof=0)
        z = (current["negative_count"] - mean) / std if std > 0 else 0
    else:
        z = 0

    prev_mentions = previous.iloc[-1]["mentions"] if len(previous) else 0
    velocity = (
        ((current["mentions"] - prev_mentions) / prev_mentions) * 100
        if prev_mentions > 0 else 0
    )

    negative_ratio = float(current["negative_ratio"])
    mean_sentiment = float(df["sentiment"].mean())

    if "issue" in df.columns:
        issue_share = df["issue"].value_counts(normalize=True)
        concentration = float(issue_share.iloc[0]) if len(issue_share) else 0
    else:
        concentration = 0

    volume_score = min(negative_ratio * 40, 40)
    anomaly_score = min(max(z, 0) * 8, 25)
    velocity_score = min(max(velocity, 0) / 10, 20)
    intensity_score = min(abs(min(mean_sentiment, 0)) * 15, 15)
    concentration_score = min(concentration * 5, 5)

    risk = round(
        volume_score
        + anomaly_score
        + velocity_score
        + intensity_score
        + concentration_score,
        1,
    )

    if risk >= 75:
        level = "CRISIS"
    elif risk >= 55:
        level = "WARNING"
    elif risk >= 30:
        level = "CAUTION"
    else:
        level = "NORMAL"

    n = len(df)
    sample_conf = min(n / 40, 1.0)
    source_conf = min(df["source"].nunique() / 3, 1.0) if "source" in df else 0
    confidence = round((0.6 * sample_conf + 0.4 * source_conf) * 100)

    return {
        "risk": risk,
        "level": level,
        "z_score": float(z),
        "velocity": float(velocity),
        "negative_ratio": negative_ratio,
        "mean_sentiment": mean_sentiment,
        "concentration": concentration,
        "confidence": confidence,
    }


def risk_color(level):
    return {
        "CRISIS": "#b91c1c",
        "WARNING": "#d97706",
        "CAUTION": "#ca8a04",
        "NORMAL": "#15803d",
    }.get(level, "#475569")


def playbook(level):
    if level == "CRISIS":
        return [
            "즉시 위기 대응 책임자 소집",
            "사실관계·법률 리스크 동시 검증",
            "공식 입장 및 1차 대응 메시지 신속 확정",
            "핵심 언론·고객 채널 모니터링 강화",
            "2차 확산 가능 이슈와 추가 피해 여부 점검",
        ]
    if level == "WARNING":
        return [
            "핵심 논란의 사실관계 확인",
            "대변인·고객응대 메시지 정렬",
            "주요 확산 채널 집중 모니터링",
            "부정 기사 증가 원인별 대응안 준비",
        ]
    if level == "CAUTION":
        return [
            "이슈 키워드 추적 강화",
            "반복적으로 등장하는 부정 문구 확인",
            "추가 확산 여부 관찰",
        ]
    return [
        "정상 모니터링 유지",
        "주요 키워드 및 신규 이슈 감시",
    ]


# -----------------------------
# UI
# -----------------------------
st.title("🚨 JAMYUNG.AI")
st.caption("평판 위기 조기경보 프로토타입 · v3.1")

with st.sidebar:
    st.header("분석 설정")

    keyword = st.text_input("기업/브랜드 키워드", value="삼성전자")

    today = datetime.now(KST).date()
    start_date = st.date_input(
        "시작일",
        value=today - timedelta(days=7),
        max_value=today,
    )
    end_date = st.date_input(
        "종료일",
        value=today,
        max_value=today,
    )

    mode = st.radio(
        "데이터 모드",
        ["시뮬레이션", "실데이터"],
        index=0,
    )

    run = st.button("🔎 분석 실행", type="primary", width="stretch")

    st.divider()
    st.caption("※ 현재 실데이터는 Naver News RSS 기반의 best-effort 수집입니다.")
    st.caption("※ 사전 기반 감성분석이므로 AI 모델의 판단과 동일하지 않습니다.")

if start_date > end_date:
    st.error("시작일은 종료일보다 빠르거나 같아야 합니다.")
    st.stop()

if "df" not in st.session_state:
    st.session_state.df = simulation_data()
    st.session_state.result = calculate_risk(st.session_state.df)
    st.session_state.mode = "시뮬레이션"

if run:
    if mode == "시뮬레이션":
        df = simulation_data()
        result = calculate_risk(df)
        st.session_state.df = df
        st.session_state.result = result
        st.session_state.mode = mode
    else:
        try:
            with st.spinner("뉴스 데이터를 수집하는 중..."):
                df = fetch_naver_rss(keyword, start_date, end_date)
            result = calculate_risk(df)
            st.session_state.df = df
            st.session_state.result = result
            st.session_state.mode = mode
        except Exception as e:
            st.error("실데이터 수집에 실패했습니다. 시뮬레이션 데이터로 자동 대체하지 않습니다.")
            st.exception(e)

df = st.session_state.df.copy()
result = st.session_state.result

if st.session_state.mode == "실데이터":
    df = df[
        (pd.to_datetime(df["published"], utc=True).dt.tz_convert(KST).dt.date >= start_date)
        & (pd.to_datetime(df["published"], utc=True).dt.tz_convert(KST).dt.date <= end_date)
    ]

# Header status
level = result["level"]
color = risk_color(level)

st.markdown(
    f"""
    <div style="
        border:1px solid #e2e8f0;
        border-radius:18px;
        padding:22px;
        margin:10px 0 22px 0;
        background:linear-gradient(135deg,#f8fafc,#ffffff);
    ">
      <div style="font-size:14px;color:#64748b;">현재 평판 리스크</div>
      <div style="font-size:38px;font-weight:800;color:{color};">{result["risk"]}</div>
      <div style="font-size:20px;font-weight:700;color:{color};">{level}</div>
      <div style="font-size:13px;color:#64748b;margin-top:5px;">
        분석 데이터 {len(df):,}건 · 신뢰도 {result["confidence"]}%
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("부정 비율", f'{result["negative_ratio"]*100:.1f}%')
c2.metric("이상징후 Z", f'{result["z_score"]:.2f}')
c3.metric("확산 속도", f'{result["velocity"]:+.1f}%')
c4.metric("평균 감성", f'{result["mean_sentiment"]:.2f}')
c5.metric("이슈 집중도", f'{result["concentration"]*100:.1f}%')

if level == "CRISIS":
    st.error("🚨 CRISIS — 즉시 사실관계 확인과 위기 대응 체계를 가동하는 수준입니다.")
elif level == "WARNING":
    st.warning("⚠️ WARNING — 부정 신호가 뚜렷하게 증가하고 있습니다.")
elif level == "CAUTION":
    st.info("🟡 CAUTION — 주의 깊은 모니터링이 필요한 상태입니다.")
else:
    st.success("🟢 NORMAL — 현재 측정된 신호는 비교적 안정적입니다.")

# Time series
st.subheader("📈 평판 위험 추이")

ts = build_timeseries(df)
if not ts.empty:
    chart_df = ts.set_index("bucket")[["mentions", "negative_count", "negative_ratio"]]
    st.line_chart(chart_df, width="stretch")
else:
    st.info("추이 데이터를 만들 만큼의 데이터가 없습니다.")

# Issue analysis
left, right = st.columns(2)

with left:
    st.subheader("🧩 이슈 유형")
    if not df.empty:
        issue_counts = df["issue"].value_counts()
        st.bar_chart(issue_counts, width="stretch")
    else:
        st.info("이슈 데이터가 없습니다.")

with right:
    st.subheader("🔑 위험 키워드 TOP 10")
    counts = {}
    for text in df.get("risk_hits", pd.Series(dtype=str)).fillna(""):
        for word in str(text).split(", "):
            if word:
                counts[word] = counts.get(word, 0) + 1

    if counts:
        kw = (
            pd.Series(counts, name="count")
            .sort_values(ascending=False)
            .head(10)
            .to_frame()
        )
        st.dataframe(kw, width="stretch")
    else:
        st.info("감지된 위험 키워드가 없습니다.")

# Playbook
st.subheader("🛡️ 권고 대응 플레이북")

for i, action in enumerate(playbook(level), 1):
    st.write(f"**{i}.** {action}")

# Raw stream
st.subheader("📰 원문 스트림")

if not df.empty:
    display_df = df.copy()
    display_df["published"] = pd.to_datetime(display_df["published"], utc=True).dt.tz_convert(KST).dt.strftime("%Y-%m-%d %H:%M")
    display_df["sentiment"] = display_df["sentiment"].astype(int)

    st.dataframe(
        display_df[["published", "source", "issue", "sentiment", "title", "url"]],
        column_config={
            "url": st.column_config.LinkColumn("원문", display_text="열기"),
            "title": st.column_config.TextColumn("제목", width="large"),
        },
        hide_index=True,
        width="stretch",
    )

    csv = display_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ CSV 다운로드",
        data=csv,
        file_name=f"jamyung_{keyword}_{today}.csv",
        mime="text/csv",
    )
else:
    st.info("표시할 데이터가 없습니다.")

with st.expander("🔧 개발자 진단"):
    st.write({
        "version": "3.1",
        "mode": st.session_state.mode,
        "keyword": keyword,
        "date_range": f"{start_date} ~ {end_date}",
        "rows": len(df),
        "sources": sorted(df["source"].dropna().unique().tolist()) if not df.empty else [],
    })

st.caption("JAMYUNG.AI v3.1 · Rule-based reputation risk prototype")
