import os
import cv2
import time
import math
import random
import collections
from datetime import datetime
import numpy as np

# Pixel conversion & thresholds
PIXEL_TO_METERS = 0.08
PROXIMITY_ALERT_DISTANCE = 45.0
LINE_COUNT_Y = 200
ROI_BOX = [100, 80, 540, 320]
EVIDENCE_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "evidence")

class EventDeduplicator:
    def __init__(self, spatial_radius=80, temporal_window=20.0):
        self.spatial_radius = spatial_radius
        self.temporal_window = temporal_window
        self.active_incidents = {}  # incident_type -> list of dicts: {timestamp, x, y, track_ids}

    def check_duplicate(self, incident_type, centroid, track_ids):
        now = time.time()
        cx, cy = centroid
        
        if incident_type not in self.active_incidents:
            self.active_incidents[incident_type] = []
            
        self.active_incidents[incident_type] = [
            data for data in self.active_incidents[incident_type]
            if now - data['timestamp'] < self.temporal_window
        ]
        
        for data in self.active_incidents[incident_type]:
            dist = math.sqrt((cx - data['x'])**2 + (cy - data['y'])**2)
            shared_tracks = set(track_ids).intersection(set(data['track_ids']))
            if dist < self.spatial_radius or len(shared_tracks) > 0:
                data['timestamp'] = now
                data['track_ids'] = list(set(data['track_ids']).union(set(track_ids)))
                return True
                
        self.active_incidents[incident_type].append({
            'timestamp': now,
            'x': cx,
            'y': cy,
            'track_ids': list(track_ids)
        })
        return False


