import streamlit as st
import requests
import re
import sqlite3
import pandas as pd
import io
import urllib3
import json

# Desactivar advertencias de SSL no verificado si algún sitio académico las requiere
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import yt_dlp
    YOUTUBE_DISPONIBLE = True
except ImportError:
    YOUTUBE_DISPONIBLE = False

try:
    import pdfplumber
    PDF_DISPONIBLE = True
except ImportError:
    PDF_DISPONIBLE = False

try:
    from pdf2image import convert_from_bytes
    import pytesseract
    OCR_DISPONIBLE = True
except ImportError:
    OCR_DISPONIBLE = False

try:
    from bs4 import BeautifulSoup
    BS4_DISPONIBLE = True
except ImportError:
    BS4_DISPONIBLE = False

st.set_page_config(page_title="Organizador APA 7 (Español)", page_icon="📚", layout="centered")

def init_db():
    conn = sqlite3.connect("fuentes_apa.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS citas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            autor TEXT,
            anio TEXT,
            titulo TEXT,
            fuente TEXT,
            url TEXT,
            cita_apa TEXT,
            cita_in_text TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE citas ADD COLUMN cita_in_text TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def guardar_cita_db(autor, anio, titulo, fuente, url, cita_apa, cita_in_text):
    conn = sqlite3.connect("fuentes_apa.db")
    c = conn.cursor()
    c.execute('''
        INSERT INTO citas (autor, anio, titulo, fuente, url, cita_apa, cita_in_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (autor, anio, titulo, fuente, url, cita_apa, cita_in_text))
    conn.commit()
    conn.close()

def obtener_citas_db():
    conn = sqlite3.connect("fuentes_apa.db")
    df = pd.read_sql_query("SELECT id, autor, anio, titulo, fuente, cita_in_text AS 'Cita en Texto', cita_apa AS 'Referencia APA 7' FROM citas ORDER BY id DESC", conn)
    conn.close()
    return df

init_db()

st.title("📚 Organizador de Fuentes y Generador APA 7")
st.write("Extrae metadatos mediante DOI, YouTube, sitios web o escaneo de artículos PDF según las **normas APA 7.ª edición en español**.")

if "in_autor" not in st.session_state: st.session_state["in_autor"] = ""
if "in_anio" not in st.session_state: st.session_state["in_anio"] = ""
if "in_titulo" not in st.session_state: st.session_state["in_titulo"] = ""
if "in_fuente" not in st.session_state: st.session_state["in_fuente"] = ""
if "in_url" not in st.session_state: st.session_state["in_url"] = ""

def limpiar_campos():
    if not st.session_state.get("input_extraer", "").strip():
        st.session_state["in_autor"] = ""
        st.session_state["in_anio"] = ""
        st.session_state["in_titulo"] = ""
        st.session_state["in_fuente"] = ""
        st.session_state["in_url"] = ""
        if "last_referencia_apa" in st.session_state:
            del st.session_state["last_referencia_apa"]

def procesar_autores_y_citas(autor_str, titulo_str, anio_str):
    anio_clean = anio_str.strip()
    anio_ref = f"({anio_clean})" if anio_clean else "(s. f.)"
    anio_cita_texto = anio_clean.split(",")[0] if anio_clean else "s. f."
    
    if not autor_str.strip():
        titulo_corto = f"*{titulo_str.strip()[:30]}...*" if len(titulo_str.strip()) > 30 else f"*{titulo_str.strip()}*"
        cita_parentetica = f"({titulo_corto}, {anio_cita_texto})"
        cita_narrativa = f"{titulo_corto} ({anio_cita_texto})"
        return "", cita_parentetica, cita_narrativa, anio_ref

    partes = [p.strip() for p in autor_str.split(",") if p.strip()]
    apellidos = []
    autores_ref_list = []
    
    if len(partes) == 1 and " " in partes[0] and "." not in partes[0]:
        apellidos.append(partes[0])
        ref_autores = partes[0]
    else:
        i = 0
        while i < len(partes):
            part = partes[i]
            if i + 1 < len(partes) and (len(partes[i+1].replace(".", "").strip()) <= 3 or "." in partes[i+1]):
                apellidos.append(part)
                autores_ref_list.append(f"{part}, {partes[i+1]}")
                i += 2
            else:
                apellidos.append(part)
                autores_ref_list.append(part)
                i += 1
                
        if len(autores_ref_list) == 1:
            ref_autores = autores_ref_list[0]
        elif len(autores_ref_list) == 2:
            ref_autores = f"{autores_ref_list[0]} y {autores_ref_list[1]}"
        elif len(autores_ref_list) > 2:
            ref_autores = ", ".join(autores_ref_list[:-1]) + f", y {autores_ref_list[-1]}"
        else:
            ref_autores = autor_str.strip()

    if len(apellidos) == 1: autor_cita = apellidos[0]
    elif len(apellidos) == 2: autor_cita = f"{apellidos[0]} y {apellidos[1]}"
    elif len(apellidos) >= 3: autor_cita = f"{apellidos[0]} et al."
    else: autor_cita = autor_str.strip()

    cita_parentetica = f"({autor_cita}, {anio_cita_texto})"
    cita_narrativa = f"{autor_cita} ({anio_cita_texto})"
    
    return ref_autores, cita_parentetica, cita_narrativa, anio_ref

def extraer_datos_doi(doi_input):
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", doi_input)
    if not match: return False, "No se reconoció un formato de DOI válido."
    
    clean_doi = match.group(0)
    try:
        response = requests.get(f"https://api.crossref.org/works/{clean_doi}", headers={"User-Agent": "APA/1.0"}, timeout=10)
        if response.status_code == 200:
            data = response.json()["message"]
            st.session_state["in_titulo"] = data.get("title", [""])[0]
            
            autores = data.get("author", [])
            autores_fmt = [f"{a.get('family', '')}, {a.get('given', '')[0]}." for a in autores if a.get('family')]
            st.session_state["in_autor"] = ", ".join(autores_fmt)
            
            published = data.get("published-print") or data.get("published-online") or data.get("created")
            st.session_state["in_anio"] = str(published["date-parts"][0][0]) if published and "date-parts" in published else ""
                
            container = data.get("container-title", [])
            st.session_state["in_fuente"] = container[0] if container else data.get("publisher", "")
            st.session_state["in_url"] = f"https://doi.org/{clean_doi}"
            return True, "¡Metadatos DOI extraídos con éxito!"
        return False, "No se encontraron datos en Crossref."
    except Exception as e: return False, f"Error: {str(e)}"

def extraer_datos_youtube(url):
    if not YOUTUBE_DISPONIBLE: return False, "Falta instalar yt-dlp."
    try:
        ydl_opts = {'quiet': True, 'extract_flat': False}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            st.session_state["in_titulo"] = f"{info.get('title', '')} [Video]"
            st.session_state["in_autor"] = info.get('uploader', '')
            st.session_state["in_fuente"] = "YouTube"
            st.session_state["in_url"] = url
            
            raw_date = info.get('upload_date', '') 
            if raw_date and len(raw_date) == 8:
                meses = {"01":"enero", "02":"febrero", "03":"marzo", "04":"abril", "05":"mayo", "06":"junio", "07":"julio", "08":"agosto", "09":"septiembre", "10":"octubre", "11":"noviembre", "12":"diciembre"}
                st.session_state["in_anio"] = f"{raw_date[:4]}, {str(int(raw_date[6:8]))} de {meses.get(raw_date[4:6], '')}"
            else:
                st.session_state["in_anio"] = ""
        return True, "¡Metadatos de YouTube extraídos con éxito!"
    except Exception as e: return False, f"Error: {str(e)}"

# --- EXTRACTOR WEB CON ANÁLISIS DE JSON-LD Y ENLACES ---
def extraer_datos_web(url):
    if not BS4_DISPONIBLE:
        return False, "Falta instalar beautifulsoup4."
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8'
    }
    
    try:
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=12, verify=False, allow_redirects=True)
        
        if response.status_code != 200:
            return False, f"Servidor web no respondió (Código HTTP: {response.status_code})."
        
        response.encoding = response.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        titulo = ""
        autores = []
        fuente = ""
        anio = ""

        # --- FASE 1: Análisis de JSON-LD (Estructura para Google) ---
        scripts_jsonld = soup.find_all('script', type='application/ld+json')
        for script in scripts_jsonld:
            try:
                if script.string:
                    data = json.loads(script.string)
                    # A veces JSON-LD es una lista, a veces un diccionario
                    lista_datos = data if isinstance(data, list) else [data]
                    
                    for item in lista_datos:
                        if isinstance(item, dict):
                            tipo = item.get('@type', '')
                            # Buscar en esquemas de artículos
                            if tipo in ['Article', 'NewsArticle', 'BlogPosting', 'WebPage'] or (isinstance(tipo, list) and 'Article' in tipo):
                                # Extraer Autor
                                info_autor = item.get('author')
                                if isinstance(info_autor, list) and info_autor:
                                    info_autor = info_autor[0]
                                if isinstance(info_autor, dict) and info_autor.get('name'):
                                    autores.append(info_autor['name'])
                                elif isinstance(info_autor, str):
                                    autores.append(info_autor)
                                
                                # Extraer Año
                                if not anio and item.get('datePublished'):
                                    anio = item.get('datePublished')[:4]
            except:
                continue

        # --- FASE 2: Búsqueda tradicional (Si JSON-LD falla) ---
        
        # 1. TÍTULO
        tag_titulo = (soup.find('meta', attrs={'name': re.compile(r'citation_title|dc\.title', re.I)}) or
                      soup.find('meta', attrs={'property': 'og:title'}))
        if tag_titulo and tag_titulo.get('content'):
            titulo = tag_titulo['content'].strip()
        elif soup.find('h1'):
            titulo = soup.find('h1').get_text(strip=True)
        elif soup.title and soup.title.string:
            titulo = soup.title.string.strip()

        # 2. AUTORES (Meta tags, Atributos rel y Clases)
        if not autores:
            tags_autores = (soup.find_all('meta', attrs={'name': re.compile(r'citation_author|dc\.creator', re.I)}) or
                            soup.find_all('meta', attrs={'name': re.compile(r'author', re.I)}))
            for tag in tags_autores:
                if tag.get('content') and tag['content'].strip() not in autores:
                    autores.append(tag['content'].strip())

        if not autores:
            # Buscar explícitamente enlaces (a) que sean declarados como autores
            tags_rel = soup.find_all(attrs={"rel": re.compile(r"author", re.I)})
            for tag in tags_rel:
                texto_autor = tag.get_text(strip=True)
                if texto_autor and texto_autor not in autores:
                    autores.append(texto_autor)

        if not autores:
            # Buscar en clases que suelan contener al autor (ej. perfiles con hipervínculo)
            clases_autor = soup.find_all(class_=re.compile(r'author|autor|byline', re.I))
            for elem in clases_autor:
                enlace = elem.find('a') # Dar prioridad al hipervínculo dentro del contenedor
                texto_elem = enlace.get_text(strip=True) if enlace else elem.get_text(strip=True)
                
                # Limpiar textos como "Por: Juan Pérez" o "Escrito por:"
                texto_elem = re.sub(r'^(Por|By|Escrito por|Redacción):\s*', '', texto_elem, flags=re.IGNORECASE)
                
                if 0 < len(texto_elem) < 50 and texto_elem not in autores:
                    autores.append(texto_elem)
                    break

        # 3. REVISTA / FUENTE
        tag_fuente = (soup.find('meta', attrs={'name': re.compile(r'citation_journal_title|dc\.source', re.I)}) or
                      soup.find('meta', attrs={'property': 'og:site_name'}))
        if tag_fuente and tag_fuente.get('content'):
            fuente = tag_fuente['content'].strip()

        # 4. AÑO
        if not anio:
            tag_fecha = (soup.find('meta', attrs={'name': re.compile(r'citation_publication_date|citation_date|dc\.date', re.I)}) or
                         soup.find('meta', attrs={'property': 'article:published_time'}))
            if tag_fecha and tag_fecha.get('content'):
                match_anio = re.search(r'\b(19\d{2}|20[0-2]\d)\b', tag_fecha['content'])
                if match_anio:
                    anio = match_anio.group(0)

            if not anio:
                match_anio = re.search(r'\b(19\d{2}|20[0-2]\d)\b', response.text)
                if match_anio:
                    anio = match_anio.group(0)

        # Asignación a variables
        if titulo: st.session_state["in_titulo"] = titulo
        if autores: st.session_state["in_autor"] = ", ".join(autores)
        if fuente: st.session_state["in_fuente"] = fuente
        if anio: st.session_state["in_anio"] = anio
        st.session_state["in_url"] = url

        if titulo or autores:
            return True, "¡Metadatos del sitio web extraídos con éxito!"
        else:
            return False, "La página cargó pero no expuso metadatos estándar. Completa los campos faltantes manualmente."

    except Exception as e:
        return False, f"Error al acceder a la página web: {str(e)}"

# --- ESCÁNER ESTRUCTURAL DE PDF ---
def procesar_pdf_profundo(archivo_pdf_bytes):
    if not PDF_DISPONIBLE:
        return False, "Librería pdfplumber no disponible."
    
    try:
        with pdfplumber.open(io.BytesIO(archivo_pdf_bytes)) as pdf:
            if not pdf.pages:
                return False, "El PDF está vacío."
            
            primera_pagina = pdf.pages[0]
            texto_completo = primera_pagina.extract_text() or ""
            
            palabras = primera_pagina.extract_words(
                extra_attrs=["size", "fontname"],
                keep_blank_chars=False
            )
            
            if not palabras:
                if OCR_DISPONIBLE:
                    texto_acumulado = ""
                    imagenes = convert_from_bytes(archivo_pdf_bytes, first_page=1, last_page=1)
                    for img in imagenes:
                        texto_acumulado += pytesseract.image_to_string(img, lang='spa') + "\n"
                    st.session_state["in_titulo"] = "Documento Escaneado"
                    st.session_state["in_fuente"] = "Requiere revisión manual (OCR)"
                    return True, "PDF escaneado. Se aplicó OCR básico."
                else:
                    return False, "No se pudo extraer texto legible del PDF."

            palabras_ordenadas = sorted(palabras, key=lambda w: (round(w['top'], 1), w['x0']))
            lineas = []
            linea_actual = []
            top_actual = None
            
            for p in palabras_ordenadas:
                if top_actual is None or abs(p['top'] - top_actual) <= 3:
                    linea_actual.append(p)
                    top_actual = p['top']
                else:
                    lineas.append(linea_actual)
                    linea_actual = [p]
                    top_actual = p['top']
            if linea_actual:
                lineas.append(linea_actual)

            bloques = []
            for l in lineas:
                texto_linea = " ".join(w['text'] for w in l).strip()
                if texto_linea:
                    tamano_promedio = sum(w['size'] for w in l) / len(l)
                    bloques.append({
                        "texto": texto_linea,
                        "size": tamano_promedio,
                        "top": l[0]['top'],
                        "bottom": l[0]['bottom']
                    })

            if not bloques:
                return False, "No se encontraron bloques legibles."

            tamanos = [b["size"] for b in bloques]
            max_size = max(tamanos)
            
            lineas_titulo = []
            indice_titulo_fin = 0
            es_bloque_titulo = False

            for idx, b in enumerate(bloques):
                if abs(b["size"] - max_size) <= 1.5:
                    lineas_titulo.append(b["texto"])
                    es_bloque_titulo = True
                    indice_titulo_fin = idx
                elif es_bloque_titulo:
                    break

            titulo = " ".join(lineas_titulo).strip()

            bloques_despues_titulo = bloques[indice_titulo_fin + 1:]
            autores_encontrados = []

            for b in bloques_despues_titulo[:5]:
                texto = b["texto"]
                if re.search(r'http|@|vol|no\.|issn|doi|revista|recibido|aceptado', texto, re.IGNORECASE):
                    continue
                if len(texto) > 3 and not re.search(r'\b(19\d{2}|20[0-3]\d)\b', texto):
                    autores_encontrados.append(texto)
                    if len(autores_encontrados) >= 2:
                        break

            autor = " / ".join(autores_encontrados) if autores_encontrados else ""

            altura_pagina = primera_pagina.height
            lineas_revista = []

            for b in bloques:
                es_encabezado_o_pie = (b["top"] < 100) or (b["bottom"] > (altura_pagina - 100))
                if es_encabezado_o_pie and re.search(r'revista|iztacala|vol|no\.|issn|pp|\d{4}', b["texto"], re.IGNORECASE):
                    lineas_revista.append(b["texto"])

            if not lineas_revista:
                for b in bloques:
                    if re.search(r'Revista|Volumen|Vol\.|No\.|Issue|\(\d{4}\)', b["texto"], re.IGNORECASE):
                        lineas_revista.append(b["texto"])

            revista = " - ".join(lineas_revista) if lineas_revista else "Documento PDF"

            matches_anios = re.findall(r'\b(19\d{2}|20[0-2]\d)\b', texto_completo)
            anio = matches_anios[0] if matches_anios else ""

            st.session_state["in_titulo"] = titulo
            st.session_state["in_autor"] = autor
            st.session_state["in_fuente"] = revista
            st.session_state["in_anio"] = anio
            st.session_state["in_url"] = ""

            return True, "¡Análisis del PDF completado!"

    except Exception as e:
        return False, f"Error al procesar la estructura del PDF: {str(e)}"

# --- INTERFAZ STREAMLIT ---
tab1, tab2 = st.tabs(["➕ Crear Cita y Referencia", "🔍 Mis Citas Guardadas"])

with tab1:
    st.subheader("1. Extraer datos automáticamente")
    
    input_busqueda = st.text_input("Pega una URL (YouTube/Revista web), código DOI o link:", key="input_extraer", on_change=limpiar_campos)
    col_btn, col_file = st.columns([1, 2])
    
    with col_btn:
        if st.button("🔍 Extraer de URL/DOI", type="primary", key="btn_extraer_main"):
            if input_busqueda:
                if "youtube.com" in input_busqueda or "youtu.be" in input_busqueda:
                    exito, msg = extraer_datos_youtube(input_busqueda)
                elif "10." in input_busqueda or "doi.org" in input_busqueda:
                    exito, msg = extraer_datos_doi(input_busqueda)
                else:
                    exito, msg = extraer_datos_web(input_busqueda)
                    
                if exito:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
                    st.rerun()
            else:
                st.warning("Por favor ingresa una URL o DOI.")

    with col_file:
        archivo_pdf = st.file_uploader("Sube tu archivo PDF:", type=["pdf"])
        if archivo_pdf is not None:
            if st.button("🚀 Analizar PDF Inteligentemente", type="secondary"):
                with st.spinner("Escaneando tipografías y estructura del PDF..."):
                    bytes_data = archivo_pdf.read()
                    exito, msg = procesar_pdf_profundo(bytes_data)
                    if exito:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

    st.divider()
    st.subheader("2. Datos de la fuente")
    
    autor_in = st.text_input("Autor(es) / Canal de YouTube", key="in_autor")
    anio_in = st.text_input("Año o Fecha", key="in_anio")
    titulo_in = st.text_input("Título del artículo o libro", key="in_titulo")
    fuente_in = st.text_input("Revista / Editorial / Sitio Web", key="in_fuente")
    url_in = st.text_input("URL / DOI", key="in_url")
    
    st.divider()
    
    if st.button("📝 Generar Cita y Referencia APA 7", key="btn_generar_cita", type="primary"):
        ref_autores, cita_par, cita_nar, anio_ref = procesar_autores_y_citas(autor_in, titulo_in, anio_in)
        
        st.subheader("📌 1. Cita en el Texto (In-text Citation)")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Cita Parentética**:")
            st.code(cita_par, language=None)
        with c2:
            st.markdown("**Cita Narrativa**:")
            st.code(cita_nar, language=None)
            
        st.subheader("📖 2. Referencia APA 7.ª edición")
        partes_ref = []
        if ref_autores:
            partes_ref.append(f"{ref_autores}")
            partes_ref.append(f"{anio_ref}.")
        else:
            if titulo_in.strip(): partes_ref.append(f"*{titulo_in.strip()}*.")
            partes_ref.append(f"{anio_ref}.")
            
        if ref_autores and titulo_in.strip():
            if "YouTube" in fuente_in:
                partes_ref.append(f"{titulo_in.strip()}.")
            else:
                partes_ref.append(f"*{titulo_in.strip()}*.")
            
        if fuente_in.strip(): partes_ref.append(f"{fuente_in.strip()}.")
        if url_in.strip(): partes_ref.append(url_in.strip())
            
        referencia_final = " ".join(partes_ref)
        st.code(referencia_final, language=None)
        
        st.session_state["last_cita_in_text"] = f"Par: {cita_par} | Nar: {cita_nar}"
        st.session_state["last_referencia_apa"] = referencia_final

    if "last_referencia_apa" in st.session_state:
        if st.button("💾 Guardar en Base de Datos", key="btn_guardar"):
            if titulo_in.strip() or autor_in.strip():
                guardar_cita_db(autor_in, anio_in, titulo_in, fuente_in, url_in, st.session_state["last_referencia_apa"], st.session_state["last_cita_in_text"])
                st.success("¡Cita y Referencia guardadas exitosamente!")
            else:
                st.error("Ingresa al menos el título o autor.")

with tab2:
    st.subheader("🔍 Base de Datos de Fuentes Guardadas")
    df_citas = obtener_citas_db()
    
    if not df_citas.empty:
        busqueda = st.text_input("🔎 Buscar:", key="search_db")
        if busqueda:
            df_citas = df_citas[df_citas['titulo'].str.contains(busqueda, case=False, na=False) | df_citas['autor'].str.contains(busqueda, case=False, na=False)]
        st.dataframe(df_citas, use_container_width=True)