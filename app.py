"""打卡系统 - 在线多用户版 (北京时间 / 多次打卡)"""
import socket
socket.getfqdn = lambda *args: '127.0.0.1'

import os
from datetime import datetime, date, timedelta, timezone
import calendar
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')

# ---------- 北京时间 ----------
BJ_TZ = timezone(timedelta(hours=8))


def bj_now():
    return datetime.now(BJ_TZ)


def bj_today():
    return bj_now().date()


def bj_time_str():
    return bj_now().strftime('%H:%M:%S')


# ---------- 数据库 ----------
IS_POSTGRES = 'DATABASE_URL' in os.environ


def get_db():
    if IS_POSTGRES:
        import psycopg2
        return psycopg2.connect(os.environ['DATABASE_URL'])
    else:
        import sqlite3
        conn = sqlite3.connect(
            os.path.join(os.path.dirname(__file__), 'attendance.db')
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # 兼容旧表：删掉重建（新项目没影响）
    if IS_POSTGRES:
        cur.execute("DROP TABLE IF EXISTS attendance_records")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(80) UNIQUE NOT NULL,
                password_hash VARCHAR(200) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance_records (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                date DATE NOT NULL,
                check_in TIME NOT NULL,
                check_out TIME,
                summary TEXT
            )
        """)
    else:
        cur.execute("DROP TABLE IF EXISTS attendance_records")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                date TEXT NOT NULL,
                check_in TEXT NOT NULL,
                check_out TEXT,
                summary TEXT
            )
        """)

    conn.commit()
    conn.close()


init_db()


# ---------- 辅助 ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '未登录'}), 401
        return f(*args, **kwargs)
    return decorated


def calc_hours(check_in, check_out):
    if not check_in or not check_out:
        return 0
    fmt = '%H:%M:%S'
    t1 = datetime.strptime(str(check_in)[:8], fmt)
    t2 = datetime.strptime(str(check_out)[:8], fmt)
    return round((t2 - t1).total_seconds() / 3600, 2)


def query(user_id, start_date, end_date):
    """查询日期范围内的打卡记录"""
    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = %s AND date >= %s AND date <= %s ORDER BY date, id',
            (user_id, start_date, end_date)
        ).fetchall()
    else:
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date, id',
            (user_id, start_date, end_date)
        ).fetchall()
    conn.close()
    return rows


# ---------- 认证 ----------
@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'})
    if len(username) < 2 or len(username) > 20:
        return jsonify({'success': False, 'message': '用户名长度2-20个字符'})
    if len(password) < 4:
        return jsonify({'success': False, 'message': '密码至少4个字符'})

    conn = get_db()
    cur = conn.cursor()

    existing = cur.execute(
        'SELECT id FROM users WHERE username = ?' if not IS_POSTGRES
        else 'SELECT id FROM users WHERE username = %s',
        (username,)
    ).fetchone()

    if existing:
        conn.close()
        return jsonify({'success': False, 'message': '用户名已存在'})

    pwhash = generate_password_hash(password)
    cur.execute(
        'INSERT INTO users (username, password_hash) VALUES (?, ?)' if not IS_POSTGRES
        else 'INSERT INTO users (username, password_hash) VALUES (%s, %s)',
        (username, pwhash)
    )
    conn.commit()
    conn.close()

    session['user_id'] = cur.lastrowid if not IS_POSTGRES else _get_user_id(username)
    session['username'] = username
    return jsonify({'success': True, 'username': username})


