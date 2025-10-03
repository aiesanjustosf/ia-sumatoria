# IA AIE - Control tarjetas crédito/débito (App unificada)

App Streamlit para resumir importes de liquidaciones de tarjetas **Cabal / Visa / Mastercard / Maestro** de **cualquier banco**.

## Lógica incluida
- **IVA 21%**: incluye `S/ARANC`, `S/DTO FIN ADQ CONT` y `RI S/DTO F.OTORG` (aunque no tenga `21,00%` visible).
- **IVA 10,5%**: incluye `L.25063 S/DTO F.OTOR 10,50%` y `S/COSTO FINANCIERO 10,50%`.
- **Percepciones IVA**: RG 2408 `3,00%` + `1,50%`.
- **Retenciones**: IIBB, IVA, Ganancias.
- **Filtro**: oculta la fila `-IVA (21% en Débitos al Comercio)` si aparece.
- **Informe PDF**: `IA_sumatoria.pdf` con fila de **TOTAL GENERAL**.

## Archivos
- `app.py` — interfaz Streamlit (logo + favicon).
- `backend.py` — extracción y sumatoria.
- `requirements.txt` — dependencias.
- `.streamlit/config.toml` — tema.
- `logo_aie.png` y `favicon.ico` — reemplazá por tus archivos reales si querés.

## Deploy
1. Subí todos estos archivos a un repo en GitHub.
2. Streamlit Cloud → New app → elegí el repo → `Main file: app.py` → Deploy.
