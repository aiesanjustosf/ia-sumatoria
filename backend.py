import re
import io
import datetime
import pdfplumber
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# ============ Config Holistor ============
HOLISTOR_COLUMNS = [
    "Fecha Emisión ", "Fecha Recepción", "Cpbte", "Tipo", "Suc.", "Número",
    "Razón Social/Denominación Proveedor", "Tipo Doc.", "CUIT", "Domicilio", "C.P.", "Pcia",
    "Cond Fisc", "Cód. Neto", "Neto Gravado", "Alíc.", "IVA Liquidado", "IVA Crédito",
    "Cód. NG/EX", "Conceptos NG/EX", "Cód. P/R", "Perc./Ret.", "Pcia P/R", "Total",
]

BANK_MASTER = {
    "MACRO": {"razon_social": "BANCO MACRO S.A.", "cuit": "30500010084", "domicilio": "AV. EDUARDO MADERO 1182", "cp": "C1106", "pcia": "00", "cond_fisc": "RI"},
    "NBSF": {"razon_social": "NUEVO BANCO DE SANTA FE S.A.", "cuit": "30692432661", "domicilio": "TUCUMAN 2545", "cp": "3000", "pcia": "21", "cond_fisc": "RI"},
    "BNA": {"razon_social": "BANCO DE LA NACION ARGENTINA", "cuit": "30500010912", "domicilio": "BARTOLOME MITRE 326", "cp": "C1036", "pcia": "00", "cond_fisc": "RI"},
    "CREDICOOP": {"razon_social": "BANCO CREDICOOP COOPERATIVO LTDO.", "cuit": "30571421352", "domicilio": "RECONQUISTA 484", "cp": "1003", "pcia": "00", "cond_fisc": "RI"},
    "NO_DETECTADO": {"razon_social": "BANCO EMISOR NO DETECTADO", "cuit": "", "domicilio": "", "cp": "", "pcia": "", "cond_fisc": "RI"},
}

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
    s = s.replace("$", "")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    if not s:
        return 0.0
    # AR format: 1.234,56. Also accepts 1234,56.
    if "," in s:
        val = float(s.replace(".", "").replace(",", "."))
    else:
        val = float(s)
    return -val if neg else val


def format_money(x: float) -> str:
    return f"{float(x or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def round2(x) -> float:
    return round(float(x or 0), 2)


def read_pdf_text_from_bytes(pdf_bytes: bytes, max_pages: int | None = None) -> str:
    texts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for page in pages:
            texts.append((page.extract_text() or "").replace("\xa0", " ").replace("−", "-"))
    return "\n".join(texts)

# ============ Banco / fecha ============
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


