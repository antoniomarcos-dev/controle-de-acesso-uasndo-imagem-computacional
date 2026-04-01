# Roteiro Técnico de Evolução do Sistema de Monitoramento Inteligente

**Objetivo:** Estabelecer o roteiro técnico de evolução do sistema de monitoramento inteligente, partindo de uma Prova de Conceito (PoC) local para um ecossistema distribuído de inteligência urbana englobando hospitais (UPAs), trânsito e segurança pública.

---

## 1. Fase 1: Prova de Conceito (Evento Local - Dias 10 e 11)
**Foco:** Estabilidade, Desempenho (FPS) e Apresentação Profissional.

A versão atual do sistema opera localmente processando detecção de objetos (YOLOv8) e inferência demográfica (Redes Caffe) sequencialmente. Para garantir o sucesso no ambiente controlado inicial, as seguintes otimizações serão aplicadas:

* **Otimização de Processamento (Gargalo de FPS):** Implementação de *frame skipping* dinâmico. A extração de dados demográficos (idade e gênero) não ocorrerá em todos os quadros, mas sim em intervalos (ex: a cada 3 frames), liberando a CPU/GPU para focar no rastreamento contínuo das pessoas.
* **Integração Visual (Streaming no Dashboard):** O feed de vídeo ao vivo deixará de ser renderizado em uma janela externa de depuração do sistema operacional (`cv2.imshow`). Será criada uma rota no backend (`/api/video_feed`) transmitindo o processamento via protocolo MJPEG diretamente para a interface web (HTML/Dashboard), conferindo um aspecto mais limpo e comercial ao produto.
* **Controle de Ambiente:** Recomendação estrita de iluminamento homogêneo no local da portaria, visto que os modelos demográficos atuais (Levi-Hassner) baseados em RGB sofrem perda de precisão sob luzes de contraste severo ou contraluz.

---

## 2. Fase 2: Implementação em Saúde Pública (UPAs e Hospitais)
**Foco:** Persistência de Dados, Análise de Longo Prazo e Distribuição.

Com a validação da PoC, o sistema migra de um "aplicativo de notebook" para uma arquitetura cliente-servidor robusta.

* **Arquitetura de Dados:** Substituição do armazenamento local em CSV (atual `storage.py`) por um banco de dados otimizado para séries temporais (ex: **PostgreSQL + TimescaleDB**). Isso habilitará relatórios complexos, cruzamentos de horários de pico e perfis epidemiológicos baseados em fluxo de entrada.
* **Edge Computing:** Ao invés de enviar vídeo pesado pela rede da prefeitura, mini-computadores com aceleração gráfica (como NVIDIA Jetson Nanos ou Intel NUCs) serão instalados junto às câmeras nas UPAs. O vídeo será processado localmente, e apenas metadados leves (JSON contendo contagem, idade e gênero) serão transmitidos à central.
* **Atualização de Modelos de IA:** Transição dos modelos baseados em Caffe para bibliotecas modernas de extração demográfica (como **DeepFace** ou classificadores baseados em MobileNetV2), melhorando substancialmente a acurácia de leitura.

---

## 3. Fase 3: Monitoramento Viário (Mobilidade Urbana)
**Foco:** Lógica Poligonal, Adaptação de Classes e Velocidade.

Expandir a tecnologia para ruas exige a capacidade de lidar com variáveis climáticas, perspectivas de câmera complexas e alta velocidade.

* **Contagem e Classificação de Veículos:** Adaptação da classe de detecção no YOLOv8 de `0` (pessoas) para abranger `car`, `motorcycle`, `bus`, `truck`.
* **Linhas Virtuais Complexas (RoI - Região de Interesse):** A lógica binária de cruzamento de uma linha reta simples (`self.line_x`) será substituída por polígonos direcionais flexíveis que contornam faixas de rodagem específicas.
* **Leitura de Placas Automática (ALPR):** Implementação de um módulo dedicado a detectar, recortar e ler o OCR numérico das placas (modelos LPRNet ou YOLO treinados especificamente em placas do Mercosul).
* **Estimativa de Velocidade (Homografia):** Calibração das câmeras via Transformação de Perspectiva. Mapeando os pixels do vídeo em métricas de distância reais do asfalto, o rastreador (BoT-SORT) informará a velocidade do veículo com base no tempo de deslocamento entre dois pontos.

---

## 4. Fase 4: Integração com Segurança Pública (Polícias Civil e Militar)
**Foco:** Reconhecimento Facial, Segurança Cidadã e Alertas em Tempo Real.

A etapa final do sistema conecta a captação de imagem à segurança institucional.

* **Extração de Biometria Facial:** Quando uma face for detectada, ao invés de apenas classificar gênero/idade, o sistema gerará um **embedding facial** (um vetor matemático complexo de 128 ou 512 dimensões) usando motores de ponta da indústria (como ArcFace/InsightFace).
* **Cruzamento de Bancos de Dados:** Esses vetores serão consultados em tempo real de forma segura contra bancos de dados das Polícias Civil e Militar (buscando foragidos, alertas de pessoas desaparecidas, ou veículos com restrição/roubo via ALPR).
* **Painel de Alertas Táticos:** O dashboard que hoje exibe gráficos de contagem terá um módulo focado em "Alertas Críticos", notificando instantaneamente os órgãos competentes caso haja "match" com alta confiabilidade na leitura de uma placa ou biometria facial.

---

> **Nota:** Este documento servirá como visão tecnológica e guia estratégico.
