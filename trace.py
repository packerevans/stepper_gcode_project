import cv2
import numpy as np
import json
from flask import Flask, request, jsonify, render_template

app = Flask(__name__, template_folder='.')

# --- THE SANDIFY LOGIC CLASS ---
class SandTableGenerator:
    def __init__(self, table_radius_mm=202.6):
        self.R = table_radius_mm

    def process(self, file_stream, density=1.0):
        # 1. Decode Image from Memory
        file_bytes = np.asarray(bytearray(file_stream.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_GRAYSCALE)
        
        # 2. Resize & Edge Detect
        h, w = img.shape
        scale_factor = density 
        new_size = (int(w * scale_factor), int(h * scale_factor))
        img = cv2.resize(img, new_size)
        img_blur = cv2.GaussianBlur(img, (5, 5), 0)
        edges = cv2.Canny(img_blur, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        # 3. Map to Table Coordinates (mm)
        scale = (self.R * 1.8) / max(new_size) 
        cx, cy = new_size[0] / 2, new_size[1] / 2
        mapped_paths = []
        
        for cnt in contours:
            epsilon = 0.005 * cv2.arcLength(cnt, False)
            approx = cv2.approxPolyDP(cnt, epsilon, False)
            if len(approx) > 2:
                path = []
                for point in approx:
                    # Center and scale (Flip Y for standard cartesian)
                    x = (point[0][0] - cx) * scale
                    y = (cy - point[0][1]) * scale 
                    path.append([x, y])
                mapped_paths.append(np.array(path))

        # 4. Greedy Optimization (Connect lines seamlessly)
        if not mapped_paths:
            return []
            
        current_pos = np.array([self.R, 0.0]) # Start at perimeter
        ordered_points = [{'x': float(self.R), 'y': 0.0, 'type': 'point'}]
        pool = mapped_paths.copy()

        while pool:
            best_idx = -1
            best_dist = float('inf')
            reverse_contour = False

            for i, contour in enumerate(pool):
                d_start = np.linalg.norm(contour[0] - current_pos)
                d_end = np.linalg.norm(contour[-1] - current_pos)

                if d_start < best_dist:
                    best_dist, best_idx, reverse_contour = d_start, i, False
                if d_end < best_dist:
                    best_dist, best_idx, reverse_contour = d_end, i, True

            chosen = pool.pop(best_idx)
            if reverse_contour: chosen = chosen[::-1]

            # Add points to list
            for p in chosen:
                ordered_points.append({'x': float(p[0]), 'y': float(p[1]), 'type': 'point'})
            
            current_pos = chosen[-1]

        # 5. Return to perimeter
        ordered_points.append({'x': float(self.R), 'y': 0.0, 'type': 'point'})
        return ordered_points

# --- FLASK ROUTES ---

@app.route('/')
def index():
    # Serves your HTML file (ensure your HTML is named index.html)
    return render_template('index.html') 

@app.route('/process_image', methods=['POST'])
def process_image():
    file = request.files.get('image')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    
    try:
        gen = SandTableGenerator(table_radius_mm=202.6)
        # Process image and get X/Y points back
        points = gen.process(file, density=0.5) 
        return jsonify({'success': True, 'points': points})
    except Exception as e:
        print(e)
        return jsonify({'success': False, 'error': str(e)})

# Add your existing G-code routes here (send_gcode_block, etc.) if needed

if __name__ == '__main__':
    # Run on all interfaces so Pi is accessible
    app.run(host='0.0.0.0', port=5000)
