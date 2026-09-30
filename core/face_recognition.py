import cv2
import numpy as np
import os

CASCADE_PATH = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
face_cascade = cv2.CascadeClassifier(CASCADE_PATH)


def _check_cascade():
    if face_cascade.empty():
        return False, f"Cascade file not loaded! Path: {CASCADE_PATH}"
    return True, "OK"


def train_model(dataset_path='dataset/faces', trainer_path='trainer/face_trainer.yml'):
    ok, msg = _check_cascade()
    if not ok:
        return False, msg, 0, 0
    
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    faces = []
    ids = []
    
    if not os.path.exists(dataset_path):
        return False, "Dataset folder not found", 0, 0
    
    student_folders = [f for f in os.listdir(dataset_path) 
                       if os.path.isdir(os.path.join(dataset_path, f))]
    
    if not student_folders:
        return False, "No student folders found", 0, 0
    
    for student_id in student_folders:
        student_folder = os.path.join(dataset_path, student_id)
        
        for img_file in os.listdir(student_folder):
            if not img_file.lower().endswith('.jpg'):
                continue
            
            img_path = os.path.join(student_folder, img_file)
            img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
            
            if img is None:
                continue
            
            detected_faces = face_cascade.detectMultiScale(img, 1.1, 5, minSize=(50, 50))
            
            for (x, y, w, h) in detected_faces:
                face_roi = img[y:y+h, x:x+w]
                faces.append(face_roi)
                ids.append(int(student_id))
    
    if len(faces) == 0:
        return False, "No faces detected in dataset", 0, 0
    
    recognizer.train(faces, np.array(ids))
    os.makedirs(os.path.dirname(trainer_path), exist_ok=True)
    recognizer.save(trainer_path)
    
    return True, f"Trained on {len(faces)} faces from {len(set(ids))} students", len(faces), len(set(ids))


def is_model_trained(trainer_path='trainer/face_trainer.yml'):
    return os.path.exists(trainer_path)


def get_dataset_stats(dataset_path='dataset/faces'):
    stats = {'total_students': 0, 'total_images': 0, 'students': []}
    
    if not os.path.exists(dataset_path):
        return stats
    
    student_folders = [f for f in os.listdir(dataset_path) 
                       if os.path.isdir(os.path.join(dataset_path, f))]
    
    stats['total_students'] = len(student_folders)
    
    for student_id in student_folders:
        student_folder = os.path.join(dataset_path, student_id)
        image_count = len([f for f in os.listdir(student_folder) 
                          if f.lower().endswith('.jpg')])
        stats['total_images'] += image_count
        stats['students'].append({'id': student_id, 'images': image_count})
    
    return stats


def detect_face_and_eyes(img, trainer_path='trainer/face_trainer.yml'):
    """
    Face aur eyes dono detect karta hai.
    Return: dict with student_id, confidence, eye_count, face_found, status
    """
    result = {
        'student_id': None,
        'confidence': None,
        'eye_count': 0,
        'face_found': False,
        'status': 'OK'
    }
    
    if not os.path.exists(trainer_path):
        result['status'] = 'Model not trained'
        return result
    
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    
    detected_faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(50, 50))
    
    if len(detected_faces) == 0:
        result['status'] = 'No face detected'
        return result
    
    result['face_found'] = True
    
    (x, y, w, h) = detected_faces[0]
    face_roi = gray[y:y+h, x:x+w]
    
    from core.blink_detection import detect_eyes
    result['eye_count'] = detect_eyes(face_roi)
    
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(trainer_path)
    
    student_id, confidence = recognizer.predict(face_roi)
    result['student_id'] = student_id
    result['confidence'] = confidence
    
    return result


def get_current_subject(day, current_time, timetable_rows):
    for row in timetable_rows:
        if row['day'] != day:
            continue
        
        start = row['start_time']
        end = row['end_time']
        
        if start <= current_time <= end:
            return row['subject_id']
    
    return None