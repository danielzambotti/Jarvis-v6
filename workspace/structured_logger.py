"""Structured Logger
This script logs each interaction in a structured format using JSON.
The log entries contain the following fields:
- timestamp: The time of the interaction in milliseconds since epoch.
- skill_used: The skill used for the interaction.
- sha256_hash: A SHA256 hash of the first 50 characters of the input.
- latency_ms: The latency of the interaction in milliseconds.

This script uses the Python standard library and does not require any external packages.
"""

import json
import hashlib
import time

def log_interaction(input_data, skill_used):
    try:
        # Calculate SHA256 hash of the first 50 characters of the input
        sha256_hash = hashlib.sha256(input_data[:50].encode()).hexdigest()[:64]
        
        # Calculate latency in milliseconds
        start_time = time.time()
        # Add your code here to process the interaction
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)
        
        # Create log entry as a JSON object
        log_entry = {
            "timestamp": int(time.time() * 1000),
            "skill_used": skill_used,
            "sha256_hash": sha256_hash,
            "latency_ms": latency_ms
        }
        
        return json.dumps(log_entry)
    
    except Exception as e:
        print(f"Error logging interaction: {str(e)}")
        return None

if __name__ == "__main__":
    input_data = "Example input data"
    skill_used = "Example skill used"
    log_entry = log_interaction(input_data, skill_used)
    if log_entry is not None:
        print(log_entry)