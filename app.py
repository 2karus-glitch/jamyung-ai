import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

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

NAVER_RSS_URL = "https://news.naver.com/main/rss/search.naver?query={}"
GOOGLE_RSS_URL = "https://news.google.com/rss/search?q={}&hl=ko&gl=KR&ceid=KR:ko"

SOURCES = {
    "Naver News": NAVER_RSS_URL,
    "Google News": GOOGLE_RSS_URL,
}

ALERT_LEVEL_ORDER = {"NORMAL": 0, "CAUTION": 1, "WARNING": 2, "CRISIS": 3}

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
LLM_MODEL_OPTIONS = {
    "Claude Haiku 4.5 (빠름/저렴)": "claude-haiku-4-5-20251001",
    "Claude Sonnet 5 (정확도 우선)": "claude-sonnet-5",
}


# -----------------------------
# Utilities
# -----------------------------
def clean_text(value):
    if value is None:
        return ""
    return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)


def score_sentiment_rule(text):
    """Dictionary-based fallback sentiment scorer."""
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


def normalize_title_for_dedup(title):
    t = re.sub(r"\s+", " ", str(title)).strip().lower()
    t = re.sub(r"[^\w가-힣 ]", "", t)
    return t


def dedup_articles(df):
    """Drop near-duplicate articles (same story picked up by multiple sources)."""
    if df.empty:
        return df
    out = df.copy()
    out["_dedup_key"] = out["title"].map(normalize_title_for_dedup)
    out = out.sort_values("published").drop_duplicates(subset="_dedup_key", keep="first")
    return out.drop(columns="_dedup_key").reset_index(drop=True)


