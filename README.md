## 🧾 **README.md**

```markdown
# PSC Calculadora Automática (Selenium)

Script em Python que automatiza o login e o preenchimento da calculadora do portal **PSC - Precatórios Sem Complicação**, extraindo todos os campos do resultado e salvando em **JSON** (modo headless e sem screenshots por padrão).

---

## 🚀 Funcionalidades

- Login automático no portal PSC  
- Preenchimento completo da calculadora  
- Coleta de **todos os campos exibidos** no resultado (labels e valores)  
- Exportação para arquivo **JSON** em `/out/`  
- Compatível com execução **headless (sem interface gráfica)**  
- Não gera `.png`, `.txt` nem `.html` — apenas o `.json`

---

## 🧩 Estrutura

```

📂 psc_calc_tjsp/
├── psc_calc.py
├── requirements.txt
├── Dockerfile
├── .gitignore
└── out/

````

---

## ⚙️ Requisitos

- Python **3.10+**
- Google Chrome instalado (local ou container)
- Bibliotecas Python:
  - `selenium`
  - `webdriver-manager`

Instalação:
```bash
python -m venv env
env\Scripts\activate   # Windows
# source env/bin/activate  # Linux/macOS
pip install -r requirements.txt
````

---

## 🧠 Uso

### Execução direta (local)

```bash
python psc_calc.py \
  --email "usuario@exemplo.com" \
  --senha "minhasenha" \
  --numero-precatorio "0077044-50.2023.8.26.0500" \
  --tipo "Comum" \
  --ano-venc "2023" \
  --data-ult-liquid "2024-10-07" \
  --valor-precatorio "80000,00" \
  --principal "80000,00" \
  --juros-mora "20000,00" \
  --indice "IPCA-E" \
  --incide-ir "Não" \
  --timeout 30 \
  --timeout-calc 90 \
  --headless
```

> 🧾 O resultado será salvo em `out/resultado_calc_<timestamp>.json`.

---

## 🧱 Estrutura do JSON

Exemplo simplificado:

```json
{
  "when": "20251007_214505",
  "url": "https://psc.precatoriosemcomplicacao.com.br/PSCWeb/AtualizacaoCalculos",
  "campos": {
    "valor_bruto_precatorio": 120000.00,
    "base_calculo_liquida": 100000.00,
    "ir_calculado": 5000.00,
    "prev_fgts_assistencia": 3000.00,
    "valor_liquido_cedivel": 92000.00
  },
  "valor_precatorio_cli": "80000,00"
}
```

---

## 🧰 Opções e Flags

| Parâmetro                                           | Descrição                                     |
| --------------------------------------------------- | --------------------------------------------- |
| `--email`, `--senha`                                | Credenciais de acesso ao PSC                  |
| `--numero-precatorio`                               | Número do processo                            |
| `--tipo`                                            | Tipo: `Alimentar` ou `Comum`                  |
| `--ano-venc`, `--data-ult-liquid`                   | Datas e vencimentos                           |
| `--valor-precatorio`, `--principal`, `--juros-mora` | Valores em formato pt-BR                      |
| `--indice`                                          | Índice da sentença                            |
| `--incide-ir`                                       | Tipo de IR aplicado                           |
| `--timeout`                                         | Tempo limite padrão                           |
| `--timeout-calc`                                    | Tempo limite da etapa de cálculo              |
| `--out-dir`                                         | Diretório de saída (`out/`)                   |
| `--headless`                                        | Executa sem interface (recomendado em Docker) |

---

## 🐳 Execução via Docker

### Build da imagem

```bash
docker build -t psc-calc:latest .
```

### Execução

```bash
docker run --rm -it ^
  -v "%cd%/out:/app/out" ^
  psc-calc:latest ^
  python psc_calc.py --email "usuario@exemplo.com" --senha "123" --numero-precatorio "0077044-50.2023.8.26.0500" --tipo "Comum" --ano-venc "2023" --data-ult-liquid "2024-10-07" --valor-precatorio "80000,00" --principal "80000,00" --juros-mora "20000,00" --indice "IPCA-E" --incide-ir "Não" --timeout 30 --timeout-calc 90 --headless
```

> No Linux/macOS, substitua `%cd%` por `$(pwd)`.

---

## 🧪 Verificação rápida

* Para checar o ChromeDriver:

  ```bash
  python -m webdriver_manager drivers
  ```
* Para testar o login apenas:

  ```bash
  python psc_calc.py --email ... --senha ... --numero-precatorio ... --tipo Comum ... --headless
  ```