def _parse_date_any(s: str):
    s = (s or "").strip().replace("-", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            if 2020 <= d.year <= 2035:
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


def extract_period_from_text(text: str, filename: str = "") -> dict:
    """
    Regla actual pedida: usar la última fecha que aparezca en el resumen procesado.
    Se toma por posición dentro del texto del PDF, no una fecha guardada ni la fecha actual.
    Si no encuentra fechas, intenta inferir mes/año desde nombre tipo 2602 / 202602.
    """
    clean = (text or "").replace("−", "-")
    matches = list(re.finditer(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", clean))
    parsed = []
    for m in matches:
        d = _parse_date_any(m.group(1))
        if d:
            parsed.append((m.start(), m.group(1), d))
    if parsed:
        pos, txt, d = sorted(parsed, key=lambda x: x[0])[-1]
        return _date_payload(d, periodo_label=d.strftime("%m/%Y"), fecha_texto=txt)

    # Fallback por nombre de archivo: 2602 => febrero 2026, 202602 => febrero 2026.
    fname = filename or ""
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", fname)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        next_month = datetime.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        return _date_payload(next_month - datetime.timedelta(days=1), periodo_label=f"{month:02d}/{year}", fecha_texto=m.group(0))
    m = re.search(r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(?!\d)", fname)
    if m:
        year, month = int(m.group(1)) + 2000, int(m.group(2))
        next_month = datetime.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        return _date_payload(next_month - datetime.timedelta(days=1), periodo_label=f"{month:02d}/{year}", fecha_texto=m.group(0))

    today = datetime.date.today()
    return _date_payload(today, periodo_label=today.strftime("%m/%Y"), fecha_texto="")


def extract_file_metadata(pdf_bytes: bytes, filename: str = "") -> dict:
    text = read_pdf_text_from_bytes(pdf_bytes)
    bank = detect_bank_from_bytes(pdf_bytes, filename)
    period = extract_period_from_text(text, filename=filename)
    return {**bank, **period, "filename": filename}

# ============ Extracción de importes ============
# Captura 1.234,56 / 1234,56 / -1.234,56 / (1.234,56). No captura porcentajes.
AMOUNT_RE = re.compile(r"(?<![\d/])(?:\(?[-−]?\s*\$?\s*\d{1,3}(?:\.\d{3})*,\d{2}\)?|\(?[-−]?\s*\$?\s*\d{4,},\d{2}\)?|\(?[-−]?\s*\$?\s*\d+,\d{2}\)?)(?!\s*%)(?![/\d])")


def _line_amounts(line: str) -> list[float]:
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


def _first_amount(line: str) -> float:
    vals = _line_amounts(line)
    return vals[0] if vals else 0.0


def _is_reversal(line: str) -> bool:
    up = (line or "").upper()
    return bool(re.search(r"DEVOL|REINTEG|REVERS|ANUL|EXTORNO|CONTRA\s*CARGO|AJUSTE\s*A\s*FAVOR", up))


def _signed_amount_for_tax(line: str) -> float:
    amount = _last_amount(line)
    return -amount if _is_reversal(line) else amount


def extract_tax_lines(text: str) -> dict:
    """
    Extractor único por línea. Usa conceptos amplios para Visa/Master/Maestro/Cabal.
    Además toma base directa cuando la línea de IVA trae base + alícuota + impuesto.
    """
    tot = {
        "base21_direct": 0.0, "iva21": 0.0,
        "base105_direct": 0.0, "iva105": 0.0,
        "perc_iva": 0.0, "perc_iibb": 0.0,
        "exento": 0.0,
        "ret_iibb": 0.0, "ret_iva": 0.0, "ret_gcias": 0.0,
    }
    seen = set()

    for raw_line in (text or "").splitlines():
        line = raw_line.replace("\xa0", " ").replace("−", "-")
        up = re.sub(r"\s+", " ", line.upper()).strip()
        if not up:
            continue
        amounts = _line_amounts(line)
        if not amounts:
            continue

        # evita duplicar líneas repetidas por extracción de pdfplumber en una misma página
        key_base = re.sub(r"\s+", " ", up)

        is_perc = bool(re.search(r"PERCEPCI[ÓO]N|PERCEP\.?|PERC\.?,?", up))
        is_ret = bool(re.search(r"RETENCI[ÓO]N|RET\.?,?", up))
        is_iibb = bool(re.search(r"INGRESOS\s*BRUTOS|ING\.?\s*BR|IIBB|IBB|I\.B\.?", up))
        is_iva = "IVA" in up or "I.V.A" in up
        amount = _signed_amount_for_tax(line)

        def add_once(cat: str, val: float):
            k = (cat, key_base, round2(abs(val)))
            if k not in seen:
                tot[cat] += val
                seen.add(k)

        if is_ret and is_iibb:
            add_once("ret_iibb", amount); continue
        if is_ret and is_iva:
            add_once("ret_iva", amount); continue
        if is_ret and re.search(r"GANANCIAS|IMP\.?\s*GAN|GAN\.?", up):
            add_once("ret_gcias", amount); continue
        if is_perc and is_iibb:
            add_once("perc_iibb", amount); continue
        if is_perc and is_iva:
            add_once("perc_iva", amount); continue

        # Exentos/gastos sin IVA habituales de resúmenes de tarjetas.
        if re.search(r"CARGO\s*TERMINAL|FISERV|GASTOS?\s*EXENT|SELLADO|SEGURO|MANTENIMIENTO\s*TERMINAL", up):
            # Si la línea también dice IVA, no la tratamos como exenta.
            if not is_iva:
                add_once("exento", amount); continue

        # IVA 10,5%.
        if is_iva and re.search(r"10[,\.]50\s*%|10[,\.]5\s*%|25063|COSTO\s*FINANCIERO", up):
            add_once("iva105", amount)
            if len(amounts) >= 2:
                add_once("base105_direct", amounts[0])
            continue

        # IVA 21%.
        if is_iva and (
            re.search(r"21[,\.]00\s*%|21\s*%", up)
            or re.search(r"ARANCEL|DTO\s*F\.?OTORG|DESCUENTO|SERV\.?\s*OPER\.?\s*INT|SIST\s*CUOTAS", up)
        ):
            add_once("iva21", amount)
            if len(amounts) >= 2:
                add_once("base21_direct", amounts[0])
            continue

        # Base directa si hay líneas separadas del neto sin la palabra IVA.
        if not is_iva and not is_ret and not is_perc:
            if re.search(r"ARANCEL\s*DE\s*DESCUENTO|DTO\s*F\.?OTORG|DESCUENTO\s*F\.?OTORG|SERV\.?\s*OPER\.?\s*INT|SIST\s*CUOTAS", up):
                add_once("base21_direct", amount); continue
            if re.search(r"COSTO\s*FINANCIERO|FINANCIACI[ÓO]N", up):
                add_once("base105_direct", amount); continue

    return {k: round2(v) for k, v in tot.items()}

# ============ Compatibilidad con backend anterior ============
def extract_cabal_exact(text: str) -> dict:
    t = extract_tax_lines(text)
    return {
        "iva_arancel": t["iva21"],
        "iva_costo": t["iva105"],
        "percep_rg333": t["perc_iva"],
        "ret_iibb": t["ret_iibb"],
        "menos_iva": 0.0,
    }


def extract_universal(text: str) -> dict:
    t = extract_tax_lines(text)
    return {
        "iva21": t["iva21"], "iva105": t["iva105"],
        "perc_30": 0.0, "perc_15": 0.0, "perc_qr3337": t["perc_iva"], "perc_iibb": t["perc_iibb"],
        "gasto_fiserv": t["exento"],
        "ret_iibb": t["ret_iibb"], "ret_iva": t["ret_iva"], "ret_gcias": t["ret_gcias"],
    }

# ============ Router ============
def extract_resumen_from_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    totals = {
        "base21_direct": 0.0, "iva21": 0.0,
        "base105_direct": 0.0, "iva105": 0.0,
        "perc_iva": 0.0, "perc_iibb": 0.0,
        "exento": 0.0,
        "ret_iibb": 0.0, "ret_iva": 0.0, "ret_gcias": 0.0,
    }

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = (page.extract_text() or "").replace("\xa0", " ").replace("−", "-")
            page_tot = extract_tax_lines(text)
            for k in totals:
                totals[k] += page_tot.get(k, 0.0)

    iva21 = round2(totals["iva21"])
    base21_calc = round2(iva21 / 0.21) if iva21 else 0.0
    base21 = round2(max(abs(totals["base21_direct"]), abs(base21_calc)))

    iva105 = round2(totals["iva105"])
    base105_calc = round2(iva105 / 0.105) if iva105 else 0.0
    base105 = round2(max(abs(totals["base105_direct"]), abs(base105_calc)))

    resumen = pd.DataFrame({
        "Concepto": [
            "Base Neto 21%", "IVA 21% (Total)",
            "Base Neto 10,5%", "IVA 10,5% (Total)",
            "Percepciones IVA (Total)", "Percepciones IIBB", "Gastos Exentos",
            "Retenciones IBB", "Retenciones IVA", "Retenciones Ganancias",
        ],
        "Monto Total": [
            base21, iva21,
            base105, iva105,
            round2(totals["perc_iva"]), round2(totals["perc_iibb"]), round2(totals["exento"]),
            round2(totals["ret_iibb"]), round2(totals["ret_iva"]), round2(totals["ret_gcias"]),
        ],
    })
    return resumen


def get_amount(resumen_df: pd.DataFrame, concepto: str) -> float:
    if resumen_df is None or resumen_df.empty:
        return 0.0
    mask = resumen_df["Concepto"].astype(str).str.upper().eq(concepto.upper())
    if not mask.any():
        return 0.0
    return round2(resumen_df.loc[mask, "Monto Total"].sum())

# ============ Resumen agrupado ============
def build_resumen_operativo_agrupado(resumen_df: pd.DataFrame) -> pd.DataFrame:
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
        out = pd.concat([out, pd.DataFrame([{"Concepto": "TOTAL", "Monto Total": round2(out["Monto Total"].sum())}])], ignore_index=True)
    return out

# ============ Holistor ============
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
        "Cód. Neto": "", "Neto Gravado": 0.0, "Alíc.": "", "IVA Liquidado": 0.0, "IVA Crédito": 0.0,
        "Cód. NG/EX": "", "Conceptos NG/EX": 0.0, "Cód. P/R": "", "Perc./Ret.": 0.0, "Pcia P/R": "", "Total": 0.0,
    }


def build_holistor_compras_from_resumen(resumen_df: pd.DataFrame, meta: dict, secuencia: int = 1) -> pd.DataFrame:
    rows = []
    base21 = get_amount(resumen_df, "Base Neto 21%")
    iva21 = get_amount(resumen_df, "IVA 21% (Total)")
    base105 = get_amount(resumen_df, "Base Neto 10,5%")
    iva105 = get_amount(resumen_df, "IVA 10,5% (Total)")
    perc_iva = get_amount(resumen_df, "Percepciones IVA (Total)")
    perc_iibb = get_amount(resumen_df, "Percepciones IIBB")
    exento = get_amount(resumen_df, "Gastos Exentos")
    ret_iibb = get_amount(resumen_df, "Retenciones IBB")
    ret_iva = get_amount(resumen_df, "Retenciones IVA")

    total_cbte = round2(base21 + iva21 + base105 + iva105 + exento + perc_iva + perc_iibb + ret_iibb + ret_iva)

    if base21 or iva21:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. Neto": COD_NETO_21, "Neto Gravado": base21, "Alíc.": "21,000", "IVA Liquidado": iva21, "IVA Crédito": iva21, "Total": total_cbte})
        rows.append(r)
    if base105 or iva105:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. Neto": COD_NETO_105, "Neto Gravado": base105, "Alíc.": "10,500", "IVA Liquidado": iva105, "IVA Crédito": iva105, "Total": total_cbte})
        rows.append(r)
    if exento:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. NG/EX": COD_EXENTO, "Conceptos NG/EX": exento, "Total": total_cbte})
        rows.append(r)
    if perc_iva:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. P/R": COD_PERC_IVA, "Perc./Ret.": perc_iva, "Total": total_cbte})
        rows.append(r)
    if perc_iibb:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. P/R": COD_PERC_IIBB, "Perc./Ret.": perc_iibb, "Total": total_cbte})
        rows.append(r)
    if ret_iibb:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. P/R": COD_RET_IIBB, "Perc./Ret.": ret_iibb, "Total": total_cbte})
        rows.append(r)
    if ret_iva:
        r = _base_invoice_row(meta, secuencia)
        r.update({"Cód. P/R": COD_RET_IVA, "Perc./Ret.": ret_iva, "Total": total_cbte})
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
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.HexColor("#f7f7f7"), colors.white]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e6f2ff")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(tbl)
    doc.build(story)
    return out_path
