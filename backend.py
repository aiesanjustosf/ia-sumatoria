# backend.py
# — IA AIE · Sumatoria de resúmenes — Alfonso A.
# Procesa PDFs (Visa/Master/Cabal/Fiserv), arma un detalle y una grilla-resumen.
# Ajustes pedidos:
#  - IVA 21% (Total) incluye: IVA RI SERV.OPER. INT. + IVA RI SIST CUOTAS + cualquier IVA 21%
#  - Nuevas filas en grilla: "Otras per. de IVA" (QR PERCEPCION IVA 3337) y "Gastos Exentos" (CARGO TERMINAL FISERV)
#  - Se mantienen las filas de Neto (no se eliminan)

from __future__ import annotations
import io
import re
import pdfplumber
import pandas as pd
from typing import List, Tuple

# =========================
# Utilidades
# =========================
AMOUNT_RX = r"-?\s?\d{1,3}(?:\.\d{3})*,\d{2}"

def _to_float(s: str) -> float:
    if not isinstance(s, str):
        return float(s or 0)
    s = s.replace("−", "-").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def _sum(df: pd.DataFrame, rx: re.Pattern) -> float:
    if df.empty:
        return 0.0
    m = df["Concepto"].fillna("").str.contains(rx, na=False)
    return float(df.loc[m, "Monto Total"].sum())

def _find_all_amounts(text: str, rx: re.Pattern) -> float:
    total = 0.0
    for m in rx.finditer(text):
        total += _to_float(m.group(1))
    return total

# =========================
# Patrones conceptuales
# =========================
# IVA 21%: general + especificos (pedidos)
RX_IVA21_GENERIC = re.compile(rf"IVA[^\n]{{0,120}}21[,\.]?\s*%", re.I)
RX_IVA21_SERV_INT = re.compile(r"IVA\s*RI\s*SERV\.?\s*OPER\.?\s*INT\.?", re.I)
RX_IVA21_SIST_CUOTAS = re.compile(r"IVA\s*RI\s*SIST\s*CUOTAS", re.I)  # << NUEVO (pedidos)

# IVA 10,5%
RX_IVA105_GENERIC = re.compile(r"(IVA[^\n]{0,120}10[,\.]?\s*5\s*%|10,50%)", re.I)

# Percepciones IVA RG 2408 (1,5 / 3,0)
RX_PERC_IVA_RG2408 = re.compile(r"PERCEPCI[ÓO]N\s*IVA\s*(?:RG|R\.?\s*G\.?)\s*2408", re.I)

# Otras Percepciones IVA (QR 3337)
RX_PERC_IVA_QR3337 = re.compile(r"QR\s*PERCEPCION\s*IVA\s*3337", re.I)  # << NUEVO (grilla)

# Gastos exentos (Cargo Terminal Fiserv)
RX_GASTO_FISERV = re.compile(r"CARGO\s*TERMINAL\s*FISERV", re.I)         # << NUEVO (grilla)

# Retenciones
RX_RET_IIBB = re.compile(r"RETENCION(?:ES)?\s+I(?:N)?GRESOS?\s*BR|RETENCION(?:ES)?\s+IBB", re.I)
RX_RET_IVA  = re.compile(r"RETENCION(?:ES)?\s+IVA\b", re.I)
RX_RET_GAN  = re.compile(r"RETENCION(?:ES)?\s+GANANCIAS", re.I)

# Bases Netas
RX_BASE_NETO_21  = re.compile(r"BASE\s*NETO\s*21", re.I)
RX_BASE_NETO_105 = re.compile(r"BASE\s*NETO\s*10[,\.]?\s*5", re.I)

# =========================
# Parsing PDF -> Detalle
# =========================
LINE_RX = re.compile(rf"^(?P<concepto>.+?)\s+(?P<monto>{AMOUNT_RX})\s*$")

