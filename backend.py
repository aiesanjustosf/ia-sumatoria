import re
import io
import os
import datetime
import pdfplumber
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# ============ Config Holistor ============
HOLISTOR_COLUMNS = [
    "Fecha Emisión ",
    "Fecha Recepción",
    "Cpbte",
    "Tipo",
    "Suc.",
    "Número",
    "Razón Social/Denominación Proveedor",
    "Tipo Doc.",
    "CUIT",
    "Domicilio",
    "C.P.",
    "Pcia",
    "Cond Fisc",
    "Cód. Neto",
    "Neto Gravado",
    "Alíc.",
    "IVA Liquidado",
    "IVA Crédito",
    "Cód. NG/EX",
    "Conceptos NG/EX",
    "Cód. P/R",
    "Perc./Ret.",
    "Pcia P/R",
    "Total",
]

# Ajustable si aparece otro banco emisor.
BANK_MASTER = {
    "MACRO": {
        "razon_social": "BANCO MACRO S.A.",
        "cuit": "30500010084",
        "domicilio": "AV. EDUARDO MADERO 1182",
        "cp": "C1106",
        "pcia": "00",
        "cond_fisc": "RI",
    },
    "NBSF": {
        "razon_social": "NUEVO BANCO DE SANTA FE S.A.",
        "cuit": "30692432661",
        "domicilio": "TUCUMAN 2545",
        "cp": "3000",
        "pcia": "21",
        "cond_fisc": "RI",
    },
    "BNA": {
        "razon_social": "BANCO DE LA NACION ARGENTINA",
        "cuit": "30500010912",
        "domicilio": "BARTOLOME MITRE 326",
        "cp": "C1036",
        "pcia": "00",
        "cond_fisc": "RI",
    },
    "CREDICOOP": {
        "razon_social": "BANCO CREDICOOP COOPERATIVO LTDO.",
        "cuit": "30571421352",
        "domicilio": "RECONQUISTA 484",
        "cp": "1003",
        "pcia": "00",
        "cond_fisc": "RI",
    },
    "NO_DETECTADO": {
        "razon_social": "BANCO EMISOR NO DETECTADO",
        "cuit": "",
        "domicilio": "",
        "cp": "",
        "pcia": "",
        "cond_fisc": "RI",
    },
}

# Códigos pedidos para movimientos / Holistor.
COD_NETO_21 = "524"
COD_NETO_105 = "624"
COD_EXENTO = "524"
COD_PERC_IVA = "P007"
COD_RET_IVA = "R007"
COD_RET_IIBB = "R006"
COD_PERC_IIBB = "P006"

# ============ Utils ============
def to_float_signed(s: str) -> float:
    s = (s or "").strip().replace("−", "-").replace(" ", "")
    if not s:
        return 0.0
    return float(s.replace(".", "").replace(",", "."))


def format_money(x: float) -> str:
    return f"{float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def round2(x) -> float:
    return round(float(x or 0), 2)


def read_pdf_text_from_bytes(pdf_bytes: bytes, max_pages: int | None = None) -> str:
    texts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for page in pages:
            texts.append((page.extract_text() or "").replace("\xa0", " ").replace("−", "-"))
    return "\n".join(texts)


# ============ Detección banco / período ============
def detect_bank_key_from_text(text: str, filename: str = "") -> str:
    source = f"{filename}\n{text}".upper()

    if "NUEVO BANCO DE SANTA FE" in source or "BANCO SANTA FE" in source or "NBSF" in source:
        return "NBSF"
    if "BANCO MACRO" in source or re.search(r"\bMACRO\b", source):
        return "MACRO"
    if "BANCO DE LA NACION" in source or "BANCO NACION" in source or "BNA" in source:
        return "BNA"
    if "CREDICOOP" in source:
        return "CREDICOOP"

    return "NO_DETECTADO"


def detect_bank_from_bytes(pdf_bytes: bytes, filename: str = "") -> dict:
    try:
        text = read_pdf_text_from_bytes(pdf_bytes, max_pages=2)
    except Exception:
        text = ""
    key = detect_bank_key_from_text(text, filename)
    data = BANK_MASTER.get(key, BANK_MASTER["NO_DETECTADO"]).copy()
    data["key"] = key
    data["banco"] = data["razon_social"]
    return data


