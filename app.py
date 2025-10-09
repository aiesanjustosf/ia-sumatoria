# app.py
# — IA AIE · Sumatoria de resúmenes — Alfonso A.
# Interfaz Streamlit. Sube PDFs, procesa con backend.procesar_pdfs y muestra la grilla.
import io
import base64
import streamlit as st
import pandas as pd

from backend import procesar_pdfs

st.set_page_config(
    page_title="IA AIE – Sumatorias",
    page_icon="favicon.ico",
    layout="centered",
)

# Header
col1, col2 = st.columns([1, 5], vertical_alignment="center")
with col1:
    st.image("logo_aie.png", use_container_width=True)
with col2:
    st.markdown("## Sumatorias de resúmenes de tarjetas de crédito")

st.divider()

# Opciones
c1, c2 = st.columns(2)
with c1:
    gen_pdf = st.checkbox("Generar informe PDF", value=True)
with c2:
    ver_tabla = st.checkbox("Mostrar tabla en pantalla", value=True)

# Upload
files = st.file_uploader(
    "Subí los resúmenes (PDF) — podés arrastrar varios",
    type=["pdf"], accept_multiple_files=True
)

btn = st.button("Procesar y generar resumen", type="primary", use_container_width=False)

def _df_to_csv_download(df: pd.DataFrame, filename: str, label: str):
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label=label,
        data=csv,
        file_name=filename,
        mime="text/csv",
        use_container_width=False
    )

if btn:
    if not files:
        st.warning("Subí al menos un PDF.")
        st.stop()

    file_bytes_list = [f.read() for f in files]
    resumen, detalle = procesar_pdfs(file_bytes_list)

    st.markdown("### Resumen de importes")
    if ver_tabla:
        st.dataframe(
            resumen.style.format({"Monto Total": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")}),
            use_container_width=True,
            hide_index=True,
        )

    # Descargas
    _df_to_csv_download(resumen, "resumen_importes.csv", "Descargar resumen (CSV)")
    _df_to_csv_download(detalle, "detalle_movimientos.csv", "Descargar detalle (CSV)")

    # Comprobante simple en PDF (opcional)
    if gen_pdf:
        try:
            import reportlab
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=A4)
            width, height = A4

            c.setFont("Helvetica-Bold", 14)
            c.drawString(40, height - 40, "IA AIE – Resumen de importes")
            c.setFont("Helvetica", 10)

            y = height - 80
            for _, row in resumen.iterrows():
                c.drawString(40, y, str(row["Concepto"]))
                c.drawRightString(width - 40, y, f'{row["Monto Total"]:,.2f}'.replace(",", "X").replace(".", ",").replace("X", "."))
                y -= 16
                if y < 60:
                    c.showPage(); y = height - 60
                    c.setFont("Helvetica", 10)

            c.showPage()
            c.save()
            pdf_bytes = buf.getvalue()
            st.download_button(
                "Descargar informe PDF",
                data=pdf_bytes,
                file_name="informe_sumatoria.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.info("El PDF no pudo generarse en este entorno. Podés usar los CSV descargados.")

