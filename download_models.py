import os
import urllib.request

MODELS_DIR = os.path.join(os.path.dirname(__file__), "backend", "models_data")
os.makedirs(MODELS_DIR, exist_ok=True)

models = {
    "age_net.caffemodel": "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_net.caffemodel",
    "age_deploy.prototxt": "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_deploy.prototxt",
    "gender_net.caffemodel": "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_net.caffemodel",
    "gender_deploy.prototxt": "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_deploy.prototxt",
}

for filename, url in models.items():
    filepath = os.path.join(MODELS_DIR, filename)
    if not os.path.exists(filepath):
        print(f"Downloading {filename}...")
        try:
            urllib.request.urlretrieve(url, filepath)
            print(f"Downloaded {filename}")
        except Exception as e:
            print(f"Failed to download {filename}: {e}")
    else:
        print(f"{filename} already exists.")