def extract_period_from_text(text: str) -> dict:
    """
    Devuelve:
    - fecha_emision: último día del período detectado, o fecha actual.
    - suc: MMYY para usar como punto de comprobante.
    - periodo_label: texto del período detectado.
    """
    clean = re.sub(r"\s+", " ", text or " ").upper()

    patterns = [
        r"PER[IÍ]ODO\s*(?:DESDE)?\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:AL|A|HASTA|-)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        r"PER[IÍ]ODO\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:AL|A|HASTA|-)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        r"DESDE\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:AL|A|HASTA)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        r"CIERRE\s*(?:DE\s*)?(?:LOTE|PRESENTACI[ÓO]N|RESUMEN)?[^\d]{0,20}(\d{1,2}/\d{1,2}/\d{2,4})",
        r"FECHA\s*(?:DE\s*)?(?:CIERRE|RESUMEN)[^\d]{0,20}(\d{1,2}/\d{1,2}/\d{2,4})",
    ]

    def parse_date(s):
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    for pat in patterns:
        m = re.search(pat, clean, flags=re.IGNORECASE)
        if not m:
            continue
        if len(m.groups()) >= 2:
            d1 = parse_date(m.group(1))
            d2 = parse_date(m.group(2))
            date_ref = d2 or d1
            label = f"{m.group(1)} al {m.group(2)}"
        else:
            date_ref = parse_date(m.group(1))
            label = m.group(1)
        if date_ref:
            return {
                "fecha_emision": date_ref.strftime("%d/%m/%Y"),
                "suc": date_ref.strftime("%m%y"),
                "periodo_label": label,
            }

    today = datetime.date.today()
    return {
        "fecha_emision": today.strftime("%d/%m/%Y"),
        "suc": today.strftime("%m%y"),
        "periodo_label": today.strftime("%m/%Y"),
    }


def extract_file_metadata(pdf_bytes: bytes, filename: str = "") -> dict:
    text = read_pdf_text_from_bytes(pdf_bytes)
    bank = detect_bank_from_bytes(pdf_bytes, filename)
    period = extract_period_from_text(text)
    return {**bank, **period, "filename": filename}


