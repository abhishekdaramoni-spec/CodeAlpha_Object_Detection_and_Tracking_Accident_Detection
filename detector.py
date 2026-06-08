import os
import torch
from ultralytics import YOLO

# Restrict PyTorch thread count before imports
torch.set_num_threads(1)
torch.set_num_interop_threads(1)

_yolo_model = None

def get_model():
    global _yolo_model
    if _yolo_model is None:
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yolov8n.pt")
        _yolo_model = YOLO(model_path)
        print("Model Loaded")
    return _yolo_model

# Target class configuration
TRACKED_CLASSES = {
    0: 'person',
    2: 'car',
    3: 'motorcycle',
    5: 'bus',
    7: 'truck'
}

def detect_frame(frame, conf_threshold=0.25, enabled_classes=None):
    """
    Runs YOLOv8 Nano inference on a frame (resized to 640x360).
    Returns:
        resized_frame (np.ndarray)
        detections (list of dict: {'box': [x1, y1, x2, y2], 'class_id': int})
        counts (dict of class name -> count)
    """
    import cv2
    resized = cv2.resize(frame, (640, 360))
    model = get_model()
    
    results = model(resized, conf=conf_threshold, device='cpu', verbose=False)
    
    counts = {cls_name: 0 for cls_name in TRACKED_CLASSES.values()}
    detections = []
    
    if len(results) > 0:
        boxes = results[0].boxes
        for box in boxes:
            cls_id = int(box.cls[0].item())
            if cls_id in TRACKED_CLASSES:
                cls_name = TRACKED_CLASSES[cls_id]
                
                # Check class exclusions
                if enabled_classes and cls_name not in enabled_classes:
                    continue
                    
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append({
                    'box': [int(x1), int(y1), int(x2), int(y2)],
                    'class_id': cls_id
                })
                counts[cls_name] += 1
                
    return resized, detections, counts
