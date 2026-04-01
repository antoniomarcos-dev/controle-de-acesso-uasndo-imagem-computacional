# Smart Counter AI 📊

Sistema completo de contagem inteligente de pessoas em ambientes, projetado para rodar offline e localmente, utilizando Visão Computacional de ponta (YOLOv8) e IA Demográfica (OpenCV DNN Caffe Models) conectada a um Dashboard Web elegante e reativo.

## Pré-requisitos
- **Python 3.9+** instalado na máquina.
- Uma Webcam local conectada ao seu computador (ou altere o código em `backend/main.py` para um link RTSP de câmera IP em `VideoProcessor(camera_id="rtsp://...")`).

## Instalação Passo a Passo

### 1. Instalar as Dependências
Abra o seu terminal na pasta raiz do projeto (`Ceres Conecta Hub/projeto`) e rode:
```bash
pip install -r requirements.txt
```

### 2. Efetuar o Download dos Modelos de Inteligência Artificial
Você precisará baixar os modelos Caffe pré-treinados focados no reconhecimento de rostos, classificação de pessoas e estimativa de idade. Eles não vêm no repositório por questões de tamanho.
Para facilitar, criamos um script de automação.
```bash
python scripts/download_models.py
```
*(Se não baixar por bloqueios de Firewall em redes corporativas, o sistema ainda funcionará e contará as pessoas, preenchendo as idades/gêneros como "Desconhecido").*

### 3. Rodar o Sistema (API + Tracking + Video)
O servidor foi programado em FastAPI. Para iniciar tanto o processamento de imagem em background quanto expor os endpoints do seu dashboard Web, rode:
```bash
python -m backend.main
```
Ou alternativamente, `uvicorn backend.main:app --host 0.0.0.0 --port 8000`.

*Observação: A primeira vez que você rodar, o módulo Ultralytics fará o download mágico (automático) do modelo leve `yolov8n.pt` direto do repositório deles na sua pasta local.*

### 4. Acessar o Dashboard Web
Abra seu navegador preferido e acesse nossa plataforma local:
[http://localhost:8000](http://localhost:8000)

Você verá as entradas, saídas contabilizando, e gráficos atualizando a cada segundo via Polling System API.
Uma janela de debug nativa (Dashboard AI View) também abrirá no Windows em segundo plano com uma linha virtual azul onde você pode visualizar as pessoas passando e recebendo a meta das detecções e demografia, não a feche manualmente, pause a interface de linha de comando (`CTRL+C`) para interrupção segura.

---

## Dicas e Otimizações

1. **Utilização com GPU Limitada:** O projeto foi configurado com `yolov8n.pt` o que garante um ótimo processamento por CPU (podendo extrair +10 fps em notebooks comuns dependendo da resolução). Para usar GPU de fábrica, se possuir uma placa da NVIDIA, certifique-se de instalar as bibliotecas `torch` compiladas com `CUDA`. O `Ultralytics` identificará ativamente e usará automaticamente seu dispositivo CUDA;
2. **Posição da Câmera (Crucial):** O sistema implementa uma rede de detecção baseada visualização Frontal/Meio Rosto. Por mais que a câmera deva ficar sobre a porta, **angule a lente para 45º ou 60º mirando nos passantes de frente** pois caso coloque verticalmente à 90º olhando "para baixo", a IA verá o teto da cabeça, impedindo a determinação da idade e do rosto.
3. **Escalar o Banco de Dados:** Atualmente, salvamos logs dinâmicos no CSV. Caso haja milhões de cruzamentos num ano de uso ininterrupto, no `storage.py` realize um upgrade trocando para o `sqlite3` ou um database dedicado Postgres.
