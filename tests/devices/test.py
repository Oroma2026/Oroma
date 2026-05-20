try:
    from picamera2 import Picamera2
    import cv2
    print("OK: Picamera2 & OpenCV im venv gefunden")
except Exception as e:
    print("FEHLT:", e)