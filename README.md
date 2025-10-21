# README — Cálculo de Atualização por IPCA-E (PSC) + Pipeline DB

Este repositório contém um **pipeline completo** para corrigir valores de precatórios em duas fases — **ANTES** (IPCA-E puro) e **PÓS** (IPCA-E + **2% a.a. simples**) — e gravar um **resumo** dos resultados em banco de dados. Inclui:

* **`app_4.py`**: motor de cálculo, com modo “formação”/“full”, *overrides* de fatores e *debug* mensal. 
* **`baixar_ipca_e.py`**: baixa IPCA-E direto da **SIDRA/IBGE** e gera `indices_ipcae.csv`. 
* **`gerar_indices_csv.py`**: converte planilhas (XLS/XLSX/XLS-HTML) em CSV de índices padronizado. 
* **`main.py`**: lê linhas de `esaj_detalhe_processos`, executa `app_4.py`, **parseia a saída** e **insere** em `esaj_calc_precatorio_resumo`. Usa `.env` e *overrides* opcionais. 

---

## 1) Visão geral do cálculo

* **Período ANTES**

  * *formação* (padrão): **07/(ano_venc-1) .. 12/(ano_venc)** — IPCA-E puro.
  * *full*: **07/(ano_venc-1) .. 11/2021** — IPCA-E puro.
* **Período PÓS**: **12/2021 .. fim** — **IPCA-E + 2% a.a. simples**, com **meses para 2% = n_meses_pos − 1**.
* **Juros de mora anteriores** (se informados) são **corrigidos pelos mesmos fatores** do principal (ANTES+PÓS).
* *Overrides* opcionais para bater com memórias de cálculo oficiais:
  `--override-antes` e `--override-pos-ipca`. 

---
## mermaid
flowchart TD
  subgraph Fontes
    S1[SIDRA IBGE IPCA-E] --> C1[indices_ipcae.csv]
    S2[Planilhas XLS XLSX] --> C2[indices.csv]
  end

  subgraph Calculo
    A4[app_4.py]
  end

  subgraph Orquestracao_DB
    SRC[esaj_detalhe_processos]
    MAIN[main.py]
    RE[parser regex]
    DST[esaj_calc_precatorio_resumo]
  end

  C1 --> A4
  C2 --> A4
  SRC --> MAIN
  MAIN --> A4
  A4 --> RE
  RE --> DST

  DST --> OUT1[Fatores]
  DST --> OUT2[Valores]
  DST --> OUT3[Juros]
  DST --> OUT4[Total]




## 2) Estrutura dos arquivos

```
app_4.py               # cálculo IPCA-E + 2% a.a. simples (CLI)


baixar_ipca_e.py       # baixa IPCA-E da SIDRA/IBGE → indices_ipcae.csv 
link : https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/9262-indice-nacional-de-precos-ao-consumidor-amplo-especial.html?=&t=downloads 
caminho : IPCA_E / Series_Historicas/ ipca-e_SerieHist.zip 

gerar_indices_csv.py   # converte planilha (IPCA/IPCA-E) → indices.csv
main.py                # orquestra: DB → app_4 → parse → INSERT resumo

```

* `app_4.py` aceita dois formatos de CSV de índices:

  * `indices_ipcae.csv`: `indice,ano,mes,variacao_mensal` (fração, p.ex. 0.0065).
  * `ipcae_mensal.csv`: `data,fator` (YYYY-MM; aceita **1.0043** ou **0,43%**). 
