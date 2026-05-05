"""打卡系统 - 在线多用户版"""
import socket
socket.getfqdn = lambda *args: '127.0.0.1'

import os
from datetime import datetime, date, timedelta
import calendar
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get(
    'SECRET_KEY',
    'dev-secret-change-in-production-123456'
)

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

    if IS_POSTGRES:
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
                summary TEXT,
                UNIQUE(user_id, date)
            )
        """)
    else:
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
                summary TEXT,
                UNIQUE(user_id, date)
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


def get_cur_time_str():
    return datetime.now().strftime('%H:%M:%S' if not IS_POSTGRES else '%H:%M:%S')


def calc_hours(check_in, check_out):
    if not check_in or not check_out:
        return 0
    fmt = '%H:%M:%S'
    t1 = datetime.strptime(str(check_in)[:8], fmt)
    t2 = datetime.strptime(str(check_out)[:8], fmt)
    return round((t2 - t1).total_seconds() / 3600, 2)


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
    user_id = cur.lastrowid if not IS_POSTGRES else cur.fetchone()
    if IS_POSTGRES:
        cur.execute('SELECT id FROM users WHERE username = %s', (username,))
        user_id = cur.fetchone()[0]
    conn.close()

    session['user_id'] = user_id
    session['username'] = username
    return jsonify({'success': True, 'username': username})


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


# ---------- 打卡 ----------
@app.route('/api/status')
@login_required
def get_status():
    user_id = session['user_id']
    today_str = date.today().isoformat()
    conn = get_db()

    if IS_POSTGRES:
        record = conn.cursor().execute(
            'SELECT * FROM attendance_records WHERE user_id = %s AND date = %s',
            (user_id, today_str)
        ).fetchone()
    else:
        record = conn.cursor().execute(
            'SELECT * FROM attendance_records WHERE user_id = ? AND date = ?',
            (user_id, today_str)
        ).fetchone()
    conn.close()

    if record is None:
        return jsonify({'checked_in': False})

    now = get_cur_time_str()
    current_hours = 0
    if record['check_out'] is None:
        current_hours = calc_hours(str(record['check_in'])[:8], now)

    return jsonify({
        'checked_in': True,
        'checked_out': record['check_out'] is not None,
        'check_in_time': str(record['check_in'])[:5],
        'check_out_time': str(record['check_out'])[:5] if record['check_out'] else None,
        'summary': record['summary'],
        'current_hours': current_hours,
    })


@app.route('/api/check_in', methods=['POST'])
@login_required
def check_in():
    user_id = session['user_id']
    today_str = date.today().isoformat()
    now = get_cur_time_str()

    conn = get_db()
    cur = conn.cursor()
    try:
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
        return jsonify({'success': True, 'time': now})
    except Exception:
        return jsonify({'success': False, 'message': '今天已经打过卡了'})
    finally:
        conn.close()


@app.route('/api/check_out', methods=['POST'])
@login_required
def check_out():
    user_id = session['user_id']
    today_str = date.today().isoformat()
    now = get_cur_time_str()
    data = request.get_json()
    summary = (data or {}).get('summary', '')

    conn = get_db()
    cur = conn.cursor()

    if IS_POSTGRES:
        record = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = %s AND date = %s',
            (user_id, today_str)
        ).fetchone()
    else:
        record = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = ? AND date = ?',
            (user_id, today_str)
        ).fetchone()

    if record is None:
        conn.close()
        return jsonify({'success': False, 'message': '今天还没有上班打卡'})
    if record['check_out'] is not None:
        conn.close()
        return jsonify({'success': False, 'message': '今天已经打过下班卡了'})

    if IS_POSTGRES:
        cur.execute(
            'UPDATE attendance_records SET check_out = %s, summary = %s WHERE user_id = %s AND date = %s',
            (now, summary, user_id, today_str)
        )
    else:
        cur.execute(
            'UPDATE attendance_records SET check_out = ?, summary = ? WHERE user_id = ? AND date = ?',
            (now, summary, user_id, today_str)
        )
    conn.commit()

    hours = calc_hours(str(record['check_in'])[:8], now)
    conn.close()
    return jsonify({'success': True, 'time': now, 'hours': hours})


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

    # 删除旧记录（如果存在）
    if IS_POSTGRES:
        cur.execute(
            'DELETE FROM attendance_records WHERE user_id = %s AND date = %s',
            (user_id, record_date)
        )
        cur.execute(
            'INSERT INTO attendance_records (user_id, date, check_in, check_out, summary) VALUES (%s, %s, %s, %s, %s)',
            (user_id, record_date, check_in_full, check_out_full, summary)
        )
    else:
        cur.execute(
            'DELETE FROM attendance_records WHERE user_id = ? AND date = ?',
            (user_id, record_date)
        )
        cur.execute(
            'INSERT INTO attendance_records (user_id, date, check_in, check_out, summary) VALUES (?, ?, ?, ?, ?)',
            (user_id, record_date, check_in_full, check_out_full, summary)
        )
    conn.commit()

    hours = calc_hours(check_in_full, check_out_full) if check_out_full else 0
    conn.close()
    return jsonify({'success': True, 'date': record_date, 'hours': hours})


# ---------- 删除记录 ----------
@app.route('/api/records/<date_str>', methods=['DELETE'])
@login_required
def delete_record(date_str):
    user_id = session['user_id']
    conn = get_db()
    cur = conn.cursor()

    if IS_POSTGRES:
        cur.execute(
            'DELETE FROM attendance_records WHERE user_id = %s AND date = %s',
            (user_id, date_str)
        )
    else:
        cur.execute(
            'DELETE FROM attendance_records WHERE user_id = ? AND date = ?',
            (user_id, date_str)
        )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()

    if deleted:
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '未找到该日期的记录'})


# ---------- 数据接口 ----------
def _query_records(user_id, start_date, end_date):
    """查询指定日期范围内的打卡记录"""
    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = %s AND date >= %s AND date <= %s ORDER BY date',
            (user_id, start_date, end_date)
        ).fetchall()
    else:
        rows = cur.execute(
            'SELECT * FROM attendance_records WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date',
            (user_id, start_date, end_date)
        ).fetchall()
    conn.close()
    return rows


@app.route('/api/hours/daily')
@login_required
def daily_hours():
    user_id = session['user_id']
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

    records = _query_records(user_id, monday.isoformat(), sunday.isoformat())
    now_str = get_cur_time_str()

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        day_data = {
            'date': d.isoformat(),
            'label': f'{d.month}/{d.day} {weekday_names[i]}',
            'hours': 0, 'check_in': None, 'check_out': None, 'summary': None
        }
        for r in records:
            r_date = str(r['date'])[:10] if IS_POSTGRES else r['date']
            if r_date == d.isoformat():
                end = str(r['check_out'])[:8] if r['check_out'] else (now_str if d == today else None)
                hours = calc_hours(str(r['check_in'])[:8], end) if end else 0
                day_data.update({
                    'hours': hours,
                    'check_in': str(r['check_in'])[:5],
                    'check_out': str(r['check_out'])[:5] if r['check_out'] else None,
                    'summary': r['summary'],
                })
        days.append(day_data)
    return jsonify(days)


@app.route('/api/hours/weekly')
@login_required
def weekly_hours():
    user_id = session['user_id']
    today = date.today()
    first_day = today.replace(day=1)
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    records = _query_records(user_id, first_day.isoformat(), last_day.isoformat())

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
    today = date.today()
    year_str = str(today.year)

    conn = get_db()
    cur = conn.cursor()
    if IS_POSTGRES:
        rows = cur.execute(
            "SELECT * FROM attendance_records WHERE user_id = %s AND date >= %s AND date <= %s ORDER BY date",
            (user_id, f'{year_str}-01-01', f'{year_str}-12-31')
        ).fetchall()
    else:
        rows = cur.execute(
            "SELECT * FROM attendance_records WHERE user_id = ? AND date LIKE ? ORDER BY date",
            (user_id, f'{year_str}-%')
        ).fetchall()
    conn.close()

    months = []
    for m in range(1, 13):
        prefix = f'{year_str}-{m:02d}'
        month_hours = 0
        days_worked = 0
        for r in rows:
            r_date_str = str(r['date'])[:10] if IS_POSTGRES else r['date']
            if r_date_str.startswith(prefix) and r['check_out']:
                month_hours += calc_hours(str(r['check_in'])[:8], str(r['check_out'])[:8])
                days_worked += 1
        months.append({
            'label': f'{m}月',
            'hours': round(month_hours, 2),
            'days': days_worked,
        })
    return jsonify(months)


if __name__ == '__main__':
    mode = 'PRODUCTION' if IS_POSTGRES else 'DEV (SQLite)'
    print(f'=== 打卡系统 [{mode}] ===')
    print(f'    http://127.0.0.1:5000')
    app.run(host='127.0.0.1', debug=not IS_POSTGRES, port=5000)
