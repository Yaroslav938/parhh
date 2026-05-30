import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from playwright.sync_api import sync_playwright

st.set_page_config(
    page_title="HH Recruiter Browser Parser",
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
    padding: 2rem 2.5rem; border-radius: 14px; margin-bottom: 1.25rem; color: white;
}
.metric-card {
    background: white; border:1px solid #e8eaf6; border-radius:12px; padding:1rem 1.25rem;
    text-align:center; box-shadow:0 2px 8px rgba(26,35,126,0.07);
}
.metric-card .value { font-size:1.8rem; font-weight:700; color:#1a237e; }
.metric-card .label { font-size:.8rem; color:#6b7280; text-transform:uppercase; }
.skill-badge {
    display:inline-block; padding:.2rem .65rem; border-radius:999px; margin:.15rem;
    font-size:.78rem; font-weight:500; background:#e8eaf6; color:#1a237e; border:1px solid #c5cae9;
}
.skill-badge.matched { background:#1a237e; color:white; border-color:#1a237e; }
.info-box {
    background:#eff6ff; border-left:4px solid #1a237e; padding:.75rem 1rem; border-radius:0 8px 8px 0;
    margin:.5rem 0; font-size:.9rem;
}
.tip-box {
    background:#f0fdf4; border-left:4px solid #16a34a; padding:.75rem 1rem; border-radius:0 8px 8px 0;
    margin:.5rem 0; font-size:.9rem;
}
</style>
""", unsafe_allow_html=True)

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
    "active_search": "🟢 Активно ищет",
    "looking_for_offers": "🟡 Рассматривает предложения",
    "not_looking_for_job": "⚫ Не ищет",
    "has_job_offer": "🟠 Есть предложение",
    "accepted_job_offer": "🔵 Принял оффер",
}

OUT = Path('output')
OUT.mkdir(exist_ok=True)
COOKIE_FILE = OUT / 'hh_cookies.json'
STORAGE_FILE = OUT / 'hh_storage_state.json'


def parse_url_params(url: str):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    query = qs.get('text', [''])[0]
    area = qs.get('area', ['113'])[0]
    exp = qs.get('experience', [''])[0]
    sal = qs.get('salary', [''])[0]
    return query, area, exp, sal


def extract_json_array(html: str, key: str):
    idx = html.find(f'"{key}":')
    if idx == -1:
        return []
    start = idx + len(f'"{key}":')
    depth = 0
    end = start
    in_str = False
    esc = False
    for i in range(start, min(start + 1200000, len(html))):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(html[start:end])
    except Exception:
        return []


def parse_resume_item(item: dict) -> dict:
    attrs = item.get('_attributes', {}) or {}
    resume_hash = attrs.get('hash', '') if isinstance(attrs, dict) else ''
    resume_id = attrs.get('id', '') if isinstance(attrs, dict) else ''
    permission = attrs.get('permission', '') if isinstance(attrs, dict) else ''

    def get_str(field):
        lst = item.get(field, [])
        return lst[0].get('string', '') if lst and isinstance(lst[0], dict) else ''

    title = get_str('title')
    age = get_str('age')
    exp_months = get_str('totalExperience')

    sal_lst = item.get('salary') or []
    salary_amt = sal_lst[0].get('amount', 0) if sal_lst else 0
    salary_cur = sal_lst[0].get('currency', 'RUR') if sal_lst else 'RUR'

    pred_lst = item.get('predictedSalary') or []
    pred_sal = pred_lst[0].get('amount', 0) if pred_lst else 0

    skills = []
    for s in (item.get('keySkills') or []):
        if isinstance(s, dict) and 'string' in s:
            t = s['string'].strip()
            if 1 < len(t) < 80:
                skills.append(t)

    status_raw = ''
    js_lst = item.get('jobSearchStatus') or []
    if js_lst and isinstance(js_lst[0], dict):
        inner = js_lst[0].get('jobSearchStatus', {}) or {}
        status_raw = inner.get('name', '') if isinstance(inner, dict) else ''

    return {
        'id': str(resume_id),
        'hash': resume_hash,
        'title': title,
        'age': int(age) if str(age).isdigit() else None,
        'exp_months': int(exp_months) if str(exp_months).isdigit() else 0,
        'salary_want': int(salary_amt) if salary_amt else None,
        'salary_pred': int(pred_sal) if pred_sal else None,
        'salary_cur': salary_cur,
        'skills': skills,
        'status': status_raw,
        'url': f'https://hh.ru/resume/{resume_hash}' if resume_hash else '',
        'can_view': permission in ('view_without_contacts', 'full_access'),
    }


def run_hh_search_logged_in(search_url: str, pages: int = 3, headless: bool = True):
    resumes = []
    debug = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(STORAGE_FILE) if STORAGE_FILE.exists() else None)
        page = context.new_page()
        page.goto('https://hh.ru/', wait_until='domcontentloaded', timeout=90000)

        if 'login' in page.url or 'auth' in page.url:
            browser.close()
            return [], ['Сессия неавторизована. Нужно заново сохранить cookies/storage state.']

        for pg in range(pages):
            url = search_url
            if 'page=' in url:
                url = re.sub(r'page=\d+', f'page={pg}', url)
            else:
                sep = '&' if '?' in url else '?'
                url = f'{url}{sep}page={pg}'
            page.goto(url, wait_until='networkidle', timeout=90000)
            html = page.content()
            raw = extract_json_array(html, 'resumes')
            debug.append(f'page={pg} url={page.url} resumes={len(raw)}')
            if not raw:
                break
            added = 0
            for item in raw:
                parsed = parse_resume_item(item)
                rid = parsed['id']
                if rid and rid not in seen:
                    seen.add(rid)
                    resumes.append(parsed)
                    added += 1
            if added == 0:
                break
            time.sleep(1.0)

        context.storage_state(path=str(STORAGE_FILE))
        browser.close()
    return resumes, debug


def save_storage_state_interactive(headless: bool = False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto('https://hh.ru/account/login', wait_until='domcontentloaded', timeout=90000)
        return browser, context, page


def exp_label(months: int) -> str:
    if not months:
        return 'Нет опыта'
    y = months // 12
    m = months % 12
    parts = []
    if y:
        parts.append(f'{y} г.')
    if m:
        parts.append(f'{m} мес.')
    return ' '.join(parts)

with st.sidebar:
    st.markdown('### 🔐 Авторизация HH')
    auth_mode = st.radio('Режим авторизации', ['Использовать сохранённую сессию', 'Вставить storage_state JSON'])
    if auth_mode == 'Вставить storage_state JSON':
        storage_text = st.text_area('storage_state JSON из Playwright', height=180, placeholder='{"cookies": [...], "origins": [...]}')
        if st.button('💾 Сохранить storage_state', use_container_width=True):
            if storage_text.strip():
                STORAGE_FILE.write_text(storage_text.strip(), encoding='utf-8')
                st.success('storage_state сохранён')
            else:
                st.error('Вставь JSON')
    st.markdown('---')
    st.markdown('### 🎯 Поиск кандидатов')
    input_mode = st.radio('Режим ввода', ['🔎 Параметры поиска', '🔗 URL со страницы hh.ru'])
    if input_mode == '🔎 Параметры поиска':
        search_query = st.text_input('Должность / ключевые слова', placeholder='Python developer, биоинформатик...')
        area_name = st.selectbox('Регион', list(AREA_OPTIONS.keys()))
        area_id = AREA_OPTIONS[area_name]
        exp_name = st.selectbox('Опыт', list(EXP_OPTIONS.keys()))
        exp_value = EXP_OPTIONS[exp_name]
        sal_name = st.selectbox('Зарплата от', list(SALARY_OPTIONS.keys()))
        sal_value = SALARY_OPTIONS[sal_name]
        url_input = None
    else:
        url_input = st.text_area('URL со страницы поиска резюме', height=100, placeholder='https://hh.ru/search/resume?...')
        search_query = None
        area_id = '113'
        exp_value = ''
        sal_value = ''
    st.markdown('---')
    pages = st.slider('Страниц резюме', 1, 20, 3)
    top_n = st.slider('Топ навыков', 5, 40, 20)
    min_freq = st.slider('Мин. частота навыка', 1, 20, 2)
    required_skills_text = st.text_input('Обязательные навыки', placeholder='Python, SQL, Docker')
    active_only = st.checkbox('Только активно ищущих', value=False)
    headless = st.checkbox('Headless browser', value=True)
    run_btn = st.button('🚀 Запустить поиск', type='primary', use_container_width=True)

st.markdown("""
<div class="main-header">
  <h1>🎯 HH Recruiter Browser Parser</h1>
  <p>Поиск кандидатов через авторизованный браузер Playwright, чтобы hh.ru реально реагировал на поисковый запрос.</p>
</div>
""", unsafe_allow_html=True)

if not run_btn:
    st.markdown("""
<div class="info-box">
Этот вариант нужен потому, что анонимный HTML поиска резюме на hh.ru отдаёт почти одинаковую выдачу для разных запросов. Авторизованный браузер решает это и получает реальный SERP аккаунта.</div>
<div class="tip-box">
Как получить <code>storage_state</code>: локально запусти маленький Playwright-скрипт, залогинься в hh.ru, сохрани state и вставь JSON сюда.</div>
""", unsafe_allow_html=True)
    st.code("""from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto('https://hh.ru/account/login')
    input('После логина нажми Enter...')
    context.storage_state(path='hh_storage_state.json')
    browser.close()
""")
    st.stop()

if not STORAGE_FILE.exists():
    st.error('Нет сохранённого storage_state. Сначала вставь JSON сессии hh.ru.')
    st.stop()

if input_mode == '🔗 URL со страницы hh.ru' and url_input and url_input.strip():
    search_url = url_input.strip()
    q, area_id, exp_value, sal_value = parse_url_params(search_url)
    query_label = q or 'custom_url'
else:
    if not search_query or not search_query.strip():
        st.error('Введи поисковый запрос.')
        st.stop()
    query_label = search_query.strip()
    params = {
        'text': query_label,
        'area': area_id,
        'order_by': 'relevance',
        'ored_clusters': 'true',
        'search_period': '0',
    }
    if exp_value:
        params['experience'] = exp_value
    if sal_value:
        params['salary'] = sal_value
    qs = '&'.join(f'{k}={v}' for k, v in params.items() if v)
    search_url = f'https://hh.ru/search/resume?{qs}'

with st.spinner('Открываю hh.ru через Playwright и собираю реальные результаты...'):
    resumes, debug = run_hh_search_logged_in(search_url=search_url, pages=pages, headless=headless)

if not resumes:
    st.error('Не удалось получить резюме. Проверь актуальность storage_state и доступ аккаунта к поиску резюме.')
    if debug:
        st.code('\n'.join(debug))
    st.stop()

required_skills = [x.strip().lower() for x in required_skills_text.split(',') if x.strip()]
filtered = resumes
if active_only:
    filtered = [r for r in filtered if r['status'] == 'active_search']
if required_skills:
    def ok(r):
        s = [x.lower() for x in r['skills']]
        return all(any(req in y for y in s) for req in required_skills)
    filtered = [r for r in filtered if ok(r)]

if not filtered:
    st.warning('После фильтров кандидатов не осталось.')
    st.code('\n'.join(debug))
    st.stop()

all_skills = []
for r in filtered:
    all_skills.extend(r['skills'])
sc = Counter(all_skills)
skill_df = pd.DataFrame(sc.most_common(), columns=['Навык', 'Кол-во кандидатов'])
if not skill_df.empty:
    skill_df['% кандидатов'] = (skill_df['Кол-во кандидатов'] / len(filtered) * 100).round(1)
    skill_df = skill_df[skill_df['Кол-во кандидатов'] >= min_freq].copy()

salaries = [r['salary_want'] for r in filtered if r.get('salary_want')]
exp_months = [r['exp_months'] for r in filtered if r.get('exp_months')]
median_salary = int(sorted(salaries)[len(salaries)//2]) if salaries else 0
avg_exp = round(sum(exp_months) / max(1, len(exp_months)) / 12, 1)
active_count = sum(1 for r in filtered if r['status'] == 'active_search')

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-card"><div class="value">{len(filtered)}</div><div class="label">Кандидатов</div></div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-card"><div class="value">{active_count}</div><div class="label">Активно ищут</div></div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="metric-card"><div class="value">{median_salary:,}</div><div class="label">Медиана зарплаты</div></div>', unsafe_allow_html=True)
with c4:
    st.markdown(f'<div class="metric-card"><div class="value">{avg_exp}</div><div class="label">Средний опыт, лет</div></div>', unsafe_allow_html=True)

st.markdown('### 📊 Навыки кандидатов')
if skill_df.empty:
    st.info('У кандидатов нет извлечённых навыков.')
else:
    top_df = skill_df.head(top_n)
    fig = px.bar(top_df[::-1], x='Кол-во кандидатов', y='Навык', orientation='h', color='Кол-во кандидатов',
                 color_continuous_scale=['#c5cae9', '#3949ab', '#1a237e'], text='Кол-во кандидатов')
    fig.update_traces(textposition='outside')
    fig.update_layout(coloraxis_showscale=False, height=max(350, len(top_df)*30), margin=dict(l=10,r=60,t=20,b=20))
    st.plotly_chart(fig, use_container_width=True)

col_a, col_b = st.columns(2)
with col_a:
    st.markdown('### 💰 Зарплаты')
    if salaries:
        fig_hist = px.histogram(x=salaries, nbins=20, color_discrete_sequence=['#3949ab'])
        fig_hist.add_vline(x=median_salary, line_dash='dash', line_color='#e53935')
        fig_hist.update_layout(height=320, margin=dict(l=10,r=10,t=20,b=20))
        st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info('Нет зарплатных ожиданий.')
with col_b:
    st.markdown('### 📈 Опыт')
    buckets = {'Нет опыта':0, '<1 года':0, '1–3 года':0, '3–6 лет':0, '6–10 лет':0, '>10 лет':0}
    for m in exp_months:
        if not m: buckets['Нет опыта'] += 1
        elif m < 12: buckets['<1 года'] += 1
        elif m < 36: buckets['1–3 года'] += 1
        elif m < 72: buckets['3–6 лет'] += 1
        elif m < 120: buckets['6–10 лет'] += 1
        else: buckets['>10 лет'] += 1
    fig2 = px.bar(x=list(buckets.keys()), y=list(buckets.values()), color=list(buckets.values()), color_continuous_scale=['#c5cae9','#1a237e'])
    fig2.update_layout(coloraxis_showscale=False, height=320, margin=dict(l=10,r=10,t=20,b=20))
    st.plotly_chart(fig2, use_container_width=True)

st.markdown('### 👥 Кандидаты')
for r in filtered[:80]:
    title = r['title'] or '(без названия)'
    status = STATUS_LABELS.get(r['status'], r['status'])
    sal = f"{r['salary_want']:,} {r['salary_cur']}" if r.get('salary_want') else (f"~{r['salary_pred']:,} ₽" if r.get('salary_pred') else 'не указана')
    meta = ' · '.join(x for x in [f"{r['age']} лет" if r.get('age') else '', exp_label(r['exp_months']), f'💰 {sal}'] if x)
    with st.expander(f"**{title}** | {status}"):
        st.caption(meta)
        if r['skills']:
            badges = ''
            for s in r['skills'][:12]:
                cls = 'matched' if required_skills and any(req in s.lower() for req in required_skills) else ''
                badges += f'<span class="skill-badge {cls}">{s}</span>'
            st.markdown(badges, unsafe_allow_html=True)
        if r['url']:
            st.link_button('📄 Открыть резюме', r['url'])

export_df = pd.DataFrame([{
    'id': r['id'],
    'title': r['title'],
    'age': r['age'],
    'exp_months': r['exp_months'],
    'salary_want': r['salary_want'],
    'salary_pred': r['salary_pred'],
    'status': r['status'],
    'skills': ', '.join(r['skills']),
    'url': r['url'],
} for r in filtered])
export_df.to_csv(OUT / 'candidates.csv', index=False, encoding='utf-8-sig')
if not skill_df.empty:
    skill_df.to_csv(OUT / 'skills.csv', index=False, encoding='utf-8-sig')

st.markdown('### 🔧 Debug')
st.code('\n'.join(debug[:20]))