* `baixar_ipca_e.py` consulta a **tabela 1737 (IPCA-E), variável 63 (var. mensal %)** nos meses de referência (**mar/jun/set/dez**), e **expande cada leitura para os 3 meses do trimestre**. Gera `indices_ipcae.csv`. 
* `gerar_indices_csv.py` lê XLS/XLSX/**XLS-HTML** (muito comum em órgãos públicos), detecta colunas de **Ano/Mês/Var. Mensal** ou o formato **amplo (JAN..DEZ)**, normaliza e grava `indices.csv`. 
* `main.py`:

  * Lê do DB: `id, numero_ordem, cpf, numero_processo_cnj, valor_total_requisitado AS valor_precatorio, valor_principal_bruto AS principal, EXTRACT(YEAR FROM data_base_atualizacao) AS ano_base, juros_moratorios AS juros_mora` (de `esaj_detalhe_processos`).
  * Executa `app_4.py` (com *overrides* por `.env`), **parseia** fatores/valores com *regex* robusto e insere em `esaj_calc_precatorio_resumo`. 

---

## 3) Pré-requisitos

* Python 3.10+
* Pacotes:

  ```bash
  pip install -U python-dotenv psycopg2-binary pandas requests openpyxl lxml html5lib beautifulsoup4
  ```
* Banco **PostgreSQL** acessível e `.env` configurado (abaixo).

---

## 4) Configuração (.env)

Crie um arquivo `.env` na raiz:

```ini
DB_HOST=localhost
DB_PORT=5432
DB_NAME=seu_banco
DB_USER=seu_usuario
DB_PASSWORD=seu_segredo

# Overrides opcionais (string decimal, ponto):
OVERRIDE_ANTES=1.08370280
OVERRIDE_POS_IPCA=1.21414986
```

`main.py` consome essas variáveis e acusa faltantes antes de rodar. 

---

## 5) Obtendo os índices IPCA-E (duas opções)

### Opção A — Online via SIDRA/IBGE (recomendado)

Gera `indices_ipcae.csv` já no **formato aceito por `app_4.py`**:

```bash
python baixar_ipca_e.py --out indices_ipcae.csv
```

O script usa **tabela 1737** e **variável 63 (variação mensal %)**, replicando a leitura trimestral para os três meses do trimestre. 

### Opção B — A partir de planilha XLS/XLSX/XLS-HTML

Se você possui uma planilha histórica do índice:

```bash
python gerar_indices_csv.py \
  --xlsx ipca_202509SerieHist.xls \
  --sheet 0 \
  --indice "IPCA-E" \
  --out indices.csv \
  --header-row 4
```

* Detecta automaticamente colunas **Ano/Mês/Var** ou formato **JAN..DEZ**.
* Normaliza **0,21% → 0.0021** (fração mensal). 

> **Validação rápida**: o CSV final deve conter: `indice,ano,mes,variacao_mensal` (fração). 

---

## 6) Uso do motor de cálculo (unitário)

Exemplos:

```bash
# Cálculo padrão (formação): 07/(av-1) .. 12/(av); PÓS=12/2021..último CSV
python app_4.py --principal 1097665.34 --ano-venc 2008 --indices-csv indices_ipcae.csv --juros-mora-ant 471676.23 --debug

# Fixar fim do PÓS (recorte):
python app_4.py --principal 60532.69 --ano-venc 2020 --indices-csv indices_ipcae.csv --juros-mora-ant 0 --pos-fim 2025-10

# Usando overrides para bater memória oficial:
python app_4.py --principal 86486.13 --ano-venc 2010 --indices-csv indices_ipcae.csv \
  --juros-mora-ant 42176.53 --override-antes 1.08370280 --override-pos-ipca 1.21414986 --debug
```

* `--antes-mode {formacao,full}` muda o corte do ANTES.
* Juros simples 2% a.a. usam **meses_para_2aa = (n_meses_pos − 1)**. 

---

## 7) Execução em lote com banco (`main.py`)

Lê da tabela fonte, roda `app_4.py` por linha, parseia e insere no **resumo**:

```bash
# Todos (com limite)
python main.py --limit 50 --verbose

# Apenas um ID específico
python main.py --id 19 --verbose
```

* A query padrão já traz: **`cpf`** e **`numero_processo_cnj`**, além dos valores usados no cálculo.
* O script normaliza formatos BR de moeda/fator, ajusta ano (`EXTRACT(YEAR ...)`) e **coalesce** campos para **NOT NULL** antes do INSERT. 

---

## 8) Requisitos de schema (exemplo)

> Ajuste aos nomes/Tipos do seu ambiente. Os exemplos abaixo seguem os campos utilizados pelos scripts.

### Tabela fonte: `esaj_detalhe_processos`

Usada por `main.py` na seleção de dados. Deve conter ao menos:

```sql
CREATE TABLE IF NOT EXISTS public.esaj_detalhe_processos (
  id                    BIGSERIAL PRIMARY KEY,
  numero_ordem          TEXT,
  cpf                   VARCHAR(11) NOT NULL,
  numero_processo_cnj   VARCHAR(30) NOT NULL,
  valor_total_requisitado NUMERIC(18,2),
  valor_principal_bruto   NUMERIC(18,2),
  juros_moratorios        NUMERIC(18,2),
  data_base_atualizacao   DATE
);
```

> A presença de **`cpf`** e **`numero_processo_cnj`** é obrigatória no `SELECT` de `main.py`. 

### Tabela destino: `esaj_calc_precatorio_resumo`

Recebe os campos parseados do cálculo:

```sql
CREATE TABLE IF NOT EXISTS public.esaj_calc_precatorio_resumo (
  id                           BIGSERIAL PRIMARY KEY,
  cpf                          VARCHAR(11) NOT NULL,
  numero_processo_cnj          VARCHAR(30) NOT NULL,
  fator_ipcae_antes            NUMERIC(18,8) NOT NULL,
  fator_ipcae_pos              NUMERIC(18,8) NOT NULL,
  fator_juros_2aa_simples      NUMERIC(18,8) NOT NULL,
  meses_para_2aa               INTEGER NOT NULL,
  principal_original           NUMERIC(18,2) NOT NULL,
  principal_apos_antes         NUMERIC(18,2) NOT NULL,
  principal_pos_ipca           NUMERIC(18,2) NOT NULL,
  principal_final_ipca_2aa     NUMERIC(18,2) NOT NULL,
  juros_mora_anteriores_base   NUMERIC(18,2) NOT NULL,
  juros_mora_apos_antes        NUMERIC(18,2) NOT NULL,
  juros_mora_final_corrigido   NUMERIC(18,2) NOT NULL,
  total_corrigido              NUMERIC(18,2) NOT NULL,
  created_at                   TIMESTAMP DEFAULT now()
);
```

> O `INSERT` e a montagem do *payload* com *coalesce* (0/0.0) para **NOT NULL** estão em `main.py`. 

---

## 9) Como funciona o *parser* da saída

`main.py` executa `app_4.py` e analisa cada linha com *regex* tolerante a variações (acentos, pontuação, “R$ …”). Extrai:

* **Fatores**: `fator_ipcae_antes`, `fator_ipcae_pos`, `fator_juros_2aa_simples`, e `meses_para_2aa`.
* **Valores monetários**: principal (original/apos_antes/pos_ipca/final), juros (base/apos_antes/final) e **total_corrigido**.
  Em seguida, grava em `esaj_calc_precatorio_resumo`. 

---

## 10) Dicas de diagnóstico

* **Sem índices suficientes**: se `--pos-fim` ultrapassa o último mês do CSV, use `--clip-pos` em `app_4.py` para ajustar automaticamente ao último mês disponível. 
* **Moeda com vírgula**: `main.py` normaliza “R$ 1.234,56” → `1234.56` antes de chamar o cálculo. 
* **Planilha “.xls” estranha**: `gerar_indices_csv.py` tenta **HTML embutido** com `lxml`/`bs4`/`html5lib`; use `--encoding-hint`/`--header-row`/`--table-index` para destravar. 
* **Conferência manual**: rode `app_4.py --debug` para ver **mês a mês** os fatores aplicados. 

---

## 11) Exemplos de ponta a ponta

1. **Gerar índices** (SIDRA) → **Calcular** (unitário):

```bash
python baixar_ipca_e.py --out indices_ipcae.csv
python app_4.py --principal 72500.00 --ano-venc 2020 --indices-csv indices_ipcae.csv --juros-mora-ant 0 --debug
```

2. **Gerar de XLS** → **Lote com DB**:

```bash
python gerar_indices_csv.py --xlsx ipca_hist.xls --sheet 0 --indice "IPCA-E" --out indices.csv --header-row 4
# ajuste --indices-csv se quiser usar 'indices.csv'
python main.py --limit 100 --verbose
```

---

## 12) Notas finais

* Este pipeline foi desenhado para **bater com memórias oficiais**, permitindo *overrides* de fatores **ANTES** e **PÓS** quando necessário. 
* A captura de saída e o *insert* foram construídos para serem **robustos a variações de formatação** e **NOT NULL** no destino. 

Se quiser, eu também disponibilizo um **`Makefile`/script de inicialização** com targets para `venv`, `indices`, `run-one`, `run-batch` — é só pedir.
