import os
import re
import io
import zipfile
import streamlit as st
import pandas as pd

from backend import (
    extract_resumen_from_bytes,
    extract_file_metadata,
    build_holistor_compras_from_resumen,
    build_report_pdf,
    format_money,
    HOLISTOR_COLUMNS,
    BANK_MASTER,
    build_resumen_operativo_agrupado,
)

APP_TITLE = "IA AIE - Control tarjetas crédito/débito"
PAGE_ICON = "favicon.ico"
LOGO_FILE = "logo_aie.png"
MAX_MB = 50

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=PAGE_ICON if os.path.exists(PAGE_ICON) else None,
    layout="centered",
)

left, right = st.columns([1, 3])
with left:
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, use_container_width=True)
with right:
    st.title(APP_TITLE)
    st.caption("Procesa múltiples resúmenes de tarjetas y exporta resumen operativo + compras Holistor")

st.markdown('<hr style="margin:8px 0 20px 0;">', unsafe_allow_html=True)

pdf_files = st.file_uploader(
    "📄 PDF de resumen de tarjeta",
    type=["pdf"],
    accept_multiple_files=True,
)

col1, col2 = st.columns(2)
with col1:
    gen_pdf = st.checkbox("Generar informe PDF", value=True)
with col2:
    show_table = st.checkbox("Mostrar tablas en pantalla", value=True)

BANK_FALLBACK_LABELS = {
    "Nuevo Banco de Santa Fe": "NBSF",
    "Banco Macro": "MACRO",
    "Banco Nación": "BNA",
    "Banco Credicoop": "CREDICOOP",
}

st.markdown("### Banco emisor")
banco_fallback_label = st.selectbox(
    "Si el banco no se detecta automáticamente, usar:",
    options=list(BANK_FALLBACK_LABELS.keys()),
    index=0,
)
forzar_banco = st.checkbox(
    "Forzar este banco para todos los archivos",
    value=False,
    help="Activar solo si todos los PDFs pertenecen al mismo banco y la detección automática no corresponde.",
)


def aplicar_banco_manual_si_corresponde(meta: dict, banco_key: str, forzar: bool = False) -> dict:
    meta = dict(meta or {})
    if forzar or meta.get("key") == "NO_DETECTADO":
        bank_data = BANK_MASTER.get(banco_key, BANK_MASTER["NO_DETECTADO"]).copy()
        for k, v in bank_data.items():
            meta[k] = v
        meta["key"] = banco_key
        meta["banco"] = meta.get("razon_social", "")
        meta["banco_asignado_manual"] = True
    else:
        meta["banco_asignado_manual"] = False
    return meta


