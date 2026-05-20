#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cam_web.py – Raspberry Pi 5 Kamerastream über Flask
---------------------------------------------------
- Greift auf Picamera2 zu
- Bietet MJPEG-Stream unter /video_feed
- Läuft auf Port 8081
"""

from flask import Flask, Response, render_template_string
from picamera2 import Picamera2
import cv2

app = Flask(__name__)

# Kamera initialisieren
picam2 = Picamera2()
config = picam2.create_video_configuration(main={"size": (640, 480)})
picam2.configure(config)
picam2.start()

HTML = """
<!DOCTYPE html>
<html>
  <head><title>Raspberry Pi Kamera</title></head>
  <body>
    <h1>Live-Stream</h1>
    <img src="{{ url_for('video_feed') }}" width="640" height="480">
  </body>
</html>
"""

def generate():
    while True:
        frame = picam2.capture_array()
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' +
               jpeg.tobytes() +
               b'\r\n')

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/video_feed")
def video_feed():
    return Response(generate(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, threaded=True)