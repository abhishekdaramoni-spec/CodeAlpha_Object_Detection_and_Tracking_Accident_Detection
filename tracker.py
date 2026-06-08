import numpy as np
from deep_sort_realtime.deepsort_tracker import DeepSort

class CentroidTracker:
    def __init__(self, max_disappeared=10, max_distance=60):
        self.next_obj_id = 0
        self.objects = {}       # obj_id -> centroid (cx, cy)
        self.disappeared = {}   # obj_id -> frame counter
        self.history = {}       # obj_id -> list of tuples (cx, cy, bbox, class_id)
        self.classes = {}       # obj_id -> class_id
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    def register(self, centroid, bbox, class_id):
        self.objects[self.next_obj_id] = centroid
        self.history[self.next_obj_id] = [(centroid[0], centroid[1], bbox, class_id)]
        self.classes[self.next_obj_id] = class_id
        self.disappeared[self.next_obj_id] = 0
        self.next_obj_id += 1

    def deregister(self, obj_id):
        if obj_id in self.objects:
            del self.objects[obj_id]
        if obj_id in self.disappeared:
            del self.disappeared[obj_id]
        if obj_id in self.history:
            del self.history[obj_id]
        if obj_id in self.classes:
            del self.classes[obj_id]

    def update(self, rects, class_ids):
        if len(rects) == 0:
            for obj_id in list(self.disappeared.keys()):
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self.deregister(obj_id)
            return self.objects

        input_centroids = np.zeros((len(rects), 2), dtype="int")
        for (i, (startX, startY, endX, endY)) in enumerate(rects):
            cX = int((startX + endX) / 2.0)
            cY = int((startY + endY) / 2.0)
            input_centroids[i] = (cX, cY)

        if len(self.objects) == 0:
            for i in range(len(input_centroids)):
                self.register(input_centroids[i], rects[i], class_ids[i])
        else:
            object_ids = list(self.objects.keys())
            object_centroids = np.array(list(self.objects.values()))

            D = np.linalg.norm(object_centroids[:, np.newaxis] - input_centroids, axis=2)

            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]

            used_rows = set()
            used_cols = set()

            for (row, col) in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue

                if D[row, col] > self.max_distance:
                    continue

                obj_id = object_ids[row]
                self.objects[obj_id] = input_centroids[col]
                self.disappeared[obj_id] = 0
                
                self.history[obj_id].append((input_centroids[col][0], input_centroids[col][1], rects[col], class_ids[col]))
                if len(self.history[obj_id]) > 60:
                    self.history[obj_id].pop(0)

                used_rows.add(row)
                used_cols.add(col)

            unused_rows = set(range(0, D.shape[0])).difference(used_rows)
            unused_cols = set(range(0, D.shape[1])).difference(used_cols)

            for row in unused_rows:
                obj_id = object_ids[row]
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self.deregister(obj_id)

            for col in unused_cols:
                self.register(input_centroids[col], rects[col], class_ids[col])

        return self.objects
