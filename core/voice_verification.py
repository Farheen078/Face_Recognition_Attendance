import os
import pickle
import numpy as np
import librosa
from numpy.linalg import norm


VOICE_SAMPLE_RATE = 22050
VOICE_DURATION = 3
N_MFCC = 20


def extract_mfcc(audio_path):
    """Audio file se MFCC features nikalta hai"""
    try:
        y, sr = librosa.load(audio_path, sr=VOICE_SAMPLE_RATE, duration=VOICE_DURATION)
        
        if len(y) < sr * 0.5:
            return None
        
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC)
        
        mfcc_mean = np.mean(mfcc.T, axis=0)
        mfcc_std = np.std(mfcc.T, axis=0)
        
        features = np.concatenate([mfcc_mean, mfcc_std])
        
        return features
    except Exception as e:
        print(f"MFCC extraction error: {e}")
        return None


def extract_mfcc_from_bytes(audio_bytes, sample_rate=VOICE_SAMPLE_RATE):
    """Audio bytes se directly MFCC nikalta hai (webm/wav)"""
    import tempfile
    
    temp_path = os.path.join('uploads', 'temp_voice.webm')
    os.makedirs('uploads', exist_ok=True)
    
    with open(temp_path, 'wb') as f:
        f.write(audio_bytes)
    
    try:
        y, sr = librosa.load(temp_path, sr=VOICE_SAMPLE_RATE, duration=VOICE_DURATION)
        
        if len(y) < sr * 0.5:
            return None
        
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC)
        mfcc_mean = np.mean(mfcc.T, axis=0)
        mfcc_std = np.std(mfcc.T, axis=0)
        
        return np.concatenate([mfcc_mean, mfcc_std])
    except Exception as e:
        print(f"MFCC bytes extraction error: {e}")
        return None
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def cosine_similarity(a, b):
    """Do vectors ke beech similarity calculate karta hai"""
    if a is None or b is None:
        return 0.0
    
    a_norm = norm(a)
    b_norm = norm(b)
    
    if a_norm == 0 or b_norm == 0:
        return 0.0
    
    return float(np.dot(a, b) / (a_norm * b_norm))


def save_voice_feature(student_id, features, features_path='trainer/voice_features.pkl'):
    """Student ke voice features save karta hai"""
    os.makedirs(os.path.dirname(features_path), exist_ok=True)
    
    if os.path.exists(features_path):
        with open(features_path, 'rb') as f:
            data = pickle.load(f)
    else:
        data = {}
    
    data[str(student_id)] = features.tolist()
    
    with open(features_path, 'wb') as f:
        pickle.dump(data, f)
    
    return True


def load_voice_features(features_path='trainer/voice_features.pkl'):
    """Saare students ke voice features load karta hai"""
    if not os.path.exists(features_path):
        return {}
    
    with open(features_path, 'rb') as f:
        return pickle.load(f)


def verify_voice(audio_bytes, student_id=None, features_path='trainer/voice_features.pkl', threshold=0.75):
    """
    Voice verify karta hai.
    Agar student_id diya → specific student se compare
    Warna → saare students se compare karke best match dhundhta hai
    """
    features_db = load_voice_features(features_path)
    
    if not features_db:
        return None, 0.0, "No voice data enrolled"
    
    new_features = extract_mfcc_from_bytes(audio_bytes)
    
    if new_features is None:
        return None, 0.0, "Could not extract voice features"
    
    if student_id is not None:
        sid = str(student_id)
        if sid not in features_db:
            return None, 0.0, "Student voice not enrolled"
        
        stored = np.array(features_db[sid])
        sim = cosine_similarity(new_features, stored)
        
        if sim >= threshold:
            return student_id, sim, "OK"
        else:
            return None, sim, f"Similarity too low ({sim:.2f})"
    
    best_id = None
    best_sim = 0.0
    
    for sid_str, stored_features in features_db.items():
        stored = np.array(stored_features)
        sim = cosine_similarity(new_features, stored)
        
        if sim > best_sim:
            best_sim = sim
            best_id = int(sid_str)
    
    if best_sim >= threshold:
        return best_id, best_sim, "OK"
    else:
        return None, best_sim, "No matching voice found"


def is_voice_enrolled(student_id, features_path='trainer/voice_features.pkl'):
    """Check karta hai ki student ka voice enroll hua hai ya nahi"""
    features_db = load_voice_features(features_path)
    return str(student_id) in features_db