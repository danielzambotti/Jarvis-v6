"""
Creates a directory called 'Jarvis 2.0' on the local disk C.
"""

import os

def create_jarvis_directory():
    try:
        if not os.path.exists('C:/Jarvis 2.0'):
            os.makedirs('C:/Jarvis 2.0')
            print("Directory created successfully!")
        else:
            print("The directory already exists.")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    create_jarvis_directory()