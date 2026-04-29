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


MONTHS_ES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


def _parse_date_any(s: str):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            if 2000 <= d.year <= 2099:
                return d
        except ValueError:
            pass
    return None


def _date_payload(d: datetime.date, periodo_label: str = "", fecha_texto: str = "") -> dict:
    return {
        "fecha_emision": d.strftime("%d/%m/%Y"),
        "suc": d.strftime("%m%y"),
        "periodo_label": periodo_label or d.strftime("%m/%Y"),
        "fecha_detectada_texto": fecha_texto,
    }


def extract_period_from_text(text: str) -> dict:
    """
    Fecha del comprobante: fecha de cierre / fin de período del resumen.
    No toma simplemente la última fecha del PDF, porque algunos resúmenes traen
    fechas sueltas de comprobantes/vencimientos que pueden pertenecer a otro período.
    Si no encuentra período explícito, usa el mes/año más frecuente del resumen y
    toma la última fecha dentro de ese mes.
    """
    raw = text or ""
    clean = re.sub(r"\s+", " ", raw).upper()

    period_patterns = [
        r"PER[IÍ]ODO\s*(?:DESDE|DEL)?\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:AL|A|HASTA|-)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"(?:DESDE|DEL)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:AL|A|HASTA|-)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"FECHA\s*(?:DE)?\s*(?:CIERRE|HASTA|FINAL|FIN)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"(?:CIERRE|HASTA)\s*(?:DEL\s*)?(?:RESUMEN|PER[IÍ]ODO|LOTE)?\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    ]
    for pat in period_patterns:
        m = re.search(pat, clean, flags=re.IGNORECASE)
        if not m:
            continue
        end_txt = m.group(m.lastindex)
        end_date = _parse_date_any(end_txt)
        if end_date:
            if m.lastindex and m.lastindex >= 2:
                periodo_label = f"{m.group(1)} al {m.group(2)}"
            else:
                periodo_label = end_date.strftime("%m/%Y")
            return _date_payload(end_date, periodo_label=periodo_label, fecha_texto=end_txt)

    m = re.search(r"(?:PER[IÍ]ODO|RESUMEN|LIQUIDACI[ÓO]N)\s*[:\-]?\s*(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPTIEMBRE|SETIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)\s+(20\d{2})", clean)
    if m:
        month = MONTHS_ES[m.group(1)]
        year = int(m.group(2))
        next_month = datetime.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        end_date = next_month - datetime.timedelta(days=1)
        return _date_payload(end_date, periodo_label=f"{month:02d}/{year}", fecha_texto=m.group(0))

    m = re.search(r"(?:PER[IÍ]ODO|RESUMEN|LIQUIDACI[ÓO]N)\s*[:\-]?\s*(\d{1,2})[/-](20\d{2}|\d{2})", clean)
    if m:
        month = int(m.group(1))
        year = int(m.group(2))
        if year < 100:
            year += 2000
        if 1 <= month <= 12:
            next_month = datetime.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            end_date = next_month - datetime.timedelta(days=1)
            return _date_payload(end_date, periodo_label=f"{month:02d}/{year}", fecha_texto=m.group(0))

    date_matches = list(re.finditer(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", clean))
    fechas_validas = []
    for m in date_matches:
        d = _parse_date_any(m.group(1))
        if not d:
            continue
        ctx = clean[max(0, m.start() - 40): m.end() + 40]
        is_due = bool(re.search(r"VENC|PAGO\s+MINIMO|PAGO\s+M[IÍ]NIMO", ctx))
        fechas_validas.append({"pos": m.start(), "txt": m.group(1), "date": d, "due": is_due})

    operativas = [x for x in fechas_validas if not x["due"]]
    base = operativas if operativas else fechas_validas

    if base:
        counts = {}
        for item in base:
            key = (item["date"].year, item["date"].month)
            counts[key] = counts.get(key, 0) + 1
        modal_key = sorted(counts.items(), key=lambda kv: (kv[1], kv[0][0], kv[0][1]), reverse=True)[0][0]
        candidates = [x for x in base if (x["date"].year, x["date"].month) == modal_key]
        chosen = sorted(candidates, key=lambda x: (x["date"], x["pos"]))[-1]
        return _date_payload(chosen["date"], periodo_label=f"{modal_key[1]:02d}/{modal_key[0]}", fecha_texto=chosen["txt"])

    today = datetime.date.today()
    return _date_payload(today, periodo_label=today.strftime("%m/%Y"), fecha_texto="")


def extract_file_metadata(pdf_bytes: bytes, filename: str = "") -> dict:
    text = read_pdf_text_from_bytes(pdf_bytes)
    bank = detect_bank_from_bytes(pdf_bytes, filename)
    period = extract_period_from_text(text)
    return {**bank, **period, "filename": filename}


# ============ CABAL ============

AMOUNT_RE = re.compile(r"[-−]?\s*\d+(?:\.\d{3})*,\d{2}")


def _line_amounts(line: str) -> list[float]:
    """
    Toma importes monetarios de una línea. Antes elimina porcentajes para no
    confundir 21,00% / 10,50% con montos.
    """
    clean_line = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", line or "")
    vals = []
    for m in AMOUNT_RE.finditer(clean_line):
        try:
            vals.append(abs(to_float_signed(m.group(0))))
        except Exception:
            pass
    return vals


def _last_amount(line: str) -> float:
    vals = _line_amounts(line)
    return vals[-1] if vals else 0.0


def _is_credit_or_reversal(line: str) -> bool:
    up = (line or "").upper()
    return bool(re.search(r"DEVOL|REINTEG|REVERS|ANUL|EXTORNO|CREDITO|CR[ÉE]DITO", up))


CABAL_PATTERNS = {
    "IVA_ARANCEL_21": re.compile(r"IVA S/ARANCEL DE DESCUENTO\s+21,00%.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "IVA_COSTO_10_5": re.compile(r"IVA S/COSTO FINANCIERO\s+10,50%.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "PERCEPCION_RG333": re.compile(r"PERCEPCION DE IVA RG 333.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "RETENCION_IB": re.compile(r"RETENCION DE INGRESOS BR.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
    "MENOS_IVA_21": re.compile(r"[-−]IVA\s+21,00%.*?([\d.]+,\d{2})\s*[-−]", re.IGNORECASE),
}


def extract_cabal_exact(text: str) -> dict:
    """
    Cabal robusto por línea. El backend anterior dependía de regex con puntos de
    miles y signo final; si el PDF cambiaba mínimamente el formato, perdía importes.
    """
    tot = {"iva_arancel": 0.0, "iva_costo": 0.0, "percep_rg333": 0.0, "ret_iibb": 0.0, "menos_iva": 0.0}

    for raw_line in (text or "").splitlines():
        line = raw_line.replace("\xa0", " ").replace("−", "-")
        up = line.upper()
        amount = _last_amount(line)
        if not amount:
            continue

        if "IVA S/ARANCEL DE DESCUENTO" in up and re.search(r"21[,\.]00\s*%", up):
            tot["iva_arancel"] += amount
        elif "IVA S/COSTO FINANCIERO" in up and re.search(r"10[,\.]50\s*%", up):
            tot["iva_costo"] += amount
        elif re.search(r"PERCEPCI[ÓO]N\s*(DE\s*)?IVA", up) and re.search(r"(RG|R\.G\.?)\s*333|3337|2408", up):
            tot["percep_rg333"] += amount
        elif re.search(r"RETENCI[ÓO]N", up) and re.search(r"INGRESOS\s*BR|ING\.?\s*BR|IIBB", up):
            tot["ret_iibb"] += amount
        elif re.search(r"^\s*[-]?\s*IVA\s+21[,\.]00\s*%", up):
            tot["menos_iva"] += amount

    # Fallback de seguridad: conserva los patrones validados si aparecieran líneas no capturadas.
    regex_tot = {"iva_arancel": 0.0, "iva_costo": 0.0, "percep_rg333": 0.0, "ret_iibb": 0.0, "menos_iva": 0.0}
    for key, rx in CABAL_PATTERNS.items():
        for m in rx.finditer(text or ""):
            val = abs(to_float_signed(m.group(1)))
            if key == "IVA_ARANCEL_21":
                regex_tot["iva_arancel"] += val
            elif key == "IVA_COSTO_10_5":
                regex_tot["iva_costo"] += val
            elif key == "PERCEPCION_RG333":
                regex_tot["percep_rg333"] += val
            elif key == "RETENCION_IB":
                regex_tot["ret_iibb"] += val
            elif key == "MENOS_IVA_21":
                regex_tot["menos_iva"] += val

    # Usa el mayor por rubro para no duplicar cuando ambos métodos capturan la misma línea.
    for k in tot:
        tot[k] = max(round2(tot[k]), round2(regex_tot[k]))
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
    """
    Extractor universal robusto por línea.
    Evita perder montos cuando el PDF no usa separador de miles o mueve el signo.
    Clasifica por concepto y toma el último importe monetario de cada línea.
    """
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

    for raw_line in (text or "").splitlines():
        line = raw_line.replace("\xa0", " ").replace("−", "-")
        up = line.upper()
        amount = _last_amount(line)
        if not amount:
            continue

        is_perc = bool(re.search(r"PERCEPCI[ÓO]N|PERCEP\.?", up))
        is_ret = bool(re.search(r"RETENCI[ÓO]N|RET\.?", up))
        is_iibb = bool(re.search(r"INGRESOS\s*BRUTOS|ING\.?\s*BR|IIBB|IBB", up))
        is_iva = "IVA" in up

        if is_ret and is_iibb:
            tot["ret_iibb"] += amount
            continue
        if is_ret and is_iva:
            tot["ret_iva"] += amount
            continue
        if is_ret and re.search(r"GANANCIAS|IMP\.?\s*GAN", up):
            tot["ret_gcias"] += amount
            continue
        if is_perc and is_iibb:
            tot["perc_iibb"] += amount
            continue
        if is_perc and is_iva:
            # Se mantiene todo dentro de Percepción IVA para exportar P007.
            if re.search(r"1[,\.]50\s*%", up):
                tot["perc_15"] += amount
            elif re.search(r"3[,\.]00\s*%", up):
                tot["perc_30"] += amount
            else:
                tot["perc_qr3337"] += amount
            continue

        if "CARGO TERMINAL FISERV" in up or "CARGO" in up and "TERMINAL" in up and "FISERV" in up:
            tot["gasto_fiserv"] += amount
            continue

        # IVA débito/crédito fiscal. Excluye percepciones/retenciones ya capturadas.
        if is_iva and not is_perc and not is_ret:
            if re.search(r"10[,\.]50\s*%|10[,\.]5\s*%|25063|COSTO\s*FINANCIERO", up):
                tot["iva105"] += amount
                continue
            if re.search(r"21[,\.]00\s*%|21\s*%", up) or re.search(r"DTO\s*F\.?OTORG|SERV\.?\s*OPER\.?\s*INT|SIST\s*CUOTAS|ARANCEL", up):
                tot["iva21"] += amount
                continue

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
            page_uni = extract_universal(text)

            if any(v != 0.0 for v in page_cabal.values()):
                saw_cabal = True
                for k in cabal_tot:
                    cabal_tot[k] += page_cabal[k]

                # En páginas Cabal no sumamos IVA/percepción IVA universal para no duplicar.
                # Sí rescatamos conceptos que Cabal exacto no cubre.
                for k in ("perc_iibb", "gasto_fiserv", "ret_iva", "ret_gcias"):
                    uni_tot[k] += page_uni.get(k, 0.0)
            else:
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


# ============ Resumen operativo agrupado ============
def build_resumen_operativo_agrupado(resumen_df: pd.DataFrame) -> pd.DataFrame:
    """
    Vista operativa para pantalla/PDF: separa neto e IVA para que el control
    sea legible sin mezclar base + impuesto.
    El Excel de detalle operativo conserva todos los movimientos por archivo.
    """
    if resumen_df is None or resumen_df.empty:
        return pd.DataFrame(columns=["Concepto", "Monto Total"])

    def total(concepto: str) -> float:
        return get_amount(resumen_df, concepto)

    rows = [
        {"Concepto": "Neto al 21%", "Monto Total": total("Base Neto 21%")},
        {"Concepto": "IVA al 21%", "Monto Total": total("IVA 21% (Total)")},
        {"Concepto": "Neto al 10,5%", "Monto Total": total("Base Neto 10,5%")},
        {"Concepto": "IVA al 10,5%", "Monto Total": total("IVA 10,5% (Total)")},
        {"Concepto": "Exentos", "Monto Total": total("Gastos Exentos")},
        {"Concepto": "Percepción IVA", "Monto Total": total("Percepciones IVA (Total)")},
        {"Concepto": "Percepción IIBB", "Monto Total": total("Percepciones IIBB")},
        {"Concepto": "Retención IVA", "Monto Total": total("Retenciones IVA")},
        {"Concepto": "Retención IIBB", "Monto Total": total("Retenciones IBB")},
        {"Concepto": "Retención Ganancias", "Monto Total": total("Retenciones Ganancias")},
    ]
    out = pd.DataFrame(rows)
    out = out[out["Monto Total"].round(2) != 0].reset_index(drop=True)

    if not out.empty:
        total_general = round2(out["Monto Total"].sum())
        out = pd.concat([
            out,
            pd.DataFrame([{"Concepto": "TOTAL", "Monto Total": total_general}]),
        ], ignore_index=True)

    return out


# ============ Holistor compras ============
def _base_invoice_row(meta: dict, secuencia: int) -> dict:
    return {
        "Fecha Emisión ": meta.get("fecha_emision", ""),
        "Fecha Recepción": meta.get("fecha_emision", ""),
        "Cpbte": "RB",
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
    already_has_total = False

    for _, row in resumen_df.iterrows():
        concepto = str(row["Concepto"])
        val = float(row["Monto Total"])
        if concepto.strip().upper() == "TOTAL":
            already_has_total = True
        else:
            total_general += val
        data.append([concepto, format_money(val)])

    if agregar_total_general and not already_has_total:
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
