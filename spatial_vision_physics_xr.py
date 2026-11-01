"""
=========================================================================================
PART 2 OF 3: spatial_vision_physics_xr.py
SPATIAL OS v5.0 — COMPUTER VISION, GESTURE ENGINE 2.0, REAL PHYSICS & XR RENDERER
=========================================================================================
يعمل هذا القسم بسلاسة سواء تم دمجه في ملف واحد مع القسم الأول أو استيراده كوحدة مستقلة.
يغطي الأقسام الإلزامية التالية:
- القسم الخامس: الرؤية الحاسوبية والإدراك البصري (Hand Tracking, 1-Euro Filter, Optical Flow AR,
  Scene Change & Motion Detection, Document/Frame OCR Scanner, Quantum Ghost Prediction 150ms).
- القسم السادس: الواجهة المكانية والواقع المعزز XR (Holographic Glass Shader, HDR Bloom,
  Emitter Pedestals, Multi-Object Spatial Pinning, 3D Tube Air Sculptor, Tesla XR & Apex Brain).
- اختبارات التحقق الآلية الخاصة بمحركات الفيزياء والرؤية والهندسة الفراغية.
=========================================================================================
"""

from collections import deque
from dataclasses import dataclass
import math
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np


# =====================================================================
# 1. إعدادات الرؤية الحاسوبية ومستويات الأداء (Vision & XR Config)
# =====================================================================
@dataclass
class XRQualityProfile:
    name: str
    max_particles: int
    render_glass_shader: bool
    enable_hdr_bloom: bool
    lightning_branches: int
    sphere_segments: Tuple[int, int]


class SpatialXRConfig:
    CAMERA_WIDTH = 1280
    CAMERA_HEIGHT = 720
    TARGET_FPS = 60
    MODEL_PATH = "hand_landmarker.task"
    MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"

    QUALITY_PROFILES = {
        1: XRQualityProfile("LOW (Performance)", 80, False, False, 1, (6, 10)),
        2: XRQualityProfile("MEDIUM (Balanced)", 180, True, True, 3, (8, 14)),
        3: XRQualityProfile("HIGH (AAA Cinema)", 350, True, True, 5, (10, 18)),
    }

    HAND_BONES = [
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (5, 9), (9, 10), (10, 11), (11, 12),
        (9, 13), (13, 14), (14, 15), (15, 16),
        (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)
    ]


# =====================================================================
# 2. الفلاتر الرياضية، التوقع الشبحي، وتثبيت الواقع المختلط (Tracking & AR)
# =====================================================================
class OneEuroFilter:
    """فلتر (1€ Filter) القياسي لتنعيم إحداثيات التتبع دون تأخير (Zero-Lag Smoothing)"""
    def __init__(self, min_cutoff: float = 1.3, beta: float = 0.045, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev: Optional[float] = None
        self.dx_prev: float = 0.0
        self.t_prev: Optional[float] = None

    def _alpha(self, cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / max(1e-5, dt))

    def filter(self, x: float, t: float) -> float:
        if self.t_prev is None or self.x_prev is None:
            self.x_prev, self.t_prev = x, t
            return x
        dt = max(1e-4, t - self.t_prev)
        self.t_prev = t
        dx = (x - self.x_prev) / dt
        edx = self._alpha(self.d_cutoff, dt) * dx + (1.0 - self._alpha(self.d_cutoff, dt)) * self.dx_prev
        self.dx_prev = edx
        cutoff = self.min_cutoff + self.beta * abs(edx)
        a = self._alpha(cutoff, dt)
        self.x_prev = a * x + (1.0 - a) * self.x_prev
        return self.x_prev


class QuantumGhostPredictor:
    """
    ميزة Quantum Ghost Prediction:
    تحلل آخر 20 إطاراً لحركة اليد وتتوقع الموقع والزاوية بعد 150ms في المستقبل.
    """
    def __init__(self, history_len: int = 20, lookahead_sec: float = 0.150):
        self.history: deque = deque(maxlen=history_len)
        self.lookahead = lookahead_sec
        self.enabled = True

    def update(self, x: float, y: float, rx: float, ry: float):
        self.history.append((x, y, rx, ry, time.time()))

    def predict(self, w: int, h: int) -> Optional[Tuple[float, float, float, float]]:
        if not self.enabled or len(self.history) < 5:
            return None
        x_now, y_now, rx_now, ry_now, t_now = self.history[-1]
        x_old, y_old, rx_old, ry_old, t_old = self.history[0]
        dt = max(0.015, t_now - t_old)
        vx, vy = (x_now - x_old) / dt, (y_now - y_old) / dt
        if math.hypot(vx, vy) < 35.0:
            return None
        px = max(60.0, min(w - 60.0, x_now + vx * self.lookahead))
        py = max(60.0, min(h - 60.0, y_now + vy * self.lookahead))
        prx = rx_now + ((rx_now - rx_old) / dt) * self.lookahead
        pry = ry_now + ((ry_now - ry_old) / dt) * self.lookahead
        return px, py, prx, pry


class EnhancedARAnchor:
    """
    تثبيت الواقع المختلط (AR World Anchor):
    يستخدم التدفق البصري (Lucas-Kanade Optical Flow) مع إعادة اكتشاف النقاط تلقائياً
    وتقدير زاوية دوران الكاميرا لتثبيت المجسمات فوق المكتب أو الغرفة.
    """
    def __init__(self):
        self.enabled = False
        self.prev_gray: Optional[np.ndarray] = None
        self.pts: Optional[np.ndarray] = None

    def _detect_features(self, gray: np.ndarray, cx: float, cy: float) -> np.ndarray:
        mask = np.zeros_like(gray)
        h, w = gray.shape
        x1, y1 = max(0, int(cx - 240)), max(0, int(cy - 240))
        x2, y2 = min(w, int(cx + 240)), min(h, int(cy + 240))
        mask[y1:y2, x1:x2] = 255
        feat = cv2.goodFeaturesToTrack(gray, maxCorners=45, qualityLevel=0.05, minDistance=12, mask=mask)
        if feat is None or len(feat) < 6:
            grid = [[cx + dx, cy + dy] for dx in (-45, 0, 45) for dy in (-45, 0, 45)]
            feat = np.array(grid, dtype=np.float32).reshape(-1, 1, 2)
        return feat

    def toggle(self, gray: np.ndarray, cx: float, cy: float) -> bool:
        self.enabled = not self.enabled
        if self.enabled:
            self.prev_gray = gray.copy()
            self.pts = self._detect_features(gray, cx, cy)
        return self.enabled

    def update(self, gray: np.ndarray, cx: float, cy: float) -> Tuple[float, float, float]:
        if not self.enabled or self.prev_gray is None:
            return 0.0, 0.0, 0.0
        if self.pts is None or len(self.pts) < 6:
            self.pts = self._detect_features(gray, cx, cy)

        new_pts, status, _ = cv2.calcOpticalFlowPyrLK(
            self.prev_gray, gray, self.pts, None, winSize=(21, 21), maxLevel=3
        )
        self.prev_gray = gray.copy()

        if new_pts is not None and status is not None:
            good_new = new_pts[status == 1]
            good_old = self.pts[status == 1]
            if len(good_new) >= 4:
                shift = np.mean(good_new - good_old, axis=0)
                d_rot = 0.0
                m, _ = cv2.estimateAffinePartial2D(good_old, good_new)
                if m is not None:
                    d_rot = math.atan2(m[1, 0], m[0, 0])
                self.pts = good_new.reshape(-1, 1, 2)
                if len(self.pts) < 8:
                    self.pts = self._detect_features(gray, cx + float(shift[0]), cy + float(shift[1]))
                return float(shift[0]), float(shift[1]), d_rot

        self.pts = self._detect_features(gray, cx, cy)
        return 0.0, 0.0, 0.0


# =====================================================================
# 3. الإدراك البصري الشامل، كشف الحركة، والـ OCR (Scene & Vision Analyzer)
# =====================================================================
class ScenePerceptionAnalyzer:
    """
    القسم الخامس: الرؤية الحاسوبية والإدراك البصري.
    - يكشف نسبة الحركة وتغيرات المشهد (Motion & Scene Change Detection).
    - يحلل الإضاءة والتباين ويحدد مناطق الأجسام النشطة (Active Motion Bounding Boxes).
    - يدعم التعرف الضوئي على النصوص (OCR) عند طلب المستخدم (عبر pytesseract إن توفر، أو كشف مناطق النصوص).
    - يولّد وصفاً نصياً صادقاً للمشهد مع درجة الثقة دون ادعاء التعرف على هويات الأشخاص.
    """
    def __init__(self):
        self.prev_small_gray: Optional[np.ndarray] = None
        self.motion_percentage: float = 0.0
        self.brightness_level: float = 0.0
        self.motion_bboxes: List[Tuple[int, int, int, int]] = []
        self.last_ocr_text: str = "Press [O] to scan scene text / OCR"

    def analyze_frame(self, frame_gray: np.ndarray, hands_count: int, active_gesture: str) -> Dict[str, Any]:
        small = cv2.resize(frame_gray, (320, 180))
        self.brightness_level = round(float(np.mean(small)) / 255.0 * 100.0, 1)
        self.motion_bboxes.clear()

        if self.prev_small_gray is not None:
            diff = cv2.absdiff(small, self.prev_small_gray)
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
            changed_pixels = int(np.count_nonzero(thresh))
            self.motion_percentage = round((changed_pixels / (320.0 * 180.0)) * 100.0, 2)

            # استخراج حدود المناطق المتحركة (Motion Object Bounding Boxes)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            scale_x, scale_y = frame_gray.shape[1] / 320.0, frame_gray.shape[0] / 180.0
            for cnt in contours:
                if cv2.contourArea(cnt) > 140:
                    x, y, w_c, h_c = cv2.boundingRect(cnt)
                    self.motion_bboxes.append((int(x * scale_x), int(y * scale_y), int(w_c * scale_x), int(h_c * scale_y)))

        self.prev_small_gray = small

        desc = (
            f"Scene Illumination: {self.brightness_level}% | Motion Activity: {self.motion_percentage}% | "
            f"Tracked Hands: {hands_count} (Gesture: {active_gesture})"
        )
        return {
            "brightness_pct": self.brightness_level,
            "motion_pct": self.motion_percentage,
            "motion_regions_count": len(self.motion_bboxes),
            "hands_detected": hands_count,
            "scene_description": desc,
            "identity_disclaimer": "System tracks geometric landmarks and motion only; biometric identity recognition is disabled."
        }

    def run_ocr_on_frame(self, frame_bgr: np.ndarray) -> str:
        """يحاول قراءة النصوص من إطار الكاميرا عبر pytesseract أو يكشف الكتل النصية عالية التباين"""
        try:
            import pytesseract
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            txt = pytesseract.image_to_string(rgb).strip()
            if txt:
                clean = " ".join(txt.split())[:85]
                self.last_ocr_text = f"OCR READ: '{clean}'"
                return self.last_ocr_text
        except Exception:
            pass

        # في حال عدم تثبيت محرك Tesseract الخارجي، نفحص مناطق الحواف النصية عالية الكثافة بصدق
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 200)
        edge_density = round((np.count_nonzero(edges) / float(gray.size)) * 100.0, 1)
        self.last_ocr_text = (
            f"OCR SCAN: Detected {edge_density}% high-contrast glyph edges "
            f"(Install optional 'pytesseract' for full character decoding)."
        )
        return self.last_ocr_text


