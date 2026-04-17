import sys
import os
import json
import urllib.parse
import time
from datetime import datetime, timedelta, timezone

import requests
import feedparser
import openpyxl
import yfinance as yf
import pandas as pd
from bs4 import BeautifulSoup
from dateutil import parser
from openpyxl.styles import Font, PatternFill, Alignment

# ─────────────────────────────────────
# [1] 설정 및 매체별 타임존 매핑 테이블
# ─────────────────────────────────────
PROVIDER_CONFIG = {
    "Al Jazeera": {"url": "https://www.aljazeera.com/xml/rss/all.xml", "tz": 3, "loc": "국외"},
    "BBC": {"url": "http://feeds.bbci.co.uk/news/world/rss.xml", "tz": 0, "loc": "국외"},
    "Reuters": {"url": "https://www.reuters.com/arc/outboundfeeds/rss/topics/world/", "tz": 0, "loc": "국외"},
    "AP": {"url": "https://apnews.com/rss/world-news", "tz": -5, "loc": "국외"},
    "TASS": {"url": "https://tass.com/rss/v2.xml", "tz": 3, "loc": "국외"},
    "Kyodo": {"url": "https://english.kyodonews.net/rss/news.xml", "tz": 9, "loc": "국외"},
    "Xinhua": {"url": "http://www.xinhuanet.com/english/rss/worldrss.xml", "tz": 8, "loc": "국외"},
    "Google_KR": {"url": "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko", "tz": 9, "loc": "국내"}
}

CATEGORIES = {
    "원인/공급 요인": [ "전쟁", "분쟁", "산유국", "OPEC", "가스", "기름 폭등", "석유",  "석유 폭등", "oil", "gas", "oil price"],
    "에너지/인프라": ["발전소", "전력", "전기", "전기료", "전기 요금", "요금 인상", "요금 폭등", "에너지", "energy", "항만", "LNG", "energy crisis"],
    "시장/금융": [ "금리", "환율", "주식", "채권", "물가", "인플레이션", "스테그플레이션", "interest", "stock", "exchange", "inflation", "Stagflation", "rate hike"],
    "생활/영향": ["전기료", "택시", "배달", "물류", "화물차", "민생", "요금", "Electricity bill", "Taxi", "Delivery", "Logistics", "Truck"],
    "정책/대응": ["지원금", "보조금", "대책", "규제", "예산", "지자체", "policy", "response", "subsidy", "tariff", "utility bill"]
}

# [추가] 유튜브 채널 분류 데이터
YOUTUBE_CHANNELS = {
    "경제·금융 지식": [
        {"name": "슈카월드", "id": "UCsJ6RuBiTVWRX156FVbeaGg"},
        {"name": "삼프로TV", "id": "UChlv4GSd7OQl3js-jkLOnFA"},
    ],
    "주식 및 테크": [
        {"name": "염블리(염승환)", "id": "UCDnIfMiHBNs1RP7y6B6wf1Q"}, # 예시 ID
        {"name": "월급쟁이 부자들", "id": "UCDSj40X9FFUAnx1nv7gQhcA"}
    ],
    "자산 및 금리": [
        {"name": "이효석의 입풀", "id": "UCxvdCnvGODDyuvnELnLkQWw"},
    ],
    "핵심 브리핑": [
        {"name": "소수몽키", "id": "UCC3yfxS5qC6PCwDzetUuEWg"},
        {"name": "경제갤러리", "id": "UCuPWXwE1pnjhNIZ2AStzOVA"},
        {"name": "유쾌한경제학", "id": "UCaLsqraxQaaRfm7HyT1x6ug"}
    ]
}

OUTPUT_XLSX = "news_result.xlsx"
OUTPUT_HTML = "docs/market.html"
DAYS_RANGE = 3

# ─────────────────────────────────────
# [2] 기능 함수부
# ─────────────────────────────────────

def classify_category(title):
    title_lower = title.lower()
    for cat, kws in CATEGORIES.items():
        if any(kw in title_lower for kw in kws):
            return cat
    return "기타"

