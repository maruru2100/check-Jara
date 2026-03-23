-- 1. 選手基本情報 (カナと漢字を分けて管理)
CREATE TABLE rowers (
    id SERIAL PRIMARY KEY,
    kana TEXT,
    kanji TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (kana, kanji) -- 重複保存防止（ON CONFLICT用）
);

-- 2. 選手プロフィール (年度ごとの所属・体格)
CREATE TABLE rower_profiles (
    id SERIAL PRIMARY KEY,
    rower_id INTEGER REFERENCES rowers(id),
    year INTEGER NOT NULL,
    affiliation VARCHAR(255), -- 所属（大学・企業名）
    height DECIMAL(5, 2),     -- 身長
    weight DECIMAL(5, 2),     -- 体重
    UNIQUE (rower_id, year)   -- 1選手1年1レコード
);

-- 3. 大会情報
CREATE TABLE regattas (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE, -- 大会名でユニーク制約（ON CONFLICT用）
    start_date DATE,
    location VARCHAR(255)
);

-- 4. 種目 (女子ダブルスカル、男子エイトなど)
CREATE TABLE events (
    id SERIAL PRIMARY KEY,
    regatta_id INTEGER REFERENCES regattas(id),
    event_name VARCHAR(100) NOT NULL,
    UNIQUE (regatta_id, event_name)
);

-- 5. レース (予選、準決勝、決勝など)
CREATE TABLE races (
    id SERIAL PRIMARY KEY,
    event_id INTEGER REFERENCES events(id),
    race_no INTEGER,           
    race_round VARCHAR(50),    
    race_time TIMESTAMP WITH TIME ZONE,
    UNIQUE (event_id, race_no)
);

-- 6. クルー (レーン使用なしの場合もレコードとして保持)
CREATE TABLE crews (
    id SERIAL PRIMARY KEY,
    race_id INTEGER REFERENCES races(id),
    team_name VARCHAR(255),    -- 空の場合は「レーン使用無し」が入る
    lane_no INTEGER,           
    rank_in_race INTEGER,      
    total_time INTERVAL        
);

-- 7. クルーメンバー
CREATE TABLE crew_members (
    id SERIAL PRIMARY KEY,
    crew_id INTEGER REFERENCES crews(id),
    rower_id INTEGER REFERENCES rowers(id),
    position VARCHAR(10)       
);

-- 8. スプリットタイム
CREATE TABLE split_times (
    id SERIAL PRIMARY KEY,
    crew_id INTEGER REFERENCES crews(id),
    distance_meters INTEGER NOT NULL, 
    split_time INTERVAL NOT NULL      
);