class HandTrackerModule:
    """متعقب مفاصل اليدين الـ 21 مع تقدير المسافة التقريبية من الكاميرا (Depth/Distance Estimation)"""
    def __init__(self):
        self.camera_ready = True
        if not os.path.exists(SpatialXRConfig.MODEL_PATH):
            try:
                print("[INFO] Downloading MediaPipe Hand Landmarker Model...")
                urllib.request.urlretrieve(SpatialXRConfig.MODEL_URL, SpatialXRConfig.MODEL_PATH)
            except Exception as e:
                print(f"[WARNING] Could not download hand model ({e}). Running in headless/fallback mode.")
                self.camera_ready = False

        self.detector = None
        if os.path.exists(SpatialXRConfig.MODEL_PATH):
            base_options = python.BaseOptions(model_asset_path=SpatialXRConfig.MODEL_PATH)
            options = vision.HandLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,
                num_hands=2,
                min_hand_detection_confidence=0.55,
                min_hand_presence_confidence=0.55,
                min_tracking_confidence=0.55,
            )
            self.detector = vision.HandLandmarker.create_from_options(options)

        self.start_time = time.time()
        self.filters = {0: (OneEuroFilter(), OneEuroFilter()), 1: (OneEuroFilter(), OneEuroFilter())}

    def process_frame(self, frame_rgb: np.ndarray, w: int, h: int) -> List[Dict[str, Any]]:
        if self.detector is None:
            return []
        now = time.time()
        ts_ms = max(1, int((now - self.start_time) * 1000))
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        res = self.detector.detect_for_video(mp_img, ts_ms)

        hands_data = []
        if res.hand_landmarks:
            for idx, hand_lms in enumerate(res.hand_landmarks):
                pts = [(int(lm.x * w), int(lm.y * h)) for lm in hand_lms]
                wrist, middle_mcp = pts[0], pts[9]
                fx, fy = self.filters[idx % 2]
                smooth_px = int(fx.filter((wrist[0] + middle_mcp[0]) * 0.5, now))
                smooth_py = int(fy.filter((wrist[1] + middle_mcp[1]) * 0.5, now))
                hand_size = max(35.0, math.hypot(middle_mcp[0] - wrist[0], middle_mcp[1] - wrist[1]))
                roll = math.atan2(middle_mcp[1] - wrist[1], middle_mcp[0] - wrist[0])

                # تقدير المسافة التقريبية بالسنتيمتر بناءً على الحجم الظاهري لكف اليد
                est_distance_cm = round(max(18.0, min(120.0, 4800.0 / hand_size)), 1)

                hands_data.append({
                    "id": idx,
                    "pts": pts,
                    "palm": (smooth_px, smooth_py),
                    "size": hand_size,
                    "roll": roll,
                    "est_distance_cm": est_distance_cm,
                    "fingertips": [pts[4], pts[8], pts[12], pts[16], pts[20]],
                })
        return hands_data


# =====================================================================
# 4. Gesture Engine 2.0 + Custom AI Pose Learner
# =====================================================================
class CustomGestureLearner:
    """تعلم وتصنيف الإيماءات الشخصية مع حساب درجة الثقة (Cosine Similarity Confidence)"""
    def __init__(self):
        self.templates: Dict[str, np.ndarray] = {}

    def extract_embedding(self, h_data: Dict[str, Any]) -> np.ndarray:
        pts, wrist, size = h_data["pts"], h_data["pts"][0], h_data["size"]
        dists = [math.hypot(p[0] - wrist[0], p[1] - wrist[1]) / size for p in pts[1:]]
        cross = [
            math.hypot(pts[4][0] - pts[8][0], pts[4][1] - pts[8][1]) / size,
            math.hypot(pts[8][0] - pts[12][0], pts[8][1] - pts[12][1]) / size,
            math.hypot(pts[12][0] - pts[16][0], pts[12][1] - pts[16][1]) / size,
            math.hypot(pts[16][0] - pts[20][0], pts[16][1] - pts[20][1]) / size,
            math.hypot(pts[4][0] - pts[20][0], pts[4][1] - pts[20][1]) / size,
        ]
        vec = np.array(dists + cross, dtype=np.float32)
        return vec / max(1e-6, float(np.linalg.norm(vec)))

    def register_gesture(self, h_data: Dict[str, Any]) -> str:
        name = f"CUSTOM_POSE_{len(self.templates) + 1}"
        self.templates[name] = self.extract_embedding(h_data)
        return name

    def match(self, h_data: Dict[str, Any]) -> Tuple[Optional[str], float]:
        if not self.templates:
            return None, 0.0
        vec = self.extract_embedding(h_data)
        best_name, best_score = None, 0.0
        for name, tmpl in self.templates.items():
            sim = max(0.0, 1.0 - float(np.linalg.norm(vec - tmpl)) * 1.35)
            if sim > best_score:
                best_score, best_name = sim, name
        return (best_name, best_score) if best_score >= 0.84 else (None, best_score)


