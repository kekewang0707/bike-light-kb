"""自行车灯电商知识库 - Streamlit 入口"""

import streamlit as st
import psycopg2
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="自行车灯知识库",
    page_icon="🚲",
    layout="wide",
)

# ---------------------------------------------------------------------------
# 数据库连接
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "bikelight_kb"),
    "user": os.getenv("DB_USER", "bikelight"),
    "password": os.getenv("DB_PASSWORD", "bikelight123"),
}


@st.cache_resource
def get_db_connection():
    """获取数据库连接（缓存复用）"""
    return psycopg2.connect(**DB_CONFIG)


def check_db_health() -> dict:
    """检查数据库健康状态"""
    status = {
        "postgres": {"healthy": False, "message": ""},
        "chromadb": {"healthy": False, "message": ""},
    }

    # 检查 PostgreSQL
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
        status["postgres"]["healthy"] = True
        status["postgres"]["message"] = "连接正常"
    except Exception as e:
        status["postgres"]["message"] = str(e)

    # 检查 ChromaDB
    try:
        import chromadb

        client = chromadb.HttpClient(host="localhost", port=8000)
        client.heartbeat()
        status["chromadb"]["healthy"] = True
        status["chromadb"]["message"] = "连接正常"
    except Exception as e:
        status["chromadb"]["message"] = str(e)

    return status


# ---------------------------------------------------------------------------
# 主页面
# ---------------------------------------------------------------------------
st.title("🚲 自行车灯电商知识库")
st.caption("选品决策 & 内容创作 — 数据驱动，言之有物")

st.divider()

# ---- 系统状态 ----
st.header("🔧 系统状态")

status = check_db_health()

col1, col2 = st.columns(2)

with col1:
    pg = status["postgres"]
    if pg["healthy"]:
        st.success(f"**PostgreSQL 16** — {pg['message']}")
    else:
        st.error(f"**PostgreSQL 16** — {pg['message']}")

with col2:
    ch = status["chromadb"]
    if ch["healthy"]:
        st.success(f"**ChromaDB** — {ch['message']}")
    else:
        st.warning(f"**ChromaDB** — {ch['message']}")

st.divider()

# ---- 数据概览 ----
st.header("📊 数据概览")

if status["postgres"]["healthy"]:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            product_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM hot_rankings")
            ranking_count = cur.fetchone()[0]

        m1, m2, m3 = st.columns(3)
        m1.metric("商品总数", product_count)
        m2.metric("热榜条目", ranking_count)
        m3.metric("最后更新", datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception:
        st.info("数据库连接正常，等待数据导入。请先运行爬虫采集数据。")
else:
    st.warning("请先启动 Docker 服务：`docker compose up -d`")

st.divider()

# ---- 快速入口 ----
st.header("🚀 快速入口")

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.button("📋 商品列表", use_container_width=True, disabled=True)
with c2:
    st.button("🔥 热榜排行", use_container_width=True, disabled=True)
with c3:
    st.button("🔍 智能搜索", use_container_width=True, disabled=True)
with c4:
    st.button("📝 内容生成", use_container_width=True, disabled=True)

st.divider()
st.caption(f"© 2026 Bike Light Knowledge Base | 启动时间: {datetime.now().isoformat(timespec='seconds')}")
