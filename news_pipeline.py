# news_pipeline.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import urllib.parse
import time
import os
import re

# ─────────────────────────────────────
# ★ 설정
# ─────────────────────────────────────
SMTP_HOST   = "smtp.mailplug.co.kr"
SMTP_PORT   = 465
MAIL_USER   = "jsjang@suwon.re.kr"
MAIL_APP_PW = "9osUB*1AjYKD%-lpkn<O"
RECIPIENTS  = [
    "jsjang@suwon.re.kr",
    "jwlee@suwon.re.kr",
    "tw.kang@suwon.re.kr",
    "jineon@suwon.re.kr",
]
OUTPUT_XLSX = r"C:\news_pipeline\news_result.xlsx"
OUTPUT_HTML = r"C:\news_pipeline\docs\index.html"
DAYS_RANGE  = 30

KEYWORDS = {
    "관광 및 K-컬처": ["K-컬처", "K-culture", "K-푸드", "K-food", "K-POP", "한류"],
    "혁신산업":       ["바이오", "반도체", "AI", "인공지능"],
    "경제자유구역":   ["경제자유구역", "경자특구", "경자구역"],
    "수원시 관련":    ["수원시", "수원특례시"],
}

CATEGORY_COLORS = {
    "관광 및 K-컬처": {"bg": "#D6E4F0", "text": "#0C447C", "header": "#2E75B6"},
    "혁신산업":       {"bg": "#D5F5E3", "text": "#27500A", "header": "#2E8B57"},
    "경제자유구역":   {"bg": "#FEF9E7", "text": "#633806", "header": "#D4A017"},
    "수원시 관련":    {"bg": "#FDEDEC", "text": "#A32D2D", "header": "#C0392B"},
}

# ─────────────────────────────────────
# 1. Google News RSS 크롤링
# ─────────────────────────────────────
def get_google_news(keyword: str, category: str) -> list[dict]:
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    results = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_RANGE)
    for entry in feed.entries:
        try:
            pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            continue
        if pub < cutoff:
            continue
        source = entry.get("source", {})
        results.append({
            "유형":     category,
            "키워드":   keyword,
            "기사제목": entry.get("title", ""),
            "기사링크": entry.get("link", ""),
            "보도일":   pub,
            "언론사":   source.get("title", "") if isinstance(source, dict) else "",
        })
    return results


def crawl_all() -> list[dict]:
    all_articles = []
    for category, kw_list in KEYWORDS.items():
        for kw in kw_list:
            print(f"  크롤링 중: [{category}] {kw}")
            try:
                articles = get_google_news(kw, category)
                all_articles.extend(articles)
                time.sleep(0.5)
            except Exception as e:
                print(f"    오류: {e}")
    seen = set()
    unique = []
    for a in all_articles:
        if a["기사링크"] not in seen:
            seen.add(a["기사링크"])
            unique.append(a)
    print(f"\n수집 완료: {len(unique)}건 (중복 제거 후)")
    return unique


# ─────────────────────────────────────
# 2. 본문 수집
# ─────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SELECTORS = [
    "article p", "#articleBody p", "#articeBody p",
    ".article_body p", ".article-body p", ".article_txt p",
    ".news_text p", ".news-content p", ".content p",
    ".story-news p", ".main_txt p", ".text p",
    "#newsct_article p", ".go_trans p", "p",
]

def resolve_google_news_url(url: str) -> str:
    try:
        from gnewsdecoder import new_decoderv1
        result = new_decoderv1(url)
        if result and result.get("status") == "OK":
            decoded = result.get("decoded_url", "")
            if decoded and "google.com" not in decoded:
                return decoded
    except Exception:
        pass
    try:
        clean = re.sub(r"\?.*$", "", url)
        clean = clean.replace("/rss/articles/", "/articles/")
        session = requests.Session()
        session.headers.update(HEADERS)
        resp = session.get(clean, timeout=15, allow_redirects=True)
        final_url = resp.url
        if final_url and "google.com" not in final_url:
            return final_url
        html = resp.text
        for pattern in [
            r'data-n-au="([^"]+)"',
            r'window\.location\.href\s*=\s*["\']([^"\']+)["\']',
            r'window\.location\s*=\s*["\']([^"\']+)["\']',
        ]:
            m = re.search(pattern, html)
            if m and "google.com" not in m.group(1):
                return m.group(1)
    except Exception:
        pass
    try:
        from newspaper import Article
        article = Article(url, language="ko")
        article.download()
        article.parse()
        if article.canonical_link and "google.com" not in article.canonical_link:
            return article.canonical_link
    except Exception:
        pass
    return url