def _pdf_to_lines(pdf_bytes: bytes) -> List[str]:
    lines: List[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            for raw in txt.splitlines():
                line = raw.strip()
                if line:
                    lines.append(line)
    return lines

def _detalle_from_lines(lines: List[str]) -> pd.DataFrame:
    rows = []
    for ln in lines:
        m = LINE_RX.search(ln)
        if not m:
            # si el PDF no separa bien, igual capturamos montos al final
            # heurística: último token con formato importe
            tokens = ln.split()
            if not tokens:
                continue
            tail = tokens[-1]
            if re.fullmatch(AMOUNT_RX, tail):
                concepto = ln[: ln.rfind(tail)].strip(" .-—\t")
                monto = tail
            else:
                continue
        else:
            concepto = m.group("concepto").strip(" .-—\t")
            monto = m.group("monto")
        rows.append({"Concepto": concepto, "Monto Total": _to_float(monto)})
    if not rows:
        return pd.DataFrame(columns=["Concepto", "Monto Total"])
    df = pd.DataFrame(rows)
    # Normalizamos espacios múltiples
    df["Concepto"] = df["Concepto"].str.replace(r"\s+", " ", regex=True)
    return df

# =========================
# Resumen / Grilla
# =========================
def _calcular_iva21(df: pd.DataFrame) -> float:
    # Total IVA 21% = genérico + SERV.OPER.INT + SIST CUOTAS (pedido)
    tot = 0.0
    tot += _sum(df, RX_IVA21_GENERIC)
    tot += _sum(df, RX_IVA21_SERV_INT)
    tot += _sum(df, RX_IVA21_SIST_CUOTAS)  # << incluye SIST CUOTAS
    return tot

def _calcular_iva105(df: pd.DataFrame) -> float:
    return _sum(df, RX_IVA105_GENERIC)

def _calcular_base_neto(df: pd.DataFrame, tasa: float, iva_total: float) -> float:
    # Si ya viene impreso "Base Neto XX" en el resumen bancario, lo priorizamos.
    rx = RX_BASE_NETO_21 if tasa == 0.21 else RX_BASE_NETO_105
    impreso = _sum(df, rx)
    if abs(impreso) > 0:
        return impreso
    # Si no está impreso, lo estimamos a partir del IVA detectado.
    if tasa > 0:
        return iva_total / tasa
    return 0.0

def construir_resumen(detalle: pd.DataFrame) -> pd.DataFrame:
    iva21 = _calcular_iva21(detalle)
    iva105 = _calcular_iva105(detalle)

    base21 = _calcular_base_neto(detalle, 0.21, iva21)
    base105 = _calcular_base_neto(detalle, 0.105, iva105)

    perc_iva_total = _sum(detalle, RX_PERC_IVA_RG2408)
    otras_perc_iva = _sum(detalle, RX_PERC_IVA_QR3337)  # << NUEVO
    gastos_exentos = _sum(detalle, RX_GASTO_FISERV)     # << NUEVO

    ret_iibb = _sum(detalle, RX_RET_IIBB)
    ret_iva  = _sum(detalle, RX_RET_IVA)
    ret_gan  = _sum(detalle, RX_RET_GAN)

    filas = [
        {"Concepto": "Base Neto 21%",          "Monto Total": base21},
        {"Concepto": "IVA 21% (Total)",        "Monto Total": iva21},  # incluye SIST CUOTAS + SERV.OPER.INT
        {"Concepto": "Base Neto 10,5%",        "Monto Total": base105},
        {"Concepto": "IVA 10,5% (Total)",      "Monto Total": iva105},
        {"Concepto": "Percepciones IVA (Total)","Monto Total": perc_iva_total},
        {"Concepto": "Otras per. de IVA",      "Monto Total": otras_perc_iva},  # << NUEVO
        {"Concepto": "Gastos Exentos",         "Monto Total": gastos_exentos},  # << NUEVO
        {"Concepto": "Retenciones IBB",        "Monto Total": ret_iibb},
        {"Concepto": "Retenciones IVA",        "Monto Total": ret_iva},
        {"Concepto": "Retenciones Ganancias",  "Monto Total": ret_gan},
    ]
    resumen = pd.DataFrame(filas)
    return resumen

# API principal usada por app.py
def procesar_pdfs(file_bytes_list: List[bytes]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Retorna (resumen, detalle)
    - detalle: concatenación de todos los detalles de cada PDF
    - resumen: grilla con totales solicitados
    """
    detalles = []
    for fb in file_bytes_list:
        lines = _pdf_to_lines(fb)
        det = _detalle_from_lines(lines)
        if not det.empty:
            detalles.append(det)

    if detalles:
        detalle_all = pd.concat(detalles, ignore_index=True)
    else:
        detalle_all = pd.DataFrame(columns=["Concepto", "Monto Total"])

    resumen = construir_resumen(detalle_all)
    return resumen, detalle_all