# ============ CABAL ============
CABAL_PATTERNS = {
    "IVA_ARANCEL_21": re.compile(r"IVA S/ARANCEL DE DESCUENTO\s+21,00%.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "IVA_COSTO_10_5": re.compile(r"IVA S/COSTO FINANCIERO\s+10,50%.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "PERCEPCION_RG333": re.compile(r"PERCEPCION DE IVA RG 333.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "RETENCION_IB": re.compile(r"RETENCION DE INGRESOS BR.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "MENOS_IVA_21": re.compile(r"[-−]IVA\s+21,00%.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
}


def extract_cabal_exact(text: str) -> dict:
    tot = {"iva_arancel": 0.0, "iva_costo": 0.0, "percep_rg333": 0.0, "ret_iibb": 0.0, "menos_iva": 0.0}
    for key, rx in CABAL_PATTERNS.items():
        for m in rx.finditer(text):
            val = abs(to_float_signed(m.group(1)))
            if key == "IVA_ARANCEL_21":
                tot["iva_arancel"] += val
            elif key == "IVA_COSTO_10_5":
                tot["iva_costo"] += val
            elif key == "PERCEPCION_RG333":
                tot["percep_rg333"] += val
            elif key == "RETENCION_IB":
                tot["ret_iibb"] += val
            elif key == "MENOS_IVA_21":
                tot["menos_iva"] += val
    return tot


# ============ UNIVERSAL Visa/Master/Maestro ============
RX_IVA21_ANY = re.compile(
    r"(IVA[^\n]{0,200}?21,00\s*%)[^\n]{0,120}?([\-−]?\s?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_IVA21_RI_DTO_FOT = re.compile(
    r"(IVA\s*RI\s*CRED\.?\s*FISC\.?\s*COMERCIO\s*S/DTO\s*F\.?OTORG)[^\d\-−]{0,120}"
    r"([\-−]?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_IVA21_RI_SERV_INT = re.compile(
    r"(IVA\s*RI\s*SERV\.?\s*OPER\.?\s*INT\.?)\s*[^\d\-−]{0,120}"
    r"([\-−]?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_IVA21_RI_SIST_CUOTAS = re.compile(
    r"(IVA\s*RI\s*SIST\s*CUOTAS)[^\d\-−]{0,120}"
    r"([\-−]?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_IVA105 = re.compile(
    r"(IVA\s*CRED\.?\s*FISC\.?\s*COM\.?\s*L\.?\s*25063\s*S/DTO\s*F\.?OTOR\s*10,50%|"
    r"IVA\s*S/COSTO\s*FINANCIERO\s*10,50%)"
    r"[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_PERC_IVA_30 = re.compile(
    r"PERCEPCI[ÓO]N\s*IVA\s*(?:R\.?\s*G\.?|RG)\s*2408\s*3,00\s*%[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_PERC_IVA_15 = re.compile(
    r"PERCEPCI[ÓO]N\s*IVA\s*(?:R\.?\s*G\.?|RG)\s*2408\s*1,50\s*%[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_PERC_IVA_QR3337 = re.compile(
    r"QR\s*PERCEPCION\s*IVA\s*3337[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_GASTO_TERMINAL_FISERV = re.compile(
    r"CARGO\s*TERMINAL\s*FISERV[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RX_RET_IIBB = re.compile(r"RETENCION\s*ING\.?\s*BRUTOS[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
RX_RET_IVA = re.compile(r"RETENCI[ÓO]N\s*IVA[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
RX_RET_GCIAS = re.compile(r"RETENCI[ÓO]N\s*(IMP\.?\s*GANANCIAS|GANANCIAS)[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)

# Si aparece una percepción IIBB explícita, la mandamos a P006.
RX_PERC_IIBB = re.compile(
    r"PERCEPCI[ÓO]N\s*(?:DE\s*)?(?:ING\.?\s*BRUTOS|IIBB|INGRESOS\s*BRUTOS)[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)


def extract_universal(text: str) -> dict:
    tot = {
        "iva21": 0.0,
        "iva105": 0.0,
        "perc_30": 0.0,
        "perc_15": 0.0,
        "perc_qr3337": 0.0,
        "perc_iibb": 0.0,
        "gasto_fiserv": 0.0,
        "ret_iibb": 0.0,
        "ret_iva": 0.0,
        "ret_gcias": 0.0,
    }
    for m in RX_IVA21_ANY.finditer(text):
        tot["iva21"] += abs(to_float_signed(m.group(2)))
    for m in RX_IVA21_RI_DTO_FOT.finditer(text):
        tot["iva21"] += abs(to_float_signed(m.group(2)))
    for m in RX_IVA21_RI_SERV_INT.finditer(text):
        tot["iva21"] += abs(to_float_signed(m.group(2)))
    for m in RX_IVA21_RI_SIST_CUOTAS.finditer(text):
        tot["iva21"] += abs(to_float_signed(m.group(2)))
    for m in RX_IVA105.finditer(text):
        tot["iva105"] += abs(to_float_signed(m.group(2)))
    for m in RX_PERC_IVA_30.finditer(text):
        tot["perc_30"] += abs(to_float_signed(m.group(1)))
    for m in RX_PERC_IVA_15.finditer(text):
        tot["perc_15"] += abs(to_float_signed(m.group(1)))
    for m in RX_PERC_IVA_QR3337.finditer(text):
        tot["perc_qr3337"] += abs(to_float_signed(m.group(1)))
    for m in RX_PERC_IIBB.finditer(text):
        tot["perc_iibb"] += abs(to_float_signed(m.group(1)))
    for m in RX_GASTO_TERMINAL_FISERV.finditer(text):
        tot["gasto_fiserv"] += abs(to_float_signed(m.group(1)))
    for m in RX_RET_IIBB.finditer(text):
        tot["ret_iibb"] += abs(to_float_signed(m.group(1)))
    for m in RX_RET_IVA.finditer(text):
        tot["ret_iva"] += abs(to_float_signed(m.group(1)))
    for m in RX_RET_GCIAS.finditer(text):
        tot["ret_gcias"] += abs(to_float_signed(m.group(2)))
    return {k: round2(v) for k, v in tot.items()}


# ============ Router + Consolidación ============
def extract_resumen_from_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    cabal_tot = {"iva_arancel": 0.0, "iva_costo": 0.0, "percep_rg333": 0.0, "ret_iibb": 0.0, "menos_iva": 0.0}
    uni_tot = {
        "iva21": 0.0,
        "iva105": 0.0,
        "perc_30": 0.0,
        "perc_15": 0.0,
        "perc_qr3337": 0.0,
        "perc_iibb": 0.0,
        "gasto_fiserv": 0.0,
        "ret_iibb": 0.0,
        "ret_iva": 0.0,
        "ret_gcias": 0.0,
    }
    saw_cabal = False

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = (page.extract_text() or "").replace("\xa0", " ").replace("−", "-")
            page_cabal = extract_cabal_exact(text)
            if any(v != 0.0 for v in page_cabal.values()):
                saw_cabal = True
                for k in cabal_tot:
                    cabal_tot[k] += page_cabal[k]
            else:
                page_uni = extract_universal(text)
                for k in uni_tot:
                    uni_tot[k] += page_uni[k]

    if saw_cabal:
        iva21 = round2(cabal_tot["iva_arancel"] + cabal_tot["menos_iva"])
        base21 = round2(iva21 / 0.21) if iva21 else 0.0
        iva105 = round2(cabal_tot["iva_costo"])
        base105 = round2(iva105 / 0.105) if iva105 else 0.0
        percep_iva = round2(cabal_tot["percep_rg333"] + uni_tot["perc_qr3337"] + uni_tot["perc_30"] + uni_tot["perc_15"])
        gastos_exentos = round2(uni_tot["gasto_fiserv"])
        perc_iibb = round2(uni_tot["perc_iibb"])
        ret_iibb = round2(cabal_tot["ret_iibb"] + uni_tot["ret_iibb"])
        ret_iva = round2(uni_tot["ret_iva"])
        ret_gcs = round2(uni_tot["ret_gcias"])
    else:
        iva21 = round2(uni_tot["iva21"])
        base21 = round2(iva21 / 0.21) if iva21 else 0.0
        iva105 = round2(uni_tot["iva105"])
        base105 = round2(iva105 / 0.105) if iva105 else 0.0
        percep_iva = round2(uni_tot["perc_30"] + uni_tot["perc_15"] + uni_tot["perc_qr3337"])
        gastos_exentos = round2(uni_tot["gasto_fiserv"])
        perc_iibb = round2(uni_tot["perc_iibb"])
        ret_iibb = round2(uni_tot["ret_iibb"])
        ret_iva = round2(uni_tot["ret_iva"])
        ret_gcs = round2(uni_tot["ret_gcias"])

    resumen = pd.DataFrame(
        {
            "Concepto": [
                "Base Neto 21%",
                "IVA 21% (Total)",
                "Base Neto 10,5%",
                "IVA 10,5% (Total)",
                "Percepciones IVA (Total)",
                "Percepciones IIBB",
                "Gastos Exentos",
                "Retenciones IBB",
                "Retenciones IVA",
                "Retenciones Ganancias",
            ],
            "Monto Total": [
                base21,
                iva21,
                base105,
                iva105,
                percep_iva,
                perc_iibb,
                gastos_exentos,
                ret_iibb,
                ret_iva,
                ret_gcs,
            ],
        }
    )
    return resumen


def get_amount(resumen_df: pd.DataFrame, concepto: str) -> float:
    if resumen_df is None or resumen_df.empty:
        return 0.0
    mask = resumen_df["Concepto"].astype(str).str.upper().eq(concepto.upper())
    if not mask.any():
        return 0.0
    return round2(resumen_df.loc[mask, "Monto Total"].sum())


# ============ Holistor compras ============
def _base_invoice_row(meta: dict, secuencia: int) -> dict:
    return {
        "Fecha Emisión ": meta.get("fecha_emision", ""),
        "Fecha Recepción": meta.get("fecha_emision", ""),
        "Cpbte": "FC",
        "Tipo": "A",
        "Suc.": meta.get("suc", ""),
        "Número": str(secuencia).zfill(8),
        "Razón Social/Denominación Proveedor": meta.get("razon_social", ""),
        "Tipo Doc.": "80",
        "CUIT": meta.get("cuit", ""),
        "Domicilio": meta.get("domicilio", ""),
        "C.P.": meta.get("cp", ""),
        "Pcia": meta.get("pcia", ""),
        "Cond Fisc": meta.get("cond_fisc", "RI"),
        "Cód. Neto": "",
        "Neto Gravado": 0.0,
        "Alíc.": "",
        "IVA Liquidado": 0.0,
        "IVA Crédito": 0.0,
        "Cód. NG/EX": "",
        "Conceptos NG/EX": 0.0,
        "Cód. P/R": "",
        "Perc./Ret.": 0.0,
        "Pcia P/R": "",
        "Total": 0.0,
    }


def build_holistor_compras_from_resumen(resumen_df: pd.DataFrame, meta: dict, secuencia: int = 1) -> pd.DataFrame:
    """
    Genera filas para importación de compras Holistor.
    Regla aplicada:
    - Proveedor = razón social del banco emisor.
    - CUIT = CUIT del banco emisor.
    - TD = 80.
    - Suc. = período detectado en formato MMYY.
    - Número = secuencia 00000001, 00000002, etc.
    - 21%: Cód. Neto 524.
    - 10,5%: Cód. Neto 624.
    - Exento: Cód. NG/EX 524.
    - Percepción IVA: P007.
    - Retención IVA: R007.
    - Retención IIBB: R006.
    - Percepción IIBB: P006.
    """
    rows = []

    base21 = get_amount(resumen_df, "Base Neto 21%")
    iva21 = get_amount(resumen_df, "IVA 21% (Total)")
    base105 = get_amount(resumen_df, "Base Neto 10,5%")
    iva105 = get_amount(resumen_df, "IVA 10,5% (Total)")
    perc_iva = get_amount(resumen_df, "Percepciones IVA (Total)")
    perc_iibb = get_amount(resumen_df, "Percepciones IIBB")
    gastos_exentos = get_amount(resumen_df, "Gastos Exentos")
    ret_iibb = get_amount(resumen_df, "Retenciones IBB")
    ret_iva = get_amount(resumen_df, "Retenciones IVA")

    total_cbte = round2(base21 + iva21 + base105 + iva105 + gastos_exentos + perc_iva + perc_iibb + ret_iibb + ret_iva)

    if base21 or iva21:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. Neto": COD_NETO_21,
            "Neto Gravado": round2(base21),
            "Alíc.": "21,000",
            "IVA Liquidado": round2(iva21),
            "IVA Crédito": round2(iva21),
            "Total": total_cbte,
        })
        rows.append(r)

    if base105 or iva105:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. Neto": COD_NETO_105,
            "Neto Gravado": round2(base105),
            "Alíc.": "10,500",
            "IVA Liquidado": round2(iva105),
            "IVA Crédito": round2(iva105),
            "Total": total_cbte,
        })
        rows.append(r)

    if gastos_exentos:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. NG/EX": COD_EXENTO,
            "Conceptos NG/EX": round2(gastos_exentos),
            "Total": total_cbte,
        })
        rows.append(r)

    if perc_iva:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. P/R": COD_PERC_IVA,
            "Perc./Ret.": round2(perc_iva),
            "Total": total_cbte,
        })
        rows.append(r)

    if perc_iibb:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. P/R": COD_PERC_IIBB,
            "Perc./Ret.": round2(perc_iibb),
            "Total": total_cbte,
        })
        rows.append(r)

    if ret_iibb:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. P/R": COD_RET_IIBB,
            "Perc./Ret.": round2(ret_iibb),
            "Total": total_cbte,
        })
        rows.append(r)

    if ret_iva:
        r = _base_invoice_row(meta, secuencia)
        r.update({
            "Cód. P/R": COD_RET_IVA,
            "Perc./Ret.": round2(ret_iva),
            "Total": total_cbte,
        })
        rows.append(r)

    return pd.DataFrame(rows, columns=HOLISTOR_COLUMNS)


# ============ PDF builder ============
def build_report_pdf(resumen_df: pd.DataFrame, out_path: str, titulo: str, agregar_total_general: bool = True):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    normal = styles["Normal"]
    h2 = styles["Heading2"]

    doc = SimpleDocTemplate(out_path, pagesize=A4)
    story = []

    story.append(Paragraph(titulo, title_style))
    story.append(Paragraph(f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}", normal))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Resumen de importes", h2))

    data = [["Concepto", "Monto ($)"]]
    total_general = 0.0
    for _, row in resumen_df.iterrows():
        val = float(row["Monto Total"])
        total_general += val
        data.append([str(row["Concepto"]), format_money(val)])

    if agregar_total_general:
        data.append(["TOTAL GENERAL", format_money(total_general)])

    tbl = Table(data, colWidths=[360, 140])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.HexColor("#f7f7f7"), colors.white]),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e6f2ff")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(tbl)

    doc.build(story)
    return out_path