def get_econ_indicators():
    print("  - 경제 지표 수집 중...")
    try:
        tickers = {"환율(USD/KRW)": "USDKRW=X", "금값": "GC=F", "WTI유가": "CL=F"}
        data = yf.download(list(tickers.values()), period="1mo", interval="1d")['Close']
        data.columns = [list(tickers.keys())[list(tickers.values()).index(col)] for col in data.columns]
        return data.dropna().tail(10).to_dict('list'), data.index.strftime('%m-%d').tolist()
    except:
        return {}, []

def collect_news():
    articles = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=DAYS_RANGE)
    kst = timezone(timedelta(hours=9))

    print("  - 국내외 뉴스 통합 수집 및 시차 교정 중...")
    for name, config in PROVIDER_CONFIG.items():
        url = config["url"]
        if name == "Google_KR":
            q = urllib.parse.quote("수원시 OR 가스 OR 금리 OR 정책")
            url = url.format(q=q)
        feed = feedparser.parse(url)
        for entry in feed.entries:
            try:
                raw_date = parser.parse(entry.published)
                if raw_date.tzinfo is None:
                    raw_date = raw_date.replace(tzinfo=timezone(timedelta(hours=config["tz"])))
                pub_utc = raw_date.astimezone(timezone.utc)
                if pub_utc < cutoff: continue
                crawled_utc = datetime.now(timezone.utc)
                articles.append({
                    "지역": config["loc"], "언론사": name, "제목": entry.title, "링크": entry.link,
                    "분류": classify_category(entry.title), "발행UTC": pub_utc,
                    "수집UTC": crawled_utc, "발행KST": pub_utc.astimezone(kst)
                })
            except: continue
    return sorted(articles, key=lambda x: x['발행UTC'], reverse=True)

# ─────────────────────────────────────
# [3] 출력 및 저장부 (유튜브 탭 기능 추가)
# ─────────────────────────────────────

