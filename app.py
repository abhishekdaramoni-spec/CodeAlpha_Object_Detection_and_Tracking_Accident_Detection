import os
import time
import gc
import sqlite3
import threading
import psutil
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, Response
import cv2
import numpy as np

import tracker
import detector
import accident_engine

app = Flask(__name__)
app.config['SECRET_KEY'] = 'surveillance_secret_key_8888'
app.config['MAX_CONTENT_LENGTH'] = 250 * 1024 * 1024  # Max upload size: 250MB

# Local dynamic configuration state
app.config['CONFIDENCE_THRESHOLD'] = 0.25
app.config['ENABLED_CLASSES'] = {'person', 'car', 'motorcycle', 'bus', 'truck'}

# Thread lock and state controls
thread_lock = threading.Lock()
processing_thread = None
stop_processing = False

# Global live state tracking dict for stats polling
active_counts = {
    'system_status': 'IDLE',
    'total_detections': 0,
    'active_tracks': 0,
    'current_counts': {
        'person': 0,
        'car': 0,
        'motorcycle': 0,
        'bus': 0,
        'truck': 0
    }
}

# Ensure folders exist
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
EVIDENCE_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "evidence")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EVIDENCE_FOLDER, exist_ok=True)

# Write default frame
default_frame = np.zeros((360, 640, 3), dtype=np.uint8)
cv2.putText(default_frame, "Stream Offline. Launch feed to start.", (120, 180), 
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (244, 63, 94), 1, cv2.LINE_AA)
cv2.imwrite(os.path.join(UPLOAD_FOLDER, "live_frame.jpg"), default_frame)

def get_db_connection():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            severity TEXT NOT NULL,
            screenshot_path TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'Active'
        )
    ''')
    try:
        cursor.execute("ALTER TABLE incidents ADD COLUMN status TEXT DEFAULT 'Active'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

# Initialize tracker and engine
centroid_tracker = tracker.CentroidTracker()
engine = accident_engine.AccidentEngine(centroid_tracker)

def insert_incident(severity, screenshot_path, confidence_score, description):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO incidents (timestamp, severity, screenshot_path, confidence_score, description, status)
        VALUES (?, ?, ?, ?, ?, 'Active')
    ''', (now_str, severity, screenshot_path, confidence_score, description))
    conn.commit()
    conn.close()

def get_incident_history(limit=10):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM incidents ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        d = dict(row)
        if 'status' not in d:
            d['status'] = 'Active'
        desc = d.get('description', '')
        if 'Proximity' in desc:
            d['incident_type'] = 'Proximity Warning'
        else:
            d['incident_type'] = 'Vehicle Collision'
        results.append(d)
    return results

def get_analytics_kpi():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM incidents')
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM incidents WHERE severity = 'High'")
    high_cnt = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM incidents WHERE severity = 'Moderate'")
    mod_cnt = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM incidents WHERE severity = 'Low'")
    low_cnt = cursor.fetchone()[0]
    
    conn.close()
    return {
        'total': total,
        'High': high_cnt,
        'Moderate': mod_cnt,
        'Low': low_cnt
    }

