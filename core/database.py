import sqlite3
from config import Config


def get_db():
    conn = sqlite3.connect(Config.DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll_no TEXT UNIQUE NOT NULL,
            course TEXT,
            semester INTEGER,
            face_trained BOOLEAN DEFAULT 0,
            voice_enrolled BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_code TEXT UNIQUE NOT NULL,
            subject_name TEXT NOT NULL,
            teacher_name TEXT,
            semester INTEGER
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS timetable (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER,
            day TEXT,
            start_time TEXT,
            end_time TEXT,
            FOREIGN KEY(subject_id) REFERENCES subjects(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enrollment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            subject_id INTEGER,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(subject_id) REFERENCES subjects(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            subject_id INTEGER,
            date DATE NOT NULL,
            time TIME NOT NULL,
            method TEXT,
            face_confidence REAL,
            voice_similarity REAL,
            liveness_passed BOOLEAN,
            status TEXT DEFAULT 'present',
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(subject_id) REFERENCES subjects(id),
            UNIQUE(student_id, subject_id, date)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS face_print (
            face_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER UNIQUE,
            lbph_signature TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS voice_print (
            voice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER UNIQUE,
            mfcc_signature TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized")


def create_default_admin():
    from werkzeug.security import generate_password_hash
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM admin WHERE username = 'admin'")
    if not cursor.fetchone():
        password_hash = generate_password_hash('admin123')
        cursor.execute(
            "INSERT INTO admin (username, password_hash) VALUES (?, ?)",
            ('admin', password_hash)
        )
        conn.commit()
        print("Default admin created: admin / admin123")
    
    conn.close()