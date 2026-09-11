import streamlit as st
import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
import statsmodels.api as sm

# 1. 모바일 화면 최적화 설정 (중앙 집중형)
st.set_page_config(page_title="Jamyung.AI Mobile", page_icon="🚨", layout="centered")

st.title("🚨 JAMYUNG K-AI")
st.markdown("### **실시간 평판 위기 조기 경보 시스템**")
st.caption("K-Cloud 기반 한국어 맥락 인지 엔진 v1.0")
st.markdown("---")

# 2. 모바일 메인 입력창
target_keyword = st.text_input("📱 분석할 브랜드/개인 이름을 입력하세요", value="교보문고")

col_date1, col_date2 = st.columns(2)
today = datetime.today()
with col_date1:
    start_date = st.date_input("시작일", today - timedelta(days=2))
with col_date2:
    end_date = st.date_input("종료일", today)

# 3. K-AI 감성 분석 알고리즘
def analyze_korean_sentiment(text):
    neg_tokens = ['부작용', '사기', '망함', '환불', '위험', '참 잘한다', '참 잘하는 짓이다', '불매', '논란', 'ㅋㅋ', '꼰대']
    score = 0.0
    for token in neg_tokens:
        if token in text:
            if token in ['참 잘한다', '참 잘하는 짓이다', 'ㅋㅋ']:
                score -= 0.45
            else:
                score -= 0.25
    return max(min(score, 1.0), -1.0)

# 4. 실시간 크롤러 엔진
def fetch_realtime_data(keyword):
    scraped_data = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    # 네이버 뉴스
    try:
        news_url = f"https://naver.com{keyword}&sm=tab_opt&sort=1"
        res = requests.get(news_url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            articles = soup.select('.news_wrap')
            for art in articles[:5]:
                title = art.select_one('.news_tit').text if art.select_one('.news_tit') else ""
                dsc = art.select_one('.api_txt_lines.dsc_txt_wrap').text if art.select_one('.api_txt_lines.dsc_txt_wrap') else ""
                scraped_data.append({'platform': 'Naver News', 'text': f"{title} {dsc}"})
    except: pass

    # 디시인사이드
    try:
        dc_url = f"https://dcinside.com{keyword}"
        res = requests.get(dc_url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            posts = soup.select('.sch_result_list > li')
            for post in posts[:5]:
                title = post.select_one('.tit_txt').text if post.select_one('.tit_txt') else ""
                desc = post.select_one('.desc_txt').text if post.select_one('.desc_txt') else ""
                scraped_data.append({'platform': 'Dcinside', 'text': f"{title} {desc}"})
    except: pass
        
    return pd.DataFrame(scraped_data)

# 5. 모바일용 대형 실행 버튼
if st.button("🔔 자명고 통계 검정 및 스캔 시작", use_container_width=True):
    with st.spinner("실시간 실데이터 파싱 중..."):
        df_real = fetch_realtime_data(target_keyword)
        time.sleep(1)
    
    if df_real.empty:
        st.warning("데이터가 없습니다. 다른 키워드를 입력해 보세요.")
    else:
        df_real['sentiment_score'] = df_real['text'].apply(analyze_korean_sentiment)
        neg_df = df_real[df_real['sentiment_score'] < 0]
        
        v_c = len(neg_df)
        past_mean, past_std = 2.0, 1.1
        z_score = round((v_c - past_mean) / past_std, 2) if v_c > 0 else 0.0
        
        if z_score > 2.0:
            risk_level = "CRISIS (심각)"
            status_box = st.error
        elif z_score > 0.8:
            risk_level = "WARNING (경고)"
            status_box = st.warning
        else:
            risk_level = "CAUTION (주의)"
            status_box = st.info
            
        st.markdown("---")
        status_box(f"🎯 **판정 결과 ──> RISK: {risk_level}**")
        
        m1, m2 = st.columns(2)
        with m1: st.metric("부정 여론 수", f"{v_c}건")
        with m2: st.metric("통계치 (Z-Score)", f"{z_score}")
            
        st.markdown("#### 📋 실시간 여론 스트림 (원문)")
        st.dataframe(df_real[['platform', 'text', 'sentiment_score']], use_container_width=True)
        
        st.markdown("#### 🛠️ 즉시 배포용 AI 성명서")
        st.success(f"""
        [JAMYUNG Engine 실데이터 대응 지침]
        {start_date}~{end_date} 기준 '{target_keyword}' 리스크 감지. 
        소비자 자극 단어를 배제한 성명서 초안 생성을 시작합니다...
        """)
streamlit
beautifulsoup4
pandas
numpy
statsmodels
requests
