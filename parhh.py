import streamlit as st
import requests
import time
import re
import json
from collections import Counter
from urllib.parse import urlparse, parse_qs
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="HH.ru Recruiter Analytics",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.main-header {
    background: linear-gradient(135deg, #1a237e 0%, #283593 50%, #0c4e54 100%);
    padding: 2rem 2.5rem; border-radius: 14px;
    margin-bottom: 1.5rem; color: white;
}
.main-header h1 { margin:0; font-size:2rem; font-weight:700; letter-spacing:-0.5px; }
.main-header p  { margin:0.5rem 0 0 0; opacity:0.85; font-size:1rem; }

.metric-card {
    background: white; border:1px solid #e8eaf6;
    border-radius:12px; padding:1.25rem 1.5rem;
    text-align:center; box-shadow:0 2px 8px rgba(26,35,126,0.07);
}
.metric-card .value { font-size:2rem; font-weight:700; color:#1a237e; line-height:1.2; }
.metric-card .label { font-size:0.8rem; color:#6b7280; margin-top:0.3rem; text-transform:uppercase; letter-spacing:.5px; }

.resume-card {
    background: white; border:1px solid #e8eaf6; border-radius:12px;
    padding:1.25rem 1.5rem; margin-bottom:0.75rem;
    box-shadow:0 1px 4px rgba(0,0,0,0.05); transition:box-shadow .2s;
}
.resume-card:hover { box-shadow:0 4px 16px rgba(26,35,126,0.12); }
.resume-title { font-size:1.05rem; font-weight:600; color:#1a237e; margin-bottom:0.3rem; }
.resume-meta  { font-size:0.85rem; color:#6b7280; margin-bottom:0.6rem; }
.skill-badge {
    display:inline-block; padding:0.2rem 0.65rem; border-radius:999px;
    font-size:0.78rem; font-weight:500; margin:0.15rem;
    background:#e8eaf6; color:#1a237e; border:1px solid #c5cae9;
}
.skill-badge.matched {
    background:#1a237e; color:white; border-color:#1a237e;
}
.status-badge {
    display:inline-block; padding:0.15rem 0.6rem; border-radius:999px;
    font-size:0.75rem; font-weight:600; margin-left:0.5rem;
}
.status-active  { background:#dcfce7; color:#166534; }
.status-open    { background:#fef3c7; color:#92400e; }
.status-passive { background:#f3f4f6; color:#374151; }

.info-box {
    background:#eff6ff; border-left:4px solid #1a237e;
    padding:0.75rem 1rem; border-radius:0 8px 8px 0;
    margin:0.5rem 0; font-size:0.9rem;
}
.tip-box {
    background:#f0fdf4; border-left:4px solid #16a34a;
    padding:0.75rem 1rem; border-radius:0 8px 8px 0;
    margin:0.5rem 0; font-size:0.9rem;
}
[data-testid="stSidebar"] { background:#f8f9ff; border-right:1px solid #e8eaf6; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
HH_BASE = "https://hh.ru"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

AREA_OPTIONS = {
    "Россия (все)": "113",
    "Москва": "1",
    "Санкт-Петербург": "2",
    "Новосибирск": "4",
    "Екатеринбург": "3",
    "Казань": "88",
    "Нижний Новгород": "66",
    "Беларусь": "16",
    "Польша": "160",
}

EXP_OPTIONS = {
    "Любой": "",
    "Без опыта": "noExperience",
    "1–3 года": "between1And3",
    "3–6 лет": "between3And6",
    "Более 6 лет": "moreThan6",
}

SALARY_OPTIONS = {
    "Любая": "",
    "от 50 000 ₽": "50000",
    "от 80 000 ₽": "80000",
    "от 100 000 ₽": "100000",
    "от 150 000 ₽": "150000",
    "от 200 000 ₽": "200000",
}

STATUS_LABELS = {
    "active_search":   ("🟢 Активно ищет", "status-active"),
    "looking_for_offers": ("🟡 Рассматривает предложения", "status-open"),
    "not_looking_for_job": ("⚫ Не ищет", "status-passive"),
    "has_job_offer":   ("🟠 Есть предложение", "status-open"),
    "accepted_job_offer": ("🔵 Принял оффер", "status-passive"),
}

# ──────────────────────────────────────────────
@st.cache_resource
def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    try:
        s.get(f"{HH_BASE}/", timeout=10)
    except Exception:
        pass
    return s

def safe_get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response | None:
    session = get_session()
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                time.sleep(2 ** (attempt + 1))
            elif resp.status_code in (403, 404):
                return None
        except requests.exceptions.Timeout:
            time.sleep(2)
        except Exception:
            return None
    return None

def extract_json_array(html: str, key: str) -> list:
    idx = html.find(f'"{key}":')
    if idx == -1:
        return []
    arr_start = idx + len(f'"{key}":')
    depth = 0
    end = arr_start
    for i in range(arr_start, min(arr_start + 700000, len(html))):
        c = html[i]
        if c in '[{': depth += 1
        elif c in ']}': depth -= 1
        if depth == 0:
            end = i + 1
            break
    try:
        return json.loads(html[arr_start:end])
    except Exception:
        return []

def parse_resume_item(item: dict) -> dict:
    """Превращаем raw JSON резюме в чистый словарь."""
    attrs = item.get("_attributes", {}) or {}
    resume_hash = attrs.get("hash", "") if isinstance(attrs, dict) else ""
    resume_id   = attrs.get("id", "")   if isinstance(attrs, dict) else ""
    permission  = attrs.get("permission", "") if isinstance(attrs, dict) else ""

    def get_str(field): 
        lst = item.get(field, [])
        return lst[0]["string"] if lst and isinstance(lst[0], dict) and "string" in lst[0] else ""

    def get_num(field):
        lst = item.get(field, [])
        return lst[0]["string"] if lst and isinstance(lst[0], dict) and "string" in lst[0] else 0

    title     = get_str("title")
    age       = get_num("age")
    exp_months = get_num("totalExperience")

    sal_lst    = item.get("salary") or []
    salary_amt = sal_lst[0].get("amount", 0) if sal_lst else 0
    salary_cur = sal_lst[0].get("currency", "RUR") if sal_lst else "RUR"

    pred_lst  = item.get("predictedSalary") or []
    pred_sal  = pred_lst[0].get("amount", 0) if pred_lst else 0

    skills_raw = item.get("keySkills") or []
    skills = []
    for s in skills_raw:
        if isinstance(s, dict) and "string" in s:
            skill_text = s["string"].strip()
            # Пропускаем длинные «навыки» — это описание личных качеств
            if len(skill_text) < 60 and not skill_text[0].islower():
                skills.append(skill_text)

    status_raw = ""
    js_lst = item.get("jobSearchStatus") or []
    if js_lst and isinstance(js_lst[0], dict):
        inner = js_lst[0].get("jobSearchStatus", {}) or {}
        status_raw = inner.get("name", "") if isinstance(inner, dict) else ""

    last_active = item.get("lastActivityTime", "")[:10] if item.get("lastActivityTime") else ""
    is_online   = bool(item.get("isOnline"))

    url = f"{HH_BASE}/resume/{resume_hash}" if resume_hash else ""
    can_view_contacts = (permission == "view_without_contacts" or permission == "full_access")

    return {
        "id":           str(resume_id),
        "hash":         resume_hash,
        "title":        title,
        "age":          int(age) if age else None,
        "exp_months":   int(exp_months) if exp_months else 0,
        "salary_want":  int(salary_amt) if salary_amt else None,
        "salary_pred":  int(pred_sal) if pred_sal else None,
        "salary_cur":   salary_cur,
        "skills":       skills,
        "status":       status_raw,
        "is_online":    is_online,
        "last_active":  last_active,
        "url":          url,
        "can_view":     can_view_contacts,
    }

@st.cache_data(ttl=600, show_spinner=False)
def search_resumes(query: str, area: str, pages: int,
                   experience: str = "", salary_from: str = "",
                   order_by: str = "relevance") -> list[dict]:
    all_resumes = []
    seen_ids = set()

    for page in range(pages):
        params = {
            "text": query,
            "area": area,
            "per_page": 20,
            "page": page,
            "order_by": order_by,
        }
        if experience:
            params["experience"] = experience
        if salary_from:
            params["salary"] = salary_from

        resp = safe_get(f"{HH_BASE}/search/resume", params=params)
        if resp is None:
            break

        raw_list = extract_json_array(resp.text, "resumes")
        if not raw_list:
            break

        for item in raw_list:
            attrs = item.get("_attributes") or {}
            rid = str(attrs.get("id", "")) if isinstance(attrs, dict) else ""
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                parsed = parse_resume_item(item)
                all_resumes.append(parsed)

        time.sleep(0.35)

    return all_resumes

def exp_label(months: int) -> str:
    if not months:
        return "Нет опыта"
    y = months // 12
    m = months % 12
    parts = []
    if y: parts.append(f"{y} лет" if y >= 5 else f"{y} г.")
    if m: parts.append(f"{m} мес.")
    return " ".join(parts) if parts else "—"

def parse_url_params(url: str):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    query = qs.get("text", [""])[0]
    area  = qs.get("area",  ["113"])[0]
    exp   = qs.get("experience", [""])[0]
    sal   = qs.get("salary", [""])[0]
    return query, area, exp, sal


# ──────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🎯 Поиск кандидатов")
    st.markdown("---")

    input_mode = st.radio("Режим ввода", ["🔎 Параметры поиска", "🔗 URL со страницы hh.ru"])

    if input_mode == "🔎 Параметры поиска":
        search_query = st.text_input("Должность / ключевые слова", placeholder="Биоинформатик, Python, Менеджер...")
        area_name = st.selectbox("Регион", list(AREA_OPTIONS.keys()))
        area_id   = AREA_OPTIONS[area_name]
        exp_label_sel = st.selectbox("Опыт работы", list(EXP_OPTIONS.keys()))
        exp_value = EXP_OPTIONS[exp_label_sel]
        sal_label = st.selectbox("Зарплатные ожидания от", list(SALARY_OPTIONS.keys()))
        sal_value = SALARY_OPTIONS[sal_label]
        url_input = None
    else:
        url_input = st.text_area(
            "Ссылка со страницы поиска резюме",
            placeholder="https://hh.ru/search/resume?text=python&area=1",
            height=90,
        )
        search_query = None
        area_id = "113"
        exp_value = ""
        sal_value = ""

    st.markdown("---")
    st.markdown("### 🎛️ Параметры")
    max_pages = st.slider("Страниц (×20 резюме)", 1, 20, 3)
    order_by  = st.selectbox("Сортировка", {
        "По соответствию": "relevance",
        "По дате обновления": "publication_time",
        "По зарплате (убыв.)": "salary_desc",
        "По зарплате (возр.)": "salary_asc",
    }.keys())
    order_map = {
        "По соответствию":     "relevance",
        "По дате обновления":  "publication_time",
        "По зарплате (убыв.)": "salary_desc",
        "По зарплате (возр.)": "salary_asc",
    }
    order_val = order_map[order_by]

    st.markdown("---")
    st.markdown("### 🔬 Фильтры анализа")
    min_skill_freq = st.slider("Мин. частота навыка", 1, 20, 2)
    top_n_skills   = st.slider("Топ N навыков", 5, 40, 20)
    filter_skills_input = st.text_input(
        "🎯 Обязательные навыки (через запятую)",
        placeholder="Python, SQL, Docker",
        help="Оставить только кандидатов у которых есть ВСЕ эти навыки"
    )
    show_only_active = st.checkbox("Только активно ищущих работу", value=False)

    st.markdown("---")
    run_btn = st.button("🚀 Найти кандидатов", type="primary", use_container_width=True)
    if st.button("🗑️ Сбросить кэш", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>🎯 HH.ru Recruiter Analytics</h1>
    <p>Анализ кандидатов и рынка резюме — поиск, фильтрация, аналитика навыков</p>
</div>
""", unsafe_allow_html=True)

if not run_btn:
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("""
### Как использовать

**Режим 1 — Параметры поиска:**  
Введи должность или ключевые слова, выбери регион и нажми **Найти кандидатов**.

**Режим 2 — URL:**  
Настрой поиск на [hh.ru/search/resume](https://hh.ru/search/resume) → скопируй URL → вставь сюда.

### Что ты получишь
- 👥 **Список кандидатов** с навыками, опытом, зарплатными ожиданиями
- 📊 **Аналитика навыков** — что чаще всего встречается у кандидатов
- 🎯 **Фильтр по обязательным навыкам** — только подходящие
- 💰 **Распределение зарплатных ожиданий**
- 📈 **График опыта** по кандидатам
- ⬇️ **Экспорт в CSV** для ATS/таблиц
        """)
    with col2:
        st.markdown("""
<div class="info-box">
    ✅ <strong>Работает через VPN</strong> — веб-скрапинг, без ограничений API
</div>
<div class="tip-box">
    💡 <strong>Совет:</strong> используй фильтр «Обязательные навыки» чтобы сразу видеть только подходящих кандидатов
</div>
        """, unsafe_allow_html=True)
    st.stop()

# ──────────────────────────────────────────────
# Определяем параметры запроса
# ──────────────────────────────────────────────
if input_mode == "🔗 URL со страницы hh.ru" and url_input:
    q, area_id, exp_value, sal_value = parse_url_params(url_input.strip())
    if not q:
        st.error("Не удалось извлечь поисковый запрос из URL.")
        st.stop()
    search_query = q
    st.info(f"Из URL: **{q}** | Регион: {area_id}")
elif not search_query or not search_query.strip():
    st.error("Введи поисковый запрос!")
    st.stop()

query_clean = search_query.strip()

# ──────────────────────────────────────────────
# Загрузка данных
# ──────────────────────────────────────────────
with st.spinner(f"Ищу резюме по запросу «{query_clean}»..."):
    raw_resumes = search_resumes(
        query=query_clean, area=area_id, pages=max_pages,
        experience=exp_value, salary_from=sal_value, order_by=order_val
    )

if not raw_resumes:
    st.error("❌ Резюме не найдены. Попробуй другой запрос или регион.")
    st.stop()

# ──────────────────────────────────────────────
# Фильтрация
# ──────────────────────────────────────────────
required_skills = []
if filter_skills_input.strip():
    required_skills = [s.strip().lower() for s in filter_skills_input.split(",") if s.strip()]

resumes = raw_resumes
if show_only_active:
    resumes = [r for r in resumes if r["status"] == "active_search"]
if required_skills:
    def has_all_skills(r):
        cand_skills_lower = [s.lower() for s in r["skills"]]
        return all(any(req in cs for cs in cand_skills_lower) for req in required_skills)
    resumes = [r for r in resumes if has_all_skills(r)]

st.success(f"✅ Найдено {len(raw_resumes)} резюме | После фильтров: **{len(resumes)}**")

if not resumes:
    st.warning("⚠️ Нет кандидатов, соответствующих фильтрам. Ослабь условия.")
    st.stop()

# ──────────────────────────────────────────────
# Подготовка данных
# ──────────────────────────────────────────────
all_skills = []
for r in resumes:
    all_skills.extend(r["skills"])

skill_counter = Counter(all_skills)
skill_df = pd.DataFrame(skill_counter.most_common(), columns=["Навык", "Кол-во кандидатов"])
skill_df["% кандидатов"] = (skill_df["Кол-во кандидатов"] / len(resumes) * 100).round(1)
skill_df_f = skill_df[skill_df["Кол-во кандидатов"] >= min_skill_freq].copy()

salaries = [r["salary_want"] for r in resumes if r.get("salary_want") and r["salary_want"] > 0]
experiences = [r["exp_months"] for r in resumes if r.get("exp_months", 0) > 0]
active_count  = sum(1 for r in resumes if r["status"] == "active_search")
avg_exp_years = round(sum(experiences) / max(1, len(experiences)) / 12, 1)
median_salary = int(sorted(salaries)[len(salaries)//2]) if salaries else 0

# ──────────────────────────────────────────────
# Метрики
# ──────────────────────────────────────────────
st.markdown("---")
cols = st.columns(5)
metrics = [
    (len(resumes),        "Кандидатов"),
    (active_count,        "Активно ищут"),
    (f"{median_salary:,}" if median_salary else "—", "Медиана зарплаты"),
    (f"{avg_exp_years} л", "Средний опыт"),
    (len(skill_df_f),     "Уникальных навыков"),
]
for col, (val, label) in zip(cols, metrics):
    with col:
        st.markdown(f'<div class="metric-card"><div class="value">{val}</div><div class="label">{label}</div></div>',
                    unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# Вкладки
# ──────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📊 Аналитика", "👥 Кандидаты", "💰 Зарплаты", "📋 Таблица"])

# ──────────── TAB 1: Аналитика ────────────
with tab1:
    if skill_df_f.empty:
        st.warning(f"Нет навыков с частотой ≥{min_skill_freq}. Снизь порог в боковой панели.")
    else:
        top_df = skill_df_f.head(top_n_skills)

        st.markdown("### 📊 Топ навыков кандидатов")
        fig_bar = px.bar(
            top_df[::-1], x="Кол-во кандидатов", y="Навык", orientation="h",
            color="Кол-во кандидатов",
            color_continuous_scale=["#c5cae9", "#3949ab", "#1a237e"],
            text="Кол-во кандидатов",
            hover_data={"% кандидатов": True},
        )
        fig_bar.update_traces(textposition="outside", textfont_size=11)
        fig_bar.update_layout(
            coloraxis_showscale=False,
            height=max(350, top_n_skills * 32),
            margin=dict(l=10, r=70, t=30, b=30),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(family="Inter, sans-serif", size=12),
            xaxis=dict(gridcolor="#f0f0f0"),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        col_pie, col_exp = st.columns(2)
        with col_pie:
            st.markdown("### 🥧 Топ-10 навыков")
            t10 = skill_df_f.head(10).copy()
            rest = skill_df_f.iloc[10:]["Кол-во кандидатов"].sum()
            if rest:
                t10 = pd.concat([t10, pd.DataFrame([{"Навык": "Остальные", "Кол-во кандидатов": rest, "% кандидатов": 0}])], ignore_index=True)
            fig_pie = px.pie(t10, names="Навык", values="Кол-во кандидатов", hole=0.42,
                             color_discrete_sequence=px.colors.sequential.Blues_r)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie.update_layout(showlegend=False, height=350,
                                  margin=dict(l=0,r=0,t=10,b=10),
                                  paper_bgcolor="white",
                                  font=dict(family="Inter, sans-serif", size=11))
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_exp:
            st.markdown("### 📈 Распределение опыта")
            exp_buckets = {"Нет опыта": 0, "< 1 года": 0, "1–3 года": 0,
                           "3–6 лет": 0, "6–10 лет": 0, "> 10 лет": 0}
            for r in resumes:
                m = r.get("exp_months", 0)
                if not m:                      exp_buckets["Нет опыта"] += 1
                elif m < 12:                   exp_buckets["< 1 года"] += 1
                elif m < 36:                   exp_buckets["1–3 года"] += 1
                elif m < 72:                   exp_buckets["3–6 лет"] += 1
                elif m < 120:                  exp_buckets["6–10 лет"] += 1
                else:                          exp_buckets["> 10 лет"] += 1
            fig_exp = px.bar(
                x=list(exp_buckets.keys()), y=list(exp_buckets.values()),
                color=list(exp_buckets.values()),
                color_continuous_scale=["#c5cae9", "#1a237e"],
                labels={"x": "Опыт", "y": "Кандидатов"},
                text=list(exp_buckets.values()),
            )
            fig_exp.update_traces(textposition="outside")
            fig_exp.update_layout(
                coloraxis_showscale=False, height=350,
                margin=dict(l=10, r=10, t=20, b=30),
                plot_bgcolor="white", paper_bgcolor="white",
                font=dict(family="Inter, sans-serif", size=12),
                yaxis=dict(gridcolor="#f0f0f0"),
            )
            st.plotly_chart(fig_exp, use_container_width=True)

        # Статусы поиска
        st.markdown("### 📡 Статус поиска работы")
        status_counts = Counter(r["status"] for r in resumes if r.get("status"))
        if status_counts:
            status_df = pd.DataFrame(
                [(STATUS_LABELS.get(k, (k, ""))[0], v) for k, v in status_counts.most_common()],
                columns=["Статус", "Кол-во"]
            )
            fig_st = px.bar(status_df, x="Статус", y="Кол-во",
                            color="Кол-во", color_continuous_scale=["#c5cae9", "#1a237e"],
                            text="Кол-во")
            fig_st.update_traces(textposition="outside")
            fig_st.update_layout(
                coloraxis_showscale=False, height=280,
                margin=dict(l=10, r=10, t=20, b=30),
                plot_bgcolor="white", paper_bgcolor="white",
                font=dict(family="Inter, sans-serif", size=12),
            )
            st.plotly_chart(fig_st, use_container_width=True)

# ──────────── TAB 2: Кандидаты ────────────
with tab2:
    st.markdown(f"### 👥 Кандидаты ({len(resumes)})")

    sort_by = st.selectbox("Сортировать по", ["По умолчанию", "По опыту (убыв.)", "По зарплате (убыв.)", "По зарплате (возр.)"])
    sorted_resumes = list(resumes)
    if sort_by == "По опыту (убыв.)":
        sorted_resumes.sort(key=lambda r: r["exp_months"] or 0, reverse=True)
    elif sort_by == "По зарплате (убыв.)":
        sorted_resumes.sort(key=lambda r: r["salary_want"] or 0, reverse=True)
    elif sort_by == "По зарплате (возр.)":
        sorted_resumes.sort(key=lambda r: r["salary_want"] or 999999)

    for r in sorted_resumes[:60]:
        status_text, status_cls = STATUS_LABELS.get(r["status"], ("", ""))
        online_dot = "🟢 " if r["is_online"] else ""

        age_str = f"{r['age']} лет" if r.get("age") else ""
        exp_str = exp_label(r["exp_months"])
        sal_str = f"{r['salary_want']:,} {r['salary_cur']}" if r.get("salary_want") else (
                  f"~{r['salary_pred']:,} ₽ (прогноз)" if r.get("salary_pred") else "не указана")
        title_str = r["title"] or "(без названия)"
        meta_parts = filter(None, [age_str, exp_str, f"💰 {sal_str}", r.get("last_active", "")])
        meta_str = " · ".join(meta_parts)

        skill_badges = ""
        for s in r["skills"][:10]:
            is_match = required_skills and any(req in s.lower() for req in required_skills)
            cls = "matched" if is_match else ""
            skill_badges += f'<span class="skill-badge {cls}">{s}</span>'
        if len(r["skills"]) > 10:
            skill_badges += f'<span class="skill-badge">+{len(r["skills"])-10}</span>'

        with st.expander(f"{online_dot}**{title_str}**  {status_text}"):
            ca, cb = st.columns([4, 1])
            with ca:
                st.markdown(f'<div class="resume-meta">{meta_str}</div>', unsafe_allow_html=True)
                if skill_badges:
                    st.markdown("**Навыки:**")
                    st.markdown(skill_badges, unsafe_allow_html=True)
                else:
                    st.caption("Навыки не указаны")
            with cb:
                if r.get("url"):
                    st.link_button("📄 Открыть резюме", r["url"])

    if len(sorted_resumes) > 60:
        st.caption(f"Показано 60 из {len(sorted_resumes)}")

# ──────────── TAB 3: Зарплаты ────────────
with tab3:
    st.markdown("### 💰 Анализ зарплатных ожиданий")

    if not salaries:
        st.info("Кандидаты не указали зарплатные ожидания (или все скрыты).")
    else:
        col_s1, col_s2, col_s3, col_s4 = st.columns(4)
        with col_s1: st.metric("Минимум", f"{min(salaries):,} ₽")
        with col_s2: st.metric("Медиана",  f"{median_salary:,} ₽")
        with col_s3: st.metric("Среднее",  f"{int(sum(salaries)/len(salaries)):,} ₽")
        with col_s4: st.metric("Максимум", f"{max(salaries):,} ₽")

        fig_hist = px.histogram(
            x=salaries, nbins=20,
            labels={"x": "Зарплата (₽)", "y": "Кол-во кандидатов"},
            color_discrete_sequence=["#3949ab"],
            title="Распределение зарплатных ожиданий",
        )
        fig_hist.update_layout(
            bargap=0.05, height=360,
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(family="Inter, sans-serif", size=12),
            yaxis=dict(gridcolor="#f0f0f0"),
            margin=dict(l=10, r=10, t=50, b=30),
        )
        fig_hist.add_vline(x=median_salary, line_dash="dash", line_color="#e53935",
                           annotation_text=f"Медиана: {median_salary:,}")
        st.plotly_chart(fig_hist, use_container_width=True)

        # Зарплата vs Опыт
        sal_exp = [(r["salary_want"], r["exp_months"] / 12)
                   for r in resumes if r.get("salary_want") and r.get("exp_months")]
        if len(sal_exp) >= 5:
            df_se = pd.DataFrame(sal_exp, columns=["Зарплата", "Опыт (лет)"])
            fig_sc = px.scatter(df_se, x="Опыт (лет)", y="Зарплата",
                                color="Зарплата", color_continuous_scale=["#c5cae9", "#1a237e"],
                                trendline="ols",
                                title="Зарплата vs Опыт")
            fig_sc.update_layout(
                height=360, plot_bgcolor="white", paper_bgcolor="white",
                font=dict(family="Inter, sans-serif", size=12),
                margin=dict(l=10, r=10, t=50, b=30),
            )
            st.plotly_chart(fig_sc, use_container_width=True)

# ──────────── TAB 4: Таблица ────────────
with tab4:
    st.markdown("### 📋 Таблица резюме")

    search_t = st.text_input("🔍 Поиск по должности или навыку")
    df_table = pd.DataFrame([{
        "Должность":    r["title"],
        "Опыт":         exp_label(r["exp_months"]),
        "Зарплата (₽)": r["salary_want"] or "",
        "Навыки":       ", ".join(r["skills"][:8]),
        "Статус":       STATUS_LABELS.get(r["status"], ("—",""))[0],
        "Последн. активность": r["last_active"],
        "Ссылка":       r["url"],
    } for r in resumes])

    if search_t.strip():
        q = search_t.strip().lower()
        df_table = df_table[
            df_table["Должность"].str.lower().str.contains(q, na=False) |
            df_table["Навыки"].str.lower().str.contains(q, na=False)
        ]

    st.dataframe(
        df_table.drop(columns=["Ссылка"]),
        use_container_width=True, height=400,
        column_config={
            "Должность": st.column_config.TextColumn(width="large"),
            "Навыки":    st.column_config.TextColumn(width="large"),
            "Зарплата (₽)": st.column_config.NumberColumn(format="%d ₽"),
        },
    )

    csv_out = df_table.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Скачать CSV", data=csv_out,
                       file_name=f"candidates_{query_clean.replace(' ','_')}.csv",
                       mime="text/csv")

    if not skill_df_f.empty:
        csv_skills = skill_df_f.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("⬇️ Скачать навыки CSV", data=csv_skills,
                           file_name=f"skills_{query_clean.replace(' ','_')}.csv",
                           mime="text/csv")
