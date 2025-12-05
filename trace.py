import cv2
import numpy as np
import os
from flask import Flask, request, jsonify, render_template

# Initialize Flask, telling it to look for HTML files in the current directory ('.')
app = Flask(__name__, template_folder='.', static_folder='static')

class SandTableGenerator:
    def __init__(self, table_radius_mm=202.6):
        self.R = table_radius_mm

    def process(self, file_stream, density=1.0):
        # 1. Decode Image from Memory
        file_bytes = np.asarray(bytearray(file_stream.read()), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            raise ValueError("Could not decode image")

        # 2. Resize & Edge Detect
        # 
        h, w = img.shape
        scale_factor = density 
        new_size = (int(w * scale_factor), int(h * scale_factor))
        img = cv2.resize(img, new_size)
        
        # Blur slightly to remove noise before edge detection
        img_blur = cv2.GaussianBlur(img, (5, 5), 0)
        edges = cv2.Canny(img_blur, 50, 150)
        
        # Find Contours (the lines)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        # 3. Map to Table Coordinates (mm)
        # We use 90% of radius (self.R * 1.8) to leave a small border
        scale = (self.R * 1.8) / max(new_size) 
        cx, cy = new_size[0] / 2, new_size[1] / 2
        mapped_paths = []
        
        for cnt in contours:
            # Simplify line (Douglas-Peucker) to reduce point count
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

        # 4. Greedy Optimization (The "Sandify" Logic)
        if not mapped_paths:
            return []
            
        # START at the perimeter (Right side: x=R, y=0)
        current_pos = np.array([self.R, 0.0]) 
        
        # The output list starts with the perimeter point
        ordered_points = [{'x': float(self.R), 'y': 0.0, 'type': 'point'}]
        
        pool = mapped_paths.copy()

        while pool:
            best_idx = -1
            best_dist = float('inf')
            reverse_contour = False

            # Find the nearest start or end of any remaining line
            for i, contour in enumerate(pool):
                start_pt = contour[0]
                end_pt = contour[-1]

                d_start = np.linalg.norm(start_pt - current_pos)
                d_end = np.linalg.norm(end_pt - current_pos)

                if d_start < best_dist:
                    best_dist = d_start
                    best_idx = i
                    reverse_contour = False
                
                if d_end < best_dist:
                    best_dist = d_end
                    best_idx = i
                    reverse_contour = True

            # Pick the best line
            chosen = pool.pop(best_idx)
            if reverse_contour:
                chosen = chosen[::-1] # Flip the line if the end was closer

            # Add points to list
            for p in chosen:
                ordered_points.append({'x': float(p[0]), 'y': float(p[1]), 'type': 'point'})
            
            # Update current position to the end of the line we just drew
            current_pos = chosen[-1]

        # 5. END at the perimeter (return to home)
        ordered_points.append({'x': float(self.R), 'y': 0.0, 'type': 'point'})
        
        return ordered_points

# --- ROUTES ---

@app.route('/')
def index():
    # Serve your specific HTML file
    return render_template('AI2.html') 

@app.route('/process_image', methods=['POST'])
def process_image():
    file = request.files.get('image')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
    try:
        # Initialize generator with your table dimensions
        gen = SandTableGenerator(table_radius_mm=202.6)
        
        # Process the image
        # density=0.5 makes it faster/smoother. Increase to 1.0 for high detail.
        points = gen.process(file, density=0.5) 
        
        return jsonify({'success': True, 'points': points})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'error': str(e)})

# --- EXISTING ROUTES (Placeholders if you need them) ---
@app.route('/send_gcode_block', methods=['POST'])
def send_gcode():
    # Your existing code to talk to the motors would go here
    return jsonify({'success': True, 'message': 'Simulated send'})

@app.route('/save_design', methods=['POST'])
def save_design():
    # Your existing save logic
    return jsonify({'success': True})

if __name__ == '__main__':
    # Run on all network interfaces
    app.run(host='0.0.0.0', port=5000, debug=True)