class GestureEngineV2:
    """
    محرك الإيماءات المتسلسل (Gesture Engine 2.0):
    يفهم تسلسل الحالات: HOVER -> SELECT -> GRAB -> ROTATE -> SCALE -> RELEASE (+ DUAL_SPLIT)
    """
    def __init__(self):
        self.current_state = "HOVER"
        self.previous_state = "HOVER"
        self.confidence = 1.0
        self.state_history: deque = deque(maxlen=4)
        self.custom_learner = CustomGestureLearner()
        self.prev_roll: Optional[float] = None
        self.selected_lock = False
        self.is_fist_active = False

    def classify_single_hand(self, h_data: Dict[str, Any]) -> Tuple[str, float]:
        c_name, c_score = self.custom_learner.match(h_data)
        if c_name is not None:
            return c_name, c_score

        pts, wrist, size = h_data["pts"], h_data["pts"][0], h_data["size"]
        pinch_ratio = math.hypot(pts[8][0] - pts[4][0], pts[8][1] - pts[4][1]) / size
        tips, pips = [8, 12, 16, 20], [6, 10, 14, 18]
        ext_flags = [
            math.hypot(pts[t][0] - wrist[0], pts[t][1] - wrist[1])
            > math.hypot(pts[p][0] - wrist[0], pts[p][1] - wrist[1]) * 1.14
            for t, p in zip(tips, pips)
        ]
        ext_count = sum(ext_flags)

        roll_delta = abs(h_data["roll"] - self.prev_roll) if self.prev_roll is not None else 0.0
        self.prev_roll = h_data["roll"]
        self.is_fist_active = (ext_count == 0 and pinch_ratio >= 0.44)

        if ext_flags[0] and sum(ext_flags[1:]) == 0 and pinch_ratio >= 0.45:
            self.selected_lock = True
            return "SELECT", 0.94

        if pinch_ratio < 0.45 or (self.selected_lock and ext_count == 0):
            return "GRAB", max(0.75, min(0.99, 1.0 - pinch_ratio * 0.5))

        if ext_count == 0:
            return "ROTATE", 0.95

        if ext_count >= 3:
            self.selected_lock = False
            if roll_delta > 0.085:
                return "ROTATE", 0.92
            return "RELEASE", min(0.98, 0.72 + ext_count * 0.06)

        return "HOVER", 0.85

    def update(self, hands: List[Dict[str, Any]]) -> Tuple[str, float]:
        self.previous_state = self.current_state
        if not hands:
            self.is_fist_active = False
            raw_state, conf = "HOVER", 1.0
        elif len(hands) == 2:
            g1, c1 = self.classify_single_hand(hands[0])
            g2, c2 = self.classify_single_hand(hands[1])
            if g1 == "GRAB" and g2 == "GRAB":
                raw_state, conf = "SCALE", (c1 + c2) * 0.5
            elif g1 == "RELEASE" and g2 == "RELEASE":
                raw_state, conf = "DUAL_SPLIT", (c1 + c2) * 0.5
            else:
                raw_state, conf = "SCALE", 0.90
        else:
            raw_state, conf = self.classify_single_hand(hands[0])

        self.state_history.append(raw_state)
        if self.state_history.count(raw_state) >= 2:
            self.current_state, self.confidence = raw_state, conf
        return self.current_state, self.confidence


# =====================================================================
# 5. نظام التثبيت المكاني للمجسمات (Multi-Object Spatial Pinning System)
# =====================================================================
@dataclass
class PinnedHologramObject:
    model_name: str
    pos_x: float
    pos_y: float
    scale: float
    rot_x: float
    rot_y: float
    rot_z: float
    vel_x: float
    vel_y: float
    color: Tuple[int, int, int]
    show_pedestal: bool


class SpatialPinManager:
    """يتيح تثبيت عدة مجسمات ثلاثية الأبعاد في الفضاء [F / SPACE] أو قفل المجسم النشط [L]"""
    def __init__(self):
        self.pinned_objects: List[PinnedHologramObject] = []
        self.active_locked = False

    def pin_current_object(
        self, model_name: str, x: float, y: float, sc: float,
        rx: float, ry: float, rz: float, vx: float, vy: float,
        col: Tuple[int, int, int], pedestal: bool
    ) -> str:
        for idx, pobj in enumerate(self.pinned_objects):
            if math.hypot(pobj.pos_x - x, pobj.pos_y - y) < 65.0 and pobj.model_name == model_name:
                self.pinned_objects.pop(idx)
                return f"UNPINNED '{model_name}' FROM SPACE!"

        self.pinned_objects.append(PinnedHologramObject(
            model_name=model_name, pos_x=x, pos_y=y, scale=sc,
            rot_x=rx, rot_y=ry, rot_z=rz, vel_x=vx * 0.4, vel_y=vy * 0.4,
            color=col, show_pedestal=pedestal
        ))
        return f"PINNED '{model_name}' AT ({int(x)}, {int(y)})! Total Pinned: {len(self.pinned_objects)}"

    def toggle_active_lock(self) -> str:
        self.active_locked = not self.active_locked
        return "ACTIVE OBJECT LOCKED IN PLACE!" if self.active_locked else "ACTIVE OBJECT UNLOCKED!"

    def clear_all_pins(self) -> str:
        cnt = len(self.pinned_objects)
        self.pinned_objects.clear()
        self.active_locked = False
        return f"CLEARED ALL {cnt} PINNED HOLOGRAMS!"

    def step_and_shift(self, dt: float, dx_ar: float, dy_ar: float, drot_ar: float):
        for pobj in self.pinned_objects:
            pobj.pos_x += dx_ar
            pobj.pos_y += dy_ar
            pobj.rot_z += drot_ar
            pobj.rot_x += pobj.vel_x * dt
            pobj.rot_y += pobj.vel_y * dt


# =====================================================================
# 6. محرك الفيزياء الحقيقي + التسجيل الزمني + التراجع (Physics & Timeline)
# =====================================================================
@dataclass
class SceneStateSnapshot:
    model_idx: int
    pos_x: float
    pos_y: float
    scale: float
    rot_x: float
    rot_y: float
    rot_z: float
    explode_f: float
    gravity_f: float


class MotionRecorder:
    def __init__(self, max_frames: int = 600):
        self.frames: List[SceneStateSnapshot] = []
        self.max_frames = max_frames
        self.is_recording = False
        self.is_playing = False
        self.play_cursor = 0.0
        self.play_speed = 1.0

    def toggle_record(self):
        if self.is_recording:
            self.is_recording = False
        else:
            self.frames.clear()
            self.is_recording, self.is_playing = True, False

    def toggle_playback(self):
        if not self.frames:
            return
        self.is_recording = False
        self.is_playing = not self.is_playing
        if self.is_playing and self.play_cursor >= len(self.frames) - 1:
            self.play_cursor = 0.0

    def record_step(self, snap: SceneStateSnapshot):
        if self.is_recording:
            if len(self.frames) < self.max_frames:
                self.frames.append(snap)
            else:
                self.is_recording = False

    def get_playback_step(self) -> Optional[SceneStateSnapshot]:
        if not self.is_playing or not self.frames:
            return None
        idx = int(self.play_cursor) % len(self.frames)
        snap = self.frames[idx]
        self.play_cursor = (self.play_cursor + self.play_speed) % len(self.frames)
        return snap


class StateHistoryStack:
    def __init__(self, max_len: int = 30):
        self.undo_stack: deque = deque(maxlen=max_len)
        self.redo_stack: deque = deque(maxlen=max_len)

    def push(self, snapshot: SceneStateSnapshot):
        self.undo_stack.append(snapshot)
        self.redo_stack.clear()

    def undo(self, current: SceneStateSnapshot) -> Optional[SceneStateSnapshot]:
        if not self.undo_stack:
            return None
        self.redo_stack.append(current)
        return self.undo_stack.pop()

    def redo(self, current: SceneStateSnapshot) -> Optional[SceneStateSnapshot]:
        if not self.redo_stack:
            return None
        self.undo_stack.append(current)
        return self.redo_stack.pop()


