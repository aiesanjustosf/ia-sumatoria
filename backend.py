import re
import datetime
import pdfplumber
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# ============ Utils ============
def to_float_signed(s: str) -> float:
    s = (s or "").strip().replace("−", "-")
    return float(s.replace(".", "").replace(",", "."))

def format_money(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ============ CABAL (exacto, tu lógica validada) ============
CABAL_PATTERNS = {
    "IVA_ARANCEL_21": re.compile(r"IVA S/ARANCEL DE DESCUENTO\s+21,00%.*?([\d.]+,\d{2})\s*[-−]"),
    "IVA_COSTO_10_5": re.compile(r"IVA S/COSTO FINANCIERO\s+10,50%.*?([\d.]+,\d{2})\s*[-−]"),
    "PERCEPCION_RG333": re.compile(r"PERCEPCION DE IVA RG 333.*?([\d.]+,\d{2})\s*[-−]"),
    "RETENCION_IB": re.compile(r"RETENCION DE INGRESOS BR.*?([\d.]+,\d{2})\s*[-−]"),
    "MENOS_IVA_21": re.compile(r"[-−]IVA\s+21,00%.*?([\d.]+,\d{2})\s*[-−]"),
}

def extract_cabal_exact(text: str) -> dict:
    tot = {"iva_arancel": 0.0, "iva_costo": 0.0, "percep_rg333": 0.0, "ret_iibb": 0.0, "menos_iva": 0.0}
    for key, rx in CABAL_PATTERNS.items():
        for m in rx.finditer(text):
            val = to_float_signed(m.group(1))
            if key == "IVA_ARANCEL_21": tot["iva_arancel"] += val
            elif key == "IVA_COSTO_10_5": tot["iva_costo"] += val
            elif key == "PERCEPCION_RG333": tot["percep_rg333"] += val
            elif key == "RETENCION_IB": tot["ret_iibb"] += val
            elif key == "MENOS_IVA_21": tot["menos_iva"] += val
    return tot

# ============ UNIVERSAL (Visa/Master/Maestro, cualquier banco) ============
# IVA 21%: incluye % explícito, RI S/DTO F.OTORG y RI SERV.OPER. INT.
RX_IVA21_ANY = re.compile(
    r"(IVA[^\n]{0,200}?21,00\s*%)[^\n]{0,120}?([\-−]?\s?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE
)
RX_IVA21_RI_DTO_FOT = re.compile(
    r"(IVA\s*RI\s*CRED\.?\s*FISC\.?\s*COMERCIO\s*S/DTO\s*F\.?OTORG)[^\d\-−]{0,120}"
    r"([\-−]?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE
)
# NUEVO: IVA RI SERV.OPER. INT.  → suma a 21%
RX_IVA21_RI_SERV_INT = re.compile(
    r"(IVA\s*RI\s*SERV\.?\s*OPER\.?\s*INT\.?)[^\d\-−]{0,120}"
    r"([\-−]?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE
)

# IVA 10,5%: Ley 25063 + Costo Financiero 10,50%
RX_IVA105 = re.compile(
    r"(IVA\s*CRED\.?\s*FISC\.?\s*COM\.?\s*L\.?\s*25063\s*S/DTO\s*F\.?OTOR\s*10,50%|"
    r"IVA\s*S/COSTO\s*FINANCIERO\s*10,50%)"
    r"[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE
)

# Percepciones IVA RG 2408
RX_PERC_IVA_30 = re.compile(
    r"PERCEPCI[ÓO]N\s*IVA\s*(?:R\.?\s*G\.?|RG)\s*2408\s*3,00\s*%[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE
)
RX_PERC_IVA_15 = re.compile(
    r"PERCEPCI[ÓO]N\s*IVA\s*(?:R\.?\s*G\.?|RG)\s*2408\s*1,50\s*%[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE
)

# Retenciones universales
RX_RET_IIBB = re.compile(r"RETENCION\s*ING\.?\s*BRUTOS[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
RX_RET_IVA  = re.compile(r"RETENCI[ÓO]N\s*IVA[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
RX_RET_GCIAS= re.compile(r"RETENCI[ÓO]N\s*(IMP\.?\s*GANANCIAS|GANANCIAS)[^\d\-−]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)

def extract_universal(text: str) -> dict:
    tot = {"iva21": 0.0, "iva105": 0.0, "perc_30": 0.0, "perc_15": 0.0, "ret_iibb": 0.0, "ret_iva": 0.0, "ret_gcias": 0.0}
    for m in RX_IVA21_ANY.finditer(text):         tot["iva21"] += to_float_signed(m.group(2))
    for m in RX_IVA21_RI_DTO_FOT.finditer(text):  tot["iva21"] += to_float_signed(m.group(2))
    for m in RX_IVA21_RI_SERV_INT.finditer(text): tot["iva21"] += to_float_signed(m.group(2))  # ← NUEVO
    for m in RX_IVA105.finditer(text):            tot["iva105"] += to_float_signed(m.group(2))
    for m in RX_PERC_IVA_30.finditer(text):       tot["perc_30"] += to_float_signed(m.group(1))
    for m in RX_PERC_IVA_15.finditer(text):       tot["perc_15"] += to_float_signed(m.group(1))
    for m in RX_RET_IIBB.finditer(text):          tot["ret_iibb"] += to_float_signed(m.group(1))
    for m in RX_RET_IVA.finditer(text):           tot["ret_iva"]  += to_float_signed(m.group(1))
    for m in RX_RET_GCIAS.finditer(text):         tot["ret_gcias"]+= to_float_signed(m.group(2))
    return {k: round(v, 2) for k, v in tot.items()}

# ============ Router + Consolidación ============
def extract_resumen_from_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    tmp_path = "_aie_input.pdf"
    with open(tmp_path, "wb") as f: f.write(pdf_bytes)

    cabal_tot = {"iva_arancel": 0.0, "iva_costo": 0.0, "percep_rg333": 0.0, "ret_iibb": 0.0, "menos_iva": 0.0}
    uni_tot   = {"iva21": 0.0, "iva105": 0.0, "perc_30": 0.0, "perc_15": 0.0, "ret_iibb": 0.0, "ret_iva": 0.0, "ret_gcias": 0.0}
    saw_cabal = False

    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            text = (page.extract_text() or "").replace("\xa0", " ").replace("−", "-")
            page_cabal = extract_cabal_exact(text)
            if any(v != 0.0 for v in page_cabal.values()):
                saw_cabal = True
                for k in cabal_tot: cabal_tot[k] += page_cabal[k]
            else:
                page_uni = extract_universal(text)
                for k in uni_tot:   uni_tot[k] += page_uni[k]

    if saw_cabal:
        iva21   = round(cabal_tot["iva_arancel"], 2)     # replica tu Cabal “bien”
        base21  = round(iva21 / 0.21, 2) if iva21 else 0.0
        iva105  = round(cabal_tot["iva_costo"], 2)
        base105 = round(iva105 / 0.105, 2) if iva105 else 0.0
        percep  = round(cabal_tot["percep_rg333"], 2)
        ret_iibb= round(cabal_tot["ret_iibb"], 2)
        ret_iva = 0.0
        ret_gcs = 0.0
    else:
        iva21   = round(uni_tot["iva21"], 2)
        base21  = round(iva21 / 0.21, 2) if iva21 else 0.0
        iva105  = round(uni_tot["iva105"], 2)
        base105 = round(iva105 / 0.105, 2) if iva105 else 0.0
        percep  = round(uni_tot["perc_30"] + uni_tot["perc_15"], 2)
        ret_iibb= round(uni_tot["ret_iibb"], 2)
        ret_iva = round(uni_tot["ret_iva"], 2)
        ret_gcs = round(uni_tot["ret_gcias"], 2)

    resumen = pd.DataFrame({
        "Concepto": [
            "Base Neto 21%", "IVA 21% (Total)",
            "Base Neto 10,5%", "IVA 10,5% (Total)",
            "Percepciones IVA (Total)",
            "Retenciones IBB", "Retenciones IVA", "Retenciones Ganancias",
        ],
        "Monto Total": [base21, iva21, base105, iva105, percep, ret_iibb, ret_iva, ret_gcs],
    })
    return resumen

# ============ PDF builder (SIN footer) ============
def build_report_pdf(resumen_df: pd.DataFrame, out_path: str, titulo: str, agregar_total_general: bool = True):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]; normal = styles["Normal"]; h2 = styles["Heading2"]

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
        data.append([row["Concepto"], format_money(val)])

    if agregar_total_general:
        data.append(["TOTAL GENERAL", format_money(total_general)])

    tbl = Table(data, colWidths=[360, 140])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#222")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.whitesmoke),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN", (1,1), (-1,-1), "RIGHT"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.HexColor("#f7f7f7"), colors.white]),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#e6f2ff")),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ]))
    story.append(tbl)

    doc.build(story)
    return out_path

