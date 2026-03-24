import streamlit as st
import requests
from bs4 import BeautifulSoup
import os
import time
from sqlalchemy import create_engine, text
import pandas as pd
import sys
import logging
from logging.handlers import RotatingFileHandler # 追加
from datetime import datetime, timedelta, timezone
import copy
import re
from urllib.parse import urljoin

# --- 初期設定 ---
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
sys.stdout.reconfigure(line_buffering=True)

# 日本時間(JST)の設定
JST = timezone(timedelta(hours=+9), 'JST')

# ログ保存用ディレクトリの作成
LOG_DIR = "/logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

# ログの時間をJSTにするためのカスタムフォーマッタ
class JSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.astimezone(JST)
    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt: return dt.strftime(datefmt)
        return dt.isoformat()

# ロガーの基本設定
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Streamlitの再実行によるハンドラ重複を防ぐ
if logger.hasHandlers():
    logger.handlers.clear()

# 共通のフォーマット定義
formatter = JSTFormatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# 1. コンソール出力用ハンドラ
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# 2. ファイル出力用ハンドラ (5MB x 5世代)
file_handler = RotatingFileHandler(
    LOG_FILE, 
    maxBytes=5*1024*1024, # 5MB
    backupCount=5,         # 5世代
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 他のライブラリのログ混入を防ぐ
logger.propagate = False

def get_race_urls_from_top(top_url, html_content):
    """大会Topページから種目URL一覧を抽出する"""
    soup = BeautifulSoup(html_content, 'html.parser')
    race_urls = []
    main_content = soup.find('div', id='main')
    if not main_content:
        return []
    
    # 種目リンク（.html）を抽出
    for a in main_content.find_all('a', href=True):
        href = a['href']
        if '.html' in href and 'index' not in href and '#' not in href:
            full_url = urljoin(top_url, href)
            if full_url not in race_urls:
                race_urls.append(full_url)
    
    # リンクが極端に少ない（例: 戻るボタン1つだけ等）場合は大会Topとみなさない
    if len(race_urls) <= 1:
        return []
        
    return race_urls

def format_time(time_str):
    """ '07:39.87' -> '00:07:39.87' """
    if not time_str: return None
    ts = str(time_str).strip()
    if any(c.isalpha() for c in ts): return None
    if ":" not in ts: return None
    if ts.count(':') >= 2: return ts
    return f"00:{ts}"

# --- 解析ロジック ---
def parse_jara_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    breadcrumb = soup.find('ol', class_='race-breadcrumb')
    if breadcrumb:
        links = breadcrumb.find_all('li')
        regatta_name = links[0].get_text(strip=True) if len(links) > 0 else "不明な大会"
        event_name = links[1].get_text(strip=True) if len(links) > 1 else "不明な種目"
    else:
        title_text = soup.title.get_text(strip=True) if soup.title else ""
        regatta_name = title_text.split('|')[0].strip() if '|' in title_text else "不明な大会"
        event_name = "不明な種目"

    all_race_data = []
    race_panels = soup.find_all('div', class_='result') 
    if not race_panels:
        race_panels = soup.find_all('div', class_='panel-default')

    for panel in race_panels:
        try:
            heading = panel.find(class_='panel-heading')
            if not heading: continue
            race_no_text = heading.get_text(strip=True)
            race_no = "".join(filter(str.isdigit, race_no_text))
            
            race_info = panel.find('div', class_='race-info')
            race_round = "不明"
            if race_info:
                for div in race_info.find_all('div'):
                    text_content = div.get_text(strip=True)
                    if '組別:' in text_content:
                        race_round = text_content.replace('組別:', '').strip()

            table = panel.find('table')
            if not table: continue
            rows = table.find_all('tr')
            results = []
            
            for i, row in enumerate(rows):
                cols = row.find_all('td', recursive=False)
                if len(cols) < 6 or 'collapse' in row.get('class', []): continue
                crew_td = cols[1]
                crew_link = crew_td.find('a')
                team_name = crew_link.get_text(strip=True) if crew_link else crew_td.get_text(strip=True)
                if "着順" in team_name or not team_name: continue

                splits = [c.get_text(strip=True) for c in cols[2:6]]
                lane_no = cols[6].get_text(strip=True) if len(cols) > 6 else ""

                members = []
                if i + 1 < len(rows) and 'collapse' in rows[i+1].get('class', []):
                    member_table = rows[i+1].find('table')
                    if member_table:
                        for m_row in member_table.find_all('tr'):
                            m_cols = m_row.find_all('td')
                            if len(m_cols) < 2 or "氏名" in m_cols[0].get_text(): continue
                            name_cell = m_cols[1]
                            small_tag = name_cell.find('small')
                            kana = small_tag.get_text(strip=True) if small_tag else ""
                            kanji = name_cell.get_text(separator=" ", strip=True).replace(kana, "").strip()
                            members.append({
                                'pos': m_cols[0].get_text(strip=True), 'kana': kana, 'kanji': kanji,
                                'h': m_cols[2].get_text(strip=True) if len(m_cols) > 2 else "",
                                'w': m_cols[3].get_text(strip=True) if len(m_cols) > 3 else ""
                            })
                results.append({'rank': cols[0].get_text(strip=True), 'team_name': team_name, 'splits': splits, 'lane_no': lane_no, 'members': members})
            all_race_data.append({'race_no': race_no, 'race_round': race_round, 'results': results})
        except Exception as e:
            logger.error(f"解析エラー (Race No.{race_no if 'race_no' in locals() else 'unknown'}): {e}")
            continue
    return all_race_data, regatta_name, event_name

# --- 保存ロジック ---
def save_to_db(data, regatta_name, event_name):
    logger.info(f"=== DB保存プロセス開始 (大会: {regatta_name} / 種目: {event_name}) ===")
    
    with engine.begin() as conn:
        # 1. 大会IDの取得または作成
        conn.execute(text("INSERT INTO regattas (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"), {"n": regatta_name})
        r_id = conn.execute(text("SELECT id FROM regattas WHERE name=:n"), {"n": regatta_name}).fetchone()[0]
        
        # 2. 同一大会内に同じ種目が既に存在するか確認
        existing_event = conn.execute(text(
            "SELECT id FROM events WHERE regatta_id=:r AND event_name=:e"
        ), {"r": r_id, "e": event_name}).fetchone()

        if existing_event:
            e_id = existing_event[0]
            logger.info(f"⚠️ 既存データを発見 (Event ID: {e_id})。重複防止のため関連データを一括削除します。")
            
            # 3. 既存のレースに関連するデータをカスケード削除
            # (テーブル定義に ON DELETE CASCADE があれば races の削除だけで済みますが、念のため明示的に消します)
            conn.execute(text("""
                DELETE FROM split_times WHERE crew_id IN (
                    SELECT id FROM crews WHERE race_id IN (
                        SELECT id FROM races WHERE event_id = :e
                    )
                )
            """), {"e": e_id})
            
            conn.execute(text("""
                DELETE FROM crew_members WHERE crew_id IN (
                    SELECT id FROM crews WHERE race_id IN (
                        SELECT id FROM races WHERE event_id = :e
                    )
                )
            """), {"e": e_id})
            
            conn.execute(text("DELETE FROM crews WHERE race_id IN (SELECT id FROM races WHERE event_id = :e)"), {"e": e_id})
            conn.execute(text("DELETE FROM races WHERE event_id = :e"), {"e": e_id})
            
            logger.info(f"✅ 既存の全 {event_name} データを削除しました。")
        else:
            # 新規種目の作成
            conn.execute(text("INSERT INTO events (regatta_id, event_name) VALUES (:r, :e)"), {"r": r_id, "e": event_name})
            e_id = conn.execute(text("SELECT id FROM events WHERE regatta_id=:r AND event_name=:e"), {"r": r_id, "e": event_name}).fetchone()[0]

    # 4. 各レースの保存（ここは以前のロジックを継続）
    for r in data:
        r_no = int(r['race_no']) if str(r['race_no']).isdigit() else 0
        try:
            with engine.begin() as conn:
                logger.info(f"--- レース No.{r_no} ({r.get('race_round')}) 保存処理 ---")
                
                # レース保存 (冒頭で一括削除しているため、ここでは INSERT のみでOK)
                race_id = conn.execute(text("INSERT INTO races (event_id, race_no, race_round) VALUES (:e, :n, :ro) RETURNING id"), 
                                       {"e": e_id, "n": r_no, "ro": r['race_round']}).fetchone()[0]

                for res in r['results']:
                    member_count = len(res['members'])
                    logger.info(f"    [クルー保存] {res['team_name']} (選手数: {member_count}, Lane: {res['lane_no']})")
                    
                    def safe_int(s):
                        if not s: return None
                        s_str = "".join(filter(str.isdigit, str(s)))
                        return int(s_str) if s_str else None

                    splits = res.get('splits', [])
                    total_time_raw = splits[3] if len(splits) > 3 else None
                    
                    c_id = conn.execute(text("""
                        INSERT INTO crews (race_id, team_name, lane_no, rank_in_race, total_time) 
                        VALUES (:ri, :t, :l, :ra, :ti) RETURNING id
                    """), {
                        "ri": race_id, "t": res['team_name'], 
                        "l": safe_int(res['lane_no']), "ra": safe_int(res['rank']), 
                        "ti": format_time(total_time_raw)
                    }).fetchone()[0]
                    
                    for dist, s_time in zip([500, 1000, 1500], splits[:3]):
                        f_time = format_time(s_time)
                        if f_time:
                            conn.execute(text("INSERT INTO split_times (crew_id, distance_meters, split_time) VALUES (:ci, :d, :st)"),
                                         {"ci": c_id, "d": dist, "st": f_time})
                    
                    for m in res['members']:
                        # rowers, rower_profiles は重複して良いので ON CONFLICT
                        conn.execute(text("INSERT INTO rowers (kana, kanji) VALUES (:ka, :kj) ON CONFLICT (kana, kanji) DO NOTHING"), 
                                     {"ka": m['kana'], "kj": m['kanji']})
                        rower_id = conn.execute(text("SELECT id FROM rowers WHERE kana=:ka AND kanji=:kj"), 
                                                {"ka": m['kana'], "kj": m['kanji']}).fetchone()[0]
                        
                        try: h_val = float(m['h']) if m['h'] and m['h'] != '-' else None
                        except: h_val = None
                        try: w_val = float(m['w']) if m['w'] and m['w'] != '-' else None
                        except: w_val = None

                        conn.execute(text("""
                            INSERT INTO rower_profiles (rower_id, year, affiliation, height, weight) 
                            VALUES (:rid, 2025, :aff, :h, :w) 
                            ON CONFLICT (rower_id, year) DO UPDATE SET height=:h, weight=:w, affiliation=:aff
                        """), {"rid": rower_id, "aff": res['team_name'], "h": h_val, "w": w_val})
                        
                        conn.execute(text("INSERT INTO crew_members (crew_id, rower_id, position) VALUES (:ci, :ri, :p)"),
                                     {"ci": c_id, "ri": rower_id, "p": m['pos']})
            logger.info(f"--- レース No.{r_no} 保存成功 ---")
        except Exception as e:
            logger.error(f"レース No.{r_no} の保存中にエラー: {e}")
            continue

    logger.info("=== DB保存プロセス終了 ===")

# --- Streamlit UI ---
st.title("Rowing Race Results Parser")

tab1, tab2 = st.tabs(["📄 HTMLファイルから取り込み", "🌐 JARA URLから取り込み"])

with tab1:
    uploaded_file = st.file_uploader("JARAのレース結果HTMLファイルをアップロードしてください", type="html")
    if uploaded_file is not None:
        html_content = uploaded_file.read().decode("utf-8")
        if st.button("ファイルを解析して保存"):
            data, reg_name, event_name = parse_jara_html(html_content)
            st.info(f"大会: {reg_name} / 種目: {event_name}")
            save_to_db(data, reg_name, event_name)
            st.success(f"{len(data)} レースの保存が完了しました。")

with tab2:
    url = st.text_input("JARAのレース結果URLを入力してください", placeholder="https://www.jara.or.jp/race/...")
    if st.button("URLから取得して保存"):
        if url:
            try:
                # ブラウザに見せかけるためのヘッダー
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                
                res_text = ""
                success_fetch = False
                
                with st.spinner('データを取得中... (最大3回リトライします)'):
                    for i in range(3): # 3回までリトライ
                        try:
                            res = requests.get(url, headers=headers, timeout=15)
                            res.raise_for_status()
                            res.encoding = res.apparent_encoding
                            res_text = res.text
                            success_fetch = True
                            break # 成功したらループを抜ける
                        except requests.exceptions.RequestException as e:
                            if i < 2:
                                logger.warning(f"取得失敗。5秒後に再試行します... ({i+1}/3): {e}")
                                time.sleep(5) # 5秒待機
                            else:
                                raise e # 3回ダメならエラーを投げる
                
                if success_fetch:
                    # ログ用: 処理開始
                    logger.info(f"URL解析開始: {url}")
                    
                    # A. まずは「単一種目ページ」として解析を試みる
                    data, reg_name, event_name = parse_jara_html(res_text)
                    
                    if data:
                        # --- 通常モード（種目データが見つかった場合） ---
                        st.info(f"取得成功: {reg_name} / {event_name}")
                        save_to_db(data, reg_name, event_name)
                        st.success(f"{len(data)} レースの保存（上書き）が完了しました。")
                    else:
                        # B. 種目データがない場合のみ「大会Top」としてのリンク抽出を試みる
                        child_urls = get_race_urls_from_top(url, res_text)
                        
                        if child_urls:
                            # --- 一括取得モード ---
                            st.info(f"大会Topページを検出しました。{len(child_urls)}種目の取り込みを開始します。")
                            progress_bar = st.progress(0)
                            for idx, c_url in enumerate(child_urls):
                                try:
                                    time.sleep(2)  # 次の種目に行く前に2秒休む
                                    c_res = requests.get(c_url, headers=headers, timeout=10)
                                    c_res.raise_for_status()
                                    c_res.encoding = c_res.apparent_encoding
                                    c_data, c_reg, c_eve = parse_jara_html(c_res.text)
                                    if c_data:
                                        save_to_db(c_data, c_reg, c_eve)
                                        st.write(f"✅ {c_eve} 完了")
                                except Exception as inner_e:
                                    st.warning(f"⚠️ スキップ: {c_url} ({inner_e})")
                                    logger.error(f"一括取得中の個別エラー ({c_url}): {inner_e}")
                                progress_bar.progress((idx + 1) / len(child_urls))
                            st.success("大会全種目の一括保存が完了しました。")
                        else:
                            st.error("レース結果または種目リンクが見つかりませんでした。")
                            logger.warning(f"データ未検出: {url}")
                    
            except Exception as e:
                st.error(f"URLからの取得に失敗しました。時間をおいて再度お試しください。: {e}")
                logger.error(f"URL取得エラー: {e}")
        else:
            st.warning("URLを入力してください。")