class SpatialPhysicsEngine:
    FIELD_MODES = ["HAND_ATTRACTOR (F=300/d)", "MAGNETIC_DIPOLE", "HARMONIC_WAVE"]

    def __init__(self, w: int, h: int):
        self.pos_x, self.pos_y = w / 2.0, h / 2.0 - 20.0
        self.target_x, self.target_y = w / 2.0, h / 2.0 - 20.0
        self.scale, self.target_scale = 135.0, 135.0
        self.rot_x, self.rot_y, self.rot_z = 0.4, 0.5, 0.0
        self.vel_x, self.vel_y, self.vel_z = 0.7, 1.3, 0.2
        self.explode_factor, self.target_explode = 0.0, 0.0
        self.gravity_well_factor, self.target_gravity = 0.0, 0.0
        self.was_black_hole = False
        self.energy_level = 0.0
        self.field_mode_idx = 0
        self.particles: List[List[Any]] = []
        self.shockwaves: List[List[Any]] = []
        self.last_pulse_time = time.time()
        self.fusion_cooldown = 0.0
        self.history = StateHistoryStack()
        self.recorder = MotionRecorder()

    def get_snapshot(self, model_idx: int) -> SceneStateSnapshot:
        return SceneStateSnapshot(
            model_idx, self.pos_x, self.pos_y, self.scale,
            self.rot_x, self.rot_y, self.rot_z,
            self.explode_factor, self.gravity_well_factor
        )

    def apply_snapshot(self, snap: SceneStateSnapshot) -> int:
        self.pos_x = self.target_x = snap.pos_x
        self.pos_y = self.target_y = snap.pos_y
        self.scale = self.target_scale = snap.scale
        self.rot_x, self.rot_y, self.rot_z = snap.rot_x, snap.rot_y, snap.rot_z
        self.explode_factor = self.target_explode = snap.explode_f
        self.gravity_well_factor = self.target_gravity = snap.gravity_f
        return snap.model_idx

    def spawn_particles(
        self, count: int, max_allowed: int, x: float, y: float,
        speed_range=(50, 220), color=(0, 255, 255), size_range=(1.5, 3.0)
    ):
        for _ in range(count):
            if len(self.particles) >= max_allowed:
                break
            ang = random.uniform(0, 2.0 * math.pi)
            spd = random.uniform(*speed_range)
            self.particles.append([
                x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                random.uniform(*size_range), random.uniform(1.2, 2.5), color
            ])

    def trigger_shockwave(
        self, x: float, y: float, max_r: float = 500.0,
        color=(255, 255, 255), thick: int = 3, speed: float = 680.0
    ):
        self.shockwaves.append([x, y, 15.0, float(max_r), speed, color, thick])
        for p in self.particles:
            dx, dy = p[0] - x, p[1] - y
            dist = max(10.0, math.hypot(dx, dy))
            impulse = (480.0 / dist) * 220.0
            p[2] += (dx / dist) * impulse
            p[3] += (dy / dist) * impulse

    def trigger_big_bang(self, max_particles: int):
        self.was_black_hole = False
        self.energy_level = 1.0
        self.trigger_shockwave(self.pos_x, self.pos_y, 650, (255, 255, 255), thick=6, speed=850.0)
        self.trigger_shockwave(self.pos_x, self.pos_y, 480, (0, 180, 255), thick=4, speed=620.0)
        self.spawn_particles(95, max_particles, self.pos_x, self.pos_y, (180, 620), (0, 230, 255), (1.8, 3.5))

    def trigger_nuclear_fusion(self, fx: float, fy: float, max_particles: int):
        self.target_x, self.target_y = fx, fy
        self.energy_level = 1.0
        self.fusion_cooldown = 0.8
        self.trigger_shockwave(fx, fy, 520, (255, 255, 255), thick=5, speed=780.0)
        self.trigger_shockwave(fx, fy, 380, (0, 255, 255), thick=3, speed=560.0)
        self.spawn_particles(75, max_particles, fx, fy, (150, 520), (0, 255, 255), (1.6, 3.2))

    def step(
        self, dt: float, max_particles: int, hand_pos: Optional[Tuple[int, int]],
        theme_color: Tuple[int, int, int], split_active: bool, locked: bool = False
    ):
        if self.fusion_cooldown > 0:
            self.fusion_cooldown = max(0.0, self.fusion_cooldown - dt)

        if not locked:
            lerp_rate = min(1.0, 12.0 * dt)
            self.pos_x += (self.target_x - self.pos_x) * lerp_rate
            self.pos_y += (self.target_y - self.pos_y) * lerp_rate
            self.scale += (self.target_scale - self.scale) * lerp_rate
            self.explode_factor += (self.target_explode - self.explode_factor) * min(1.0, 8.0 * dt)
            self.gravity_well_factor += (self.target_gravity - self.gravity_well_factor) * min(1.0, 9.0 * dt)

        self.rot_x += self.vel_x * dt
        self.rot_y += self.vel_y * dt
        self.rot_z += self.vel_z * dt

        damping = math.pow(0.18, dt)
        self.vel_x = self.vel_x * damping + 0.35 * (1.0 - damping)
        self.vel_y = self.vel_y * damping + 0.65 * (1.0 - damping)
        self.vel_z = self.vel_z * damping
        self.energy_level = max(0.0, self.energy_level - 0.35 * dt)

        now = time.time()
        pulse_interval = max(0.35, 1.35 - self.energy_level * 0.95)
        if now - self.last_pulse_time > pulse_interval and not split_active:
            self.last_pulse_time = now
            self.shockwaves.append([
                self.pos_x, self.pos_y, self.scale * 0.28,
                self.scale * (1.55 + self.energy_level),
                260.0 + self.energy_level * 220.0, theme_color, 2
            ])

        attr_x, attr_y = hand_pos if hand_pos is not None else (self.pos_x, self.pos_y)
        field_mode = self.FIELD_MODES[self.field_mode_idx]

        if len(self.particles) < max_particles // 3 and random.random() < 0.35:
            self.spawn_particles(
                1, max_particles,
                attr_x + random.uniform(-220, 220),
                attr_y + random.uniform(-220, 220),
                (15, 45), theme_color
            )

        for p in self.particles[:]:
            p[5] -= dt
            if p[5] <= 0:
                self.particles.remove(p)
                continue
            dx, dy = attr_x - p[0], attr_y - p[1]
            dist = math.hypot(dx, dy)

            if self.gravity_well_factor > 0.2:
                force = 18000.0 / (dist + 1.0)
                p[2] += ((dx / max(1.0, dist)) * force - (dy / max(1.0, dist)) * force * 0.45) * dt
                p[3] += ((dy / max(1.0, dist)) * force + (dx / max(1.0, dist)) * force * 0.45) * dt
                if dist < 24.0:
                    p[5] = 0.0
            elif field_mode == "HAND_ATTRACTOR (F=300/d)":
                force = (300.0 * 35.0) / (dist + 1.0)
                p[2] += (dx / max(1.0, dist)) * force * dt
                p[3] += (dy / max(1.0, dist)) * force * dt
                p[2] *= 0.96
                p[3] *= 0.96
            elif field_mode == "MAGNETIC_DIPOLE":
                theta = math.atan2(dy, dx) - self.rot_z
                b_r, b_theta = 2.0 * math.cos(theta), math.sin(theta)
                p[2] = p[2] * 0.92 + (b_r * math.cos(theta) - b_theta * math.sin(theta)) * 180.0 * dt * 8.0
                p[3] = p[3] * 0.92 + (b_r * math.sin(theta) + b_theta * math.cos(theta)) * 180.0 * dt * 8.0
            elif field_mode == "HARMONIC_WAVE":
                wf = math.sin(dist * 0.06 - now * 7.0) * 240.0
                p[2] += (dx / max(1.0, dist)) * wf * dt
                p[3] += (dy / max(1.0, dist)) * wf * dt

            p[0] += p[2] * dt
            p[1] += p[3] * dt

        for sw in self.shockwaves[:]:
            sw[2] += sw[4] * dt
            if sw[2] >= sw[3]:
                self.shockwaves.remove(sw)


