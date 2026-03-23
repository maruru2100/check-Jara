import streamlit as st
import requests
from bs4 import BeautifulSoup
import os
import time
from sqlalchemy import create_engine, text
import pandas as pd
import sys
import logging
from datetime import datetime, timedelta, timezone
import copy

# --- 初期設定 ---
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
sys.stdout.reconfigure(line_buffering=True)

# 日本時間(JST)の設定
JST = timezone(timedelta(hours=+9), 'JST')

# ログの時間をJSTにするためのカスタムハンドラ
class JSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.astimezone(JST)
    
    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()

# ログの設定
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if logger.hasHandlers():
    logger.handlers.clear()

handler = logging.StreamHandler(sys.stdout)
formatter = JSTFormatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

logger.propagate = False

def format_time(time_str):
    """ 
    '07:39.87' -> '00:07:39.87' 
    DNS, DNF, 空文字などは None を返す
    """
    if not time_str:
        return None
    
    ts = str(time_str).strip()
    # 数字と記号以外の文字（DNS/DNF/欠場など）が含まれていたらNone
    if any(c.isalpha() for c in ts):
        return None
    
    if ":" not in ts:
        return None
        
    # 既に 00:07:39.87 の形式ならそのまま、そうでなければ 00: を付与
    if ts.count(':') >= 2:
        return ts
    return f"00:{ts}"

# --- 解析ロジック ---
def parse_jara_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 大会名・種目名
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
                if len(cols) < 6 or 'collapse' in row.get('class', []):
                    continue
                    
                crew_td = cols[1]
                crew_link = crew_td.find('a')
                team_name = crew_link.get_text(strip=True) if crew_link else crew_td.get_text(strip=True)
                
                if "着順" in team_name or not team_name: continue

                # スプリット情報の取得（空欄やDNSを許容）
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
                                'pos': m_cols[0].get_text(strip=True),
                                'kana': kana, 'kanji': kanji,
                                'h': m_cols[2].get_text(strip=True) if len(m_cols) > 2 else "",
                                'w': m_cols[3].get_text(strip=True) if len(m_cols) > 3 else ""
                            })

                results.append({
                    'rank': cols[0].get_text(strip=True),
                    'team_name': team_name,
                    'splits': splits,
                    'lane_no': lane_no,
                    'members': members
                })

            all_race_data.append({
                'race_no': race_no,
                'race_round': race_round,
                'results': results
            })
        except Exception as e:
            logger.error(f"解析エラー (Race No.{race_no if 'race_no' in locals() else 'unknown'}): {e}")
            continue # 1つのレースが壊れていても他を継続

    return all_race_data, regatta_name, event_name

# --- 保存ロジック ---
def save_to_db(data, regatta_name, event_name):
    logger.info(f"=== DB保存プロセス開始 (総レース数: {len(data)}) ===")
    
    # 大会と種目のID確定
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO regattas (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"), {"n": regatta_name})
        r_id = conn.execute(text("SELECT id FROM regattas WHERE name=:n"), {"n": regatta_name}).fetchone()[0]
        
        conn.execute(text("INSERT INTO events (regatta_id, event_name) VALUES (:r, :e) ON CONFLICT (regatta_id, event_name) DO NOTHING"), {"r": r_id, "e": event_name})
        e_id = conn.execute(text("SELECT id FROM events WHERE regatta_id=:r AND event_name=:e"), {"r": r_id, "e": event_name}).fetchone()[0]

    for r in data:
        r_no = int(r['race_no']) if str(r['race_no']).isdigit() else 0
        try:
            with engine.begin() as conn:
                logger.info(f"--- レース No.{r_no} ({r.get('race_round')}) 保存処理 ---")

                # (中略: 既存データ削除ロジック)
                existing_race = conn.execute(text("SELECT id FROM races WHERE event_id=:e AND race_no=:n"), {"e": e_id, "n": r_no}).fetchone()
                if existing_race:
                    rid = existing_race[0]
                    conn.execute(text("DELETE FROM split_times WHERE crew_id IN (SELECT id FROM crews WHERE race_id=:id)"), {"id": rid})
                    conn.execute(text("DELETE FROM crew_members WHERE crew_id IN (SELECT id FROM crews WHERE race_id=:id)"), {"id": rid})
                    conn.execute(text("DELETE FROM crews WHERE race_id=:id"), {"id": rid})
                    conn.execute(text("DELETE FROM races WHERE id=:id"), {"id": rid})

                race_id = conn.execute(text("INSERT INTO races (event_id, race_no, race_round) VALUES (:e, :n, :ro) RETURNING id"), 
                                       {"e": e_id, "n": r_no, "ro": r['race_round']}).fetchone()[0]

                for res in r['results']:
                    # ★修正ポイント: 選手数とレーンをログに出す
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
st.title("JARA レース結果スクレイピング")

uploaded_file = st.file_uploader("HTMLファイルをアップロードしてください", type="html")

if uploaded_file is not None:
    html_content = uploaded_file.read().decode("utf-8")
    if st.button("解析してDBに保存"):
        try:
            data, reg_name, event_name = parse_jara_html(html_content)
            save_to_db(data, reg_name, event_name)
            st.success(f"保存完了: {reg_name} - {event_name}")
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
            logger.error(f"Error: {e}", exc_info=True)