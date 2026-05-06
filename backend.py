
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

COD_NETO_21 = "524"
COD_NETO_105 = "624"
COD_EXENTO = "524"
COD_PERC_IVA = "P007"
COD_RET_IVA = "R007"
COD_RET_IIBB = "R006"
COD_PERC_IIBB = "P006"

CONCEPT_ORDER = [
    "Base Neto 21%",
    "IVA 21% (Total)",
    "Base Neto 10,5%",
    "IVA 10,5% (Total)",
    "Gastos Exentos",
    "Percepciones IVA (Total)",
    "Percepciones IIBB",
    "Retenciones IVA",
    "Retenciones IBB",
    "Retenciones Ganancias",
]

# ============ Utils ============
def to_float_signed(s: str) -> float:
    """
    Convierte importes argentinos conservando el signo real del importe.

    Soporta estos formatos:
      1.234,56
      -1.234,56
      1.234,56-
      (1.234,56)
      $ 1.234,56
      $ 1.234,56-

    Importante: el guion inicial de las líneas tipo "- ARANCEL $ 100,00"
    es viñeta/concepto del resumen, no signo del importe. El signo se toma
    del texto capturado como importe, no del comienzo de la descripción.
    """
    s = (s or "").strip().replace("−", "-").replace("$", "").replace(" ", "")
    if not s:
        return 0.0

    neg = False

    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]

    if s.startswith("-"):
        neg = True
        s = s[1:]

    if s.endswith("-"):
        neg = True
        s = s[:-1]

    if not s:
        return 0.0

    val = float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)
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
    if (
        "BANCO DE LA NACION" in source
        or "BCO DE LA NACION" in source
        or "BANCO NACION" in source
        or "BNA" in source
    ):
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
            continue
    return None


def _date_payload(d: datetime.date, periodo_label: str = "", fecha_texto: str = "") -> dict:
    return {
        "fecha_emision": d.strftime("%d/%m/%Y"),
        "suc": d.strftime("%m%y"),
        "periodo_label": periodo_label or d.strftime("%m/%Y"),
        "fecha_detectada_texto": fecha_texto,
    }