def save_all(articles, econ_data, econ_dates):
    # 1. 엑셀 저장
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monitoring_Data"
    header = ["지역", "분류", "언론사", "기사제목", "발행시간(KST)", "시스템수집시간(UTC)", "원문링크"]
    ws.append(header)
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E79")
        cell.font = Font(color="FFFFFF", bold=True)
    for a in articles:
        ws.append([a["지역"], a["분류"], a["언론사"], a["제목"], a["발행KST"].strftime("%Y-%m-%d %H:%M"), a["수집UTC"].strftime("%Y-%m-%d %H:%M"), a["링크"]])
    wb.save(OUTPUT_XLSX)

    # 2. 유튜브 탭 생성 로직
    tab_buttons = ""
    tab_contents = ""
    for i, (group, channels) in enumerate(YOUTUBE_CHANNELS.items()):
        active_class = "active" if i == 0 else ""
        display_style = "display:block" if i == 0 else "display:none"
        tab_buttons += f'<button class="tablinks {active_class}" onclick="openYoutubeTab(event, \'tab{i}\')">{group}</button>'
        
        video_grid = '<div class="video-grid">'
        for ch in channels:
            video_grid += f'''
            <div class="video-item">
                <small>{ch["name"]}</small>
                <iframe width="100%" height="180" src="https://www.youtube.com/embed?listType=user_uploads&list={ch["id"]}" frameborder="0" allowfullscreen></iframe>
            </div>'''
        video_grid += '</div>'
        tab_contents += f'<div id="tab{i}" class="tabcontent" style="{display_style}">{video_grid}</div>'

    # 3. HTML 생성
    top5_html = "".join([f"<li><b>[{a['언론사']}]</b> {a['제목']}</li>" for a in articles[:5]])
    news_rows = "".join([f"<tr><td>{a['발행KST'].strftime('%m-%d %H:%M')}</td><td><span class='badge {a['지역']}'>{a['지역']}</span></td><td>{a['분류']}</td><td>{a['언론사']}</td><td><a href='{a['링크']}' target='_blank'>{a['제목']}</a></td></tr>" for a in articles])

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">
        <title>통합 마켓 인사이트</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: 'Malgun Gothic', sans-serif; background: #f0f2f5; margin: 30px; }}
            .container {{ max-width: 1400px; margin: auto; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
            .card {{ background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
            /* 유튜브 탭 스타일 */
            .tab {{ overflow: hidden; border-bottom: 2px solid #1e3a8a; margin-bottom: 15px; }}
            .tab button {{ background-color: inherit; float: left; border: none; outline: none; cursor: pointer; padding: 10px 20px; transition: 0.3s; font-weight: bold; color: #666; }}
            .tab button:hover {{ background-color: #ddd; }}
            .tab button.active {{ color: #1e3a8a; border-bottom: 3px solid #1e3a8a; }}
            .video-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
            .video-item {{ background: #f9f9f9; padding: 5px; border-radius: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th {{ background: #1e3a8a; color: white; padding: 12px; }}
            td {{ padding: 10px; border-bottom: 1px solid #eee; font-size: 13px; }}
            .badge {{ padding: 3px 8px; border-radius: 10px; color: white; font-size: 11px; }}
            .국내 {{ background: #3b82f6; }} .국외 {{ background: #ef4444; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h1>📈 통합 마켓 인사이트 대시보드</h1>
                <a href="{OUTPUT_XLSX}" style="background:#10b981; color:white; padding:10px 20px; text-decoration:none; border-radius:8px; font-weight:bold;">엑셀 저장</a>
            </div>

            <div class="grid">
                <div class="card">
                    <h3>📊 주요 경제 지표</h3>
                    <div style="height:300px;"><canvas id="econChart"></canvas></div>
                </div>
                <div class="card">
                    <h3>🎥 전문가 분석 (채널 분류)</h3>
                    <div class="tab">{tab_buttons}</div>
                    {tab_contents}
                </div>
            </div>

            <div class="card" style="margin-bottom:20px;">
                <h3>🔥 실시간 뉴스 Top 5</h3>
                <ul>{top5_html}</ul>
            </div>

            <div class="card">
                <h3>📰 뉴스 타임라인</h3>
                <table>
                    <thead><tr><th>시간(KST)</th><th>지역</th><th>분류</th><th>언론사</th><th>제목</th></tr></thead>
                    <tbody>{news_rows}</tbody>
                </table>
            </div>
        </div>

        <script>
            function openYoutubeTab(evt, tabName) {{
                var i, tabcontent, tablinks;
                tabcontent = document.getElementsByClassName("tabcontent");
                for (i = 0; i < tabcontent.length; i++) {{ tabcontent[i].style.display = "none"; }}
                tablinks = document.getElementsByClassName("tablinks");
                for (i = 0; i < tablinks.length; i++) {{ tablinks[i].className = tablinks[i].className.replace(" active", ""); }}
                document.getElementById(tabName).style.display = "block";
                evt.currentTarget.className += " active";
            }}

            new Chart(document.getElementById('econChart'), {{
                type: 'line',
                data: {{
                    labels: {json.dumps(econ_dates)},
                    datasets: [
                        {{ label: '환율', data: {json.dumps(econ_data.get('환율(USD/KRW)', []))}, borderColor: '#3b82f6', tension: 0.1 }},
                        {{ label: '금값', data: {json.dumps(econ_data.get('금값', []))}, borderColor: '#f59e0b', tension: 0.1 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false }}
            }});
        </script>
    </body>
    </html>
    """
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

# ─────────────────────────────────────
# [4] 실행부
# ─────────────────────────────────────
if __name__ == "__main__":
    print("="*50)
    print("통합 파이프라인 가동...")
    econ_data, econ_dates = get_econ_indicators()
    all_news = collect_news()
    save_all(all_news, econ_data, econ_dates)
    print("✅ 완료! index.html을 확인하세요.")
    print("="*50)