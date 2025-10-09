from __future__ import annotations
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from datetime import date

# ======= núcleo numérico =======
getcontext().prec = 28
TWOPLACES = Decimal("0.01")

def D(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))

def quantize_cents(x: Decimal) -> Decimal:
    return x.quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)

def annual_to_monthly_rate(annual_rate: Decimal) -> Decimal:
    a = D(annual_rate)
    return (Decimal((1 + float(a)) ** (1/12)) - 1)

def compose_factors(series: List[Decimal], extra_monthly_rate: Decimal = D(0)) -> Decimal:
    total = Decimal("1")
    for m in series:
        total *= (Decimal("1") + D(m) + D(extra_monthly_rate))
    return total

# ======= modelos =======

IndiceLiteral = Literal["IPCA-E", "TR", "SELIC", "INPC", "Poupança"]
IncideIRLiteral = Literal["Não", "RRA", "Tabela progressiva", "Percentual informado"]
TipoLiteral = Literal["Alimentar", "Comum"]

class FatoresIndice(BaseModel):
    antes_formacao: List[Decimal] = Field(default_factory=list, description="taxas mensais antes da formação")
    graca: List[Decimal] = Field(default_factory=list, description="taxas mensais no período de graça")
    pos_graca: List[Decimal] = Field(default_factory=list, description="taxas mensais após o grace")

    class Config:
        json_encoders = {Decimal: lambda v: str(v)}

class FaixaIR(BaseModel):
    ate: Decimal                     # teto da faixa (base de cálculo do mês/competência)
    aliquota: Decimal                # ex.: 0.075 = 7,5%
    parcela_deduzir: Decimal = D(0)  # parcela a deduzir

    class Config:
        json_encoders = {Decimal: lambda v: str(v)}

class CalcInput(BaseModel):
    # campos conforme sua UI
    precatorio: str
    tipo: TipoLiteral
    ano_vencimento: int
    data_ultima_liquidacao: date

    valor_precatorio: Decimal
    principal: Decimal

    # juros (da UI): anual "juros de mora" para etapa 1
    juros_mora: Decimal = Field(Decimal("0.06"), description="taxa anual antes da formação (ex.: 0.06)")

    indice_usado_sentenca: IndiceLiteral

    incide_ir: IncideIRLiteral
    ir_rate: Optional[Decimal] = None                 # usar quando incide_ir='Percentual informado'
    tabela_ir: Optional[List[FaixaIR]] = None         # usar quando RRA / Tabela progressiva

    # fatores do índice por período (frações mensais, ex.: 0.0032 = 0,32%/mês)
    fatores_indice: FatoresIndice

    # políticas pós-EC (mantidas como padrão; ajuste se necessário)
    juros_aa_pos_grace: Decimal = Field(Decimal("0.02"), description="2% a.a. após o grace")

    class Config:
        json_encoders = {Decimal: lambda v: str(v)}

class CalcOutput(BaseModel):
    # eco de parâmetros úteis
    precatorio: str
    tipo: TipoLiteral
    ano_vencimento: int
    indice_usado_sentenca: IndiceLiteral
    incide_ir: IncideIRLiteral

    # resultados
    principal_atualizado: Decimal
    juros_mora_antes: Decimal
    juros_mora_posteriores: Decimal
    valor_bruto_precatorio: Decimal
    ir_calculado: Decimal
    base_calculo_liquida: Decimal
    valor_liquido_cedivel: Decimal

    # mensagens (ex.: tabela de IR ausente)
    ir_notice: Optional[str] = None

    class Config:
        json_encoders = {Decimal: lambda v: str(v)}

# ======= app =======

app = FastAPI(title="Calculadora de Precatórios (EC-136/25)")

@app.get("/health")
def health():
    return {"status": "ok"}

