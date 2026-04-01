"""
download_models.py — Baixa os modelos de IA necessários para o Smart Counter AI.

Modelos baixados:
1. OpenCV Face Detector (TensorFlow): opencv_face_detector_uint8.pb + .pbtxt
2. Age Net (Caffe): age_net.caffemodel + age_deploy.prototxt
3. Gender Net (Caffe): gender_net.caffemodel + gender_deploy.prototxt

Os modelos são públicos, pré-treinados, e amplamente utilizados pela comunidade OpenCV.
"""

import os
import sys
import urllib.request
import ssl
import hashlib


def get_models_dir():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_dir = os.path.join(base, 'models')
    os.makedirs(models_dir, exist_ok=True)
    return models_dir


def download_file(url, dest_path, description=""):
    """Baixa um arquivo com barra de progresso simples."""
    if os.path.exists(dest_path):
        size = os.path.getsize(dest_path)
        if size > 1000:  # Se tem mais de 1KB, provavelmente é válido
            print(f"  [OK] {os.path.basename(dest_path)} ({size:,} bytes)")
            return True

    print(f"  [BAIXANDO] {description or os.path.basename(dest_path)}...")
    print(f"             {url}")

    try:
        # Bypass SSL para redes corporativas com proxy
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) SmartCounterAI/1.0'
        })

        with urllib.request.urlopen(req, context=ctx) as response:
            total = int(response.headers.get('Content-Length', 0))
            data = b''
            chunk_size = 1024 * 256  # 256KB chunks
            downloaded = 0

            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                data += chunk
                downloaded += len(chunk)
                if total > 0:
                    pct = (downloaded / total) * 100
                    bar_len = 30
                    filled = int(bar_len * downloaded / total)
                    bar = '#' * filled + '-' * (bar_len - filled)
                    sys.stdout.write(f'\r             [{bar}] {pct:.0f}% ({downloaded:,}/{total:,})  ')
                    sys.stdout.flush()

            with open(dest_path, 'wb') as f:
                f.write(data)

            final_size = os.path.getsize(dest_path)
            print(f"\n  [SUCCESS] {os.path.basename(dest_path)} ({final_size:,} bytes)")
            return True

    except Exception as e:
        print(f"\n  [ERRO] Falha ao baixar: {e}")
        # Limpar arquivo parcial
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def main():
    models_dir = get_models_dir()

    print()
    print("=" * 60)
    print("  SMART COUNTER AI - Download de Modelos de IA")
    print("=" * 60)
    print(f"  Destino: {models_dir}")
    print()

    # URLs testadas e funcionais (repositórios públicos estáveis)
    # Usando o repositório smahesh29/Gender-and-Age-Detection que hospeda todos os modelos
    GITHUB_RAW = "https://raw.githubusercontent.com/smahesh29/Gender-and-Age-Detection/master"

    models = [
        {
            "name": "OpenCV Face Detector (config)",
            "file": "opencv_face_detector.pbtxt",
            "urls": [
                f"{GITHUB_RAW}/opencv_face_detector.pbtxt",
                "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/opencv_face_detector.pbtxt",
            ]
        },
        {
            "name": "OpenCV Face Detector (pesos TF)",
            "file": "opencv_face_detector_uint8.pb",
            "urls": [
                f"{GITHUB_RAW}/opencv_face_detector_uint8.pb",
                "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/opencv_face_detector_uint8.pb",
            ]
        },
        {
            "name": "Age Net (config Caffe)",
            "file": "age_deploy.prototxt",
            "urls": [
                f"{GITHUB_RAW}/age_deploy.prototxt",
                "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_deploy.prototxt",
            ]
        },
        {
            "name": "Age Net (pesos Caffe ~44MB)",
            "file": "age_net.caffemodel",
            "urls": [
                f"{GITHUB_RAW}/age_net.caffemodel",
            ]
        },
        {
            "name": "Gender Net (config Caffe)",
            "file": "gender_deploy.prototxt",
            "urls": [
                f"{GITHUB_RAW}/gender_deploy.prototxt",
                "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_deploy.prototxt",
            ]
        },
        {
            "name": "Gender Net (pesos Caffe ~44MB)",
            "file": "gender_net.caffemodel",
            "urls": [
                f"{GITHUB_RAW}/gender_net.caffemodel",
            ]
        },
    ]

    success_count = 0
    fail_count = 0

    for model in models:
        filepath = os.path.join(models_dir, model["file"])
        downloaded = False

        for url in model["urls"]:
            if download_file(url, filepath, model["name"]):
                downloaded = True
                break

        if downloaded:
            success_count += 1
        else:
            fail_count += 1

    print()
    print("-" * 60)
    print(f"  Resultado: {success_count} baixados, {fail_count} falhas")

    if fail_count > 0:
        print()
        print("  [!] Alguns modelos falharam ao baixar.")
        print("  [!] Alternativas:")
        print("      1. Tente novamente (pode ser instabilidade de rede)")
        print("      2. Baixe manualmente de:")
        print("         https://github.com/smahesh29/Gender-and-Age-Detection")
        print("      3. Coloque os arquivos na pasta:")
        print(f"         {models_dir}")
    else:
        print("  [OK] Todos os modelos prontos!")

    print()
    print("  NOTA: O YOLOv8n será baixado automaticamente pelo Ultralytics")
    print("        na primeira execução do sistema.")
    print("=" * 60)
    print()


if __name__ == '__main__':
    main()
