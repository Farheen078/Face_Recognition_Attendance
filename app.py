import os
import base64
import sqlite3
import datetime
import time
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
    detect_face_and_eyes, get_current_subject
)
from core.blink_detection import check_blink_pattern
from core.voice_verification import (
    extract_mfcc_from_bytes, save_voice_feature, verify_voice, is_voice_enrolled
)

app = Flask(__name__)
app.config.from_object(Config)

BLINK_TRACKER = {}
BLINK_WINDOW_SECONDS = 8
MIN_FRAMES_FOR_BLINK = 5


@app.before_request
def make_session_permanent():
    session.permanent = True


# ==================== BASIC ROUTES ====================

@app.route('/')
def index():
    """Hamesha login page dikhaye"""
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
    BLINK_TRACKER.clear()
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
        "SELECT COUNT(*) FROM attendance WHERE date = ?", (today,)
    ).fetchone()[0]
    conn.close()
    
    return render_template('admin/dashboard.html',
                          student_count=student_count,
                          subject_count=subject_count,
                          today_attendance=today_attendance)


@app.route('/admin/dashboard/chart_data')
def dashboard_chart_data():
    if 'admin_id' not in session:
        return jsonify({'success': False})
    
    conn = get_db()
    
    today = datetime.date.today()
    labels = []
    values = []
    
    for i in range(6, -1, -1):
        d = today - datetime.timedelta(days=i)
        date_str = d.isoformat()
        count = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE date = ?", (date_str,)
        ).fetchone()[0]
        labels.append(d.strftime('%a'))
        values.append(count)
    
    subject_data = conn.execute('''
        SELECT sub.subject_code, COUNT(a.id) as count
        FROM subjects sub
        LEFT JOIN attendance a ON sub.id = a.subject_id
        GROUP BY sub.id
        ORDER BY sub.subject_code
    ''').fetchall()
    
    subject_labels = [s['subject_code'] for s in subject_data]
    subject_values = [s['count'] for s in subject_data]
    
    month_start = today.replace(day=1).isoformat()
    top_students = conn.execute('''
        SELECT s.name, COUNT(a.id) as count
        FROM students s
        JOIN attendance a ON s.id = a.student_id
        WHERE a.date >= ?
        GROUP BY s.id
        ORDER BY count DESC
        LIMIT 5
    ''', (month_start,)).fetchall()
    
    top_labels = [s['name'] for s in top_students]
    top_values = [s['count'] for s in top_students]
    
    conn.close()
    
    return jsonify({
        'success': True,
        'trend': {'labels': labels, 'values': values},
        'subjects': {'labels': subject_labels, 'values': subject_values},
        'top_students': {'labels': top_labels, 'values': top_values}
    })


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
            cursor.execute(
                "INSERT INTO students (name, roll_no, course, semester) VALUES (?, ?, ?, ?)",
                (name, roll_no, course, semester)
            )
            student_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return redirect(url_for('capture_face', student_id=student_id))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('admin/register_student.html',
                                 error='Roll number already exists')
    
    return render_template('admin/register_student.html')


# ==================== FACE CAPTURE ====================