# =====================================================================
# 7. مكتبة المجسمات الـ 9 + النحات الأنبوبي الثلاثي الأبعاد (3D Geometry)
# =====================================================================
class TubeAirSculptor:
    """يبني أنابيب ثلاثية الأبعاد (3D Cylindrical Tubes) على طول مسار إصبع المستخدم في الهواء"""
    def __init__(self):
        self.active = False
        self.trail: List[Tuple[float, float, float]] = []
        self.verts: List[Tuple[float, float, float, int]] = []
        self.edges: List[Tuple[int, int, int]] = []
        self.faces: List[Tuple[int, int, int]] = []

    def clear(self):
        self.trail.clear()
        self.verts.clear()
        self.edges.clear()
        self.faces.clear()

    def add_trail_point(self, sx: int, sy: int, cx: float, cy: float, sc: float, rx: float):
        nx, ny, nz = (sx - cx) / max(45.0, sc), (sy - cy) / max(45.0, sc), math.sin(rx) * 0.45
        if self.trail:
            px, py, pz = self.trail[-1]
            if math.dist((nx, ny, nz), (px, py, pz)) < 0.08:
                return
            nx, ny, nz = px * 0.35 + nx * 0.65, py * 0.35 + ny * 0.65, pz * 0.35 + nz * 0.65
        self.trail.append((nx, ny, nz))
        if len(self.trail) > 45:
            self.trail.pop(0)
        self._rebuild_3d_tube()

    def _rebuild_3d_tube(self):
        self.verts.clear()
        self.edges.clear()
        self.faces.clear()
        if len(self.trail) < 2:
            return
        sides, radius = 6, 0.11
        for i, (tx, ty, tz) in enumerate(self.trail):
            ring_base = i * sides
            for s in range(sides):
                ang = 2.0 * math.pi * s / sides
                self.verts.append((
                    tx + radius * math.cos(ang),
                    ty + radius * math.sin(ang),
                    tz + radius * math.sin(ang + 1.0) * 0.5,
                    1
                ))
                self.edges.append((ring_base + s, ring_base + (s + 1) % sides, 1))
                if i > 0:
                    prev_base = (i - 1) * sides
                    self.edges.append((prev_base + s, ring_base + s, 2))
                    if s % 2 == 0:
                        self.faces.append((prev_base + s, ring_base + s, ring_base + (s + 1) % sides))

    def get_mesh(self):
        if len(self.verts) < 6:
            return [(0.0, 0.0, 0.0, 0), (0.25, 0.0, 0.0, 1)], [(0, 1, 1)], []
        return self.verts, self.edges, self.faces