def get_article_content(url: str) -> str:
    try:
        real_url = resolve_google_news_url(url)
        r = requests.get(real_url, headers=HEADERS, timeout=10)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        for selector in SELECTORS:
            nodes = soup.select(selector)
            text = " ".join(n.get_text(strip=True) for n in nodes)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) >= 100:
                return text
        try:
            from newspaper import Article
            article = Article(real_url, language="ko")
            article.download()
            article.parse()
            if article.text and len(article.text) >= 100:
                return article.text
        except Exception:
            pass
        return "본문 수집 실패"
    except Exception:
        return "본문 수집 실패"


def make_summary(text: str, max_chars: int = 200) -> str:
    if not text or text == "본문 수집 실패":
        return ""
    sentences = re.split(r"(?<=[.!?。])\s+", text)
    summary = ""
    for s in sentences:
        if len(summary) + len(s) <= max_chars:
            summary += s + " "
        else:
            break
    return summary.strip() if summary else text[:max_chars] + "..."


# ─────────────────────────────────────
# 3. 엑셀 저장
# ─────────────────────────────────────
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
CATEGORY_FILLS = {
    "관광 및 K-컬처": "D6E4F0",
    "혁신산업":       "D5F5E3",
    "경제자유구역":   "FEF9E7",
    "수원시 관련":    "FDEDEC",
}

