import os
import math
import time
from PIL import Image, ImageDraw

# Machine Constants (matching Sand.ino and designs.html)
TABLE_RADIUS = 202.6
L1 = 101.3
L2 = 101.3
STEPS_PER_RAD = (3200.0 / 360.0) * (180.0 / math.pi)

def calculate_ik(x, y, last_b):
    dist = math.hypot(x, y)
    max_reach = L1 + L2
    if dist > max_reach:
        x *= (max_reach / dist)
        y *= (max_reach / dist)
        dist = max_reach
    
    if dist < 1.0:
        return last_b, -math.pi * STEPS_PER_RAD
    
    cos_bend = (dist * dist - L1 * L1 - L2 * L2) / (2.0 * L1 * L2)
    bend = math.acos(max(-1.0, min(1.0, cos_bend)))
    t1 = math.atan2(y, x) - math.atan2(L2 * math.sin(bend), L1 + L2 * math.cos(bend))
    
    last_t1 = -last_b / STEPS_PER_RAD
    t1 = t1 - (round((t1 - last_t1) / (2.0 * math.pi)) * 2.0 * math.pi)
    
    return -t1 * STEPS_PER_RAD, -(bend + 1.125 * t1) * STEPS_PER_RAD

def get_xy(b, e):
    t1 = -b / STEPS_PER_RAD
    bend = -e / STEPS_PER_RAD - 1.125 * t1
    x = L1 * math.cos(t1) + L2 * math.cos(t1 + bend)
    y = L1 * math.sin(t1) + L2 * math.sin(t1 + bend)
    return x, y

def generate_thumbnail(file_path, output_path):
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        
        path = []
        last_b = 0
        last_e = 0
        
        for line in lines:
            line = line.split('#')[0].split(';')[0].strip()
            if not line:
                continue
            
            try:
                if line.upper().startswith('G1'):
                    parts = line[2:].strip().split()
                    theta = float(parts[0])
                    rho = float(parts[1])
                else:
                    parts = line.split()
                    if len(parts) >= 2:
                        theta = float(parts[0])
                        rho = float(parts[1])
                    else:
                        continue
                
                x_mm = rho * TABLE_RADIUS * math.cos(theta)
                y_mm = rho * TABLE_RADIUS * math.sin(theta)
                
                b, e = calculate_ik(x_mm, y_mm, last_b)
                # For smooth preview, we can just use the IK points directly or interpolate
                # But for a thumbnail, the raw points are usually enough
                cur_x, cur_y = get_xy(b, e)
                path.append((cur_x, cur_y))
                last_b, last_e = b, e
            except:
                continue
        
        if not path:
            return False

        # Create image
        size = 300
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Scaling
        scale = (size / (TABLE_RADIUS * 2)) * 0.9
        offset = size / 2
        
        # Transform points to pixel coordinates
        pixel_path = []
        for x, y in path:
            # Note: The website rotates the canvas 180 deg, so we flip y here if needed
            # but usually we just want a centered preview.
            px = offset + x * scale
            py = offset + y * scale
            pixel_path.append((px, py))
            
        if len(pixel_path) > 1:
            draw.line(pixel_path, fill=(0, 0, 0, 255), width=2)
            
        img.save(output_path, 'PNG')
        return True
    except Exception as e:
        print(f"Error generating thumbnail for {file_path}: {e}")
        return False

def monitor_designs(designs_folder):
    print(f"Monitoring {designs_folder} for thumbnails...")
    while True:
        try:
            files = [f for f in os.listdir(designs_folder) if f.endswith('.txt') or f.endswith('.thr')]
            for f in files:
                thumb_name = f.replace('.txt', '.png').replace('.thr', '.png')
                thumb_path = os.path.join(designs_folder, thumb_name)
                file_path = os.path.join(designs_folder, f)
                
                # If thumb doesn't exist or is older than the source file
                if not os.path.exists(thumb_path) or os.path.getmtime(file_path) > os.path.getmtime(thumb_path):
                    print(f"Generating thumbnail for {f}...")
                    generate_thumbnail(file_path, thumb_path)
            
            time.sleep(10)
        except Exception as e:
            print(f"Monitor error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        monitor_designs(sys.argv[1])