def process_stream_background(source_path):
    global stop_processing, active_counts
    cap = None
    
    try:
        if source_path == "webcam":
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                raise Exception("Surveillance camera sensor is not available.")
            print("Camera Connected")
        else:
            cap = cv2.VideoCapture(source_path)
            if not cap.isOpened():
                raise Exception(f"Failed to load video file: {source_path}")
            print("Camera Connected")
                
        active_counts['system_status'] = 'PROCESSING'
        
        while cap.isOpened() and not stop_processing:
            ret, frame = cap.read()
            if not ret:
                break
                
            conf_th = app.config.get('CONFIDENCE_THRESHOLD', 0.25)
            enabled_cls = app.config.get('ENABLED_CLASSES')
            
            # YOLO detect
            resized, detections, counts = detector.detect_frame(frame, conf_threshold=conf_th, enabled_classes=enabled_cls)
            
            # Heuristics analyze
            new_incidents = engine.analyze_frame(resized, detections)
            
            # Update live stats
            total_objs = sum(counts.values())
            active_counts['active_tracks'] = total_objs
            active_counts['current_counts'] = counts
            
            # Save new incidents
            for inc in new_incidents:
                insert_incident(
                    severity=inc['severity'],
                    screenshot_path=inc['screenshot_path'],
                    confidence_score=inc['confidence_score'],
                    description=inc['description']
                )
                
            # Draw overlay on annotated frame for live streaming
            annotated_frame = resized.copy()
            for obj_id, centroid in engine.tracker.objects.items():
                hist = engine.tracker.history[obj_id]
                if len(hist) > 0:
                    _, _, bbox, cls_id = hist[-1]
                    cls_name = detector.TRACKED_CLASSES[cls_id]
                    color = (255, 240, 0) if cls_id != 0 else (0, 255, 50)
                    cv2.rectangle(annotated_frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), color, 1)
                    label = f"ID {obj_id} {cls_name}"
                    if obj_id in engine.speeds and cls_name in ['car', 'motorcycle', 'bus', 'truck']:
                        label += f" {int(engine.speeds[obj_id])} km/h"
                    cv2.putText(annotated_frame, label, (bbox[0], bbox[1] - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
            
            # Draw crossing line
            cv2.line(annotated_frame, (0, accident_engine.LINE_COUNT_Y), (640, accident_engine.LINE_COUNT_Y), (0, 165, 255), 1)
            cv2.putText(annotated_frame, f"LINE CROSS: In: {engine.line_in_count} | Out: {engine.line_out_count}", 
                        (10, accident_engine.LINE_COUNT_Y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1, cv2.LINE_AA)
            
            # Draw ROI box
            rx1, ry1, rx2, ry2 = accident_engine.ROI_BOX
            cv2.rectangle(annotated_frame, (rx1, ry1), (rx2, ry2), (0, 255, 255), 1)
            cv2.putText(annotated_frame, "RESTRICTED ROI ZONE", (rx1 + 5, ry1 + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv2.LINE_AA)
            
            cv2.imwrite(os.path.join(UPLOAD_FOLDER, "live_frame.jpg"), annotated_frame)
            
            # Pacing loop to conserve CPU
            if source_path != "webcam":
                time.sleep(0.03)
                
    except Exception as e:
        print(f"Exception in stream thread: {e}")
        error_frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(error_frame, f"FEED ERROR: {str(e)[:40]}", (60, 180), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1, cv2.LINE_AA)
        cv2.imwrite(os.path.join(UPLOAD_FOLDER, "live_frame.jpg"), error_frame)
        
    finally:
        if cap is not None:
            cap.release()
        active_counts['system_status'] = 'IDLE'
        active_counts['active_tracks'] = 0
        active_counts['current_counts'] = {cls_name: 0 for cls_name in detector.TRACKED_CLASSES.values()}
        gc.collect()

def stop_active_thread():
    global stop_processing, processing_thread
    if processing_thread is not None and processing_thread.is_alive():
        stop_processing = True
        processing_thread.join()
    stop_processing = False

@app.route('/')
def root():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    kpis = get_analytics_kpi()
    records = get_incident_history(10)
    processing = (active_counts['system_status'] == 'PROCESSING')
    return render_template('dashboard.html', active_page='dashboard', stats=kpis, records=records, processing=processing)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    global processing_thread
    if request.method == 'POST':
        with thread_lock:
            stop_active_thread()
            
            if request.form.get('use_webcam') == 'true':
                active_counts['system_status'] = 'PROCESSING'
                processing_thread = threading.Thread(target=process_stream_background, args=("webcam",))
                processing_thread.daemon = True
                processing_thread.start()
                return redirect(url_for('dashboard'))
                
            if 'file' not in request.files:
                return redirect(url_for('upload'))
                
            uploaded_file = request.files['file']
            if uploaded_file.filename == '':
                return redirect(url_for('upload'))
                
            ext = uploaded_file.filename.split('.')[-1].lower()
            if ext == 'mp4':
                filename = f"vid_{int(time.time())}.mp4"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                uploaded_file.save(filepath)
                
                active_counts['system_status'] = 'PROCESSING'
                processing_thread = threading.Thread(target=process_stream_background, args=(filepath,))
                processing_thread.daemon = True
                processing_thread.start()
                return redirect(url_for('dashboard'))
                
            elif ext in ['png', 'jpg', 'jpeg']:
                file_bytes = np.frombuffer(uploaded_file.read(), np.uint8)
                img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                conf_th = app.config.get('CONFIDENCE_THRESHOLD', 0.25)
                enabled_cls = app.config.get('ENABLED_CLASSES')
                
                resized, detections, counts = detector.detect_frame(img, conf_threshold=conf_th, enabled_classes=enabled_cls)
                new_incidents = engine.analyze_frame(resized, detections)
                
                for inc in new_incidents:
                    insert_incident(
                        severity=inc['severity'],
                        screenshot_path=inc['screenshot_path'],
                        confidence_score=inc['confidence_score'],
                        description=inc['description']
                    )
                return redirect(url_for('dashboard'))
                
    return render_template('upload.html', active_page='upload')

@app.route('/stop')
def stop_feed():
    with thread_lock:
        stop_active_thread()
    return redirect(url_for('dashboard'))

@app.route('/video_feed')
def video_feed():
    filepath = os.path.join(UPLOAD_FOLDER, "live_frame.jpg")
    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/jpeg')
    return jsonify({'status': 'error'}), 404

@app.route('/api/stats')
def get_stats():
    kpis = get_analytics_kpi()
    recent = get_incident_history(5)
    raw_counts = active_counts['current_counts'] or {}
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT AVG(confidence_score) FROM incidents")
    avg_conf = cursor.fetchone()[0] or 0.0
    conn.close()
    
    merged_counts = {
        'person': raw_counts.get('person', 0),
        'vehicle': (
            raw_counts.get('car', 0) +
            raw_counts.get('motorcycle', 0) +
            raw_counts.get('bus', 0) +
            raw_counts.get('truck', 0)
        )
    }
    return jsonify({
        'system_status': active_counts['system_status'],
        'total_detections': kpis['total'],
        'active_tracks': active_counts['active_tracks'],
        'current_counts': merged_counts,
        'recent_incidents': recent,
        'cpu_percent': psutil.cpu_percent(),
        'memory_percent': psutil.virtual_memory().percent,
        'avg_confidence': avg_conf,
        'images_processed': 0,
        'videos_processed': 0
    })

@app.route('/api/analytics')
def get_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT severity, COUNT(*) FROM incidents GROUP BY severity")
    sevs = {row[0]: row[1] for row in cursor.fetchall()}
    
    cursor.execute("SELECT description FROM incidents")
    descriptions = [r[0] for r in cursor.fetchall()]
    
    cats = {'Vehicle Collision': 0, 'Proximity Warning': 0}
    for desc in descriptions:
        if 'Proximity' in desc:
            cats['Proximity Warning'] += 1
        else:
            cats['Vehicle Collision'] += 1
            
    cursor.execute("SELECT DATE(timestamp), COUNT(*) FROM incidents GROUP BY DATE(timestamp) ORDER BY DATE(timestamp) DESC LIMIT 7")
    daily = cursor.fetchall()
    daily_labels = [row[0] for row in reversed(daily)]
    daily_values = [row[1] for row in reversed(daily)]
    
    cursor.execute("SELECT id, confidence_score FROM incidents ORDER BY timestamp DESC LIMIT 10")
    conf_rows = cursor.fetchall()
    confidence_labels = [f"#{row[0]}" for row in reversed(conf_rows)]
    confidence_values = [row[1] for row in reversed(conf_rows)]
    
    conn.close()
    
    return jsonify({
        'severity_distribution': {
            'Low': sevs.get('Low', 0),
            'Moderate': sevs.get('Moderate', 0),
            'High': sevs.get('High', 0)
        },
        'category_distribution': {
            'Vehicle Collision': cats['Vehicle Collision'],
            'Sudden Stop': 0,
            'Pedestrian Fall': 0,
            'Proximity Warning': cats['Proximity Warning'],
            'Intrusion Alert': 0,
            'Crowd Alert': 0
        },
        'daily_labels': daily_labels,
        'daily_values': daily_values,
        'confidence_labels': confidence_labels,
        'confidence_values': confidence_values
    })

@app.route('/resolve/<int:incident_id>', methods=['POST'])
def resolve_incident(incident_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE incidents SET status = "Resolved" WHERE id = ?', (incident_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/history/clear')
def clear_history():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM incidents')
    conn.commit()
    conn.close()
    
    evidence_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "evidence")
    if os.path.exists(evidence_dir):
        for f in os.listdir(evidence_dir):
            try:
                os.remove(os.path.join(evidence_dir, f))
            except Exception as e:
                print(f"Error removing file during clear: {e}")
                
    return redirect(url_for('dashboard'))

@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.get_json() or {}
    conf = data.get('confidence_threshold')
    enabled_classes = data.get('enabled_classes')
    try:
        if conf is not None:
            app.config['CONFIDENCE_THRESHOLD'] = float(conf)
        if enabled_classes is not None:
            mapped = set()
            if 'person' in enabled_classes: mapped.add('person')
            if 'vehicle' in enabled_classes: mapped.update(['car', 'motorcycle', 'bus', 'truck'])
            app.config['ENABLED_CLASSES'] = mapped
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

if __name__ == '__main__':
    print("Application Started")
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