def save_excel(articles: list[dict], path: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "뉴스모니터링"
    cols = ["일자", "일시", "유형", "언론사", "기사제목", "요약", "기사본문", "URL"]
    ws.append(cols)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for a in articles:
        pub: datetime = a["보도일"]
        row = [
            pub.strftime("%Y-%m-%d"),
            pub.strftime("%H:%M"),
            a.get("유형", ""),
            a.get("언론사", ""),
            a.get("기사제목", ""),
            a.get("요약", ""),
            a.get("본문", ""),
            a.get("기사링크", ""),
        ]
        ws.append(row)
        fill_color = CATEGORY_FILLS.get(a.get("유형", ""), "FFFFFF")
        fill = PatternFill("solid", fgColor=fill_color)
        for cell in ws[ws.max_row]:
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    col_widths = [12, 8, 14, 16, 50, 60, 80, 50]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width
    wb.save(path)
    print(f"엑셀 저장 완료: {path}")


# ─────────────────────────────────────
# 4. HTML 대시보드 저장
# ─────────────────────────────────────
def save_html(articles: list[dict], path: str):
    now_str = datetime.now().strftime("%Y년 %m월 %d일")
    total = len(articles)

    counts = {}
    for cat in KEYWORDS:
        counts[cat] = sum(1 for a in articles if a.get("유형") == cat)

    stat_cards = ""
    for cat, cnt in counts.items():
        c = CATEGORY_COLORS.get(cat, {"bg": "#f0f0f0", "text": "#333", "header": "#666"})
        stat_cards += f"""
        <div style="background:{c['bg']};border-radius:10px;padding:16px 20px;min-width:140px;flex:1;">
          <div style="font-size:12px;color:{c['text']};margin-bottom:6px;font-weight:500;">{cat}</div>
          <div style="font-size:28px;font-weight:700;color:{c['header']};">{cnt}</div>
          <div style="font-size:11px;color:{c['text']};margin-top:4px;">건</div>
        </div>"""

    rows = ""
    for a in articles:
        pub: datetime = a["보도일"]
        cat = a.get("유형", "")
        c = CATEGORY_COLORS.get(cat, {"bg": "#ffffff", "text": "#333", "header": "#333"})
        title = a.get("기사제목", "").replace("<", "&lt;").replace(">", "&gt;")
        summary = a.get("요약", "").replace("<", "&lt;").replace(">", "&gt;")
        press = a.get("언론사", "").replace("<", "&lt;").replace(">", "&gt;")
        url = a.get("기사링크", "")
        rows += f"""
        <tr style="background:{c['bg']};">
          <td style="white-space:nowrap;color:#555;font-size:12px;">{pub.strftime("%Y-%m-%d")}</td>
          <td style="white-space:nowrap;color:#555;font-size:12px;">{pub.strftime("%H:%M")}</td>
          <td><span style="background:{c['header']};color:#fff;font-size:11px;padding:3px 8px;border-radius:12px;white-space:nowrap;">{cat}</span></td>
          <td style="font-size:12px;color:#555;white-space:nowrap;">{press}</td>
          <td style="font-weight:500;"><a href="{url}" target="_blank" style="color:{c['header']};text-decoration:none;">{title}</a></td>
          <td style="font-size:12px;color:#666;line-height:1.5;">{summary}</td>
          <td style="text-align:center;"><a href="{url}" target="_blank" style="background:{c['header']};color:#fff;font-size:11px;padding:4px 10px;border-radius:6px;text-decoration:none;white-space:nowrap;">원문 보기</a></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>뉴스 모니터링 리포트</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Malgun Gothic', Arial, sans-serif; background: #f5f6fa; color: #333; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  .header {{ background: #1F4E79; color: #fff; border-radius: 12px; padding: 24px 28px; margin-bottom: 20px; }}
  .header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 6px; }}
  .header p {{ font-size: 13px; opacity: 0.8; }}
  .stats {{ display: flex; gap: 14px; margin-bottom: 20px; flex-wrap: wrap; }}
  .total-card {{ background: #fff; border-radius: 10px; padding: 16px 20px; min-width: 140px; flex: 1; border: 1px solid #e0e0e0; }}
  .total-card .label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
  .total-card .value {{ font-size: 28px; font-weight: 700; color: #1F4E79; }}
  .table-wrap {{ background: #fff; border-radius: 12px; overflow: hidden; border: 1px solid #e0e0e0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  thead tr {{ background: #1F4E79; }}
  thead th {{ color: #fff; font-weight: 600; padding: 12px 14px; text-align: left; white-space: nowrap; }}
  tbody tr {{ border-bottom: 1px solid #eee; }}
  tbody tr:hover {{ filter: brightness(0.97); }}
  td {{ padding: 10px 14px; vertical-align: top; }}
  a:hover {{ text-decoration: underline !important; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>뉴스 모니터링 리포트</h1>
    <p>{now_str} 기준 · 최근 {DAYS_RANGE}일 수집</p>
  </div>
  <div class="stats">
    <div class="total-card">
      <div class="label">전체 기사</div>
      <div class="value">{total}</div>
    </div>
    {stat_cards}
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>일자</th>
          <th>일시</th>
          <th>유형</th>
          <th>언론사</th>
          <th>기사제목</th>
          <th>요약</th>
          <th>원문</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 저장 완료: {path}")


# ─────────────────────────────────────
# 5. 메일 발송 (엑셀 + HTML 첨부)
# ─────────────────────────────────────
def send_email(xlsx_path: str, html_path: str, article_count: int):
    now_str = datetime.now().strftime("%Y년 %m월")
    msg = MIMEMultipart()
    msg["From"]    = MAIL_USER
    msg["To"]      = ", ".join(RECIPIENTS)
    msg["Subject"] = f"[뉴스 자동 리포트] {now_str} ({article_count}건)"

    body = f"""{now_str} 뉴스 모니터링 리포트입니다.
총 {article_count}건의 기사가 수집되었습니다.

수집 키워드 분류
  - 관광 및 K-컬처: K-컬처, K-culture, K-푸드, K-food, K-POP, 한류
  - 혁신산업: 바이오, 반도체, AI, 인공지능
  - 경제자유구역: 경제자유구역, 경자특구, 경자구역
  - 수원시 관련: 수원시, 수원특례시

대시보드 URL: https://asdfghjk1210.github.io/news-dashboard

첨부 파일:
  - news_result.xlsx (엑셀)
  - news_result.html (대시보드 - 브라우저에서 열기)
"""
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for filepath, filename in [(xlsx_path, "news_result.xlsx"), (html_path, "news_result.html")]:
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(MAIL_USER, MAIL_APP_PW)
        server.sendmail(MAIL_USER, RECIPIENTS, msg.as_string())
    print(f"메일 발송 완료: {RECIPIENTS}")


# ─────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("뉴스 모니터링 파이프라인 시작")
    print("=" * 50)

    print("\n[1/5] Google News RSS 크롤링...")
    articles = crawl_all()

    print("\n[2/5] 기사 본문 + 요약 수집 중...")
    for i, a in enumerate(articles):
        print(f"  ({i+1}/{len(articles)}) {a['기사제목'][:30]}...", flush=True)
        content = get_article_content(a["기사링크"])
        a["본문"] = content
        a["요약"] = make_summary(content)
        time.sleep(0.3)

    success = sum(1 for a in articles if a["본문"] != "본문 수집 실패")
    print(f"  본문 수집 성공률: {success}/{len(articles)} ({round(success/len(articles)*100,1)}%)")

    print("\n[3/5] 엑셀 파일 생성 중...")
    save_excel(articles, OUTPUT_XLSX)

    print("\n[4/5] HTML 대시보드 생성 중...")
    save_html(articles, OUTPUT_HTML)

    print("\n[5/5] 메일 발송 중...")
    send_email(OUTPUT_XLSX, OUTPUT_HTML, len(articles))

    print("\n" + "=" * 50)
    print("전체 파이프라인 완료!")
    print("=" * 50)