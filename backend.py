import re
import datetime
import pdfplumber
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# === Utils ===
def to_float_signed(s: str) -> float:
    s = s.replace("−", "-")
    return float(s.replace(".", "").replace(",", ".").strip())

def format_money(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# === Extracción unificada (Cabal/Visa/Master/Maestro - cualquier banco) ===
def extract_resumen_from_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    tmp_path = "_aie_input.pdf"
    with open(tmp_path, "wb") as f:
        f.write(pdf_bytes)

    # IVA 21%
    # - Sobre arancel
    # - Sobre descuento financiero adquisición contado
    # - RI s/Descuento otorgado (sin exigir '21,00%')
    rx_iva21_any = re.compile(
        r"(IVA[^\n]{0,200}?21,00\s*%)[^\n]{0,80}?([\-−]?\s?\d{1,3}(?:\.\d{3})*,\d{2})",
        re.IGNORECASE
    )
    rx_iva21_ri = re.compile(
        r"(IVA\s*RI\s*CRED\.?\s*FISC\.?\s*COMERCIO\s*S/DTO\s*F\.?OTORG)[^\d\-−]{0,80}([\-−]?\d{1,3}(?:\.\d{3})*,\d{2})",
        re.IGNORECASE
    )

    # IVA 10,5% (ley 25063 + costo financiero)
    rx_iva105 = re.compile(
        r"(IVA\s*CRED\.?\s*FISC\.?\s*COM\.?\s*L\.?\s*25063\s*S/DTO\s*F\.?OTOR\s*10,50%|"
        r"IVA\s*S/COSTO\s*FINANCIERO\s*10,50%)"
        r"[^0-9]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
        re.IGNORECASE
    )

    # Percepciones IVA RG 2408 (3% y 1,5%)
    rx_perc_iva_30 = re.compile(
        r"PERCEPCI[ÓO]N\s*IVA\s*(?:R\.?\s*G\.?|RG)\s*2408\s*3,00\s*%[^0-9]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
        re.IGNORECASE
    )
    rx_perc_iva_15 = re.compile(
        r"PERCEPCI[ÓO]N\s*IVA\s*(?:R\.?\s*G\.?|RG)\s*2408\s*1,50\s*%[^0-9]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})",
        re.IGNORECASE
    )

    # Retenciones
    rx_ret_iibb = re.compile(r"RETENCION\s*ING\.?\s*BRUTOS[^0-9]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
    rx_ret_iva  = re.compile(r"RETENCI[ÓO]N\s*IVA[^0-9]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)
    rx_ret_gcias= re.compile(r"RETENCI[ÓO]N\s*(IMP\.?\s*GANANCIAS|GANANCIAS)[^0-9]*(\-?\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)

    tot_iva21 = tot_iva105 = 0.0
    tot_perc_iva_30 = tot_perc_iva_15 = 0.0
    tot_ret_iibb = tot_ret_iva = tot_ret_gcias = 0.0

    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            txt = (page.extract_text() or "").replace("\xa0", " ").replace("−", "-")

            # 21%
            for m in rx_iva21_any.finditer(txt):
                tot_iva21 += to_float_signed(m.group(2))
            for m in rx_iva21_ri.finditer(txt):
                tot_iva21 += to_float_signed(m.group(2))

            # 10,5%
            for m in rx_iva105.finditer(txt):
                tot_iva105 += to_float_signed(m.group(2))

            # Percepciones IVA
            for m in rx_perc_iva_30.finditer(txt):
                tot_perc_iva_30 += to_float_signed(m.group(1))
            for m in rx_perc_iva_15.finditer(txt):
                tot_perc_iva_15 += to_float_signed(m.group(1))

            # Retenciones
            for m in rx_ret_iibb.finditer(txt):
                tot_ret_iibb += to_float_signed(m.group(1))
            for m in rx_ret_iva.finditer(txt):
                tot_ret_iva += to_float_signed(m.group(1))
            for m in rx_ret_gcias.finditer(txt):
                tot_ret_gcias += to_float_signed(m.group(2))

    base21  = round(tot_iva21 / 0.21, 2) if tot_iva21 else 0.0
    base105 = round(tot_iva105 / 0.105, 2) if tot_iva105 else 0.0
    percep_total = round(tot_perc_iva_30 + tot_perc_iva_15, 2)

    resumen = pd.DataFrame({
        "Concepto": [
            "Base Neto 21%",
            "IVA 21% (Total)",
            "Base Neto 10,5%",
            "IVA 10,5% (Total)",
            "Percepciones IVA (RG 2408 Total)",
            "Retenciones IIBB",
            "Retenciones IVA",
            "Retenciones Ganancias",
        ],
        "Monto Total": [
            round(base21, 2),
            round(tot_iva21, 2),
            round(base105, 2),
            round(tot_iva105, 2),
            percep_total,
            round(tot_ret_iibb, 2),
            round(tot_ret_iva, 2),
            round(tot_ret_gcias, 2),
        ],
    })
    return resumen

# === PDF builder ===
def build_report_pdf(resumen_df, out_path: str, titulo: str, agregar_total_general: bool = False):
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

    # Tabla
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
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#f7f7f7"), colors.white]),
        ("BOTTOMPADDING", (0,0), (-1,0), 8),
        ("TOPPADDING", (0,0), (-1,0), 6),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#e6f2ff")),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ]))
    story.append(tbl)

    doc.build(story)
    return out_path
