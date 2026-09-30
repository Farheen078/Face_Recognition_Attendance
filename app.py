import os
import base64
import sqlite3
import datetime
import time
import shutil
import pickle
import cv2
import numpy as np
import pandas as pd
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.security import check_password_hash
from config import Config
from core.database import get_db, init_db, create_default_admin
from core.face_recognition import (
    train_model, is_model_trained, get_dataset_stats,
    detect_face_and_eyes
)
from core.blink_detection import check_blink_pattern
from core.voice_verification import (
    extract_mfcc_from_bytes, save_voice_feature, verify_voice, is_voice_enrolled,
    load_voice_features
)

app = Flask(__name__)
app.config.from_object(Config)

# Blink tracking per student
BLINK_TRACKER = {}
BLINK_WINDOW_SECONDS = 8
MIN_FRAMES_FOR_BLINK = 5

# Grace period for late marking (minutes after class start)
GRACE_PERIOD_MINUTES = 10


# ==================== TIMETABLE HELPERS ====================

def get_current_class():
    """Auto-detect current class based on timetable and current time"""
    now = datetime.datetime.now()
    day_name = now.strftime('%A')
    current_time = now.strftime('%H:%M')
    
    conn = get_db()
    row = conn.execute('''
        SELECT t.id, t.subject_id, t.start_time, t.end_time,
               s.subject_code, s.subject_name, s.teacher_name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.id
        WHERE t.day = ? AND t.start_time <= ? AND t.end_time >= ?
        LIMIT 1
    ''', (day_name, current_time, current_time)).fetchone()
    conn.close()
    return row


def get_attendance_status_for_now(start_time):
    """Determine if student should be marked present or late"""
    now = datetime.datetime.now()
    try:
        start_h, start_m = map(int, start_time.split(':'))
    except:
        return 'present'
    
    current_minutes = now.hour * 60 + now.minute
    start_minutes = start_h * 60 + start_m
    diff = current_minutes - start_minutes
    
    if diff <= GRACE_PERIOD_MINUTES:
        return 'present'
    else:
        return 'late'