def _get_user_id(username):
    conn = get_db()
    cur = conn.cursor()
    u = cur.execute(
        'SELECT id FROM users WHERE username = %s', (username,)
    ).fetchone()
    conn.close()
    return u['id'] if u else None


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'})

    conn = get_db()
    cur = conn.cursor()
    user = cur.execute(
        'SELECT id, username, password_hash FROM users WHERE username = ?' if not IS_POSTGRES
        else 'SELECT id, username, password_hash FROM users WHERE username = %s',
        (username,)
    ).fetchone()
    conn.close()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'success': False, 'message': '用户名或密码错误'})

    session['user_id'] = user['id']
    session['username'] = user['username']
    return jsonify({'success': True, 'username': user['username']})


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/me')
def me():
    if 'user_id' not in session:
        return jsonify({'logged_in': False}), 401
    return jsonify({
        'logged_in': True,
        'user_id': session['user_id'],
        'username': session['username']
    })


# ---------- 页面 ----------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('index.html')


# ---------- 状态 ----------
@app.route('/api/status')
@login_required
def get_status():
    user_id = session['user_id']
    today_str = bj_today().isoformat()
    now = bj_time_str()

    sessions = query(user_id, today_str, today_str)
    open_session = None
    total_hours = 0

    for s in sessions:
        if s['check_out'] is None:
            open_session = {
                'id': s['id'],
                'check_in': str(s['check_in'])[:5],
                'current_hours': calc_hours(str(s['check_in'])[:8], now),
            }
        else:
            total_hours += calc_hours(str(s['check_in'])[:8], str(s['check_out'])[:8])

    # 加上当前进行中的时长
    if open_session:
        total_hours += open_session['current_hours']

    return jsonify({
        'checked_in': len(sessions) > 0,
        'has_open_session': open_session is not None,
        'open_session': open_session,
        'sessions_count': len(sessions),
        'today_hours': round(total_hours, 2),
    })


# ---------- 上班 ----------
@app.route('/api/check_in', methods=['POST'])
@login_required
def check_in():
    user_id = session['user_id']
    today_str = bj_today().isoformat()
    now = bj_time_str()

    # 检查是否有未下班的 session
    sessions = query(user_id, today_str, today_str)
    for s in sessions:
        if s['check_out'] is None:
            return jsonify({'success': False, 'message': '还有未结束的工作，请先下班打卡'})

    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute(
            'INSERT INTO attendance_records (user_id, date, check_in) VALUES (%s, %s, %s)',
            (user_id, today_str, now)
        )
    else:
        cur.execute(
            'INSERT INTO attendance_records (user_id, date, check_in) VALUES (?, ?, ?)',
            (user_id, today_str, now)
        )
    conn.commit()
    session_id = cur.lastrowid if not IS_POSTGRES else _get_last_id(cur)
    conn.close()
    return jsonify({'success': True, 'time': now, 'session_id': session_id})


def _get_last_id(cur):
    cur.execute('SELECT LASTVAL()')
    return cur.fetchone()[0]


# ---------- 下班 ----------
@app.route('/api/check_out', methods=['POST'])
@login_required
def check_out():
    user_id = session['user_id']
    today_str = bj_today().isoformat()
    now = bj_time_str()
    data = request.get_json()
    summary = (data or {}).get('summary', '')

    # 找最新未下班的 session
    sessions = query(user_id, today_str, today_str)
    open_record = None
    for s in sessions:
        if s['check_out'] is None:
            open_record = s

    if open_record is None:
        return jsonify({'success': False, 'message': '没有需要下班打卡的工作'})

    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute(
            'UPDATE attendance_records SET check_out = %s, summary = %s WHERE id = %s',
            (now, summary, open_record['id'])
        )
    else:
        cur.execute(
            'UPDATE attendance_records SET check_out = ?, summary = ? WHERE id = ?',
            (now, summary, open_record['id'])
        )
    conn.commit()

    hours = calc_hours(str(open_record['check_in'])[:8], now)
    conn.close()
    return jsonify({'success': True, 'time': now, 'hours': hours, 'session_id': open_record['id']})