def _extract_payment_date_from_text(text: str):
    clean = (text or "").replace("\xa0", " ")

    # 1) Fecha real de acreditación/pago. Evita tomar la fecha impresa al pie, por ejemplo 10/04/2026.
    patterns = [
        r"Forma\s+de\s+Pago:.*?el\s+d[ií]a:\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        r"ACRED\.[^\n]*?\s(\d{1,2}/\d{1,2}/\d{2,4})\s+\d{5,}",
        r"Fecha\s+de\s+Pago:\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    ]

    for pat in patterns:
        for m in re.finditer(pat, clean, flags=re.IGNORECASE | re.DOTALL):
            d = _parse_date_any(m.group(1))
            if d:
                return d, m.group(1)

    # 2) En estos resúmenes Fiserv la fecha de pago suele aparecer en el encabezado,
    # cerca de "Fecha de Pago:", como una línea suelta.
    upper = clean.upper()
    pos = upper.find("FECHA DE PAGO")
    if pos >= 0:
        window = clean[pos:pos + 1500]
        for m in re.finditer(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", window):
            d = _parse_date_any(m.group(1))
            if d and not _looks_like_footer_date(window, m.start()):
                return d, m.group(1)

    # 3) Fallback: primera fecha válida que no sea pie de página.
    for m in re.finditer(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", clean):
        if _looks_like_footer_date(clean, m.start()):
            continue
        d = _parse_date_any(m.group(1))
        if d:
            return d, m.group(1)

    return None, ""


def _looks_like_footer_date(text: str, start_pos: int) -> bool:
    before = text[max(0, start_pos - 20):start_pos]
    return bool(re.search(r"\d+\s+de\s+\d+\s*$", before, flags=re.IGNORECASE))


def extract_period_from_text(text: str, filename: str = "") -> dict:
    d, raw = _extract_payment_date_from_text(text)
    if d:
        return _date_payload(d, periodo_label=d.strftime("%m/%Y"), fecha_texto=raw)

    # Fallback por nombre de archivo: 2602 => 02/2026.
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
    text = read_pdf_text_from_bytes(pdf_bytes, max_pages=3)
    bank = detect_bank_from_bytes(pdf_bytes, filename)
    period = extract_period_from_text(text, filename=filename)
    return {**bank, **period, "filename": filename}

# ============ Split de liquidaciones ============
def _extract_liq_number(text: str) -> str:
    clean = (text or "").replace("\xa0", " ")

    # Prioridad: pie o línea de forma de pago.
    m = re.search(r"Nro\.\s*Liq:\s*(\d{5,})", clean, flags=re.IGNORECASE)
    if m:
        return m.group(1)

    # Algunos PDFs tienen "Fecha Nro. Liquidación:" y el número aparece más abajo.
    pos = clean.upper().find("NRO. LIQUID")
    if pos >= 0:
        window = clean[pos:pos + 900]
        nums = re.findall(r"(?m)^\s*(\d{5,})\s*$", window)
        if nums:
            return nums[0]
        nums = re.findall(r"\b(\d{5,})\b", window)
        if nums:
            return nums[0]

    # Formato: ACRED... FECHA LIQ
    m = re.search(r"ACRED\.[^\n]*?\s\d{1,2}/\d{1,2}/\d{2,4}\s+(\d{5,})", clean, flags=re.IGNORECASE)
    if m:
        return m.group(1)

    return ""


def extract_card_documents_from_bytes(pdf_bytes: bytes, filename: str = "") -> list[dict]:
    """
    Divide el PDF por liquidaciones contiguas.

    Importante: no deduplica por número de liquidación en todo el PDF.
    Si una misma liquidación aparece dos veces en partes separadas de un PDF unido,
    se procesa como dos comprobantes, igual que si el usuario hubiese subido dos archivos.
    En cambio, las páginas consecutivas de una misma liquidación se consolidan como un solo comprobante.
    """
    docs: list[dict] = []
    current = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").replace("\xa0", " ").replace("−", "-")
            liq = _extract_liq_number(text) or f"SIN_LIQ_{page_idx:04d}"

            if current is None or current["liq_number"] != liq:
                occurrence = 1 + sum(1 for d in docs if d["liq_number"] == liq)
                current = {
                    "doc_id": f"{liq}__{occurrence}",
                    "liq_number": liq,
                    "pages": [],
                    "text": "",
                }
                docs.append(current)

            current["pages"].append(page_idx)
            current["text"] += "\n" + text

    out = []
    for doc in docs:
        text = doc["text"]
        date_d, raw = _extract_payment_date_from_text(text)
        if not date_d:
            date_d = datetime.date.today()
            raw = ""
        period = _date_payload(date_d, fecha_texto=raw)
        bank_key = detect_bank_key_from_text(text, filename)
        out.append({
            **doc,
            **period,
            "bank_key": bank_key,
            "pages_label": ",".join(str(p) for p in doc["pages"]),
        })
    return out

# ============ Extracción de importes ============
AMOUNT_RE = re.compile(
    r"(?<![\d/])"
    # Importe con signo adelante opcional, $ opcional y signo final opcional.
    # El signo final es frecuente en contrapartidas: 138,24-
    r"\(?[-−]?\s*\$?\s*\d{1,3}(?:\.\d{3})*,\d{2}\s*[-−]?\)?"
    r"(?!\s*%)(?![/\d])"
)


def _line_amounts(line: str) -> list[float]:
    # Quita porcentajes para que 21,00% / 3,00% no se tomen como importes.
    # Conserva el signo propio del importe, por ejemplo 138,24-.
    clean_line = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", line or "")
    vals = []
    for m in AMOUNT_RE.finditer(clean_line):
        try:
            vals.append(to_float_signed(m.group(0)))
        except Exception:
            continue
    return vals


def _is_reversal(line: str) -> bool:
    up = (line or "").upper()
    return bool(re.search(r"DEVOL|REINTEG|REVERS|ANUL|EXTORNO|CONTRA\s*CARGO|AJUSTE\s+A\s*FAVOR", up))


def _amount_from_line(line: str) -> float:
    vals = _line_amounts(line)
    if not vals:
        return 0.0

    amount = vals[-1]

    # Devuelve el signo real del importe.
    # Luego extract_tax_lines() convierte a valor absoluto porque el resumen
    # operativo busca gastos, no flujo financiero de pagos.
    if amount < 0:
        return amount

    # Si no viene firmado, solo se invierte cuando la descripción indica
    # reverso/devolución/ajuste a favor.
    return -amount if _is_reversal(line) else amount


def extract_tax_lines(text: str) -> dict:
    """
    Extrae líneas fiscales de tarjetas Fiserv / Cabal / Visa / Master / Maestro.
    En estos resúmenes el ARANCEL es el neto gravado 21% y el IVA aparece discriminado.
    """
    tot = {
        "base21_direct": 0.0,
        "iva21": 0.0,
        "base105_direct": 0.0,
        "iva105": 0.0,
        "perc_iva": 0.0,
        "perc_iibb": 0.0,
        "exento": 0.0,
        "ret_iibb": 0.0,
        "ret_iva": 0.0,
        "ret_gcias": 0.0,
    }

    # Evita duplicar dentro del mismo comprobante cuando el mismo concepto aparece en tabla y luego en resumen,
    # por ejemplo QR PERCEPCION IVA 3337.
    seen_cat_amount = set()
    seen_line = set()

    def add(cat: str, val: float, line_key: str, dedupe_amount: bool = True):
        val = round2(val)
        if val == 0:
            return

        line_id = (cat, re.sub(r"\s+", " ", line_key).strip(), abs(val))
        if line_id in seen_line:
            return
        seen_line.add(line_id)

        if dedupe_amount:
            amt_id = (cat, abs(val))
            if amt_id in seen_cat_amount:
                return
            seen_cat_amount.add(amt_id)

        tot[cat] = round2(tot[cat] + val)

    for raw_line in (text or "").splitlines():
        line = raw_line.replace("\xa0", " ").replace("−", "-")
        up = re.sub(r"\s+", " ", line.upper()).strip()
        if not up or not _line_amounts(line):
            continue

        # Resumen operativo de GASTOS:
        # en estos resúmenes las contrapartidas pueden venir con importe negativo
        # (ej.: ARANCEL $ 138,24- / IVA $ 29,03-), pero para el control de
        # gastos deben computarse como gasto igualmente. Por eso usamos valor
        # absoluto para clasificar importes fiscales/operativos.
        amount = abs(_amount_from_line(line))

        is_iva = bool(re.search(r"\bIVA\b|I\.V\.A", up))
        is_perc = bool(re.search(r"PERCEPCI[ÓO]N|PERCEP\.?|PERC\.?", up))
        is_ret = bool(re.search(r"RETENCI[ÓO]N|RET\.?", up))
        is_iibb = bool(re.search(r"INGRESOS\s*BRUTOS|ING\.?\s*BRUTOS|ING\.?\s*BR|IIBB|IBB|I\.B\.?", up))
        is_gan = bool(re.search(r"GANANCIAS|IMP\.?\s*GAN", up))

        if is_ret and is_iibb:
            add("ret_iibb", amount, up)
            continue
        if is_ret and is_iva:
            add("ret_iva", amount, up)
            continue
        if is_ret and is_gan:
            add("ret_gcias", amount, up)
            continue
        if is_perc and is_iibb:
            add("perc_iibb", amount, up)
            continue
        if is_perc and is_iva:
            add("perc_iva", amount, up)
            continue

        # Neto gravado 21%: línea resumen "- ARANCEL $ ...".
        # No tomar filas de venta, solo líneas que comienzan en ARANCEL o descripciones de arancel/costo operativo.
        if not is_iva and not is_ret and not is_perc:
            if re.search(r"^\s*[-+]?\s*ARANCEL\b", up):
                add("base21_direct", amount, up)
                continue
            if re.search(r"ARANCEL\s+DE\s+DESCUENTO|DTO\.?\s*ARANCEL|DTO\s*F\.?OTORG|SERV\.?\s*OPER\.?\s*INT|SIST\s*CUOTAS", up):
                add("base21_direct", amount, up)
                continue

            # Fiserv / Visa / NBSF: descuento financiero del adquirente.
            # Es gasto gravado al 21% y suele venir separado de su IVA:
            #   - DTO S/VENTAS FIN ADQ CONT $ 953,05
            #   - IVA S/DTO FIN ADQ CONT 21,00% $ 200,14
            #   - DTO S/VENTAS FIN ADQ CUOTA $ 5.758,98
            #   - IVA S/DTO FIN ADQ CUOTA 21,00% $ 1.209,38
            # Se exige que la línea empiece con DTO para no confundirlo con
            # "+ VENTAS C/DTO ANTIC...", que es venta presentada y no gasto.
            if re.search(r"^\s*[-+]?\s*DTO\s*S\s*/?\s*VENTAS\s*FIN\s*ADQ\b", up):
                add("base21_direct", amount, up)
                continue
            if re.search(r"CARGO\s*TERMINAL|FISERV|GASTOS?\s*EXENT|SELLADO|SEGURO|MANTENIMIENTO\s*TERMINAL", up):
                add("exento", amount, up)
                continue
            if re.search(r"COSTO\s*FINANCIERO|FINANCIACI[ÓO]N", up):
                add("base105_direct", amount, up)
                continue

        if is_iva and re.search(r"10[,\.]50\s*%|10[,\.]5\s*%|25063|COSTO\s*FINANCIERO", up):
            add("iva105", amount, up)
            vals = _line_amounts(line)
            if len(vals) >= 2:
                add("base105_direct", abs(vals[0]), up, dedupe_amount=False)
            continue

        if is_iva and (
            re.search(r"21[,\.]00\s*%|21\s*%", up)
            or re.search(r"ARANC|DTO\s*F\.?OTORG|DESCUENTO|SERV\.?\s*OPER\.?\s*INT|SIST\s*CUOTAS", up)
        ):
            add("iva21", amount, up)
            vals = _line_amounts(line)
            if len(vals) >= 2:
                add("base21_direct", abs(vals[0]), up, dedupe_amount=False)
            continue

    # Resúmenes mensuales Visa/Credicoop: el resumen trae el bloque exacto
    # "Base Imponible IVA / Monto Gravado". En ese caso NO conviene sumar
    # solo ARANCEL diario, porque el neto 21 también incluye Servicio Payway / Cobro Anticipado.
    full_text = (text or "").replace("\xa0", " ").replace("−", "-")
    full_up = re.sub(r"\s+", " ", full_text.upper())

    base21_overrides = []
    base105_overrides = []

    # Formato en líneas separadas:
    # Base Imponible IVA Monto Gravado
    # Tasa 21,00 % $ 11.153,44
    # Tasa 10,50 % $ 20.937,28
    for m in re.finditer(
        r"TASA\s+21[,\.]00\s*%\s*\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2})",
        full_text,
        flags=re.IGNORECASE,
    ):
        # Solo usarlo como override si el documento efectivamente contiene el bloque de base imponible.
        if "BASE IMPONIBLE IVA" in full_up or "MONTO GRAVADO" in full_up:
            base21_overrides.append(abs(to_float_signed(m.group(1))))

    for m in re.finditer(
        r"TASA\s+10[,\.]50\s*%\s*\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2})",
        full_text,
        flags=re.IGNORECASE,
    ):
        if "BASE IMPONIBLE IVA" in full_up or "MONTO GRAVADO" in full_up:
            base105_overrides.append(abs(to_float_signed(m.group(1))))

    if base21_overrides:
        tot["base21_direct"] = round2(sum(base21_overrides))
    if base105_overrides:
        tot["base105_direct"] = round2(sum(base105_overrides))

    # En algunos resúmenes mensuales aparece como "Percep./Retenc.AFIP-DGI" sin especificar IVA.
    # Se lo envía a Percepción IVA para no perder el importe fiscal.
    if round2(tot.get("perc_iva", 0.0)) == 0:
        perc_afip = 0.0
        for m in re.finditer(
            r"PERCEP\.?\s*/\s*RETENC\.?\s*AFIP\s*-?\s*DGI\s*\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2})",
            full_text,
            flags=re.IGNORECASE,
        ):
            perc_afip += abs(to_float_signed(m.group(1)))
        if perc_afip:
            tot["perc_iva"] = round2(perc_afip)

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
        "iva21": t["iva21"],
        "iva105": t["iva105"],
        "perc_30": 0.0,
        "perc_15": 0.0,
        "perc_qr3337": t["perc_iva"],
        "perc_iibb": t["perc_iibb"],
        "gasto_fiserv": t["exento"],
        "ret_iibb": t["ret_iibb"],
        "ret_iva": t["ret_iva"],
        "ret_gcias": t["ret_gcias"],
    }

# ============ Router ============
def _rows_from_totals(totals: dict, extra: dict | None = None) -> list[dict]:
    iva21 = round2(totals.get("iva21", 0.0))
    base21_calc = round2(iva21 / 0.21) if iva21 else 0.0
    base21_direct = round2(totals.get("base21_direct", 0.0))

    # Si hay ARANCEL explícito, ese es el neto correcto. Si no, calcular por IVA.
    base21 = base21_direct if base21_direct else base21_calc

    iva105 = round2(totals.get("iva105", 0.0))
    base105_calc = round2(iva105 / 0.105) if iva105 else 0.0
    base105_direct = round2(totals.get("base105_direct", 0.0))
    base105 = base105_direct if base105_direct else base105_calc

    values = {
        "Base Neto 21%": base21,
        "IVA 21% (Total)": iva21,
        "Base Neto 10,5%": base105,
        "IVA 10,5% (Total)": iva105,
        "Gastos Exentos": round2(totals.get("exento", 0.0)),
        "Percepciones IVA (Total)": round2(totals.get("perc_iva", 0.0)),
        "Percepciones IIBB": round2(totals.get("perc_iibb", 0.0)),
        "Retenciones IVA": round2(totals.get("ret_iva", 0.0)),
        "Retenciones IBB": round2(totals.get("ret_iibb", 0.0)),
        "Retenciones Ganancias": round2(totals.get("ret_gcias", 0.0)),
    }

    rows = []
    for concepto in CONCEPT_ORDER:
        val = values.get(concepto, 0.0)
        if round2(val) == 0:
            continue
        row = {"Concepto": concepto, "Monto Total": round2(val)}
        if extra:
            row.update(extra)
        rows.append(row)

    return rows


def extract_resumen_from_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    docs = extract_card_documents_from_bytes(pdf_bytes)
    rows = []

    for idx, doc in enumerate(docs, start=1):
        totals = extract_tax_lines(doc["text"])
        extra = {
            "__doc_id": doc["doc_id"],
            "Nro Liquidación": doc["liq_number"],
            "FechaDoc": doc["fecha_emision"],
            "PeriodoDoc": doc["periodo_label"],
            "SucDoc": doc["suc"],
            "PaginasPDF": doc["pages_label"],
            "OrdenDoc": idx,
        }
        rows.extend(_rows_from_totals(totals, extra))

    return pd.DataFrame(rows)


def get_amount(resumen_df: pd.DataFrame, concepto: str) -> float:
    if resumen_df is None or resumen_df.empty or "Concepto" not in resumen_df.columns:
        return 0.0
    mask = resumen_df["Concepto"].astype(str).str.upper().eq(concepto.upper())
    if not mask.any():
        return 0.0
    return round2(resumen_df.loc[mask, "Monto Total"].sum())

# ============ Resumen agrupado ============
def build_resumen_operativo_agrupado(resumen_df: pd.DataFrame) -> pd.DataFrame:
    if resumen_df is None or resumen_df.empty:
        return pd.DataFrame(columns=["Concepto", "Monto Total"])

    rows = [
        {"Concepto": "Neto al 21%", "Monto Total": get_amount(resumen_df, "Base Neto 21%")},
        {"Concepto": "IVA al 21%", "Monto Total": get_amount(resumen_df, "IVA 21% (Total)")},
        {"Concepto": "Neto al 10,5%", "Monto Total": get_amount(resumen_df, "Base Neto 10,5%")},
        {"Concepto": "IVA al 10,5%", "Monto Total": get_amount(resumen_df, "IVA 10,5% (Total)")},
        {"Concepto": "Exentos", "Monto Total": get_amount(resumen_df, "Gastos Exentos")},
        {"Concepto": "Percepción IVA", "Monto Total": get_amount(resumen_df, "Percepciones IVA (Total)")},
        {"Concepto": "Percepción IIBB", "Monto Total": get_amount(resumen_df, "Percepciones IIBB")},
        {"Concepto": "Retención IVA", "Monto Total": get_amount(resumen_df, "Retenciones IVA")},
        {"Concepto": "Retención IIBB", "Monto Total": get_amount(resumen_df, "Retenciones IBB")},
        {"Concepto": "Retención Ganancias", "Monto Total": get_amount(resumen_df, "Retenciones Ganancias")},
    ]

    out = pd.DataFrame(rows)
    out = out[out["Monto Total"].round(2) != 0].reset_index(drop=True)
    if not out.empty:
        out = pd.concat(
            [out, pd.DataFrame([{"Concepto": "TOTAL", "Monto Total": round2(out["Monto Total"].sum())}])],
            ignore_index=True,
        )
    return out

# ============ Holistor ============
def _base_invoice_row(meta: dict, secuencia: int | str, fecha: str | None = None, suc: str | None = None) -> dict:
    numero = str(secuencia).zfill(8) if str(secuencia).isdigit() else str(secuencia)
    return {
        "Fecha Emisión ": fecha or meta.get("fecha_emision", ""),
        "Fecha Recepción": fecha or meta.get("fecha_emision", ""),
        "Cpbte": "RB",
        "Tipo": "A",
        "Suc.": suc or meta.get("suc", ""),
        "Número": numero,
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


def _build_holistor_group(group: pd.DataFrame, meta: dict, numero) -> list[dict]:
    rows = []

    base21 = get_amount(group, "Base Neto 21%")
    iva21 = get_amount(group, "IVA 21% (Total)")
    base105 = get_amount(group, "Base Neto 10,5%")
    iva105 = get_amount(group, "IVA 10,5% (Total)")
    exento = get_amount(group, "Gastos Exentos")
    perc_iva = get_amount(group, "Percepciones IVA (Total)")
    perc_iibb = get_amount(group, "Percepciones IIBB")
    ret_iibb = get_amount(group, "Retenciones IBB")
    ret_iva = get_amount(group, "Retenciones IVA")

    fecha = (
        str(group["Fecha"].dropna().iloc[0])
        if "Fecha" in group.columns and group["Fecha"].dropna().shape[0]
        else (
            str(group["FechaDoc"].dropna().iloc[0])
            if "FechaDoc" in group.columns and group["FechaDoc"].dropna().shape[0]
            else meta.get("fecha_emision", "")
        )
    )

    suc = (
        str(group["Suc."].dropna().iloc[0])
        if "Suc." in group.columns and group["Suc."].dropna().shape[0]
        else (
            str(group["SucDoc"].dropna().iloc[0])
            if "SucDoc" in group.columns and group["SucDoc"].dropna().shape[0]
            else meta.get("suc", "")
        )
    )

    total_cbte = round2(base21 + iva21 + base105 + iva105 + exento + perc_iva + perc_iibb + ret_iibb + ret_iva)

    if base21 or iva21:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({
            "Cód. Neto": COD_NETO_21,
            "Neto Gravado": base21,
            "Alíc.": "21,000",
            "IVA Liquidado": iva21,
            "IVA Crédito": iva21,
            "Total": total_cbte,
        })
        rows.append(r)

    if base105 or iva105:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({
            "Cód. Neto": COD_NETO_105,
            "Neto Gravado": base105,
            "Alíc.": "10,500",
            "IVA Liquidado": iva105,
            "IVA Crédito": iva105,
            "Total": total_cbte,
        })
        rows.append(r)

    if exento:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({"Cód. NG/EX": COD_EXENTO, "Conceptos NG/EX": exento, "Total": total_cbte})
        rows.append(r)

    if perc_iva:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({"Cód. P/R": COD_PERC_IVA, "Perc./Ret.": perc_iva, "Total": total_cbte})
        rows.append(r)

    if perc_iibb:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({"Cód. P/R": COD_PERC_IIBB, "Perc./Ret.": perc_iibb, "Total": total_cbte})
        rows.append(r)

    if ret_iibb:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({"Cód. P/R": COD_RET_IIBB, "Perc./Ret.": ret_iibb, "Total": total_cbte})
        rows.append(r)

    if ret_iva:
        r = _base_invoice_row(meta, numero, fecha=fecha, suc=suc)
        r.update({"Cód. P/R": COD_RET_IVA, "Perc./Ret.": ret_iva, "Total": total_cbte})
        rows.append(r)

    return rows


def build_holistor_compras_from_resumen(resumen_df: pd.DataFrame, meta: dict, secuencia: int = 1) -> pd.DataFrame:
    if resumen_df is None or resumen_df.empty:
        return pd.DataFrame(columns=HOLISTOR_COLUMNS)

    rows = []

    if "__doc_id" in resumen_df.columns:
        for idx, (_, group) in enumerate(resumen_df.groupby("__doc_id", sort=False), start=0):
            if "Número" in group.columns and group["Número"].dropna().shape[0]:
                numero = str(group["Número"].dropna().iloc[0])
            else:
                numero = secuencia + idx
            rows.extend(_build_holistor_group(group, meta, numero))
    else:
        rows.extend(_build_holistor_group(resumen_df, meta, secuencia))

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
