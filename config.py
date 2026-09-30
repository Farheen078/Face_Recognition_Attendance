import os
from datetime import timedelta

class Config:
    SECRET_KEY = 'smart-attendance-secret-key-2026'
    DATABASE = 'attendance.db'
    
    # Session timeout — 2 hours inactivity
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    
    # Face recognition
    FACE_DATASET_PATH = 'dataset/faces'
    FACE_TRAINER_PATH = 'trainer/face_trainer.yml'
    FACE_CONFIDENCE_THRESHOLD = 60
    
    # Voice verification
    VOICE_DATASET_PATH = 'dataset/voices'
    VOICE_FEATURES_PATH = 'trainer/voice_features.pkl'
    VOICE_SIMILARITY_THRESHOLD = 0.75
    
    # Image capture
    IMAGES_PER_STUDENT = 40
    SESSION_TIMEOUT = 3600