class MasterGeometryLibrary:
    @staticmethod
    def build_all(sphere_segs=(8, 14)) -> Dict[str, Tuple[List, List, List]]:
        models = {}

        # 1. QUANTUM CORE
        phi = (1.0 + math.sqrt(5.0)) / 2.0
        raw_v = [
            (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
            (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
            (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1)
        ]
        ico_v = [(x / 1.9, y / 1.9, z / 1.9, 1) for x, y, z in raw_v]
        ico_e, ico_f = [], []
        n = len(ico_v)
        for i in range(n):
            for j in range(i + 1, n):
                if 0.9 < math.dist(ico_v[i][:3], ico_v[j][:3]) < 1.2:
                    ico_e.append((i, j, 1))
                    for k in range(j + 1, n):
                        if 0.9 < math.dist(ico_v[j][:3], ico_v[k][:3]) < 1.2 and 0.9 < math.dist(ico_v[i][:3], ico_v[k][:3]) < 1.2:
                            ico_f.append((i, j, k))
        off = len(ico_v)
        core_v = [(0.45, 0, 0, 0), (-0.45, 0, 0, 0), (0, 0.45, 0, 0), (0, -0.45, 0, 0), (0, 0, 0.45, 0), (0, 0, -0.45, 0)]
        core_e = [(off + i, off + j, 0) for i in range(6) for j in range(i + 1, 6) if i // 2 != j // 2]
        ring_v, ring_e = [], []
        r_off = off + len(core_v)
        N_r = 20
        for i in range(N_r):
            a = 2.0 * math.pi * i / N_r
            ring_v.append((1.45 * math.cos(a), 1.45 * math.sin(a), 0.0, 2))
            ring_e.append((r_off + i, r_off + (i + 1) % N_r, 2))
        r_off2 = r_off + N_r
        for i in range(N_r):
            a = 2.0 * math.pi * i / N_r
            ring_v.append((1.65 * math.cos(a), 0.0, 1.65 * math.sin(a), 2))
            ring_e.append((r_off2 + i, r_off2 + (i + 1) % N_r, 2))
        models["QUANTUM CORE"] = (ico_v + core_v + ring_v, ico_e + core_e + ring_e, ico_f)

        # 2. ARC REACTOR
        ar_v, ar_e, ar_f = [], [], []
        for r_idx, rad in enumerate([0.35, 0.75, 1.15]):
            seg = 24
            base = len(ar_v)
            for i in range(seg):
                a = 2.0 * math.pi * i / seg
                z_off = 0.14 if r_idx == 1 else 0.0
                ar_v.append((rad * math.cos(a), rad * math.sin(a), z_off, r_idx))
                ar_e.append((base + i, base + (i + 1) % seg, r_idx))
        for i in range(0, 24, 2):
            ar_e.append((i, 24 + i, 1))
            ar_e.append((24 + i, 48 + i, 2))
            ar_f.append((24 + i, 24 + (i + 1) % 24, 48 + i))
        models["ARC REACTOR"] = (ar_v, ar_e, ar_f)

        # 3. HELMET ORB
        sp_v, sp_e, sp_f = [], [], []
        lats, lons = sphere_segs
        for i in range(lats):
            lat = math.pi * (-0.5 + float(i) / (lats - 1))
            for j in range(lons):
                lon = 2.0 * math.pi * float(j) / lons
                x, y, z = math.cos(lat) * math.cos(lon) * 1.15, math.sin(lat) * 1.22, math.cos(lat) * math.sin(lon) * 1.15
                layer = 0 if abs(y) < 0.30 else 1
                sp_v.append((x, y, z, layer))
                curr, nxt_j = i * lons + j, i * lons + (j + 1) % lons
                sp_e.append((curr, nxt_j, layer))
                if i < lats - 1:
                    nxt_i = (i + 1) * lons + j
                    sp_e.append((curr, nxt_i, layer))
                    if (i + j) % 2 == 0:
                        sp_f.append((curr, nxt_j, nxt_i))
        models["HELMET ORB"] = (sp_v, sp_e, sp_f)

        # 4. GENOME DNA
        dn_v, dn_e = [], []
        steps = 26
        for i in range(steps):
            y = (i / (steps - 1)) * 2.4 - 1.2
            a = i * 0.50
            dn_v.append((0.72 * math.cos(a), y, 0.72 * math.sin(a), 0))
            dn_v.append((-0.72 * math.cos(a), y, -0.72 * math.sin(a), 0))
            dn_v.append((0.35 * math.cos(a + 0.2), y, 0.35 * math.sin(a + 0.2), 1))
            dn_v.append((-0.35 * math.cos(a + 0.2), y, -0.35 * math.sin(a + 0.2), 1))
            b = i * 4
            dn_e.extend([(b, b + 2, 1), (b + 2, b + 3, 0), (b + 3, b + 1, 1)])
            if i > 0:
                dn_e.extend([(b - 4, b, 0), (b - 3, b + 1, 0)])
        models["GENOME DNA"] = (dn_v, dn_e, [])

        # 5. HYPER CUBE
        hc_v, hc_e, hc_f = [], [], []
        for layer, s in enumerate([0.5, 1.05]):
            base = len(hc_v)
            for z in (-s, s):
                for y in (-s, s):
                    for x in (-s, s):
                        hc_v.append((x, y, z, layer))
            for u, v in [(0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4), (0, 4), (1, 5), (2, 6), (3, 7)]:
                hc_e.append((base + u, base + v, layer))
            if layer == 1:
                for a, b, c, d in [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]:
                    hc_f.append((base + a, base + b, base + c))
                    hc_f.append((base + a, base + c, base + d))
                for k in range(8):
                    hc_e.append((k, base + k, 2))
        models["HYPER CUBE"] = (hc_v, hc_e, hc_f)

        # 6. NET TWIN
        nt_v = [(0.0, 0.0, 0.0, 0)]
        nt_e = []
        gateways = [(0.85, 0.5, 0.4), (-0.85, 0.5, -0.4), (0.85, -0.5, -0.4), (-0.85, -0.5, 0.4)]
        for idx, (gx, gy, gz) in enumerate(gateways, start=1):
            nt_v.append((gx, gy, gz, 1))
            nt_e.append((0, idx, 1))
        nt_e.extend([(1, 2, 1), (2, 4, 1), (4, 3, 1), (3, 1, 1)])
        for g_idx in range(1, 5):
            gx, gy, gz, _ = nt_v[g_idx]
            for k in range(3):
                ang = k * (2.0 * math.pi / 3.0) + g_idx
                nt_v.append((gx * 1.55 + 0.35 * math.cos(ang), gy * 1.55 + 0.35 * math.sin(ang), gz * 1.55, 2))
                nt_e.append((g_idx, len(nt_v) - 1, 2))
        models["NET TWIN"] = (nt_v, nt_e, [])

        # 7. GALAXY SPIRAL
        gal_v, gal_e = [(0.0, 0.0, 0.0, 0)], []
        for a in range(3):
            off_a = a * (2.0 * math.pi / 3.0)
            prev_i = 0
            for i in range(1, 15):
                r = (i / 14.0) * 1.55
                th = i * 0.45 + off_a
                gal_v.append((r * math.cos(th), math.sin(i * 0.6) * 0.18, r * math.sin(th), 1 if i < 8 else 2))
                gal_e.append((prev_i, len(gal_v) - 1, 1 if i < 8 else 2))
                prev_i = len(gal_v) - 1
        models["GALAXY SPIRAL"] = (gal_v, gal_e, [])

        # 8. FRACTAL PYRAMID
        fr_v, fr_e, fr_f = [], [], []
        top_tet = [(0, -1.1, 0), (-0.95, 0.75, 0.8), (0.95, 0.75, 0.8), (0, 0.75, -1.0)]
        for c_idx, (cx, cy, cz) in enumerate(top_tet):
            base = len(fr_v)
            for vx, vy, vz in top_tet:
                fr_v.append(((cx + vx) * 0.5, (cy + vy) * 0.5, (cz + vz) * 0.5, 1))
            for u, v in [(0, 1), (0, 2), (0, 3), (1, 2), (2, 3), (3, 1)]:
                fr_e.append((base + u, base + v, 1 if c_idx == 0 else 2))
            fr_f.extend([(base, base + 1, base + 2), (base, base + 2, base + 3), (base, base + 3, base + 1)])
        models["FRACTAL PYRAMID"] = (fr_v, fr_e, fr_f)

        # 9. TESLA ROADSTER
        tr_v = [
            (-1.1, 0.2, -0.5, 1), (1.1, 0.2, -0.5, 1), (1.1, 0.2, 0.5, 1), (-1.1, 0.2, 0.5, 1),
            (-1.2, -0.05, -0.55, 0), (1.2, -0.05, -0.55, 0), (1.2, -0.05, 0.55, 0), (-1.2, -0.05, 0.55, 0),
            (-0.45, -0.42, -0.42, 0), (0.35, -0.42, -0.42, 0), (0.35, -0.42, 0.42, 0), (-0.45, -0.42, 0.42, 0)
        ]
        tr_e = [
            (0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 0, 1), (4, 5, 0), (5, 6, 0), (6, 7, 0), (7, 4, 0),
            (0, 4, 1), (1, 5, 1), (2, 6, 1), (3, 7, 1), (8, 9, 0), (9, 10, 0), (10, 11, 0), (11, 8, 0),
            (4, 8, 0), (5, 9, 0), (6, 10, 0), (7, 11, 0)
        ]
        models["TESLA ROADSTER"] = (tr_v, tr_e, [(4, 5, 9), (4, 9, 8), (6, 7, 11), (6, 11, 10)])

        return models


# =====================================================================
# 8. المحرك الهولوغرافي الشامل + محاكي تسلا + عقل Apex AI
# =====================================================================
class OmniHologramRenderer:
    LIGHT_DIR = np.array([0.35, -0.45, 0.82], dtype=np.float32)

    @staticmethod
    def draw_lightning(canvas, glow, p1, p2, color, thickness=2, jag=12):
        pts = [p1]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        steps = max(3, min(9, int(math.hypot(dx, dy) // 30)))
        for i in range(1, steps):
            t = i / steps
            pts.append((int(p1[0] + dx * t + random.uniform(-jag, jag)), int(p1[1] + dy * t + random.uniform(-jag, jag))))
        pts.append(p2)
        for i in range(len(pts) - 1):
            cv2.line(glow, pts[i], pts[i + 1], color, thickness + 2, cv2.LINE_AA)
            cv2.line(canvas, pts[i], pts[i + 1], (255, 255, 255), 1, cv2.LINE_AA)

    @staticmethod
    def draw_emitter_pedestal(canvas, glow, cx: int, base_y: int, radius: int, label: str, active=True, color=(255, 235, 0)):
        col_bright = (255, 255, 210) if active else (190, 180, 120)
        cone_overlay = np.zeros_like(canvas)
        cone_pts = np.array([
            [cx - int(radius * 0.82), base_y],
            [cx + int(radius * 0.82), base_y],
            [cx + int(radius * 1.12), base_y - int(radius * 1.85)],
            [cx - int(radius * 1.12), base_y - int(radius * 1.85)]
        ], np.int32)
        cv2.fillPoly(cone_overlay, [cone_pts], (int(color[0] * 0.16), int(color[1] * 0.14), int(color[2] * 0.05)))
        cv2.add(canvas, cone_overlay, dst=canvas)

        for r_scale, thick in [(1.0, 2), (0.76, 1), (0.52, 2), (0.28, 1)]:
            rx = max(8, int(radius * r_scale))
            ry = max(3, int(radius * 0.25 * r_scale))
            cv2.ellipse(glow, (cx, base_y), (rx, ry), 0, 0, 360, color, thick + 1, cv2.LINE_AA)
            cv2.ellipse(canvas, (cx, base_y), (rx, ry), 0, 0, 360, col_bright, 1, cv2.LINE_AA)

        spaced_lbl = " ".join(list(label[:10]))
        tw = len(spaced_lbl) * 6
        cv2.putText(glow, spaced_lbl, (cx - tw // 2, base_y + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2, cv2.LINE_AA)
        cv2.putText(canvas, spaced_lbl, (cx - tw // 2, base_y + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

    @staticmethod
    def draw_pin_lock_brackets(canvas, glow, cx: int, cy: int, scale: float, label="PINNED IN SPACE"):
        r = int(scale * 1.15)
        b_len = max(14, int(r * 0.28))
        col = (0, 255, 140)
        corners = [
            (cx - r, cy - r, 1, 1),
            (cx + r, cy - r, -1, 1),
            (cx - r, cy + r, 1, -1),
            (cx + r, cy + r, -1, -1)
        ]
        for x, y, dx, dy in corners:
            cv2.line(glow, (x, y), (x + dx * b_len, y), col, 3, cv2.LINE_AA)
            cv2.line(glow, (x, y), (x, y + dy * b_len), col, 3, cv2.LINE_AA)
            cv2.line(canvas, (x, y), (x + dx * b_len, y), (220, 255, 220), 1, cv2.LINE_AA)
            cv2.line(canvas, (x, y), (x, y + dy * b_len), (220, 255, 220), 1, cv2.LINE_AA)
        cv2.putText(canvas, f"[LOCKED: {label}]", (cx - 65, cy - r - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1, cv2.LINE_AA)

    @staticmethod
    def draw_cyber_hand_armor(canvas, glow, pts, roll, state_name, conf, energy_pct, est_dist_cm=45.0, armor_style=True):
        wrist, palm = pts[0], ((pts[0][0] + pts[9][0]) // 2, (pts[0][1] + pts[9][1]) // 2)

        if armor_style:
            hull_idx = [0, 1, 2, 3, 4, 8, 12, 16, 20, 17]
            poly = np.array([pts[i] for i in hull_idx], np.int32)
            armor = np.zeros_like(canvas)
            cv2.fillPoly(armor, [poly], (42, 42, 32))
            cv2.add(canvas, armor, dst=canvas)
            cv2.polylines(glow, [poly], True, (200, 210, 180), 1, cv2.LINE_AA)

            bar_p1 = ((wrist[0] + pts[5][0]) // 2 + 10, (wrist[1] + pts[5][1]) // 2)
            bar_p2 = (pts[9][0] + 14, pts[9][1] - 28)
            cv2.line(glow, bar_p1, bar_p2, (255, 235, 60), 10, cv2.LINE_AA)
            cv2.line(canvas, bar_p1, bar_p2, (255, 255, 210), 5, cv2.LINE_AA)

        for u, v in SpatialXRConfig.HAND_BONES:
            cv2.line(glow, pts[u], pts[v], (255, 190, 0), 2, cv2.LINE_AA)
            cv2.line(canvas, pts[u], pts[v], (240, 255, 240), 1, cv2.LINE_AA)

        rad = 42
        deg = int(math.degrees(roll))
        cv2.circle(canvas, palm, rad, (255, 210, 0), 1, cv2.LINE_AA)
        cv2.ellipse(glow, palm, (rad + 6, rad + 6), 0, deg, deg + 75, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.ellipse(glow, palm, (rad + 6, rad + 6), 0, deg + 180, deg + 255, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, f"{state_name} ({int(conf*100)}%)", (palm[0] + rad + 10, palm[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)
        cv2.putText(canvas, f"DIST:~{est_dist_cm}cm | PWR:{energy_pct}%", (palm[0] + rad + 10, palm[1] + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 255, 255), 1)

    @staticmethod
    def render_mesh(
        canvas, glow, verts, edges, faces, cx, cy, sc, rx, ry, rz,
        exp_f=0.0, grav_f=0.0, color=(255, 230, 0), draw_glass=True,
        is_net_twin=False, is_ghost=False, finger_pts=None
    ):
        proj_pts = []
        rot_3d_pts = []
        fov = 4.2

        for vx, vy, vz, layer in verts:
            expansion = max(0.06, 1.0 - grav_f * 0.92) if grav_f > 0.05 else (1.0 + exp_f * (layer * 0.8 + 0.35))
            lx, ly, lz = vx * expansion, vy * expansion, vz * expansion

            ax = rx if is_net_twin else rx * (-1.3 if layer == 2 else (1.6 if layer == 0 else 1.0))
            ay = ry if is_net_twin else ry * (1.4 if layer == 2 else (-1.6 if layer == 0 else 1.0))
            az = rz + (grav_f * 4.0 * layer)

            cy_l, sy_l = math.cos(ay), math.sin(ay)
            cx_l, sx_l = math.cos(ax), math.sin(ax)
            cz_l, sz_l = math.cos(az), math.sin(az)

            x1, z1 = lx * cy_l + lz * sy_l, -lx * sy_l + lz * cy_l
            y2, z2 = ly * cx_l - z1 * sx_l, ly * sx_l + z1 * cx_l
            x3, y3 = x1 * cz_l - y2 * sz_l, x1 * sz_l + y2 * cz_l

            rot_3d_pts.append(np.array([x3, y3, z2], dtype=np.float32))
            z_p = fov / max(0.5, fov - z2)
            px = cx + x3 * sc * z_p
            py = cy + y3 * sc * z_p

            if finger_pts and not is_ghost:
                for fx, fy in finger_pts:
                    dx_f, dy_f = px - fx, py - fy
                    d_f = math.hypot(dx_f, dy_f)
                    if 1.0 < d_f < 85.0:
                        push = ((85.0 - d_f) / 85.0) * 24.0
                        px += (dx_f / d_f) * push
                        py += (dy_f / d_f) * push

            proj_pts.append((int(px), int(py), z2, layer))

        if is_ghost:
            for u, v, _ in edges:
                cv2.line(canvas, (proj_pts[u][0], proj_pts[u][1]), (proj_pts[v][0], proj_pts[v][1]), (150, 130, 0), 1, cv2.LINE_AA)
            return proj_pts

        if draw_glass and faces and grav_f < 0.7:
            glass = np.zeros_like(canvas)
            s_faces = sorted([((proj_pts[a][2] + proj_pts[b][2] + proj_pts[c][2]) / 3.0, a, b, c) for a, b, c in faces], key=lambda x: x[0])
            light_vec = OmniHologramRenderer.LIGHT_DIR
            for avg_z, a, b, c in s_faces:
                if avg_z > -0.45:
                    v0, v1, v2 = rot_3d_pts[a], rot_3d_pts[b], rot_3d_pts[c]
                    normal = np.cross(v1 - v0, v2 - v0)
                    norm_len = float(np.linalg.norm(normal))
                    if norm_len > 1e-6:
                        normal /= norm_len
                    brightness = max(0.2, abs(float(np.dot(normal, light_vec))))
                    depth_boost = max(0.3, min(1.1, (avg_z + 1.4) / 2.4))
                    sh = brightness * depth_boost * 0.55
                    f_col = (min(255, int(color[0] * sh)), min(255, int(color[1] * sh)), min(255, int(color[2] * sh)))
                    tri = np.array([[proj_pts[a][0], proj_pts[a][1]], [proj_pts[b][0], proj_pts[b][1]], [proj_pts[c][0], proj_pts[c][1]]], np.int32)
                    cv2.fillPoly(glass, [tri], f_col)
            cv2.add(canvas, glass, dst=canvas)

        s_edges = sorted([((proj_pts[u][2] + proj_pts[v][2]) * 0.5, u, v, l) for u, v, l in edges], key=lambda x: x[0])
        now_t = time.time() * 2.2
        for idx_e, (avg_z, u, v, layer) in enumerate(s_edges):
            d_norm = max(0.2, min(1.0, (avg_z + 1.8) / 3.6))
            th = 1 if d_norm < 0.55 else (2 if d_norm < 0.82 else 3)
            col = (255, 255, 255) if layer == 0 else ((int(255 * d_norm), int(150 * d_norm), int(255 * d_norm)) if layer == 2 else (int(color[0] * d_norm), int(color[1] * d_norm), int(color[2] * d_norm)))
            p1, p2 = (proj_pts[u][0], proj_pts[u][1]), (proj_pts[v][0], proj_pts[v][1])
            if d_norm > 0.5:
                cv2.line(glow, p1, p2, col, th + 1, cv2.LINE_AA)
            cv2.line(canvas, p1, p2, col, th, cv2.LINE_AA)

            if is_net_twin:
                pkt_t = (now_t + idx_e * 0.37) % 1.0
                pk_x = int(p1[0] + (p2[0] - p1[0]) * pkt_t)
                pk_y = int(p1[1] + (p2[1] - p1[1]) * pkt_t)
                cv2.circle(glow, (pk_x, pk_y), 5, (0, 255, 180), cv2.FILLED)
                cv2.circle(canvas, (pk_x, pk_y), 3, (255, 255, 255), cv2.FILLED)

        for idx_p, (px, py, z2, layer) in enumerate(proj_pts):
            if z2 > -0.4:
                r = 5 if layer == 0 else (3 if layer == 1 else 2)
                cv2.circle(glow, (px, py), r + 2, color, cv2.FILLED)
                cv2.circle(canvas, (px, py), r, (255, 255, 255), cv2.FILLED)
                if is_net_twin and idx_p < 5:
                    lbl = "CLOUD-CORE" if idx_p == 0 else f"SRV-0{idx_p}"
                    cv2.putText(canvas, lbl, (px + 8, py - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 200), 1)
        return proj_pts


class TeslaLandingSimXR:
    """محاكي هبوط تسلا بالمنحدر الشبكي ثلاثي الأبعاد وطاولة التكتيك المائلة"""
    def __init__(self, car_mesh):
        self.car_verts, self.car_edges, self.car_faces = car_mesh
        self.sim_progress = 0.0
        self.playing = True

    def render(self, canvas, glow, w: int, h: int, hands: list, dt: float):
        if self.playing:
            self.sim_progress = (self.sim_progress + dt * 0.22) % 1.0

        tx1, ty1, tx2, ty2 = 60, 175, 510, 355
        panel = np.zeros_like(canvas)
        cv2.rectangle(panel, (tx1, ty1), (tx2, ty2), (22, 26, 20), cv2.FILLED)
        cv2.rectangle(panel, (760, 110), (1230, 390), (20, 22, 20), cv2.FILLED)
        cv2.add(canvas, panel, dst=canvas)

        cv2.rectangle(canvas, (tx1, ty1), (tx2, ty2), (120, 140, 120), 1)
        cv2.putText(canvas, "TESLA CAR DEMO 01   APPROACH", (tx1 + 18, ty1 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 210, 200), 1)
        kmh = int((1.0 - self.sim_progress) * 240)
        g_force = round(0.61 + math.sin(self.sim_progress * 6.0) * 0.15, 2)
        cv2.putText(canvas, f"0        {kmh}.0        {g_force}G        12.0        0.0", (tx1 + 22, ty1 + 72), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (220, 240, 220), 1)

        for i in range(5):
            dx, dy = tx1 + 45 + i * 85, ty1 + 112
            pts_d = np.array([[dx, dy - 7], [dx + 7, dy], [dx, dy + 7], [dx - 7, dy]], np.int32)
            if i == int(self.sim_progress * 4.9):
                cv2.fillPoly(glow, [pts_d], (60, 60, 255))
                cv2.fillPoly(canvas, [pts_d], (100, 100, 255))
            else:
                cv2.polylines(canvas, [pts_d], True, (160, 160, 160), 1)

        cv2.rectangle(canvas, (760, 110), (1230, 390), (110, 130, 120), 1)
        cv2.putText(canvas, "K.A.R.A   [SPEAKING]", (780, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (80, 190, 255), 2)
        for idx, line in enumerate([
            "The Ramp. Tesla's first landing",
            "event. Stage one, approaching",
            "across the lit floor toward deck",
            f"accelerating at {g_force} G-force."
        ]):
            cv2.putText(canvas, line, (780, 185 + idx * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 225, 225), 1)

        deck_poly = np.array([[100, 415], [1090, 415], [1255, 615], [25, 615]], np.int32)
        deck_layer = np.zeros_like(canvas)
        cv2.fillPoly(deck_layer, [deck_poly], (70, 75, 58))
        cv2.add(canvas, deck_layer, dst=canvas)
        cv2.polylines(glow, [deck_poly], True, (220, 230, 200), 2, cv2.LINE_AA)

        cv2.putText(canvas, f"APPROACH  -  {kmh} KM/H  -  A {g_force} G", (220, 448), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (230, 250, 240), 1)
        cv2.putText(canvas, "T E S L A   C A R   D E M O", (90, 530), cv2.FONT_HERSHEY_SIMPLEX, 1.30, (160, 185, 175), 2, cv2.LINE_AA)
        cv2.putText(canvas, "THRUSTER DESCENT             FIST-TWIST THE SHIP", (95, 570), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (200, 220, 210), 1)

        ramp_l, ramp_r = [], []
        steps_r = 28
        for i in range(steps_r):
            t = i / (steps_r - 1)
            rx = int(840 - t * 680 + math.sin(t * math.pi) * 90)
            ry = int(175 + (t ** 0.55) * 305)
            w_r = int(16 + t * 26)
            ramp_l.append((rx - w_r, ry - 6))
            ramp_r.append((rx + w_r, ry + 6))

        for i in range(steps_r - 1):
            cv2.line(glow, ramp_l[i], ramp_l[i + 1], (255, 240, 150), 2, cv2.LINE_AA)
            cv2.line(glow, ramp_r[i], ramp_r[i + 1], (255, 240, 150), 2, cv2.LINE_AA)
            cv2.line(canvas, ramp_l[i], ramp_r[i], (220, 255, 240), 1, cv2.LINE_AA)
            cv2.line(canvas, ramp_l[i], ramp_r[i + 1], (180, 220, 200), 1, cv2.LINE_AA)

        c_idx = min(steps_r - 1, int(self.sim_progress * (steps_r - 1)))
        car_x = (ramp_l[c_idx][0] + ramp_r[c_idx][0]) // 2
        car_y = (ramp_l[c_idx][1] + ramp_r[c_idx][1]) // 2 - 18
        car_ry = -0.6 + self.sim_progress * 0.8 + (hands[0]["roll"] * 0.7 if hands else 0.0)

        OmniHologramRenderer.render_mesh(
            canvas, glow, self.car_verts, self.car_edges, self.car_faces,
            car_x, car_y, 58.0, 0.35, car_ry, 0.0, draw_glass=True
        )


class ApexAIBrainDashboard:
    """واجهة العقل المركزي التفاعلية لعرض الوكلاء الـ 15 ونبضات البيانات الحية"""
    NODES = [
        ("Strategist / Planning", -120, -145, (255, 230, 0)),
        ("AI Research", -255, -95, (255, 230, 0)),
        ("Security Supervisor", -215, -20, (255, 230, 0)),
        ("Coding Agent", -265, 55, (0, 215, 255)),
        ("Data Analysis", -195, 100, (0, 215, 255)),
        ("Automation", -135, 140, (0, 215, 255)),
        ("Voice Agent", 0, 165, (0, 215, 255)),
        ("Cybersecurity", 175, -100, (255, 230, 0)),
        ("XR Spatial Agent", 325, -20, (255, 230, 0)),
        ("Memory Agent", 205, 35, (255, 230, 0)),
        ("Robotics Control", 245, 105, (0, 215, 255)),
        ("Engineering Lab", 145, 145, (0, 215, 255)),
    ]

    @staticmethod
    def render(canvas, glow, w: int, h: int, hands: list, t_now: float) -> Optional[str]:
        cx, cy = w // 2, h // 2 - 25
        bg_tint = np.zeros_like(canvas)
        bg_tint[:] = (75, 32, 8)
        cv2.addWeighted(canvas, 0.45, bg_tint, 0.55, 0, canvas)

        for r_orb in [140, 220, 300, 380]:
            cv2.circle(canvas, (cx, cy), r_orb, (110, 65, 15), 1, cv2.LINE_AA)

        cursor = hands[0]["pts"][8] if hands else None
        hovered_agent = None

        for idx, (name, dx, dy, col) in enumerate(ApexAIBrainDashboard.NODES):
            nx, ny = cx + dx, cy + dy
            mid_x, mid_y = cx + int(dx * 0.55), ny
            cv2.line(canvas, (cx, cy), (mid_x, mid_y), (180, 110, 20), 1, cv2.LINE_AA)
            cv2.line(canvas, (mid_x, mid_y), (nx, ny), (180, 110, 20), 1, cv2.LINE_AA)
            cv2.line(glow, (mid_x, mid_y), (nx, ny), col, 1, cv2.LINE_AA)

            pulse_t = (t_now * 0.8 + idx * 0.23) % 1.0
            px, py = int(nx + (cx - nx) * pulse_t), int(ny + (cy - ny) * pulse_t)
            cv2.circle(glow, (px, py), 4, col, cv2.FILLED)

            is_hover = cursor is not None and math.hypot(cursor[0] - nx, cursor[1] - ny) < 42
            if is_hover:
                hovered_agent = name
            rad_n = 15 if is_hover else 10
            cv2.circle(glow, (nx, ny), rad_n + 3, col, 2, cv2.LINE_AA)
            cv2.circle(canvas, (nx, ny), rad_n, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(canvas, name, (nx - 45, ny - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

        core_rad = 82 + int(math.sin(t_now * 4.0) * 3)
        cv2.circle(glow, (cx, cy), core_rad, (0, 230, 255), 8, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), core_rad, (180, 255, 255), 3, cv2.LINE_AA)
        cv2.circle(glow, (cx, cy), core_rad - 12, (255, 200, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, "JARVIS CORE", (cx - 48, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 2, cv2.LINE_AA)

        wy = cy + 215
        cv2.putText(canvas, "MULTI-AGENT NEURAL BUS ACTIVE", (cx - 95, wy + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (180, 220, 240), 1)
        for i in range(-18, 19):
            wx = cx + i * 6
            amp = max(3, int((math.sin(t_now * 12.0 + i * 0.5) * 0.5 + 0.5) * (20 - abs(i))))
            cv2.line(glow, (wx, wy - amp), (wx, wy + amp), (0, 220, 255), 2)
            cv2.line(canvas, (wx, wy - amp), (wx, wy + amp), (255, 255, 255), 1)

        return hovered_agent


def run_part2_self_verification():
    import unittest
    from tests.test_acceptance_suite import SpatialVisionPhysicsTestSuite
    print("=" * 78)
    print("SPATIAL OS v5.0 VISION, PHYSICS & XR ENGINE (PART 2/3) - SELF VERIFICATION")
    print("=" * 78)
    suite = unittest.TestLoader().loadTestsFromTestCase(SpatialVisionPhysicsTestSuite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[VERIFIED] All 6 Spatial Vision, Physics & 3D Geometry Tests Passed!")


if __name__ == "__main__":
    run_part2_self_verification()