@app.route('/admin/capture_face/<int:student_id>')
def capture_face(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student = conn.execute(
        "SELECT * FROM students WHERE id = ?", (student_id,)
    ).fetchone()
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
    filename = f"{existing + 1}.jpg"
    filepath = os.path.join(student_folder, filename)
    
    img_bytes = base64.b64decode(image_data)
    with open(filepath, 'wb') as f:
        f.write(img_bytes)
    
    return jsonify({'success': True, 'count': existing + 1})


# ==================== VOICE ENROLLMENT ====================

@app.route('/admin/enroll_voice/<int:student_id>')
def enroll_voice(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    student = conn.execute(
        "SELECT * FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    conn.close()
    
    if not student:
        return redirect(url_for('admin_dashboard'))
    
    already_enrolled = is_voice_enrolled(student_id)
    
    return render_template('admin/enroll_voice.html', 
                          student=student,
                          already_enrolled=already_enrolled)


@app.route('/admin/enroll_voice/save', methods=['POST'])
def save_voice_sample():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    student_id = request.form.get('student_id')
    
    if not student_id or 'audio' not in request.files:
        return jsonify({'success': False, 'error': 'No audio data'})
    
    audio_file = request.files['audio']
    audio_bytes = audio_file.read()
    
    features = extract_mfcc_from_bytes(audio_bytes)
    
    if features is None:
        return jsonify({'success': False, 'error': 'Could not extract voice features. Speak louder.'})
    
    success = save_voice_feature(int(student_id), features)
    
    if success:
        conn = get_db()
        conn.execute(
            "UPDATE students SET voice_enrolled = 1 WHERE id = ?",
            (student_id,)
        )
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Voice enrolled successfully'})
    
    return jsonify({'success': False, 'error': 'Failed to save'})


# ==================== TRAIN MODEL ====================

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
    
    stats = get_dataset_stats()
    model_exists = is_model_trained()
    
    return render_template('admin/train_model.html',
                          stats=stats,
                          model_exists=model_exists,
                          message=message,
                          message_type=message_type)


# ==================== SUBJECTS ====================

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
    
    subject_code = request.form['subject_code']
    subject_name = request.form['subject_name']
    teacher_name = request.form['teacher_name']
    semester = request.form['semester']
    
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO subjects (subject_code, subject_name, teacher_name, semester) VALUES (?, ?, ?, ?)",
            (subject_code, subject_name, teacher_name, semester)
        )
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
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


# ==================== TIMETABLE ====================

@app.route('/admin/timetable')
def manage_timetable():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY subject_code").fetchall()
    timetable = conn.execute('''
        SELECT t.id, t.day, t.start_time, t.end_time, 
               s.subject_code, s.subject_name, s.teacher_name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.id
        ORDER BY 
            CASE t.day
                WHEN 'Sunday' THEN 1
                WHEN 'Monday' THEN 2
                WHEN 'Tuesday' THEN 3
                WHEN 'Wednesday' THEN 4
                WHEN 'Thursday' THEN 5
                WHEN 'Friday' THEN 6
                WHEN 'Saturday' THEN 7
            END,
            t.start_time
    ''').fetchall()
    conn.close()
    
    return render_template('admin/timetable.html',
                          subjects=subjects,
                          timetable=timetable)


@app.route('/admin/timetable/add', methods=['POST'])
def add_timetable():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    subject_id = request.form['subject_id']
    day = request.form['day']
    start_time = request.form['start_time']
    end_time = request.form['end_time']
    
    conn = get_db()
    conn.execute(
        "INSERT INTO timetable (subject_id, day, start_time, end_time) VALUES (?, ?, ?, ?)",
        (subject_id, day, start_time, end_time)
    )
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


# ==================== TAKE ATTENDANCE ====================

@app.route('/admin/take_attendance')
def take_attendance():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    if not is_model_trained():
        return render_template('admin/take_attendance.html',
                             model_trained=False,
                             message='Model not trained yet. Please train the model first.')
    
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY subject_code").fetchall()
    conn.close()
    
    return render_template('admin/take_attendance.html',
                          model_trained=True,
                          subjects=subjects)


@app.route('/admin/attendance/mark', methods=['POST'])
def mark_attendance():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    data = request.get_json()
    image_data = data['image'].split(',')[1]
    subject_id = data.get('subject_id')
    
    if not subject_id:
        return jsonify({'success': False, 'error': 'No subject selected'})
    
    img_bytes = base64.b64decode(image_data)
    img_array = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    
    if img is None:
        return jsonify({'success': False, 'error': 'Invalid image'})
    
    result = detect_face_and_eyes(img)
    
    if not result['face_found']:
        return jsonify({
            'success': False,
            'message': 'No face detected',
            'recognized': False,
            'status': 'no_face'
        })
    
    if result['student_id'] is None or result['confidence'] > 75:
        return jsonify({
            'success': False,
            'message': 'Unknown face (low confidence)',
            'recognized': False,
            'status': 'unknown'
        })
    
    student_id = result['student_id']
    confidence = result['confidence']
    eye_count = result['eye_count']
    
    now = time.time()
    if student_id not in BLINK_TRACKER:
        BLINK_TRACKER[student_id] = []
    
    BLINK_TRACKER[student_id].append((now, eye_count))
    
    BLINK_TRACKER[student_id] = [
        (t, c) for (t, c) in BLINK_TRACKER[student_id]
        if now - t <= BLINK_WINDOW_SECONDS
    ]
    
    recent = BLINK_TRACKER[student_id]
    states = [c for (t, c) in recent]
    
    if len(states) < MIN_FRAMES_FOR_BLINK:
        return jsonify({
            'success': False,
            'message': f'Collecting frames... ({len(states)}/{MIN_FRAMES_FOR_BLINK})',
            'recognized': True,
            'status': 'collecting',
            'frames': len(states)
        })
    
    blink_detected = check_blink_pattern(states)
    
    if not blink_detected:
        return jsonify({
            'success': False,
            'message': 'Please blink your eyes naturally',
            'recognized': True,
            'status': 'blink_required',
            'eye_states': states
        })
    
    conn = get_db()
    student = conn.execute(
        "SELECT * FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    
    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found', 'recognized': False})
    
    today = datetime.date.today().isoformat()
    now_time = datetime.datetime.now().strftime('%H:%M:%S')
    
    existing = conn.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND subject_id = ? AND date = ?",
        (student_id, subject_id, today)
    ).fetchone()
    
    if existing:
        conn.close()
        BLINK_TRACKER.pop(student_id, None)
        return jsonify({
            'success': False,
            'message': f'Already marked for {student["name"]}',
            'recognized': True,
            'student_name': student['name'],
            'already_marked': True,
            'status': 'already_marked'
        })
    
    conn.execute(
        """INSERT INTO attendance 
           (student_id, subject_id, date, time, method, face_confidence, liveness_passed, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (student_id, subject_id, today, now_time, 'face', confidence, 1, 'present')
    )
    conn.commit()
    conn.close()
    
    BLINK_TRACKER.pop(student_id, None)
    
    return jsonify({
        'success': True,
        'message': f'✅ Liveness verified. Attendance marked for {student["name"]}',
        'recognized': True,
        'student_name': student['name'],
        'roll_no': student['roll_no'],
        'confidence': round(100 - confidence, 2),
        'status': 'marked',
        'liveness_passed': True
    })


@app.route('/admin/attendance/voice_mark', methods=['POST'])
def voice_mark_attendance():
    if 'admin_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    subject_id = request.form.get('subject_id')
    
    if not subject_id or 'audio' not in request.files:
        return jsonify({'success': False, 'error': 'No audio or subject'})
    
    audio_file = request.files['audio']
    audio_bytes = audio_file.read()
    
    student_id, similarity, status = verify_voice(audio_bytes)
    
    if student_id is None:
        return jsonify({
            'success': False,
            'message': f'Voice not recognized: {status}',
            'recognized': False,
            'similarity': round(similarity * 100, 2)
        })
    
    conn = get_db()
    student = conn.execute(
        "SELECT * FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    
    if not student:
        conn.close()
        return jsonify({'success': False, 'message': 'Student not found', 'recognized': False})
    
    today = datetime.date.today().isoformat()
    now_time = datetime.datetime.now().strftime('%H:%M:%S')
    
    existing = conn.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND subject_id = ? AND date = ?",
        (student_id, subject_id, today)
    ).fetchone()
    
    if existing:
        conn.close()
        return jsonify({
            'success': False,
            'message': f'Already marked for {student["name"]}',
            'recognized': True,
            'student_name': student['name'],
            'already_marked': True,
            'status': 'already_marked'
        })
    
    conn.execute(
        """INSERT INTO attendance 
           (student_id, subject_id, date, time, method, voice_similarity, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (student_id, subject_id, today, now_time, 'voice', similarity, 'present')
    )
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'message': f'✅ Voice verified. Attendance marked for {student["name"]}',
        'recognized': True,
        'student_name': student['name'],
        'roll_no': student['roll_no'],
        'similarity': round(similarity * 100, 2),
        'status': 'marked'
    })


@app.route('/admin/attendance/today/<int:subject_id>')
def today_attendance_list(subject_id):
    if 'admin_id' not in session:
        return jsonify({'success': False})
    
    today = datetime.date.today().isoformat()
    conn = get_db()
    rows = conn.execute('''
        SELECT a.time, a.face_confidence, a.liveness_passed, a.method, s.name, s.roll_no
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        WHERE a.subject_id = ? AND a.date = ?
        ORDER BY a.time DESC
    ''', (subject_id, today)).fetchall()
    conn.close()
    
    result = []
    for r in rows:
        result.append({
            'time': r['time'],
            'name': r['name'],
            'roll_no': r['roll_no'],
            'method': r['method'],
            'confidence': round(100 - r['face_confidence'], 2) if r['face_confidence'] else 0,
            'liveness': bool(r['liveness_passed'])
        })
    
    return jsonify({'success': True, 'attendance': result})


# ==================== REPORTS ====================

@app.route('/admin/reports')
def reports():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY subject_code").fetchall()
    students = conn.execute("SELECT * FROM students ORDER BY roll_no").fetchall()
    conn.close()
    
    today = datetime.date.today().isoformat()
    
    return render_template('admin/reports.html',
                          subjects=subjects,
                          students=students,
                          today=today)


@app.route('/admin/reports/daily')
def daily_report():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    date = request.args.get('date', datetime.date.today().isoformat())
    subject_id = request.args.get('subject_id', '')
    
    conn = get_db()
    
    query = '''
        SELECT a.date, a.time, s.name, s.roll_no, sub.subject_code, sub.subject_name, a.method, a.liveness_passed
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.date = ?
    '''
    params = [date]
    
    if subject_id:
        query += " AND a.subject_id = ?"
        params.append(subject_id)
    
    query += " ORDER BY a.time DESC"
    
    rows = conn.execute(query, params).fetchall()
    
    total_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    present_count = len(rows)
    absent_count = total_students - present_count
    
    conn.close()
    
    return render_template('admin/daily_report.html',
                          rows=rows,
                          date=date,
                          subject_id=subject_id,
                          total_students=total_students,
                          present_count=present_count,
                          absent_count=absent_count)


@app.route('/admin/reports/defaulter')
def defaulter_report():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    threshold = float(request.args.get('threshold', 75))
    
    conn = get_db()
    
    query = '''
        SELECT 
            s.id, s.name, s.roll_no, s.course,
            COUNT(a.id) as present_count
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id
        GROUP BY s.id
    '''
    
    students_data = conn.execute(query).fetchall()
    
    total_classes = conn.execute("SELECT COUNT(DISTINCT date || '-' || subject_id) FROM attendance").fetchone()[0]
    
    if total_classes == 0:
        total_classes = 1
    
    defaulters = []
    for s in students_data:
        percentage = (s['present_count'] / total_classes) * 100
        if percentage < threshold:
            defaulters.append({
                'id': s['id'],
                'name': s['name'],
                'roll_no': s['roll_no'],
                'course': s['course'],
                'present_count': s['present_count'],
                'total_classes': total_classes,
                'percentage': round(percentage, 2)
            })
    
    conn.close()
    
    return render_template('admin/defaulter_report.html',
                          defaulters=defaulters,
                          threshold=threshold,
                          total_classes=total_classes)


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
        SELECT 
            s.id, s.name, s.roll_no,
            COUNT(a.id) as present_count
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.subject_id = ?
        GROUP BY s.id
        ORDER BY s.roll_no
    ''', (subject_id,)).fetchall()
    
    total_classes = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM attendance WHERE subject_id = ?",
        (subject_id,)
    ).fetchone()[0]
    
    if total_classes == 0:
        total_classes = 1
    
    report_data = []
    for s in student_data:
        percentage = (s['present_count'] / total_classes) * 100
        report_data.append({
            'name': s['name'],
            'roll_no': s['roll_no'],
            'present_count': s['present_count'],
            'total_classes': total_classes,
            'percentage': round(percentage, 2),
            'status': 'Good' if percentage >= 75 else 'Low'
        })
    
    conn.close()
    
    return render_template('admin/subject_report.html',
                          subject=subject,
                          report_data=report_data,
                          total_classes=total_classes)


@app.route('/admin/reports/student/<int:student_id>')
def student_report(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    
    student = conn.execute(
        "SELECT * FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    
    if not student:
        conn.close()
        return redirect(url_for('reports'))
    
    subject_data = conn.execute('''
        SELECT 
            sub.id as subject_id,
            sub.subject_code,
            sub.subject_name,
            COUNT(a.id) as present_count
        FROM subjects sub
        LEFT JOIN attendance a ON sub.id = a.subject_id AND a.student_id = ?
        GROUP BY sub.id
        ORDER BY sub.subject_code
    ''', (student_id,)).fetchall()
    
    subject_breakdown = []
    total_present = 0
    total_classes = 0
    
    for sub in subject_data:
        total_subject_classes = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM attendance WHERE subject_id = ?",
            (sub['subject_id'],)
        ).fetchone()[0]
        
        if total_subject_classes == 0:
            total_subject_classes = 1
        
        percentage = (sub['present_count'] / total_subject_classes) * 100
        total_present += sub['present_count']
        total_classes += total_subject_classes
        
        subject_breakdown.append({
            'subject_code': sub['subject_code'],
            'subject_name': sub['subject_name'],
            'present': sub['present_count'],
            'total': total_subject_classes,
            'percentage': round(percentage, 2),
            'status': 'Good' if percentage >= 75 else 'Low'
        })
    
    overall_percentage = 0
    if total_classes > 0:
        overall_percentage = round((total_present / total_classes) * 100, 2)
    
    recent_records = conn.execute('''
        SELECT a.date, a.time, a.method, sub.subject_code, sub.subject_name
        FROM attendance a
        JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.student_id = ?
        ORDER BY a.date DESC, a.time DESC
        LIMIT 20
    ''', (student_id,)).fetchall()
    
    conn.close()
    
    return render_template('admin/student_report.html',
                          student=student,
                          subject_breakdown=subject_breakdown,
                          recent_records=recent_records,
                          overall_percentage=overall_percentage,
                          total_present=total_present,
                          total_classes=total_classes)


# ==================== EXCEL EXPORT ====================

@app.route('/admin/reports/export/daily')
def export_daily():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    date = request.args.get('date', datetime.date.today().isoformat())
    subject_id = request.args.get('subject_id', '')
    
    conn = get_db()
    
    query = '''
        SELECT a.date, a.time, s.name, s.roll_no, sub.subject_code, sub.subject_name, a.method, a.liveness_passed
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.date = ?
    '''
    params = [date]
    
    if subject_id:
        query += " AND a.subject_id = ?"
        params.append(subject_id)
    
    query += " ORDER BY a.time DESC"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    data = [{
        'Date': r['date'],
        'Time': r['time'],
        'Name': r['name'],
        'Roll No': r['roll_no'],
        'Subject Code': r['subject_code'],
        'Subject': r['subject_name'],
        'Method': r['method'],
        'Liveness': 'Passed' if r['liveness_passed'] else 'N/A'
    } for r in rows]
    
    df = pd.DataFrame(data)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Daily Report', index=False)
    output.seek(0)
    
    filename = f"daily_report_{date}.xlsx"
    
    return send_file(output,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name=filename)


@app.route('/admin/reports/export/defaulter')
def export_defaulter():
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    threshold = float(request.args.get('threshold', 75))
    
    conn = get_db()
    
    query = '''
        SELECT 
            s.id, s.name, s.roll_no, s.course,
            COUNT(a.id) as present_count
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id
        GROUP BY s.id
    '''
    
    students_data = conn.execute(query).fetchall()
    
    total_classes = conn.execute("SELECT COUNT(DISTINCT date || '-' || subject_id) FROM attendance").fetchone()[0]
    if total_classes == 0:
        total_classes = 1
    
    defaulters = []
    for s in students_data:
        percentage = (s['present_count'] / total_classes) * 100
        if percentage < threshold:
            defaulters.append({
                'Name': s['name'],
                'Roll No': s['roll_no'],
                'Course': s['course'],
                'Present': s['present_count'],
                'Total Classes': total_classes,
                'Percentage': round(percentage, 2)
            })
    
    conn.close()
    
    df = pd.DataFrame(defaulters)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Defaulter List', index=False)
    output.seek(0)
    
    filename = f"defaulter_list_below_{int(threshold)}.xlsx"
    
    return send_file(output,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name=filename)


@app.route('/admin/reports/export/subject/<int:subject_id>')
def export_subject(subject_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    subject = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    
    student_data = conn.execute('''
        SELECT 
            s.id, s.name, s.roll_no,
            COUNT(a.id) as present_count
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id AND a.subject_id = ?
        GROUP BY s.id
        ORDER BY s.roll_no
    ''', (subject_id,)).fetchall()
    
    total_classes = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM attendance WHERE subject_id = ?",
        (subject_id,)
    ).fetchone()[0]
    
    if total_classes == 0:
        total_classes = 1
    
    conn.close()
    
    data = []
    for s in student_data:
        percentage = (s['present_count'] / total_classes) * 100
        data.append({
            'Name': s['name'],
            'Roll No': s['roll_no'],
            'Present': s['present_count'],
            'Total Classes': total_classes,
            'Percentage': round(percentage, 2),
            'Status': 'Good' if percentage >= 75 else 'Low'
        })
    
    df = pd.DataFrame(data)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Subject Report', index=False)
    output.seek(0)
    
    filename = f"subject_{subject['subject_code']}_report.xlsx"
    
    return send_file(output,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name=filename)


@app.route('/admin/reports/export/student/<int:student_id>')
def export_student(student_id):
    if 'admin_id' not in session:
        return redirect(url_for('index'))
    
    conn = get_db()
    
    student = conn.execute(
        "SELECT * FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    
    if not student:
        conn.close()
        return redirect(url_for('reports'))
    
    records = conn.execute('''
        SELECT a.date, a.time, a.method, sub.subject_code, sub.subject_name, 
               a.face_confidence, a.voice_similarity, a.liveness_passed
        FROM attendance a
        JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.student_id = ?
        ORDER BY a.date DESC, a.time DESC
    ''', (student_id,)).fetchall()
    
    conn.close()
    
    data = []
    for r in records:
        data.append({
            'Date': r['date'],
            'Time': r['time'],
            'Subject Code': r['subject_code'],
            'Subject': r['subject_name'],
            'Method': r['method'],
            'Face Confidence': round(100 - r['face_confidence'], 2) if r['face_confidence'] else 'N/A',
            'Voice Similarity': round(r['voice_similarity'] * 100, 2) if r['voice_similarity'] else 'N/A',
            'Liveness': 'Passed' if r['liveness_passed'] else 'N/A'
        })
    
    df = pd.DataFrame(data)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Student Report', index=False)
    output.seek(0)
    
    filename = f"student_{student['roll_no']}_{student['name'].replace(' ', '_')}_report.xlsx"
    
    return send_file(output,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name=filename)


# ==================== MAIN ====================

if __name__ == '__main__':
    init_db()
    create_default_admin()
    print("Server starting...")
    print("Open: http://127.0.0.1:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)