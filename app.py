import os
import re
import streamlit as st
from backend import extract_resumen_from_bytes, build_report_pdf, format_money

APP_TITLE = "IA AIE - Control tarjetas crédito/débito"
PAGE_ICON = "favicon.ico"
LOGO_FILE = "logo_aie.png"
MAX_MB    = 50

st.set_page_config(page_title=APP_TITLE,
                   page_icon=PAGE_ICON if os.path.exists(PAGE_ICON) else None,
                   layout="centered")

left, right = st.columns([1, 3])
with left:
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, use_container_width=True)
with right:
    st.title(APP_TITLE)
    st.caption("Procesa resúmenes de tarjetas (Cabal / Visa / Mastercard / Maestro / American Express) de cualquier banco")

st.markdown('<hr style="margin:8px 0 20px 0;">', unsafe_allow_html=True)

pdf_file = st.file_uploader("📄 PDF de resumen de tarjeta", type=["pdf"])

col1, col2 = st.columns(2)
with col1:
    gen_pdf = st.checkbox("Generar informe PDF", value=True)
with col2:
    show_table = st.checkbox("Mostrar tabla en pantalla", value=True)

if st.button("Procesar y generar resumen") and pdf_file is not None:
    size_mb = len(pdf_file.getvalue()) / (1024 * 1024)
    if size_mb > MAX_MB:
        st.error(f"El archivo supera {MAX_MB} MB.")
    else:
        with st.spinner("Procesando..."):
            resumen = extract_resumen_from_bytes(pdf_file.getvalue())

            # Ocultar '-IVA ...' si apareciera (o variantes), solo visualmente
            if "Concepto" in resumen.columns:
                mask_menos_iva = resumen["Concepto"].str.contains(r"^\s*[−-]\s*IVA\b", flags=re.IGNORECASE, regex=True)
                resumen_vista = resumen.loc[~mask_menos_iva].reset_index(drop=True)
            else:
                resumen_vista = resumen.copy()

            df_display = resumen_vista.copy()
            if "Monto Total" in df_display.columns:
                df_display["Monto Total"] = df_display["Monto Total"].apply(format_money)

            if show_table:
                st.subheader("Resumen de importes")
                st.dataframe(df_display, use_container_width=True)

            if gen_pdf:
                out_path = "IA_sumatoria.pdf"
                build_report_pdf(resumen_vista, out_path,
                                 titulo="IA AIE - Control tarjetas crédito/débito",
                                 agregar_total_general=True)
                with open(out_path, "rb") as f:
                    st.download_button("⬇️ Descargar informe PDF", f, file_name=out_path, mime="application/pdf")
