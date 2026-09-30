# 🎓 Smart Attendance System

A web-based attendance management system that uses **Face Recognition** with **Voice Fallback** and **Blink Detection (Liveness Check)** to automatically mark student attendance.

## 🎯 Features

- **Face Recognition (LBPH Algorithm)** — Primary authentication
- **Voice Verification (MFCC + Cosine Similarity)** — Fallback when face fails
- **Blink Detection (Liveness Check)** — Prevents photo spoofing
- **Subject-wise Attendance** — Timetable-based tracking
- **Duplicate Prevention** — One entry per student, per subject, per day
- **Reports** — Daily, Defaulter, Subject-wise, Student-wise
- **Excel Export** — All reports exportable to .xlsx
- **Live Dashboard** — Charts and statistics

## 🛠️ Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend | HTML5, CSS3, JavaScript |
| Backend | Python 3.10 + Flask |
| Database | SQLite |
| Face Recognition | OpenCV (LBPH) |
| Voice Verification | Librosa (MFCC) |
| Blink Detection | OpenCV (Haar Cascade) |
| Charts | Chart.js |
| Reports | Pandas + OpenPyXL |

## 📋 Prerequisites

- Python 3.10
- Webcam
- Microphone
- Modern web browser (Chrome/Firefox/Edge)

## 🚀 Installation

### 1. Clone / Extract Project
```bash
cd C:\Users\User\SmartAttendanceWeb