# ---------- 补录 ----------
@app.route('/api/backfill', methods=['POST'])
@login_required
def backfill():
    user_id = session['user_id']
    data = request.get_json() or {}
    record_date = data.get('date')
    check_in = data.get('check_in')
    check_out = data.get('check_out')
    summary = data.get('summary', '')

    if not record_date or not check_in:
        return jsonify({'success': False, 'message': '日期和上班时间为必填'})

    try:
        datetime.strptime(check_in, '%H:%M')
    except ValueError:
        return jsonify({'success': False, 'message': '上班时间格式错误 (HH:MM)'})

    if check_out:
        try:
            datetime.strptime(check_out, '%H:%M')
        except ValueError:
            return jsonify({'success': False, 'message': '下班时间格式错误 (HH:MM)'})

    check_in_full = check_in + ':00'
    check_out_full = (check_out + ':00') if check_out else None

    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute(
            'INSERT INTO attendance_records (user_id, date, check_in, check_out, summary) VALUES (%s, %s, %s, %s, %s)',
            (user_id, record_date, check_in_full, check_out_full, summary)
        )
    else:
        cur.execute(
            'INSERT INTO attendance_records (user_id, date, check_in, check_out, summary) VALUES (?, ?, ?, ?, ?)',
            (user_id, record_date, check_in_full, check_out_full, summary)
        )
    conn.commit()

    hours = calc_hours(check_in_full, check_out_full) if check_out_full else 0
    conn.close()
    return jsonify({'success': True, 'date': record_date, 'hours': hours, 'session_id': cur.lastrowid})


# ---------- 删除记录 ----------
@app.route('/api/records/<int:record_id>', methods=['DELETE'])
@login_required
def delete_record(record_id):
    user_id = session['user_id']
    conn = get_db()
    cur = conn.cursor()

    if IS_POSTGRES:
        cur.execute(
            'DELETE FROM attendance_records WHERE id = %s AND user_id = %s',
            (record_id, user_id)
        )
    else:
        cur.execute(
            'DELETE FROM attendance_records WHERE id = ? AND user_id = ?',
            (record_id, user_id)
        )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()

    if deleted:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '未找到该记录'})


# ---------- 数据接口 ----------
@app.route('/api/hours/daily')
@login_required
def daily_hours():
    user_id = session['user_id']
    today = bj_today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

    records = query(user_id, monday.isoformat(), sunday.isoformat())
    now_str = bj_time_str()

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        sessions = []
        day_total = 0

        for r in records:
            r_date = str(r['date'])[:10] if IS_POSTGRES else r['date']
            if r_date == d.isoformat():
                end = str(r['check_out'])[:8] if r['check_out'] else (now_str if d == today else None)
                h = calc_hours(str(r['check_in'])[:8], end) if end else 0
                sessions.append({
                    'id': r['id'],
                    'check_in': str(r['check_in'])[:5],
                    'check_out': str(r['check_out'])[:5] if r['check_out'] else None,
                    'hours': h,
                    'summary': r['summary'],
                })
                day_total += h

        days.append({
            'date': d.isoformat(),
            'label': f'{d.month}/{d.day} {weekday_names[i]}',
            'total_hours': round(day_total, 2),
            'sessions': sessions,
        })
    return jsonify(days)


@app.route('/api/hours/weekly')
@login_required
def weekly_hours():
    user_id = session['user_id']
    today = bj_today()
    first_day = today.replace(day=1)
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    records = query(user_id, first_day.isoformat(), last_day.isoformat())

    weeks = []
    current = first_day - timedelta(days=first_day.weekday())
    week_num = 1
    while current <= last_day:
        week_end = current + timedelta(days=6)
        week_hours = 0
        for r in records:
            r_date_str = str(r['date'])[:10] if IS_POSTGRES else r['date']
            r_date = date.fromisoformat(r_date_str)
            if current <= r_date <= week_end and r['check_out']:
                week_hours += calc_hours(str(r['check_in'])[:8], str(r['check_out'])[:8])
        weeks.append({
            'label': f'第{week_num}周',
            'range': f'{current.month}/{current.day}-{week_end.month}/{week_end.day}',
            'hours': round(week_hours, 2),
        })
        current = week_end + timedelta(days=1)
        week_num += 1
    return jsonify(weeks)