def calcular_ir(modo: IncideIRLiteral, base_juros: Decimal,
                ir_rate: Optional[Decimal],
                tabela_ir: Optional[List[FaixaIR]]) -> (Decimal, Optional[str]):
    """
    Calcula IR segundo o modo:
      - 'Não' -> 0
      - 'Percentual informado' -> base_juros * ir_rate
      - 'RRA' / 'Tabela progressiva' -> aplica tabela enviada (faixas ordenadas por 'ate')
    Retorna (valor_ir, aviso_opcional)
    """
    if modo == "Não":
        return D(0), None

    if modo == "Percentual informado":
        if not ir_rate or ir_rate <= 0:
            return D(0), "incide_ir='Percentual informado' mas 'ir_rate' não foi informado (>0)."
        return quantize_cents(base_juros * ir_rate), None

    if modo in ("RRA", "Tabela progressiva"):
        if not tabela_ir or len(tabela_ir) == 0:
            return D(0), f"incide_ir='{modo}' mas 'tabela_ir' não foi fornecida. IR=0 aplicado."
        # ordena por teto
        faixas = sorted(tabela_ir, key=lambda f: f.ate)
        base = base_juros
        # aplica última faixa cujo 'ate' seja >= base
        faixa_aplicada = None
        for faixa in faixas:
            if base <= faixa.ate:
                faixa_aplicada = faixa
                break
        if faixa_aplicada is None:
            faixa_aplicada = faixas[-1]
        ir = base * faixa_aplicada.aliquota - faixa_aplicada.parcela_deduzir
        if ir < 0:
            ir = D(0)
        return quantize_cents(ir), None

    return D(0), "Modo de IR desconhecido."

@app.post("/calcular", response_model=CalcOutput)
def calcular(payload: CalcInput):
    # --- 1) Antes da formação: índice + juros_mora (anual -> mensal) ---
    r_mensal_antes = annual_to_monthly_rate(payload.juros_mora)
    fator_antes = compose_factors(payload.fatores_indice.antes_formacao or [], r_mensal_antes)
    principal_apos_antes = quantize_cents(payload.principal * fator_antes)
    juros_antes = quantize_cents(principal_apos_antes - payload.principal)

    # --- 2) Período de graça: somente índice ---
    fator_graca = compose_factors(payload.fatores_indice.graca or [], D(0))
    principal_apos_graca = quantize_cents(principal_apos_antes * fator_graca)

    # --- 3) Após o grace: índice + 2% a.a. (padrão EC) ---
    r_mensal_pos = annual_to_monthly_rate(payload.juros_aa_pos_grace)
    fator_pos = compose_factors(payload.fatores_indice.pos_graca or [], r_mensal_pos)
    principal_final = quantize_cents(principal_apos_graca * fator_pos)

    # Isolar juros posteriores: (com juros) - (somente índice)
    fator_ipca_pos_apenas = compose_factors(payload.fatores_indice.pos_graca or [], D(0))
    apenas_indice_pos = quantize_cents(principal_apos_graca * fator_ipca_pos_apenas)
    juros_posteriores = quantize_cents(principal_final - apenas_indice_pos)

    valor_bruto = principal_final

    # --- IR conforme escolha ---
    ir, notice = calcular_ir(
        modo=payload.incide_ir,
        base_juros=juros_posteriores,  # por padrão IR só sobre juros posteriores; ajuste se sua regra exigir
        ir_rate=payload.ir_rate,
        tabela_ir=payload.tabela_ir
    )

    base_liquida = quantize_cents(valor_bruto - ir)
    # não há campo de “superpreferência” nessa nova definição — se quiser, é só adicionar
    valor_liquido_cedivel = base_liquida

    return CalcOutput(
        precatorio=payload.precatorio,
        tipo=payload.tipo,
        ano_vencimento=payload.ano_vencimento,
        indice_usado_sentenca=payload.indice_usado_sentenca,
        incide_ir=payload.incide_ir,
        principal_atualizado=principal_final,
        juros_mora_antes=juros_antes,
        juros_mora_posteriores=juros_posteriores,
        valor_bruto_precatorio=valor_bruto,
        ir_calculado=ir,
        base_calculo_liquida=base_liquida,
        valor_liquido_cedivel=valor_liquido_cedivel,
        ir_notice=notice
    )