class AccidentEngine:
    def __init__(self, tracker):
        self.tracker = tracker
        self.cooldowns = {}
        self.speeds = {}
        self.vehicle_speeds_history = {}
        self.collision_states = {}
        self.frame_buffer = collections.deque(maxlen=150)
        self.pending_post_impact_saves = []
        self.deduplicator = EventDeduplicator()
        
        # Line count states
        self.line_in_count = 0
        self.line_out_count = 0
        self.crossed_in_ids = set()
        self.crossed_out_ids = set()

    def cleanup_storage(self):
        """Auto-deletes screenshots older than 30 days and enforces 500 MB disk cap."""
        os.makedirs(EVIDENCE_FOLDER, exist_ok=True)
        now = time.time()
        cutoff_time = now - (30 * 24 * 3600)  # 30 days
        
        files = []
        for filename in os.listdir(EVIDENCE_FOLDER):
            filepath = os.path.join(EVIDENCE_FOLDER, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                if stat.st_mtime < cutoff_time:
                    try:
                        os.remove(filepath)
                    except Exception as e:
                        print(f"Failed to delete expired file {filepath}: {e}")
                else:
                    files.append({
                        'path': filepath,
                        'size': stat.st_size,
                        'mtime': stat.st_mtime
                    })
                    
        # Enforce 500 MB limit (500 * 1024 * 1024 bytes)
        max_bytes = 500 * 1024 * 1024
        total_size = sum(f['size'] for f in files)
        if total_size > max_bytes:
            files.sort(key=lambda x: x['mtime'])
            for f in files:
                try:
                    os.remove(f['path'])
                    total_size -= f['size']
                    if total_size <= max_bytes:
                        break
                except Exception as e:
                    print(f"Failed to delete file {f['path']} during disk limit enforcement: {e}")

    def _is_cooled_down(self, key, seconds=60.0):
        now = time.time()
        if key in self.cooldowns:
            if now - self.cooldowns[key] < seconds:
                return False
        self.cooldowns[key] = now
        return True

    def calculate_iou(self, boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        if interArea == 0:
            return 0.0
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        return interArea / float(boxAArea + boxBArea - interArea)

    def get_displacement(self, history_slice):
        if len(history_slice) < 2:
            return 0.0
        dist = 0.0
        for i in range(1, len(history_slice)):
            p1 = history_slice[i-1]
            p2 = history_slice[i]
            dist += np.linalg.norm(np.array([p1[0], p1[1]]) - np.array([p2[0], p2[1]]))
        return dist

    def estimate_speed(self, obj_id, hist):
        if len(hist) < 5:
            return 0.0
        disp = self.get_displacement(hist[-5:])
        frames_delta = len(hist[-5:]) - 1
        fps = 30.0
        speed_kmh = (disp * PIXEL_TO_METERS * fps / frames_delta) * 3.6
        prev_speed = self.speeds.get(obj_id, speed_kmh)
        smoothed = 0.7 * prev_speed + 0.3 * speed_kmh
        self.speeds[obj_id] = smoothed
        return smoothed

    def check_line_crossing(self, obj_id, hist):
        if len(hist) < 2:
            return
        y_prev = hist[-2][1]
        y_curr = hist[-1][1]
        if y_prev < LINE_COUNT_Y <= y_curr:
            if obj_id not in self.crossed_in_ids:
                self.crossed_in_ids.add(obj_id)
                self.line_in_count += 1
        elif y_prev >= LINE_COUNT_Y > y_curr:
            if obj_id not in self.crossed_out_ids:
                self.crossed_out_ids.add(obj_id)
                self.line_out_count += 1

    def save_evidence_media(self, frame, description, severity, affected_boxes):
        os.makedirs(EVIDENCE_FOLDER, exist_ok=True)
        img = frame.copy()
        h, w = img.shape[:2]
        color_map = {
            'Low': (250, 150, 0),
            'Moderate': (0, 140, 250),
            'High': (50, 50, 250)
        }
        color = color_map.get(severity, (50, 50, 250))
        for box in affected_boxes:
            cv2.rectangle(img, (box[0], box[1]), (box[2], box[3]), color, 2)
            
        overlay = img.copy()
        cv2.rectangle(overlay, (0, 0), (w, 45), (15, 15, 15), -1)
        cv2.addWeighted(overlay, 0.7, img, 0.3, 0, img)
        
        time_str = datetime.now().strftime("%H:%M:%S")
        text = f"[{severity.upper()} ALERT] {description} ({time_str})"
        cv2.putText(img, text, (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rand_id = random.randint(1000, 9999)
        filename = f"screenshot_{timestamp}_{rand_id}.jpg"
        filepath = os.path.join(EVIDENCE_FOLDER, filename)
        cv2.imwrite(filepath, img)
        self.cleanup_storage()
        return f"static/evidence/{filename}"

    def save_triple_screenshots(self, frame, description, severity, affected_boxes):
        os.makedirs(EVIDENCE_FOLDER, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rand_id = random.randint(1000, 9999)
        base_filename = f"screenshot_{timestamp}_{rand_id}"
        
        # 1. Pre-impact (24 frames prior)
        pre_frame = self.frame_buffer[-24].copy() if len(self.frame_buffer) >= 24 else (self.frame_buffer[0].copy() if self.frame_buffer else frame.copy())
        h, w = pre_frame.shape[:2]
        overlay_pre = pre_frame.copy()
        cv2.rectangle(overlay_pre, (0, 0), (w, 45), (15, 15, 15), -1)
        cv2.addWeighted(overlay_pre, 0.7, pre_frame, 0.3, 0, pre_frame)
        cv2.putText(pre_frame, f"[PRE-IMPACT] {description}", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        cv2.imwrite(os.path.join(EVIDENCE_FOLDER, f"{base_filename}_pre.jpg"), pre_frame)
        
        # 2. Impact frame
        imp_frame = frame.copy()
        color = (50, 50, 250)
        for box in affected_boxes:
            cv2.rectangle(imp_frame, (box[0], box[1]), (box[2], box[3]), color, 2)
        overlay_imp = imp_frame.copy()
        cv2.rectangle(overlay_imp, (0, 0), (w, 45), (15, 15, 15), -1)
        cv2.addWeighted(overlay_imp, 0.7, imp_frame, 0.3, 0, imp_frame)
        cv2.putText(imp_frame, f"[COLLISION] {description}", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        primary_filepath = os.path.join(EVIDENCE_FOLDER, f"{base_filename}.jpg")
        cv2.imwrite(primary_filepath, imp_frame)
        
        # 3. Queue post-impact save (12 frames later)
        self.pending_post_impact_saves.append({
            'countdown': 12,
            'filepath': os.path.join(EVIDENCE_FOLDER, f"{base_filename}_post.jpg"),
            'description': description,
            'affected_boxes': affected_boxes
        })
        
        self.cleanup_storage()
        return f"static/evidence/{base_filename}.jpg"

    def analyze_frame(self, frame, detections):
        self.frame_buffer.append(frame.copy())
        rects = [d['box'] for d in detections]
        class_ids = [d['class_id'] for d in detections]
        
        self.tracker.update(rects, class_ids)
        
        incidents = []
        active_ids = list(self.tracker.objects.keys())
        vehicle_classes = {2, 3, 5, 7} # car, motorcycle, bus, truck
        
        # Line crossings, speeds and speed histories update
        for obj_id in active_ids:
            hist = self.tracker.history[obj_id]
            cls_id = self.tracker.classes[obj_id]
            self.check_line_crossing(obj_id, hist)
            
            if cls_id in vehicle_classes and len(hist) >= 5:
                self.estimate_speed(obj_id, hist)
                
            if obj_id not in self.vehicle_speeds_history:
                self.vehicle_speeds_history[obj_id] = []
            curr_speed = self.speeds.get(obj_id, 0.0)
            self.vehicle_speeds_history[obj_id].append(curr_speed)
            if len(self.vehicle_speeds_history[obj_id]) > 60:
                self.vehicle_speeds_history[obj_id].pop(0)
                
        # Clean inactive objects speed cache
        for oid in list(self.vehicle_speeds_history.keys()):
            if oid not in active_ids:
                del self.vehicle_speeds_history[oid]

        # Check Pedestrian-Vehicle Proximity Warning (Low Severity)
        for pid in active_ids:
            if self.tracker.classes[pid] == 0:
                for vid in active_ids:
                    if self.tracker.classes[vid] in vehicle_classes:
                        c_p = self.tracker.objects[pid]
                        c_v = self.tracker.objects[vid]
                        dist = math.sqrt((c_p[0] - c_v[0])**2 + (c_p[1] - c_v[1])**2)
                        if dist < PROXIMITY_ALERT_DISTANCE:
                            key = f"proximity_{pid}_{vid}"
                            if self._is_cooled_down(key, seconds=15.0):
                                cx = int((c_p[0] + c_v[0]) / 2.0)
                                cy = int((c_p[1] + c_v[1]) / 2.0)
                                if not self.deduplicator.check_duplicate('Proximity Warning', (cx, cy), [pid, vid]):
                                    desc = f"Proximity Warning: Pedestrian (ID {pid}) and Vehicle (ID {vid}) in close proximity ({dist:.1f}px)."
                                    bbox_p = self.tracker.history[pid][-1][2]
                                    bbox_v = self.tracker.history[vid][-1][2]
                                    path = self.save_evidence_media(frame, desc, 'Low', [bbox_p, bbox_v])
                                    incidents.append({
                                        'incident_type': 'Proximity Warning',
                                        'severity': 'Low',
                                        'description': desc,
                                        'screenshot_path': path,
                                        'confidence_score': 0.70
                                    })

        # Check Vehicle Collisions & Severity Heuristics
        for i in range(len(active_ids)):
            for j in range(i + 1, len(active_ids)):
                id1 = active_ids[i]
                id2 = active_ids[j]
                cls1 = self.tracker.classes[id1]
                cls2 = self.tracker.classes[id2]
                
                if cls1 in vehicle_classes and cls2 in vehicle_classes:
                    hist1 = self.tracker.history[id1]
                    hist2 = self.tracker.history[id2]
                    
                    if len(hist1) > 0 and len(hist2) > 0:
                        bbox1 = hist1[-1][2]
                        bbox2 = hist2[-1][2]
                        iou = self.calculate_iou(bbox1, bbox2)
                        pair_key = (min(id1, id2), max(id1, id2))
                        
                        # Velocity calculation
                        def get_velocity_vector(hist):
                            if len(hist) < 2: return (0.0, 0.0)
                            p_prev, p_curr = hist[-2], hist[-1]
                            return ((p_curr[0]-p_prev[0])*30.0*PIXEL_TO_METERS, (p_curr[1]-p_prev[1])*30.0*PIXEL_TO_METERS)
                        vel1 = get_velocity_vector(hist1)
                        vel2 = get_velocity_vector(hist2)
                        v_rel = (vel1[0] - vel2[0], vel1[1] - vel2[1])
                        relative_speed = math.sqrt(v_rel[0]**2 + v_rel[1]**2) * 3.6
                        
                        # Check overlap & relative movement
                        if iou > 0.05 and (relative_speed > 5.0 or pair_key in self.collision_states):
                            if pair_key not in self.collision_states:
                                self.collision_states[pair_key] = {
                                    'overlap_frames': 0,
                                    'stationary_frames': 0,
                                    'pre_speeds': {
                                        id1: np.mean(self.vehicle_speeds_history[id1][:-5]) if len(self.vehicle_speeds_history[id1]) >= 6 else self.speeds.get(id1, 0.0),
                                        id2: np.mean(self.vehicle_speeds_history[id2][:-5]) if len(self.vehicle_speeds_history[id2]) >= 6 else self.speeds.get(id2, 0.0)
                                    },
                                    'pre_vels': {id1: vel1, id2: vel2},
                                    'consecutive_active': 0
                                }
                            state = self.collision_states[pair_key]
                            state['overlap_frames'] += 1
                            curr_speed1 = self.speeds.get(id1, 0.0)
                            curr_speed2 = self.speeds.get(id2, 0.0)
                            
                            # Cancel if normal speed resumed
                            if curr_speed1 > 15.0 and curr_speed2 > 15.0:
                                state['consecutive_active'] = 0
                                state['overlap_frames'] = 0
                                state['stationary_frames'] = 0
                                continue
                                
                            if curr_speed1 < 3.0 or curr_speed2 < 3.0:
                                state['stationary_frames'] += 1
                                
                            # 1. Speed Drop (25%)
                            v_pre1 = max(0.1, state['pre_speeds'][id1])
                            v_pre2 = max(0.1, state['pre_speeds'][id2])
                            drop1 = max(0.0, (v_pre1 - curr_speed1)/v_pre1 * 100.0) if v_pre1 > 5.0 else 0.0
                            drop2 = max(0.0, (v_pre2 - curr_speed2)/v_pre2 * 100.0) if v_pre2 > 5.0 else 0.0
                            speed_drop_score = max(0.0, min(100.0, max(drop1, drop2)))
                            
                            # 2. Direction Change (25%)
                            def get_heading_vector(hist, start_idx, end_idx):
                                if len(hist) < abs(start_idx): return (0.0, 0.0)
                                return (hist[end_idx][0]-hist[start_idx][0], hist[end_idx][1]-hist[start_idx][1])
                            h_pre1 = state['pre_vels'][id1]
                            h_post1 = get_heading_vector(hist1, -5, -1)
                            h_pre2 = state['pre_vels'][id2]
                            h_post2 = get_heading_vector(hist2, -5, -1)
                            
                            def get_angle_diff(v1, v2):
                                n1 = math.sqrt(v1[0]**2 + v1[1]**2)
                                n2 = math.sqrt(v2[0]**2 + v2[1]**2)
                                if n1 < 0.5 or n2 < 0.5: return 0.0
                                cos_t = (v1[0]*v2[0] + v1[1]*v2[1]) / (n1*n2)
                                cos_t = max(-1.0, min(1.0, cos_t))
                                return math.acos(cos_t) * 180.0 / math.pi
                            ang1 = get_angle_diff(h_pre1, h_post1)
                            ang2 = get_angle_diff(h_pre2, h_post2)
                            max_ang = max(ang1, ang2)
                            dir_change_score = min(100.0, (max_ang / 90.0) * 100.0)
                            
                            # 3. Vehicle Rotation (15%)
                            def get_rotation_angle(hist):
                                if len(hist) < 15: return 0.0
                                v_start = (hist[5][0]-hist[0][0], hist[5][1]-hist[0][1])
                                v_end = (hist[-1][0]-hist[-5][0], hist[-1][1]-hist[-5][1])
                                return get_angle_diff(v_start, v_end)
                            rot1 = get_rotation_angle(hist1)
                            rot2 = get_rotation_angle(hist2)
                            max_rot = max(rot1, rot2)
                            rotation_score = min(100.0, (max_rot / 45.0) * 100.0)
                            rotation_triggered = (max_rot >= 45.0)
                            
                            # 4. Road Departure (15%)
                            c1 = self.tracker.objects[id1]
                            c2 = self.tracker.objects[id2]
                            def check_road_departure(c):
                                return c[0] < 150 or c[0] > 490 or c[1] < 100 or c[1] > 300
                            road_dep1 = check_road_departure(c1)
                            road_dep2 = check_road_departure(c2)
                            road_departure_triggered = (road_dep1 or road_dep2)
                            road_departure_score = 100.0 if road_departure_triggered else 0.0
                            
                            # 5. Barrier Collision (guardrail, divider, structure)
                            def check_barrier_collision(c):
                                return c[0] < 160 or c[0] > 480 or (300 < c[0] < 340) or c[1] < 110 or c[1] > 290
                            barrier_coll1 = check_barrier_collision(c1)
                            barrier_coll2 = check_barrier_collision(c2)
                            barrier_collision_triggered = (barrier_coll1 or barrier_coll2)
                            
                            # 6. Debris & Smoke (10%)
                            def get_area_change(hist):
                                if len(hist) < 5: return 1.0
                                area_pre = (hist[0][2][2]-hist[0][2][0])*(hist[0][2][3]-hist[0][2][1])
                                area_curr = (hist[-1][2][2]-hist[-1][2][0])*(hist[-1][2][3]-hist[-1][2][1])
                                if area_pre <= 0: return 1.0
                                return area_curr / float(area_pre)
                            exp1 = get_area_change(hist1)
                            exp2 = get_area_change(hist2)
                            max_exp = max(exp1, exp2)
                            debris_triggered = (max_exp > 1.3)
                            dust_smoke_triggered = (max_exp > 1.4)
                            debris_score = min(100.0, (max_exp - 1.0) * 200.0) if max_exp > 1.0 else 0.0
                            
                            # 7. Stationary Duration (10%)
                            stationary_score = min(100.0, (state['stationary_frames']/12.0)*100.0)
                            
                            # Weighted Score
                            final_score = (
                                0.25 * speed_drop_score +
                                0.25 * dir_change_score +
                                0.15 * rotation_score +
                                0.15 * road_departure_score +
                                0.10 * debris_score +
                                0.10 * stationary_score
                            )
                            
                            # Overrides
                            escalate_high = False
                            reasons = []
                            if road_departure_triggered and barrier_collision_triggered:
                                escalate_high = True
                                reasons.append("Road departure + Barrier override")
                            elif road_departure_triggered:
                                escalate_high = True
                                reasons.append("Road departure")
                            elif barrier_collision_triggered:
                                escalate_high = True
                                reasons.append("Barrier collision")
                            if rotation_triggered:
                                escalate_high = True
                                reasons.append("Rotation > 45 deg")
                            if debris_triggered:
                                escalate_high = True
                                reasons.append("Debris scattering")
                            if ang1 > 30.0 and ang2 > 30.0:
                                escalate_high = True
                                reasons.append("Double sudden trajectory changes")
                            if dust_smoke_triggered:
                                escalate_high = True
                                reasons.append("Dust/smoke cloud")
                            if final_score >= 70.0:
                                escalate_high = True
                                reasons.append(f"Score {int(final_score)} >= 70")
                                
                            if final_score >= 40.0:
                                state['consecutive_active'] += 1
                            else:
                                state['consecutive_active'] = max(0, state['consecutive_active'] - 1)
                                
                            trigger_frames = 3 if (escalate_high or final_score >= 70.0) else 10
                            if state['consecutive_active'] >= trigger_frames:
                                key = f"collision_{min(id1, id2)}_{max(id1, id2)}"
                                if self._is_cooled_down(key, seconds=60.0):
                                    severity = 'High' if (escalate_high or final_score >= 70.0) else 'Moderate'
                                    desc = f"Vehicle Collision (Score: {int(final_score)}/100) - {', '.join(reasons)}"
                                    if severity == 'High':
                                        path = self.save_triple_screenshots(frame, desc, 'High', [bbox1, bbox2])
                                    else:
                                        path = self.save_evidence_media(frame, desc, 'Moderate', [bbox1, bbox2])
                                        
                                    incidents.append({
                                        'incident_type': 'Vehicle Collision',
                                        'severity': severity,
                                        'description': desc,
                                        'screenshot_path': path,
                                        'confidence_score': round(final_score / 100.0, 2)
                                    })
                        else:
                            if pair_key in self.collision_states:
                                del self.collision_states[pair_key]

        # Process queued post impact saves
        active_saves = []
        for save in self.pending_post_impact_saves:
            save['countdown'] -= 1
            if save['countdown'] <= 0:
                post_frame = frame.copy()
                h, w = post_frame.shape[:2]
                overlay_post = post_frame.copy()
                cv2.rectangle(overlay_post, (0, 0), (w, 45), (15, 15, 15), -1)
                cv2.addWeighted(overlay_post, 0.7, post_frame, 0.3, 0, post_frame)
                cv2.putText(post_frame, f"[POST-IMPACT] {save['description']}", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
                cv2.imwrite(save['filepath'], post_frame)
            else:
                active_saves.append(save)
        self.pending_post_impact_saves = active_saves
        
        return incidents