@app.route('/api/hours/monthly')
@login_required
def monthly_hours():
    user_id = session['user_id']
    year_str = str(bj_today().year)

    if IS_POSTGRES:
        records = query(user_id, f'{year_str}-01-01', f'{year_str}-12-31')
    else:
        rows = _query_like(user_id, f'{year_str}-%')
        records = rows

    months = []
    for m in range(1, 13):
        prefix = f'{year_str}-{m:02d}'
        month_hours = 0
        days_set = set()
        for r in records:
            r_date_str = str(r['date'])[:10] if IS_POSTGRES else r['date']
            if r_date_str.startswith(prefix) and r['check_out']:
                month_hours += calc_hours(str(r['check_in'])[:8], str(r['check_out'])[:8])
                days_set.add(r_date_str)
        months.append({
            'label': f'{m}月',
            'hours': round(month_hours, 2),
            'days': len(days_set),
        })
    return jsonify(months)


def _query_like(user_id, pattern):
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM attendance_records WHERE user_id = ? AND date LIKE ? ORDER BY date",
        (user_id, pattern)
    ).fetchall()
    conn.close()
    return rows


# ---------- 全部记录 ----------
@app.route('/api/records/all')
@login_required
def all_records():
    user_id = session['user_id']
    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = %s ORDER BY date DESC, id DESC',
            (user_id,)
        ).fetchall()
    else:
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = ? ORDER BY date DESC, id DESC',
            (user_id,)
        ).fetchall()
    conn.close()

    from collections import OrderedDict
    days = OrderedDict()
    for r in rows:
        r_date = str(r['date'])[:10] if IS_POSTGRES else r['date']
        if r_date not in days:
            days[r_date] = {'date': r_date, 'total_hours': 0, 'sessions': []}
        h = calc_hours(str(r['check_in'])[:8], str(r['check_out'])[:8]) if r['check_out'] else 0
        days[r_date]['sessions'].append({
            'id': r['id'],
            'check_in': str(r['check_in'])[:5],
            'check_out': str(r['check_out'])[:5] if r['check_out'] else None,
            'hours': h,
            'summary': r['summary'],
        })
        days[r_date]['total_hours'] = round(days[r_date]['total_hours'] + h, 2)
    return jsonify(list(days.values()))


# ---------- 每年 ----------
@app.route('/api/hours/yearly')
@login_required
def yearly_hours():
    user_id = session['user_id']

    if IS_POSTGRES:
        records = query(user_id, '2000-01-01', '2100-12-31')
    else:
        conn = get_db()
        cur = conn.cursor()
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = ? ORDER BY date',
            (user_id,)
        ).fetchall()
        conn.close()
        records = rows

    years = {}
    for r in records:
        r_date_str = str(r['date'])[:10] if IS_POSTGRES else r['date']
        year = r_date_str[:4]
        if year not in years:
            years[year] = {'label': f'{year}年', 'hours': 0, 'days': set()}
        if r['check_out']:
            years[year]['hours'] += calc_hours(str(r['check_in'])[:8], str(r['check_out'])[:8])
            years[year]['days'].add(r_date_str)

    result = []
    for y in sorted(years.keys()):
        result.append({
            'label': years[y]['label'],
            'hours': round(years[y]['hours'], 2),
            'days': len(years[y]['days']),
        })
    return jsonify(result)


if __name__ == '__main__':
    mode = 'PRODUCTION' if IS_POSTGRES else 'DEV (SQLite)'
    print(f'=== 打卡系统 [{mode}] (北京时间) ===')
    print(f'    http://127.0.0.1:5000')
    app.run(host='127.0.0.1', debug=not IS_POSTGRES, port=5000)
