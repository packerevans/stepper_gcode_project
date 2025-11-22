# Add this import at the top
import subprocess

def get_wifi_networks():
    """Scans for available Wi-Fi networks using nmcli."""
    try:
        # Run nmcli to list networks
        result = subprocess.run(
            ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=10
        )
        networks = []
        seen_ssids = set()
        
        for line in result.stdout.split('\n'):
            if not line or line == "--": continue
            parts = line.split(":")
            if len(parts) < 3: continue
            ssid = parts[0]
            if not ssid or ssid in seen_ssids: continue # Deduplicate
            
            seen_ssids.add(ssid)
            networks.append({
                "ssid": ssid,
                "signal": parts[1],
                "security": parts[2]
            })
        return networks
    except Exception as e:
        print(f"Wifi Scan Error: {e}")
        return []

def connect_to_wifi(ssid, password):
    """Tells NetworkManager to connect to a new network."""
    try:
        # 1. Delete if it exists (to update password)
        subprocess.run(["sudo", "nmcli", "con", "delete", ssid], capture_output=True)
        
        # 2. Add and Connect (High Priority)
        cmd = [
            "sudo", "nmcli", "dev", "wifi", "connect", ssid,
            "password", password
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Set high priority so it beats the Hotspot next boot
            subprocess.run(["sudo", "nmcli", "con", "modify", ssid, "connection.autoconnect-priority", "100"])
            return True, "Connected! Rebooting..."
        else:
            return False, f"Failed: {result.stderr}"
    except Exception as e:
        return False, str(e)