def auto_close_past_sessions():
    """Auto-mark absent for past classes today"""
    now = datetime.datetime.now()
    today = now.date().isoformat()
    day_name = now.strftime('%A')
    current_time = now.strftime('%H:%M')
    
    conn = get_db()
    
    past_classes = conn.execute('''
        SELECT subject_id, start_time, end_time
        FROM timetable
        WHERE day = ? AND end_time < ?
    ''', (day_name, current_time)).fetchall()
    
    for cls in past_classes:
        closed = conn.execute('''
            SELECT COUNT(*) FROM attendance
            WHERE subject_id = ? AND date = ? AND status = 'absent'
        ''', (cls['subject_id'], today)).fetchone()[0]
        
        if closed > 0:
            continue
        
        students = conn.execute("SELECT id FROM students").fetchall()
        
        attended = conn.execute('''
            SELECT student_id FROM attendance
            WHERE subject_id = ? AND date = ?
        ''', (cls['subject_id'], today)).fetchall()
        attended_ids = {r['student_id'] for r in attended}
        
        for s in students:
            if s['id'] not in attended_ids:
                conn.execute('''
                    INSERT OR IGNORE INTO attendance
                    (student_id, subject_id, date, time, method, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (s['id'], cls['subject_id'], today, cls['end_time'], 'auto', 'absent'))
        
        conn.commit()
    
    conn.close()


@app.before_request
def make_session_permanent():
    session.permanent = True


@app.before_request
def run_auto_close():
    try:
        auto_close_past_sessions()
    except Exception as e:
        print(f"Auto-close error: {e}")


# ==================== BASIC ROUTES ====================

@app.route('/')
def index():
    return render_template('login.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db()
        admin = conn.execute(
            "SELECT * FROM admin WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        
        if admin and check_password_hash(admin['password_hash'], password):
            session['admin_id'] = admin['id']
            session['admin_username'] = admin['username']
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ==================== ADMIN DASHBOARD ====================

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student_count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    subject_count = conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
    today = datetime.date.today().isoformat()
    today_attendance = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date = ? AND status != 'absent'", (today,)
    ).fetchone()[0]
    conn.close()
    
    current_class = get_current_class()
    
    now = datetime.datetime.now()
    day_name = now.strftime('%A')
    current_time = now.strftime('%H:%M')
    
    conn = get_db()
    today_schedule = conn.execute('''
        SELECT t.start_time, t.end_time, s.subject_code, s.subject_name, s.teacher_name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.id
        WHERE t.day = ?
        ORDER BY t.start_time
    ''', (day_name,)).fetchall()
    conn.close()
    
    schedule_list = []
    for s in today_schedule:
        if s['end_time'] < current_time:
            status = 'done'
        elif s['start_time'] <= current_time <= s['end_time']:
            status = 'active'
        else:
            status = 'upcoming'
        schedule_list.append({
            'start_time': s['start_time'],
            'end_time': s['end_time'],
            'subject_code': s['subject_code'],
            'subject_name': s['subject_name'],
            'teacher_name': s['teacher_name'],
            'status': status
        })
    
    return render_template('admin/dashboard.html',
                          student_count=student_count,
                          subject_count=subject_count,
                          today_attendance=today_attendance,
                          current_class=current_class,
                          today_schedule=schedule_list,
                          now_time=current_time,
                          day_name=day_name)


@app.route('/admin/dashboard/chart_data')
def dashboard_chart_data():
    if 'admin_id' not in session:
        return jsonify({'success': False})
    
    conn = get_db()
    today = datetime.date.today()
    labels, values = [], []
    
    for i in range(6, -1, -1):
        d = today - datetime.timedelta(days=i)
        count = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE date = ? AND status != 'absent'",
            (d.isoformat(),)
        ).fetchone()[0]
        labels.append(d.strftime('%a'))
        values.append(count)
    
    subject_data = conn.execute('''
        SELECT sub.subject_code, COUNT(a.id) as count
        FROM subjects sub LEFT JOIN attendance a ON sub.id = a.subject_id AND a.status != 'absent'
        GROUP BY sub.id ORDER BY sub.subject_code
    ''').fetchall()
    
    month_start = today.replace(day=1).isoformat()
    top_students = conn.execute('''
        SELECT s.name, COUNT(a.id) as count
        FROM students s JOIN attendance a ON s.id = a.student_id
        WHERE a.date >= ? AND a.status != 'absent' GROUP BY s.id ORDER BY count DESC LIMIT 5
    ''', (month_start,)).fetchall()
    
    conn.close()
    
    return jsonify({
        'success': True,
        'trend': {'labels': labels, 'values': values},
        'subjects': {'labels': [s['subject_code'] for s in subject_data], 'values': [s['count'] for s in subject_data]},
        'top_students': {'labels': [s['name'] for s in top_students], 'values': [s['count'] for s in top_students]}
    })


@app.route('/admin/current_class')
def current_class_api():
    if 'admin_id' not in session:
        return jsonify({'success': False})
    
    cls = get_current_class()
    if not cls:
        return jsonify({'success': True, 'active': False})
    
    return jsonify({
        'success': True,
        'active': True,
        'subject_code': cls['subject_code'],
        'subject_name': cls['subject_name'],
        'teacher_name': cls['teacher_name'],
        'start_time': cls['start_time'],
        'end_time': cls['end_time'],
        'subject_id': cls['subject_id']
    })


# ==================== LIVE MONITOR ====================

@app.route('/admin/monitor/<int:subject_id>')
def live_monitor(subject_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subject = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    conn.close()
    
    if not subject:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/live_monitor.html', subject=subject)


# ==================== STUDENT KIOSK (PUBLIC) ====================

@app.route('/student')
def student_portal():
    current = get_current_class()
    
    if current:
        today = datetime.date.today().isoformat()
        conn = get_db()
        count = conn.execute('''
            SELECT COUNT(*) FROM attendance
            WHERE subject_id = ? AND date = ? AND status != 'absent'
        ''', (current['subject_id'], today)).fetchone()[0]
        conn.close()
    else:
        count = 0
    
    return render_template('student/portal.html',
                          subject=current,
                          active=current is not None,
                          count=count)


@app.route('/student/mark', methods=['POST'])
def student_mark():
    current = get_current_class()
    
    if not current:
        return jsonify({'success': False, 'message': 'No active class right now', 'status': 'no_class'})
    
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({'success': False, 'message': 'No image data'})
    
    image_data = data['image'].split(',')[1]
    img_bytes = base64.b64decode(image_data)
    img_array = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    
    if img is None:
        return jsonify({'success': False, 'message': 'Invalid image'})
    
    result = detect_face_and_eyes(img)
    
    if not result['face_found']:
        return jsonify({'success': False, 'message': 'No face detected', 'status': 'no_face'})
    
    if result['student_id'] is None or result['confidence'] > 75:
        return jsonify({'success': False, 'message': 'Face not recognized', 'status': 'unknown'})
    
    student_id = result['student_id']
    confidence = result['confidence']
    eye_count = result['eye_count']
    
    now = time.time()
    if student_id not in BLINK_TRACKER:
        BLINK_TRACKER[student_id] = []
    
    BLINK_TRACKER[student_id].append((now, eye_count))
    BLINK_TRACKER[student_id] = [(t, c) for (t, c) in BLINK_TRACKER[student_id] if now - t <= BLINK_WINDOW_SECONDS]
    
    states = [c for (t, c) in BLINK_TRACKER[student_id]]
    
    if len(states) < MIN_FRAMES_FOR_BLINK:
        return jsonify({'success': False, 'message': f'Verifying liveness... ({len(states)}/5)', 'status': 'collecting'})
    
    if not check_blink_pattern(states):
        return jsonify({'success': False, 'message': 'Please blink your eyes', 'status': 'blink_required'})
    
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    
    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found', 'status': 'error'})
    
    today = datetime.date.today().isoformat()
    now_time = datetime.datetime.now().strftime('%H:%M:%S')
    
    existing = conn.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND subject_id = ? AND date = ?",
        (student_id, current['subject_id'], today)
    ).fetchone()
    
    if existing:
        conn.close()
        BLINK_TRACKER.pop(student_id, None)
        return jsonify({
            'success': False,
            'message': f'Already marked for {student["name"]}',
            'student_name': student['name'],
            'status': 'already_marked',
            'attendance_status': existing['status']
        })
    
    attendance_status = get_attendance_status_for_now(current['start_time'])
    
    conn.execute(
        """INSERT INTO attendance (student_id, subject_id, date, time, method, face_confidence, liveness_passed, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (student_id, current['subject_id'], today, now_time, 'face', confidence, 1, attendance_status)
    )
    conn.commit()
    conn.close()
    
    BLINK_TRACKER.pop(student_id, None)
    
    status_text = 'Present' if attendance_status == 'present' else 'Late'
    
    return jsonify({
        'success': True,
        'message': f'✅ {student["name"]} — {status_text}',
        'student_name': student['name'],
        'roll_no': student['roll_no'],
        'confidence': round(100 - confidence, 2),
        'status': 'marked',
        'attendance_status': attendance_status
    })


@app.route('/student/voice_mark', methods=['POST'])
def student_voice_mark():
    current = get_current_class()
    
    if not current:
        return jsonify({'success': False, 'message': 'No active class'})
    
    if 'audio' not in request.files:
        return jsonify({'success': False, 'message': 'No audio'})
    
    audio_bytes = request.files['audio'].read()
    student_id, similarity, status = verify_voice(audio_bytes)
    
    if student_id is None:
        return jsonify({
            'success': False,
            'message': f'Voice not recognized',
            'similarity': round(similarity * 100, 2)
        })
    
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    
    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found'})
    
    today = datetime.date.today().isoformat()
    now_time = datetime.datetime.now().strftime('%H:%M:%S')
    
    existing = conn.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND subject_id = ? AND date = ?",
        (student_id, current['subject_id'], today)
    ).fetchone()
    
    if existing:
        conn.close()
        return jsonify({
            'success': False,
            'message': f'Already marked for {student["name"]}',
            'student_name': student['name'],
            'status': 'already_marked'
        })
    
    attendance_status = get_attendance_status_for_now(current['start_time'])
    
    conn.execute(
        """INSERT INTO attendance (student_id, subject_id, date, time, method, voice_similarity, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (student_id, current['subject_id'], today, now_time, 'voice', similarity, attendance_status)
    )
    conn.commit()
    conn.close()
    
    status_text = 'Present' if attendance_status == 'present' else 'Late'
    
    return jsonify({
        'success': True,
        'message': f'✅ {student["name"]} — {status_text} (Voice)',
        'student_name': student['name'],
        'roll_no': student['roll_no'],
        'similarity': round(similarity * 100, 2),
        'status': 'marked',
        'attendance_status': attendance_status
    })


@app.route('/admin/attendance/today/<int:subject_id>')
def today_attendance_list(subject_id):
    today = datetime.date.today().isoformat()
    conn = get_db()
    rows = conn.execute('''
        SELECT a.time, a.status, a.face_confidence, a.method, a.liveness_passed, s.name, s.roll_no
        FROM attendance a JOIN students s ON a.student_id = s.id
        WHERE a.subject_id = ? AND a.date = ? ORDER BY a.time DESC
    ''', (subject_id, today)).fetchall()
    conn.close()
    
    result = [{'time': r['time'], 'name': r['name'], 'roll_no': r['roll_no'],
               'method': r['method'], 'status': r['status'],
               'confidence': round(100 - r['face_confidence'], 2) if r['face_confidence'] else 0,
               'liveness': bool(r['liveness_passed'])} for r in rows]
    
    return jsonify({'success': True, 'attendance': result})


# ==================== STUDENT REGISTRATION ====================

@app.route('/admin/register_student', methods=['GET', 'POST'])
def register_student():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        name = request.form['name']
        roll_no = request.form['roll_no']
        course = request.form['course']
        semester = request.form['semester']
        
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO students (name, roll_no, course, semester) VALUES (?, ?, ?, ?)",
                          (name, roll_no, course, semester))
            student_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return redirect(url_for('capture_face', student_id=student_id))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('admin/register_student.html', error='Roll number already exists')
    
    return render_template('admin/register_student.html')


# ==================== MANAGE STUDENTS ====================

@app.route('/admin/students')
def manage_students():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    students = conn.execute('''
        SELECT s.*, 
               (SELECT COUNT(*) FROM attendance WHERE student_id = s.id AND status != 'absent') as present_count
        FROM students s
        ORDER BY s.roll_no
    ''').fetchall()
    conn.close()
    
    return render_template('admin/students.html', students=students)


@app.route('/admin/students/delete/<int:student_id>')
def delete_student(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    conn.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
    conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()
    
    student_folder = os.path.join('dataset', 'faces', str(student_id))
    if os.path.exists(student_folder):
        shutil.rmtree(student_folder)
    
    try:
        features_path = 'trainer/voice_features.pkl'
        if os.path.exists(features_path):
            with open(features_path, 'rb') as f:
                data = pickle.load(f)
            
            if str(student_id) in data:
                del data[str(student_id)]
                with open(features_path, 'wb') as f:
                    pickle.dump(data, f)
    except Exception as e:
        print(f"Error removing voice features: {e}")
    
    return redirect(url_for('manage_students'))


# ==================== FACE CAPTURE ====================

@app.route('/admin/capture_face/<int:student_id>')
def capture_face(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    
    if not student:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/capture_face.html', student=student)


@app.route('/admin/capture_face', methods=['POST'])
def save_face_image():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    data = request.get_json()
    student_id = data['student_id']
    image_data = data['image'].split(',')[1]
    
    student_folder = os.path.join('dataset', 'faces', str(student_id))
    os.makedirs(student_folder, exist_ok=True)
    
    existing = len([f for f in os.listdir(student_folder) if f.endswith('.jpg')])
    filepath = os.path.join(student_folder, f"{existing + 1}.jpg")
    
    img_bytes = base64.b64decode(image_data)
    with open(filepath, 'wb') as f:
        f.write(img_bytes)
    
    return jsonify({'success': True, 'count': existing + 1})


@app.route('/admin/enroll_voice/<int:student_id>')
def enroll_voice(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    
    if not student:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/enroll_voice.html',
                          student=student,
                          already_enrolled=is_voice_enrolled(student_id))


@app.route('/admin/enroll_voice/save', methods=['POST'])
def save_voice_sample():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    student_id = request.form.get('student_id')
    if not student_id or 'audio' not in request.files:
        return jsonify({'success': False, 'error': 'No audio data'})
    
    audio_bytes = request.files['audio'].read()
    features = extract_mfcc_from_bytes(audio_bytes)
    
    if features is None:
        return jsonify({'success': False, 'error': 'Could not extract features'})
    
    if save_voice_feature(int(student_id), features):
        conn = get_db()
        conn.execute("UPDATE students SET voice_enrolled = 1 WHERE id = ?", (student_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Voice enrolled'})
    
    return jsonify({'success': False, 'error': 'Failed to save'})


@app.route('/admin/train_model', methods=['GET', 'POST'])
def train_model_route():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    message = None
    message_type = None
    
    if request.method == 'POST':
        success, msg, total_images, total_students = train_model()
        message = msg
        message_type = 'success' if success else 'error'
    
    return render_template('admin/train_model.html',
                          stats=get_dataset_stats(),
                          model_exists=is_model_trained(),
                          message=message,
                          message_type=message_type)


@app.route('/admin/subjects')
def manage_subjects():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY subject_code").fetchall()
    conn.close()
    return render_template('admin/subjects.html', subjects=subjects)


@app.route('/admin/subjects/add', methods=['POST'])
def add_subject():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    try:
        conn.execute("INSERT INTO subjects (subject_code, subject_name, teacher_name, semester) VALUES (?, ?, ?, ?)",
                    (request.form['subject_code'], request.form['subject_name'],
                     request.form['teacher_name'], request.form['semester']))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()
    return redirect(url_for('manage_subjects'))


@app.route('/admin/subjects/delete/<int:subject_id>')
def delete_subject(subject_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    conn.execute("DELETE FROM timetable WHERE subject_id = ?", (subject_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('manage_subjects'))


@app.route('/admin/timetable')
def manage_timetable():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY subject_code").fetchall()
    timetable = conn.execute('''
        SELECT t.id, t.day, t.start_time, t.end_time, s.subject_code, s.subject_name, s.teacher_name
        FROM timetable t JOIN subjects s ON t.subject_id = s.id
        ORDER BY CASE t.day WHEN 'Sunday' THEN 1 WHEN 'Monday' THEN 2 WHEN 'Tuesday' THEN 3
            WHEN 'Wednesday' THEN 4 WHEN 'Thursday' THEN 5 WHEN 'Friday' THEN 6 WHEN 'Saturday' THEN 7 END, t.start_time
    ''').fetchall()
    conn.close()
    
    return render_template('admin/timetable.html', subjects=subjects, timetable=timetable)


@app.route('/admin/timetable/add', methods=['POST'])
def add_timetable():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    conn.execute("INSERT INTO timetable (subject_id, day, start_time, end_time) VALUES (?, ?, ?, ?)",
                (request.form['subject_id'], request.form['day'],
                 request.form['start_time'], request.form['end_time']))
    conn.commit()
    conn.close()
    return redirect(url_for('manage_timetable'))


@app.route('/admin/timetable/delete/<int:entry_id>')
def delete_timetable(entry_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    conn.execute("DELETE FROM timetable WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('manage_timetable'))


# ==================== REPORTS ====================

@app.route('/admin/reports')
def reports():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY subject_code").fetchall()
    students = conn.execute("SELECT * FROM students ORDER BY roll_no").fetchall()
    conn.close()
    
    return render_template('admin/reports.html', subjects=subjects, students=students,
                          today=datetime.date.today().isoformat())


@app.route('/admin/reports/daily')
def daily_report():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    date = request.args.get('date', datetime.date.today().isoformat())
    subject_id = request.args.get('subject_id', '')
    
    conn = get_db()
    query = '''SELECT a.date, a.time, s.name, s.roll_no, sub.subject_code, sub.subject_name,
                      a.method, a.liveness_passed, a.status
               FROM attendance a JOIN students s ON a.student_id = s.id
               JOIN subjects sub ON a.subject_id = sub.id WHERE a.date = ?'''
    params = [date]
    
    if subject_id:
        query += " AND a.subject_id = ?"
        params.append(subject_id)
    
    query += " ORDER BY a.time DESC"
    rows = conn.execute(query, params).fetchall()
    
    total_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    present_count = sum(1 for r in rows if r['status'] == 'present')
    late_count = sum(1 for r in rows if r['status'] == 'late')
    absent_count = sum(1 for r in rows if r['status'] == 'absent')
    
    conn.close()
    
    return render_template('admin/daily_report.html', rows=rows, date=date, subject_id=subject_id,
                          total_students=total_students, present_count=present_count,
                          late_count=late_count, absent_count=absent_count)


@app.route('/admin/reports/defaulter')
def defaulter_report():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    threshold = float(request.args.get('threshold', 75))
    conn = get_db()
    
    students_data = conn.execute('''
        SELECT s.id, s.name, s.roll_no, s.course,
               COUNT(CASE WHEN a.status != 'absent' THEN 1 END) as present_count
        FROM students s LEFT JOIN attendance a ON s.id = a.student_id
        GROUP BY s.id
    ''').fetchall()
    
    total_classes = conn.execute(
        "SELECT COUNT(DISTINCT date || '-' || subject_id) FROM attendance"
    ).fetchone()[0] or 1
    
    defaulters = [{'id': s['id'], 'name': s['name'], 'roll_no': s['roll_no'], 'course': s['course'],
                   'present_count': s['present_count'], 'total_classes': total_classes,
                   'percentage': round((s['present_count'] / total_classes) * 100, 2)}
                  for s in students_data if (s['present_count'] / total_classes) * 100 < threshold]
    conn.close()
    
    return render_template('admin/defaulter_report.html', defaulters=defaulters,
                          threshold=threshold, total_classes=total_classes)


@app.route('/admin/reports/subject/<int:subject_id>')
def subject_report(subject_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subject = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    
    if not subject:
        conn.close()
        return redirect(url_for('reports'))
    
    student_data = conn.execute('''
        SELECT s.id, s.name, s.roll_no,
               COUNT(CASE WHEN a.status != 'absent' THEN 1 END) as present_count
        FROM students s LEFT JOIN attendance a ON s.id = a.student_id AND a.subject_id = ?
        GROUP BY s.id ORDER BY s.roll_no
    ''', (subject_id,)).fetchall()
    
    total_classes = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM attendance WHERE subject_id = ?",
        (subject_id,)
    ).fetchone()[0] or 1
    conn.close()
    
    report_data = [{'name': s['name'], 'roll_no': s['roll_no'], 'present_count': s['present_count'],
                    'total_classes': total_classes,
                    'percentage': round((s['present_count'] / total_classes) * 100, 2),
                    'status': 'Good' if (s['present_count'] / total_classes) * 100 >= 75 else 'Low'}
                   for s in student_data]
    
    return render_template('admin/subject_report.html', subject=subject,
                          report_data=report_data, total_classes=total_classes)


@app.route('/admin/reports/student/<int:student_id>')
def student_report(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    
    if not student:
        conn.close()
        return redirect(url_for('reports'))
    
    subject_data = conn.execute('''
        SELECT sub.id as subject_id, sub.subject_code, sub.subject_name,
               COUNT(CASE WHEN a.status != 'absent' THEN 1 END) as present_count
        FROM subjects sub LEFT JOIN attendance a ON sub.id = a.subject_id AND a.student_id = ?
        GROUP BY sub.id ORDER BY sub.subject_code
    ''', (student_id,)).fetchall()
    
    subject_breakdown = []
    total_present = 0
    total_classes = 0
    
    for sub in subject_data:
        total_subj = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM attendance WHERE subject_id = ?",
            (sub['subject_id'],)
        ).fetchone()[0] or 1
        pct = (sub['present_count'] / total_subj) * 100
        total_present += sub['present_count']
        total_classes += total_subj
        subject_breakdown.append({
            'subject_code': sub['subject_code'], 'subject_name': sub['subject_name'],
            'present': sub['present_count'], 'total': total_subj,
            'percentage': round(pct, 2),
            'status': 'Good' if pct >= 75 else 'Low'
        })
    
    overall = round((total_present / total_classes) * 100, 2) if total_classes > 0 else 0
    
    recent = conn.execute('''
        SELECT a.date, a.time, a.method, a.status, sub.subject_code, sub.subject_name
        FROM attendance a JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.student_id = ? ORDER BY a.date DESC, a.time DESC LIMIT 20
    ''', (student_id,)).fetchall()
    conn.close()
    
    return render_template('admin/student_report.html', student=student,
                          subject_breakdown=subject_breakdown, recent_records=recent,
                          overall_percentage=overall,
                          total_present=total_present, total_classes=total_classes)


# ==================== EXCEL EXPORTS ====================

@app.route('/admin/reports/export/daily')
def export_daily():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    date = request.args.get('date', datetime.date.today().isoformat())
    subject_id = request.args.get('subject_id', '')
    
    conn = get_db()
    query = '''SELECT a.date, a.time, s.name, s.roll_no, sub.subject_code, sub.subject_name,
                      a.method, a.liveness_passed, a.status
               FROM attendance a JOIN students s ON a.student_id = s.id
               JOIN subjects sub ON a.subject_id = sub.id WHERE a.date = ?'''
    params = [date]
    if subject_id:
        query += " AND a.subject_id = ?"
        params.append(subject_id)
    query += " ORDER BY a.time DESC"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    data = [{'Date': r['date'], 'Time': r['time'], 'Name': r['name'], 'Roll No': r['roll_no'],
             'Subject Code': r['subject_code'], 'Subject': r['subject_name'],
             'Method': r['method'], 'Status': r['status'].capitalize(),
             'Liveness': 'Passed' if r['liveness_passed'] else 'N/A'} for r in rows]
    
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Daily Report', index=False)
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name=f"daily_report_{date}.xlsx")


@app.route('/admin/reports/export/defaulter')
def export_defaulter():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    threshold = float(request.args.get('threshold', 75))
    conn = get_db()
    
    students_data = conn.execute('''
        SELECT s.id, s.name, s.roll_no, s.course,
               COUNT(CASE WHEN a.status != 'absent' THEN 1 END) as present_count
        FROM students s LEFT JOIN attendance a ON s.id = a.student_id GROUP BY s.id
    ''').fetchall()
    
    total_classes = conn.execute("SELECT COUNT(DISTINCT date || '-' || subject_id) FROM attendance").fetchone()[0] or 1
    conn.close()
    
    defaulters = [{'Name': s['name'], 'Roll No': s['roll_no'], 'Course': s['course'],
                   'Present': s['present_count'], 'Total Classes': total_classes,
                   'Percentage': round((s['present_count'] / total_classes) * 100, 2)}
                  for s in students_data if (s['present_count'] / total_classes) * 100 < threshold]
    
    df = pd.DataFrame(defaulters)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Defaulter List', index=False)
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name=f"defaulter_list.xlsx")


@app.route('/admin/reports/export/subject/<int:subject_id>')
def export_subject(subject_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subject = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    student_data = conn.execute('''
        SELECT s.id, s.name, s.roll_no,
               COUNT(CASE WHEN a.status != 'absent' THEN 1 END) as present_count
        FROM students s LEFT JOIN attendance a ON s.id = a.student_id AND a.subject_id = ?
        GROUP BY s.id ORDER BY s.roll_no
    ''', (subject_id,)).fetchall()
    total_classes = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM attendance WHERE subject_id = ?", (subject_id,)
    ).fetchone()[0] or 1
    conn.close()
    
    data = [{'Name': s['name'], 'Roll No': s['roll_no'], 'Present': s['present_count'],
             'Total Classes': total_classes,
             'Percentage': round((s['present_count'] / total_classes) * 100, 2),
             'Status': 'Good' if (s['present_count'] / total_classes) * 100 >= 75 else 'Low'}
            for s in student_data]
    
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Subject Report', index=False)
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name=f"subject_{subject['subject_code']}.xlsx")


@app.route('/admin/reports/export/student/<int:student_id>')
def export_student(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    
    if not student:
        conn.close()
        return redirect(url_for('reports'))
    
    records = conn.execute('''
        SELECT a.date, a.time, a.method, a.status, sub.subject_code, sub.subject_name,
               a.face_confidence, a.voice_similarity, a.liveness_passed
        FROM attendance a JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.student_id = ? ORDER BY a.date DESC, a.time DESC
    ''', (student_id,)).fetchall()
    conn.close()
    
    data = [{'Date': r['date'], 'Time': r['time'], 'Subject Code': r['subject_code'],
             'Subject': r['subject_name'], 'Method': r['method'],
             'Status': r['status'].capitalize(),
             'Face Confidence': round(100 - r['face_confidence'], 2) if r['face_confidence'] else 'N/A',
             'Voice Similarity': round(r['voice_similarity'] * 100, 2) if r['voice_similarity'] else 'N/A',
             'Liveness': 'Passed' if r['liveness_passed'] else 'N/A'} for r in records]
    
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Student Report', index=False)
    output.seek(0)
    
    filename = f"student_{student['roll_no']}_{student['name'].replace(' ', '_')}.xlsx"
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name=filename)


# ==================== MAIN ====================

if __name__ == '__main__':
    init_db()
    create_default_admin()
    print("=" * 60)
    print("  Smart Attendance System — Auto Session from Timetable")
    print("=" * 60)
    print("  Teacher Panel : http://127.0.0.1:5000")
    print("  Student Kiosk : http://127.0.0.1:5000/student")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)