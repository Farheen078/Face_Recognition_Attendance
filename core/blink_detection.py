import cv2
import time
import os

EYE_CASCADE_PATH = cv2.data.haarcascades + 'haarcascade_eye.xml'
eye_cascade = cv2.CascadeClassifier(EYE_CASCADE_PATH)


def detect_eyes(face_roi_gray):
    """Face region mein eyes detect karta hai. Return: eye count"""
    if face_roi_gray is None or face_roi_gray.size == 0:
        return 0
    
    h, w = face_roi_gray.shape
    upper_face = face_roi_gray[0:int(h*0.6), :]
    
    eyes = eye_cascade.detectMultiScale(
        upper_face,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(15, 15)
    )
    return len(eyes)


def check_blink_pattern(states):
    """
    Eye states ka pattern check karta hai.
    states: list of eye_counts (integers)
    Return: True if blink pattern found
    """
    if len(states) < 5:
        return False
    
    found_open = False
    found_closed = False
    
    for i, count in enumerate(states):
        if not found_open and count >= 1:
            found_open = True
        elif found_open and not found_closed and count == 0:
            found_closed = True
        elif found_open and found_closed and count >= 1:
            return True
    
    return False


def cleanup_cascade():
    """Check karta hai ki cascade file load hui ya nahi"""
    if eye_cascade.empty():
        return False, f"Eye cascade not loaded! Path: {EYE_CASCADE_PATH}"
    return True, "OK"