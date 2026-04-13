import os
import math

# CONFIGURATION
DESIGNS_FOLDER = 'templates/designs'
OUTPUT_FOLDER = 'templates/designs' # Overwriting or saving as .thr

def convert_g1_to_thr(line):
    """
    Converts a G-code line (G1 X Y) to Theta-Rho.
    Assumes X is Theta and Y is Rho (as commonly used in this project).
    """
    parts = line.strip().split()
    if not parts or parts[0] != 'G1':
        return None
    
    try:
        # Based on fluf.txt format: G1 -71 91 1000
        # X is index 1, Y is index 2
        # However, Theta-Rho is usually Theta (angle) and Rho (radius 0-1)
        # If your G-code is already in Theta/Rho but has 'G1' prefix:
        theta = float(parts[1])
        rho = float(parts[2])
        
        # If Rho is in mm (like 0-101.3), we might need to normalize to 0-1
        # But looking at your firmware, it multiplies rho by tableRadius.
        # So we should output them as "Theta Rho"
        return f"{theta} {rho}"
    except (IndexError, ValueError):
        return None

def process_all_files():
    files = [f for f in os.listdir(DESIGNS_FOLDER) if f.endswith('.txt')]
    count = 0
    
    for filename in files:
        filepath = os.path.join(DESIGNS_FOLDER, filename)
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        # Check if it looks like G-code
        if any(l.strip().startswith('G1') for l in lines[:10]):
            print(f"Converting {filename}...")
            new_lines = []
            for line in lines:
                thr = convert_g1_to_thr(line)
                if thr:
                    new_lines.append(thr)
            
            # Save back to same file (or a .thr file)
            # Firmware currently reads .txt files
            with open(filepath, 'w') as f:
                f.write('\n'.join(new_lines))
            count += 1
            
    print(f"Finished. Converted {count} files.")

if __name__ == "__main__":
    process_all_files()