def fetch_rss_source(source_name, url_template, keyword, start_date, end_date, timeout=10):
    """Best-effort RSS collection for a single source.

    This does not bypass access controls. If the endpoint is unavailable,
    the caller reports the collection failure instead of fabricating data.
    """
    url = url_template.format(quote(keyword))
    headers = {
        "User-Agent": "Mozilla/5.0 JAMYUNG.AI/4.0",
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
            score, hits = score_sentiment_rule(title)
            rows.append({
                "published": pub_kst,
                "source": source_name,
                "title": title,
                "url": link,
                "sentiment": score,
                "risk_hits": hits,
                "issue": classify_issue(title),
            })

    return pd.DataFrame(rows)


def collect_realtime(keyword, start_date, end_date, enabled_sources):
    """Collect from all enabled sources. Partial failures are reported, never hidden."""
    frames = []
    errors = []

    for name in enabled_sources:
        url_template = SOURCES[name]
        try:
            frame = fetch_rss_source(name, url_template, keyword, start_date, end_date)
            frames.append(frame)
        except Exception as e:
            errors.append((name, str(e)))

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined = dedup_articles(combined)
    else:
        combined = pd.DataFrame()

    return combined, errors


def simulation_data():
    rng = np.random.default_rng(42)
    now = datetime.now(KST)

    rows = []
    for i in range(45):
        offset = timedelta(hours=6 * (45 - i))
        negative = i >= 10

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


# -----------------------------
# AI (optional) sentiment scoring
# -----------------------------
def _extract_json_array(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def llm_sentiment_batch(df, api_key, model, batch_size=20, timeout=30):
    """Score sentiment/issue with Claude. Falls back to rule-based scores per-row on any failure.

    Returns (df_with_scores, ai_scored_count, failed_count).
    """
    out = df.copy().reset_index(drop=True)
    ai_scored = 0
    failed = 0

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    categories = list(RISK_KEYWORDS.keys()) + ["기타"]

    for start in range(0, len(out), batch_size):
        chunk = out.iloc[start:start + batch_size]
        items = [{"i": int(idx), "title": row["title"]} for idx, row in chunk.iterrows()]

        prompt = (
            "다음은 기업/브랜드 평판 모니터링을 위한 한국어 뉴스 제목 목록입니다. "
            "각 제목에 대해 기업 평판 관점에서 감성 점수와 이슈 유형을 매기세요.\n\n"
            f"이슈 유형은 반드시 다음 중 하나여야 합니다: {categories}\n\n"
            "각 항목에 대해 다음 JSON 스키마로만 응답하세요. 다른 설명, 코드블록, 텍스트를 "
            "절대 포함하지 마세요:\n"
            '[{"i": <int>, "sentiment": <int, -5~5, 5=매우 긍정 -5=매우 부정>, '
            '"issue": <string>, "risk_hits": <string, 위험 키워드 콤마구분 또는 빈 문자열>}]\n\n'
            f"제목 목록:\n{json.dumps(items, ensure_ascii=False)}"
        )

        payload = {
            "model": model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            parsed = _extract_json_array("".join(text_blocks))

            for entry in parsed:
                idx = entry.get("i")
                if idx is None or idx not in out.index:
                    continue
                sentiment = entry.get("sentiment")
                issue = entry.get("issue")
                risk_hits = entry.get("risk_hits", "")
                if isinstance(sentiment, (int, float)):
                    out.at[idx, "sentiment"] = int(sentiment)
                    ai_scored += 1
                if isinstance(issue, str) and issue in categories:
                    out.at[idx, "issue"] = issue
                if isinstance(risk_hits, str):
                    out.at[idx, "risk_hits"] = risk_hits
        except Exception:
            # Fall back silently to the rule-based scores already present for this chunk.
            failed += len(chunk)

    return out, ai_scored, failed


# -----------------------------
# AI (optional) issue clustering
# -----------------------------
def cluster_issues(df, max_clusters=6, min_rows=8):
    if not SKLEARN_AVAILABLE or len(df) < min_rows:
        return None

    titles = df["title"].astype(str).tolist()
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, max_features=3000)
    try:
        X = vectorizer.fit_transform(titles)
    except ValueError:
        return None

    k = max(2, min(max_clusters, len(df) // 5))
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = km.fit_predict(X)

    terms = vectorizer.get_feature_names_out()
    order = km.cluster_centers_.argsort()[:, ::-1]

    cluster_names = {}
    for c in range(k):
        top = []
        for idx in order[c]:
            term = terms[idx].strip()
            if len(term) >= 2 and term not in top:
                top.append(term)
            if len(top) == 3:
                break
        cluster_names[c] = " ".join(top) if top else f"클러스터 {c + 1}"

    out = df.copy()
    out["issue_cluster"] = [cluster_names[label] for label in labels]
    return out


# -----------------------------
# Risk calculation (rolling baseline)
# -----------------------------
def build_timeseries(df, bucket_hours=6):
    if df.empty:
        return pd.DataFrame()

    x = df.copy()
    x["published"] = pd.to_datetime(x["published"], errors="coerce", utc=True)
    x = x.dropna(subset=["published"]).sort_values("published")

    x["bucket"] = x["published"].dt.floor(f"{bucket_hours}h")
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


def empty_risk_result():
    return {
        "risk": 0, "level": "NORMAL", "z_score": 0, "velocity": 0,
        "negative_ratio": 0, "mean_sentiment": 0, "concentration": 0,
        "confidence": 0, "baseline_points": 0, "baseline_days": 0,
    }


def calculate_risk(df, bucket_hours=6):
    if df.empty:
        return empty_risk_result()

    ts = build_timeseries(df, bucket_hours=bucket_hours)
    if ts.empty:
        return empty_risk_result()

    current = ts.iloc[-1]
    baseline = ts.iloc[:-1]  # rolling baseline: every bucket before the current one

    if len(baseline) >= 2:
        mean = baseline["negative_count"].mean()
        std = baseline["negative_count"].std(ddof=0)
        z = (current["negative_count"] - mean) / std if std > 0 else 0
    else:
        z = 0

    prev_mentions = baseline.iloc[-1]["mentions"] if len(baseline) else 0
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
        volume_score + anomaly_score + velocity_score + intensity_score + concentration_score, 1
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
    baseline_conf = min(len(baseline) / 8, 1.0)
    confidence = round((0.5 * sample_conf + 0.2 * source_conf + 0.3 * baseline_conf) * 100)

    baseline_days = 0
    if len(baseline):
        span = baseline["bucket"].max() - baseline["bucket"].min()
        baseline_days = round(span.total_seconds() / 86400, 1)

    return {
        "risk": risk,
        "level": level,
        "z_score": float(z),
        "velocity": float(velocity),
        "negative_ratio": negative_ratio,
        "mean_sentiment": mean_sentiment,
        "concentration": concentration,
        "confidence": confidence,
        "baseline_points": len(baseline),
        "baseline_days": baseline_days,
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
# Webhook alerts
# -----------------------------
def send_webhook_alert(url, text, timeout=6):
    try:
        r = requests.post(url, json={"text": text}, timeout=timeout)
        return r.status_code < 400, str(r.status_code)
    except Exception as e:
        return False, str(e)


# -----------------------------
# UI — Sidebar
# -----------------------------
st.title("🚨 JAMYUNG.AI")
st.caption("평판 위기 조기경보 프로토타입 · v4.0 (데모)")

if "alert_log" not in st.session_state:
    st.session_state.alert_log = []
if "last_alert_level" not in st.session_state:
    st.session_state.last_alert_level = None

with st.sidebar:
    st.header("분석 설정")

    keyword = st.text_input("기업/브랜드 키워드", value="삼성전자")

    today = datetime.now(KST).date()
    start_date = st.date_input("시작일", value=today - timedelta(days=7), max_value=today)
    end_date = st.date_input("종료일", value=today, max_value=today)

    mode = st.radio("데이터 모드", ["시뮬레이션", "실데이터"], index=0)

    enabled_sources = []
    if mode == "실데이터":
        enabled_sources = st.multiselect(
            "뉴스 소스", list(SOURCES.keys()), default=list(SOURCES.keys())
        )

    st.divider()
    with st.expander("⚙️ 고급 설정"):
        bucket_hours = st.selectbox("시계열 버킷 간격(시간)", [6, 12, 24], index=0)

        st.caption("AI 기반 감성분석 (선택) — 사전 기반 규칙 대신 Claude로 문맥을 판단합니다.")
        use_ai_sentiment = st.checkbox("AI 감성분석 사용", value=False)
        api_key = ""
        llm_model_label = list(LLM_MODEL_OPTIONS.keys())[0]
        if use_ai_sentiment:
            api_key = st.text_input("Anthropic API Key", type="password")
            llm_model_label = st.selectbox("모델", list(LLM_MODEL_OPTIONS.keys()))
            st.caption("키는 세션에만 사용되며 저장되지 않습니다.")

        st.caption("AI 이슈 클러스터링 (선택) — 고정 카테고리 대신 제목 유사도로 군집화합니다.")
        use_ai_clustering = st.checkbox(
            "AI 이슈 클러스터링 사용",
            value=False,
            disabled=not SKLEARN_AVAILABLE,
            help=None if SKLEARN_AVAILABLE else "scikit-learn이 설치되어 있지 않습니다.",
        )

    with st.expander("🔔 웹훅 알림 (선택)"):
        webhook_url = st.text_input("웹훅 URL (Slack/Discord 호환)", value="")
        alert_threshold = st.selectbox("알림 기준 등급", ["WARNING", "CRISIS"], index=0)
        if st.button("테스트 알림 전송", width="stretch"):
            if not webhook_url:
                st.warning("웹훅 URL을 먼저 입력하세요.")
            else:
                ok, info = send_webhook_alert(
                    webhook_url,
                    f"[JAMYUNG.AI 테스트] '{keyword}' 모니터링 웹훅 연결 테스트입니다.",
                )
                if ok:
                    st.success("테스트 알림을 전송했습니다.")
                else:
                    st.error(f"전송 실패: {info}")

    run = st.button("🔎 분석 실행", type="primary", width="stretch")

    st.divider()
    st.caption("※ 실데이터는 공개 뉴스 RSS 기반의 best-effort 수집입니다.")
    st.caption("※ AI 기능을 켜지 않으면 사전 기반 감성분석이 사용됩니다.")

if start_date > end_date:
    st.error("시작일은 종료일보다 빠르거나 같아야 합니다.")
    st.stop()

# -----------------------------
# Run analysis
# -----------------------------
if "df" not in st.session_state:
    st.session_state.df = simulation_data()
    st.session_state.result = calculate_risk(st.session_state.df, bucket_hours=6)
    st.session_state.mode = "시뮬레이션"
    st.session_state.bucket_hours = 6
    st.session_state.ai_sentiment_used = False
    st.session_state.ai_cluster_df = None
    st.session_state.source_errors = []

if run:
    if mode == "시뮬레이션":
        df = simulation_data()
        ai_used = False
        source_errors = []
    else:
        if not enabled_sources:
            st.error("최소 하나 이상의 뉴스 소스를 선택하세요.")
            st.stop()
        with st.spinner("뉴스 데이터를 수집하는 중..."):
            df, source_errors = collect_realtime(keyword, start_date, end_date, enabled_sources)

        if df.empty:
            st.error("실데이터 수집에 모두 실패했습니다. 시뮬레이션 데이터로 자동 대체하지 않습니다.")
            for name, err in source_errors:
                st.exception(RuntimeError(f"{name}: {err}"))
            st.stop()

        if source_errors:
            failed_names = ", ".join(name for name, _ in source_errors)
            st.warning(f"일부 소스 수집에 실패했습니다 ({failed_names}). 나머지 소스 데이터로 계속합니다.")

        ai_used = False
        if use_ai_sentiment:
            if not api_key:
                st.warning("API Key가 없어 AI 감성분석을 건너뛰고 사전 기반 규칙을 사용합니다.")
            else:
                with st.spinner("AI 감성분석 중..."):
                    model_id = LLM_MODEL_OPTIONS[llm_model_label]
                    df, ai_scored, ai_failed = llm_sentiment_batch(df, api_key, model_id)
                ai_used = ai_scored > 0
                if ai_failed:
                    st.info(f"{ai_scored}건은 AI로, {ai_failed}건은 규칙 기반으로 채점되었습니다.")

    cluster_df = None
    if use_ai_clustering and not df.empty:
        cluster_df = cluster_issues(df)
        if cluster_df is None:
            st.caption("클러스터링에 필요한 최소 기사 수(8건)를 채우지 못해 생략되었습니다.")

    result = calculate_risk(df, bucket_hours=bucket_hours)

    st.session_state.df = df
    st.session_state.result = result
    st.session_state.mode = mode
    st.session_state.bucket_hours = bucket_hours
    st.session_state.ai_sentiment_used = ai_used
    st.session_state.ai_cluster_df = cluster_df
    st.session_state.source_errors = source_errors

df = st.session_state.df.copy()
result = st.session_state.result
bucket_hours = st.session_state.get("bucket_hours", 6)

if st.session_state.mode == "실데이터":
    df = df[
        (pd.to_datetime(df["published"], utc=True).dt.tz_convert(KST).dt.date >= start_date)
        & (pd.to_datetime(df["published"], utc=True).dt.tz_convert(KST).dt.date <= end_date)
    ]

level = result["level"]
color = risk_color(level)

# -----------------------------
# Alerting (fires once per level escalation, not on every rerun)
# -----------------------------
if webhook_url and ALERT_LEVEL_ORDER[level] >= ALERT_LEVEL_ORDER[alert_threshold]:
    last = st.session_state.last_alert_level
    should_alert = last is None or ALERT_LEVEL_ORDER[level] > ALERT_LEVEL_ORDER.get(last, -1)
    if should_alert:
        msg = f"[JAMYUNG.AI] '{keyword}' 평판 리스크가 {level} 단계입니다. (Risk Score {result['risk']})"
        ok, info = send_webhook_alert(webhook_url, msg)
        st.session_state.alert_log.append({
            "time": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "risk": result["risk"],
            "sent_ok": ok,
            "detail": info,
        })
        st.session_state.last_alert_level = level
elif ALERT_LEVEL_ORDER[level] < ALERT_LEVEL_ORDER.get(st.session_state.last_alert_level or "NORMAL", 0):
    # Risk has cooled down — reset so a future re-escalation can alert again.
    st.session_state.last_alert_level = level

# -----------------------------
# UI — Header status
# -----------------------------
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
        · 베이스라인 {result["baseline_points"]}개 구간(약 {result["baseline_days"]}일)
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

badges = []
if st.session_state.mode == "실데이터":
    badges.append(f"소스: {', '.join(enabled_sources) if enabled_sources else '-'}")
if st.session_state.get("ai_sentiment_used"):
    badges.append("AI 감성분석 적용")
if st.session_state.get("ai_cluster_df") is not None:
    badges.append("AI 이슈 클러스터링 적용")
if badges:
    st.caption(" · ".join(badges))

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
ts = build_timeseries(df, bucket_hours=bucket_hours)
if not ts.empty:
    chart_df = ts.set_index("bucket")[["mentions", "negative_count", "negative_ratio"]]
    st.line_chart(chart_df, width="stretch")
else:
    st.info("추이 데이터를 만들 만큼의 데이터가 없습니다.")

# Issue analysis
left, right = st.columns(2)

with left:
    st.subheader("🧩 이슈 유형 (규칙 기반)")
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
        kw = pd.Series(counts, name="count").sort_values(ascending=False).head(10).to_frame()
        st.dataframe(kw, width="stretch")
    else:
        st.info("감지된 위험 키워드가 없습니다.")

# AI clustering (optional)
cluster_df = st.session_state.get("ai_cluster_df")
if cluster_df is not None:
    st.subheader("🧠 AI 이슈 클러스터링 (실험적)")
    st.caption("제목 유사도 기반 자동 군집화 결과입니다. 고정 카테고리보다 세분화된 주제를 보여줄 수 있습니다.")
    cluster_counts = cluster_df["issue_cluster"].value_counts()
    st.bar_chart(cluster_counts, width="stretch")

# Playbook
st.subheader("🛡️ 권고 대응 플레이북")
for i, action in enumerate(playbook(level), 1):
    st.write(f"**{i}.** {action}")

# Alert log
if st.session_state.alert_log:
    with st.expander(f"🔔 알림 로그 ({len(st.session_state.alert_log)}건, 세션 내)"):
        st.dataframe(pd.DataFrame(st.session_state.alert_log), hide_index=True, width="stretch")

# Raw stream
st.subheader("📰 원문 스트림")

if not df.empty:
    display_df = df.copy()
    display_df["published"] = pd.to_datetime(display_df["published"], utc=True).dt.tz_convert(KST).dt.strftime("%Y-%m-%d %H:%M")
    display_df["sentiment"] = display_df["sentiment"].astype(int)

    cols = ["published", "source", "issue", "sentiment", "title", "url"]
    if cluster_df is not None:
        display_df = display_df.merge(
            cluster_df[["title", "issue_cluster"]].drop_duplicates("title"),
            on="title", how="left",
        )
        cols.insert(3, "issue_cluster")

    st.dataframe(
        display_df[cols],
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
        "version": "4.0",
        "mode": st.session_state.mode,
        "keyword": keyword,
        "date_range": f"{start_date} ~ {end_date}",
        "bucket_hours": bucket_hours,
        "rows": len(df),
        "sources": sorted(df["source"].dropna().unique().tolist()) if not df.empty else [],
        "source_errors": st.session_state.get("source_errors", []),
        "ai_sentiment_used": st.session_state.get("ai_sentiment_used", False),
        "ai_clustering_used": cluster_df is not None,
        "sklearn_available": SKLEARN_AVAILABLE,
        "baseline_points": result["baseline_points"],
        "baseline_days": result["baseline_days"],
    })

st.caption("JAMYUNG.AI v4.0 · Rule-based + optional AI-assisted reputation risk prototype")