def limpiar_vista_resumen(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df_vista = df.copy()
    if "Concepto" in df_vista.columns:
        mask_menos_iva = df_vista["Concepto"].astype(str).str.contains(
            r"^\s*[−-]\s*IVA\b",
            flags=re.IGNORECASE,
            regex=True,
            na=False,
        )
        df_vista = df_vista.loc[~mask_menos_iva].reset_index(drop=True)
    return df_vista


def df_to_excel_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = io.BytesIO()

    numeric_columns = {
        "Monto Total",
        "Neto Gravado",
        "IVA Liquidado",
        "IVA Crédito",
        "Conceptos NG/EX",
        "Perc./Ret.",
        "Total",
    }

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.book[sheet_name]
        ws.freeze_panes = "A2"

        header_to_col = {cell.value: cell.column for cell in ws[1]}
        for header in numeric_columns:
            col_idx = header_to_col.get(header)
            if not col_idx:
                continue
            for row_idx in range(2, ws.max_row + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                if cell.value in (None, ""):
                    continue
                try:
                    cell.value = float(cell.value)
                    cell.number_format = "#,##0.00"
                except (TypeError, ValueError):
                    pass

        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter
            for cell in col:
                value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(value))
            ws.column_dimensions[col_letter].width = min(max_length + 2, 42)

    output.seek(0)
    return output.getvalue()


def build_zip(files_dict: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files_dict.items():
            zf.writestr(filename, content)
    buffer.seek(0)
    return buffer.getvalue()


if st.button("Procesar y generar archivos"):
    if not pdf_files:
        st.warning("Subí al menos un PDF para procesar.")
        st.stop()

    resumenes = []
    compras_holistor = []
    procesados = []
    errores = []
    secuencia = 1

    with st.spinner("Procesando archivos..."):
        for pdf_file in pdf_files:
            file_bytes = pdf_file.getvalue()
            size_mb = len(file_bytes) / (1024 * 1024)

            if size_mb > MAX_MB:
                errores.append({"Archivo": pdf_file.name, "Error": f"Supera {MAX_MB} MB"})
                continue

            try:
                meta = extract_file_metadata(file_bytes, filename=pdf_file.name)
                meta = aplicar_banco_manual_si_corresponde(
                    meta,
                    BANK_FALLBACK_LABELS[banco_fallback_label],
                    forzar=forzar_banco,
                )
                resumen = extract_resumen_from_bytes(file_bytes)

                if resumen is None or resumen.empty:
                    errores.append({"Archivo": pdf_file.name, "Error": "No se detectaron importes"})
                    continue

                resumen = resumen.copy()
                resumen.insert(0, "Archivo", pdf_file.name)
                resumen.insert(1, "Banco", meta.get("razon_social", ""))
                resumen.insert(2, "Fecha", meta.get("fecha_emision", ""))
                resumen.insert(3, "Periodo", meta.get("periodo_label", ""))
                resumen.insert(4, "Suc.", meta.get("suc", ""))
                resumen.insert(5, "Número", str(secuencia).zfill(8))
                resumenes.append(resumen)

                compras = build_holistor_compras_from_resumen(
                    resumen,
                    meta=meta,
                    secuencia=secuencia,
                )
                if compras is not None and not compras.empty:
                    compras_holistor.append(compras)

                procesados.append(
                    {
                        "Archivo": pdf_file.name,
                        "Banco": meta.get("razon_social", ""),
                        "CUIT": meta.get("cuit", ""),
                        "Fecha": meta.get("fecha_emision", ""),
                        "Periodo": meta.get("periodo_label", ""),
                        "Suc.": meta.get("suc", ""),
                        "Número": str(secuencia).zfill(8),
                        "Asignado manual": "Sí" if meta.get("banco_asignado_manual") else "No",
                    }
                )

                secuencia += 1

            except Exception as e:
                errores.append({"Archivo": pdf_file.name, "Error": str(e)})

    if not resumenes:
        st.error("No se pudo procesar ningún archivo.")
        if errores:
            st.dataframe(pd.DataFrame(errores), use_container_width=True)
        st.stop()

    resumen_total = pd.concat(resumenes, ignore_index=True)
    resumen_vista = limpiar_vista_resumen(resumen_total)
    resumen_agrupado = build_resumen_operativo_agrupado(resumen_vista)

    if compras_holistor:
        compras_total = pd.concat(compras_holistor, ignore_index=True)
        compras_total = compras_total[HOLISTOR_COLUMNS]
    else:
        compras_total = pd.DataFrame(columns=HOLISTOR_COLUMNS)

    if show_table:
        tab1, tab2, tab3, tab4 = st.tabs(["Resumen operativo", "Detalle operativo", "Compras Holistor", "Archivos"])

        with tab1:
            st.subheader("Resumen operativo consolidado")
            st.caption("Vista agrupada para control rápido. El Excel operativo conserva todos los movimientos.")
            df_display = resumen_agrupado.copy()
            if "Monto Total" in df_display.columns:
                df_display["Monto Total"] = df_display["Monto Total"].apply(format_money)
            st.dataframe(df_display, use_container_width=True)

        with tab2:
            st.subheader("Detalle operativo")
            df_detalle = resumen_vista.copy()
            if "Monto Total" in df_detalle.columns:
                df_detalle["Monto Total"] = df_detalle["Monto Total"].apply(format_money)
            st.dataframe(df_detalle, use_container_width=True)

        with tab3:
            st.subheader("Excel de compras para importar en Holistor")
            st.dataframe(compras_total, use_container_width=True)

        with tab4:
            st.subheader("Archivos procesados")
            st.dataframe(pd.DataFrame(procesados), use_container_width=True)
            if errores:
                st.warning("Algunos archivos tuvieron errores.")
                st.dataframe(pd.DataFrame(errores), use_container_width=True)

    resumen_excel_bytes = df_to_excel_bytes(resumen_vista, sheet_name="Detalle operativo")
    resumen_agrupado_excel_bytes = df_to_excel_bytes(resumen_agrupado, sheet_name="Resumen agrupado")
    compras_excel_bytes = df_to_excel_bytes(compras_total, sheet_name="HWCompra-modelo")

    download_files = {
        "resumen_operativo_detalle.xlsx": resumen_excel_bytes,
        "resumen_operativo_agrupado.xlsx": resumen_agrupado_excel_bytes,
        "compras_holistor.xlsx": compras_excel_bytes,
    }

    if gen_pdf:
        out_path = "IA_sumatoria_tarjetas.pdf"
        build_report_pdf(
            resumen_agrupado,
            out_path,
            titulo="IA AIE - Control tarjetas crédito/débito",
            agregar_total_general=True,
        )
        with open(out_path, "rb") as f:
            pdf_bytes = f.read()
        download_files["resumen_operativo.pdf"] = pdf_bytes
        st.download_button(
            "⬇️ Descargar informe PDF",
            pdf_bytes,
            file_name="resumen_operativo.pdf",
            mime="application/pdf",
        )

    st.download_button(
        "⬇️ Descargar resumen operativo Excel (detalle completo)",
        resumen_excel_bytes,
        file_name="resumen_operativo_detalle.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.download_button(
        "⬇️ Descargar resumen operativo agrupado Excel",
        resumen_agrupado_excel_bytes,
        file_name="resumen_operativo_agrupado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.download_button(
        "⬇️ Descargar compras Holistor Excel",
        compras_excel_bytes,
        file_name="compras_holistor.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    zip_bytes = build_zip(download_files)
    st.download_button(
        "⬇️ Descargar todo en ZIP",
        zip_bytes,
        file_name="salida_tarjetas_aie.zip",
        mime="application/zip",
    )

st.markdown(
    '<div style="margin-top:24px;text-align:center;color:#666;font-size:12px;">'
    '© AIE – Herramienta para uso interno | Developer Alfonso Alderete'
    '</div>',
    unsafe_allow_html=True,
)
