import datetime
import io
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
import pandas as pd
import requests
import streamlit as st
import urllib3

try:
    import isbnlib
    ISBNLIB_DISPONIBLE = True
except ImportError:
    ISBNLIB_DISPONIBLE = False

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

# Configuración de página
st.set_page_config(
    page_title="Organizador APA 7 (Español)", page_icon="📚", layout="centered"
)


# --- TRADUCCIÓN AUTOMÁTICA AL ESPAÑOL ---
def traducir_al_espanol(texto):
    """Traduce un texto o palabra clave al español usando la API pública de traducción."""
    if not texto or not texto.strip():
        return texto
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": "es",
            "dt": "t",
            "q": texto.strip(),
        }
        response = requests.get(url, params=params, timeout=3)
        if response.status_code == 200:
            data = response.json()
            return data[0][0][0]
    except Exception:
        pass
    return texto


# --- HELPER ISBN ---
def es_isbn(texto):
    """Verifica si la cadena contiene un formato ISBN-10 o ISBN-13 (con o sin guiones)."""
    if not texto:
        return False
    clean = re.sub(r"[^\dX]", "", str(texto).upper())
    
    if len(clean) == 10:
        return bool(re.match(r"^\d{9}[\dX]$", clean))
    elif len(clean) == 13:
        return bool(re.match(r"^(978|979)\d{10}$", clean))
    return False

# --- LISTA DE PALABRAS A IGNORAR (STOPWORDS) PARA ETIQUETAS ---
STOPWORDS = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "más",
    "pero", "sus", "le", "ya", "o", "este", "sí", "porque", "esta", "entre",
    "cuando", "muy", "sin", "sobre", "también", "me", "hasta", "hay", "donde",
    "quien", "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni",
    "contra", "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mí",
    "antes", "algunos", "qué", "unos", "yo", "otro", "otras", "otra", "él",
    "tanto", "esa", "estos", "mucho", "quienes", "nada", "muchos", "cual",
    "poco", "ella", "estar", "estas", "algunas", "algo", "nosotros", "mi",
    "mis", "tú", "te", "ti", "tu", "tus", "video", "canal", "suscribete",
    "suscríbete", "oficial", "completo", "link", "links", "redes", "sociales",
    "instagram", "facebook", "twitter", "youtube", "gracias", "bienvenidos",
    "hola", "http", "https", "com", "www", "isbn", "edition", "edicion", "editorial"
}


def extraer_palabras_clave_texto(texto):
    """Extrae hashtags y palabras clave, traduciéndolas al español."""
    if not texto:
        return []

    sugerencias = []

    hashtags_directos = re.findall(r"#(\w+)", texto)
    for h in hashtags_directos:
        h_traducido = traducir_al_espanol(h)
        clean_h = f"#{re.sub(r'[^\w]', '', h_traducido).capitalize()}"
        if clean_h not in sugerencias:
            sugerencias.append(clean_h)

    palabras = re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{4,}\b", texto)
    palabras_filtradas = []
    for p in palabras:
        if p.lower() not in STOPWORDS and p.lower() not in palabras_filtradas:
            palabras_filtradas.append(p)
            if len(palabras_filtradas) >= 12:
                break

    for p in palabras_filtradas:
        p_traducida = traducir_al_espanol(p)
        clean_p = re.sub(r"[^\w]", "", p_traducida).capitalize()
        tag = f"#{clean_p}"
        if tag not in sugerencias and len(clean_p) > 2:
            sugerencias.append(tag)

    return sugerencias


# --- CONTROL DE ACCESO PERSISTENTE ---
CONTRASEÑA_CORRECTA = "Cypher"

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if st.query_params.get("auth") == "CypherOK":
    st.session_state["autenticado"] = True

if not st.session_state["autenticado"]:
    st.title("🔒 Acceso Restringido")
    st.write("Ingresa la clave de acceso para utilizar el organizador de fuentes.")

    clave_ingresada = st.text_input("Contraseña:", type="password")

    if st.button("Entrar", type="primary") or (
        clave_ingresada and clave_ingresada == CONTRASEÑA_CORRECTA
    ):
        if clave_ingresada == CONTRASEÑA_CORRECTA:
            st.session_state["autenticado"] = True
            st.query_params["auth"] = "CypherOK"
            st.rerun()
        else:
            st.error("Contraseña incorrecta. Inténtalo de nuevo.")

    st.stop()

# --- CONFIGURACIÓN GROBID Y BASE DE DATOS ---
GROBID_URL = "https://grobid.kermitt.org/api/processHeaderDocument"


def init_db():
    conn = sqlite3.connect("fuentes_apa.db")
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS citas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        autor TEXT,
        anio TEXT,
        titulo TEXT,
        fuente TEXT,
        url TEXT,
        cita_in_text TEXT,
        cita_apa TEXT,
        tipo_fuente TEXT DEFAULT 'General',
        es_favorito INTEGER DEFAULT 0,
        tags TEXT DEFAULT '',
        proyecto TEXT DEFAULT 'General'
    )""")

    columnas_nuevas = [
        ("tipo_fuente", "TEXT DEFAULT 'General'"),
        ("es_favorito", "INTEGER DEFAULT 0"),
        ("tags", "TEXT DEFAULT ''"),
        ("proyecto", "TEXT DEFAULT 'General'"),
    ]

    for col_nombre, col_tipo in columnas_nuevas:
        try:
            c.execute(f"ALTER TABLE citas ADD COLUMN {col_nombre} {col_tipo}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


init_db()


def guardar_cita_db(
    autor, anio, titulo, fuente, url, cita_in_text, cita_apa,
    tipo_fuente="General", es_favorito=0, tags="", proyecto="General"
):
    conn = sqlite3.connect("fuentes_apa.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO citas (autor, anio, titulo, fuente, url, cita_in_text, cita_apa, tipo_fuente, es_favorito, tags, proyecto)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (autor, anio, titulo, fuente, url, cita_in_text, cita_apa, tipo_fuente, es_favorito, tags, proyecto))
    conn.commit()
    conn.close()


def obtener_citas_db():
    conn = sqlite3.connect("fuentes_apa.db")
    df = pd.read_sql_query("""
        SELECT id, autor, anio, titulo, fuente, url, 
                  cita_in_text AS 'Cita en Texto', 
                  cita_apa AS 'Referencia APA 7',
                  tipo_fuente, es_favorito, tags, proyecto
           FROM citas ORDER BY id DESC
    """, conn)
    conn.close()
    return df


# --- INTERFAZ PRINCIPAL Y SESSION STATE ---
st.title("📚 Organizador de Fuentes y Generador APA 7")
st.write(
    "Extrae metadatos mediante **DOI, ISBN, YouTube, páginas web o archivos"
    " PDF** según las **normas APA 7.ª edición en español**."
)

if "in_autor" not in st.session_state:
    st.session_state["in_autor"] = ""
if "in_anio" not in st.session_state:
    st.session_state["in_anio"] = ""
if "in_titulo" not in st.session_state:
    st.session_state["in_titulo"] = ""
if "in_fuente" not in st.session_state:
    st.session_state["in_fuente"] = ""
if "in_url" not in st.session_state:
    st.session_state["in_url"] = ""
if "in_tipo_fuente" not in st.session_state:
    st.session_state["in_tipo_fuente"] = "Artículo de Revista"
if "select_tipo_fuente" not in st.session_state:
    st.session_state["select_tipo_fuente"] = "Artículo de Revista"
if "in_tags" not in st.session_state:
    st.session_state["in_tags"] = ""
if "in_proyecto" not in st.session_state:
    st.session_state["in_proyecto"] = "General"
if "sugerencias_tags" not in st.session_state:
    st.session_state["sugerencias_tags"] = []
if "input_extraer" not in st.session_state:
    st.session_state["input_extraer"] = ""
if "pestana_activa" not in st.session_state:
    st.session_state["pestana_activa"] = "➕ Crear Cita y Referencia"


def limpiar_formulario():
    keys_a_limpiar = [
        "in_autor", "in_anio", "in_titulo", "in_fuente", 
        "in_url", "in_tags", "input_extraer"
    ]
    for key in keys_a_limpiar:
        if key in st.session_state:
            # En lugar de asignar "", eliminamos la key del state 
            # para que el widget se reinicie limpio en el próximo render
            del st.session_state[key]
            
    st.session_state["in_tipo_fuente"] = "Artículo de Revista"
    st.session_state["select_tipo_fuente"] = "Artículo de Revista"
    st.session_state["sugerencias_tags"] = []
    
    if "last_referencia_apa" in st.session_state:
        del st.session_state["last_referencia_apa"]
    if "last_cita_in_text" in st.session_state:
        del st.session_state["last_cita_in_text"]
    
    # Forzar un re-ejecución limpia si se llama desde un botón
    # st.rerun()
def agregar_tag_sugerido(tag_a_agregar):
    actuales = [
        t.strip()
        for t in st.session_state.get("in_tags", "").split(",")
        if t.strip()
    ]
    if tag_a_agregar not in actuales:
        actuales.append(tag_a_agregar)
        st.session_state["in_tags"] = ", ".join(actuales)


def actualizar_tipo_al_tipear():
    texto = (
        st.session_state.get("in_url", "").strip()
        or st.session_state.get("input_extraer", "").strip()
    )
    texto_lower = texto.lower()

    nuevo_tipo = None
    if "youtube.com" in texto_lower or "youtu.be" in texto_lower:
        nuevo_tipo = "Video / Multimedia"
    elif "doi.org" in texto_lower or "10." in texto_lower:
        nuevo_tipo = "Artículo de Revista"
    elif es_isbn(texto):
        nuevo_tipo = "Libro"
    elif (
        texto_lower.startswith("http://")
        or texto_lower.startswith("https://")
        or "www." in texto_lower
    ):
        nuevo_tipo = "Página Web"

    if nuevo_tipo:
        st.session_state["in_tipo_fuente"] = nuevo_tipo
        st.session_state["select_tipo_fuente"] = nuevo_tipo


def al_cambiar_input_extraer():
    if not st.session_state.get("input_extraer", "").strip():
        limpiar_formulario()
    else:
        actualizar_tipo_al_tipear()


def procesar_autores_y_citas(autor_str, titulo_str, anio_str):
    anio_clean = anio_str.strip()
    anio_ref = f"({anio_clean})" if anio_clean else "(s. f.)"
    anio_cita_texto = anio_clean.split(",")[0] if anio_clean else "s. f."

    if not autor_str.strip():
        titulo_corto = (
            f"*{titulo_str.strip()[:30]}...*"
            if len(titulo_str.strip()) > 30
            else f"*{titulo_str.strip()}*"
        )
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
            if i + 1 < len(partes) and (
                len(partes[i + 1].replace(".", "").strip()) <= 3
                or "." in partes[i + 1]
            ):
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
            ref_autores = (
                ", ".join(autores_ref_list[:-1]) + f", y {autores_ref_list[-1]}"
            )
        else:
            ref_autores = autor_str.strip()

    if len(apellidos) == 1:
        autor_cita = apellidos[0]
    elif len(apellidos) == 2:
        autor_cita = f"{apellidos[0]} y {apellidos[1]}"
    elif len(apellidos) >= 3:
        autor_cita = f"{apellidos[0]} et al."
    else:
        autor_cita = autor_str.strip()

    cita_parentetica = f"({autor_cita}, {anio_cita_texto})"
    cita_narrativa = f"{autor_cita} ({anio_cita_texto})"

    return ref_autores, cita_parentetica, cita_narrativa, anio_ref


# --- NORMALIZADOR DE FECHAS APA 7 (ESPAÑOL) ---
def normalizar_fecha_apa(fecha_str):
    """Convierte cualquier formato de fecha (año, año-mes, o fecha completa) 
    a las normas APA 7 en español (Ej: '2023', 'mayo de 2023', o '12 de mayo de 2023')."""
    if not fecha_str:
        return ""
    
    fecha_str = str(fecha_str).strip()
    
    meses_en_a_es = {
        "january": "enero", "jan": "enero",
        "february": "febrero", "feb": "febrero",
        "march": "marzo", "mar": "marzo",
        "april": "abril", "apr": "abril",
        "may": "mayo",
        "june": "junio", "jun": "junio",
        "july": "july", "jul": "julio", "july": "julio",
        "august": "agosto", "aug": "agosto",
        "september": "septiembre", "sep": "septiembre",
        "october": "octubre", "oct": "octubre",
        "november": "noviembre", "nov": "noviembre",
        "december": "diciembre", "dec": "diciembre"
    }

    for eng, esp in meses_en_a_es.items():
        if eng in fecha_str.lower():
            fecha_str = fecha_str.lower().replace(eng, esp)

    match_completa = re.search(r"\b(19\d{2}|20[0-3]\d)[-/](\d{1,2})[-/](\d{1,2})\b", fecha_str)
    if match_completa:
        anio, mes, dia = match_completa.groups()
        meses_nombres = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", 
                         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        try:
            nombre_mes = meses_nombres[int(mes)]
            return f"{int(dia)} de {nombre_mes} de {anio}"
        except IndexError:
            pass

    match_anio_mes = re.search(r"\b(19\d{2}|20[0-3]\d)[-/](\d{1,2})\b", fecha_str)
    if match_anio_mes:
        anio, mes = match_anio_mes.groups()
        meses_nombres = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", 
                         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        try:
            nombre_mes = meses_nombres[int(mes)]
            return f"{nombre_mes} de {anio}"
        except IndexError:
            pass

    for esp in meses_en_a_es.values():
        if esp in fecha_str.lower():
            match_y = re.search(r"\b(19\d{2}|20[0-3]\d)\b", fecha_str)
            if match_y:
                anio = match_y.group(0)
                return f"{esp} de {anio}"

    match_solo_anio = re.search(r"\b(19\d{2}|20[0-3]\d)\b", fecha_str)
    if match_solo_anio:
        return match_solo_anio.group(0)

    return fecha_str


# --- CAPAS DE EXTRACCIÓN DE DATOS ---
def extraer_datos_isbn_web(clean_isbn):
    if not BS4_DISPONIBLE:
        return False

    url = f"https://isbnsearch.org/isbn/{clean_isbn}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")

            h1 = soup.find("h1")
            if not h1:
                return False
            titulo = h1.get_text(strip=True)

            bookinfo = soup.find("div", class_="bookinfo")
            if not bookinfo:
                return False

            texto_info = bookinfo.get_text()

            publisher_match = re.search(r"Publisher:\s*([^\n]+)", texto_info)
            editorial = publisher_match.group(1).strip() if publisher_match else ""

            anio = ""
            date_match = re.search(r"Publication Date:\s*([^\n]+)", texto_info)
            if date_match:
                anio = normalizar_fecha_apa(date_match.group(1))
            
            if not anio:
                anio_match_gen = re.search(r"\b(19\d{2}|20[0-3]\d)\b", texto_info)
                if anio_match_gen:
                    anio = anio_match_gen.group(0)

            author_match = re.search(r"Author:\s*([^\n]+)", texto_info)
            autor = author_match.group(1).strip() if author_match else ""

            st.session_state["in_titulo"] = titulo
            if autor:
                st.session_state["in_autor"] = autor
            if editorial:
                st.session_state["in_fuente"] = editorial
            if anio:
                st.session_state["in_anio"] = anio
            st.session_state["in_url"] = url
            st.session_state["in_tipo_fuente"] = "Libro"
            st.session_state["select_tipo_fuente"] = "Libro"

            sug = extraer_palabras_clave_texto(titulo)
            st.session_state["sugerencias_tags"] = sug[:10]

            return True
    except Exception:
        pass

    return False


def extraer_datos_isbn(isbn_input):
    st.session_state["sugerencias_tags"] = []
    clean_isbn = re.sub(r"[^\dX]", "", isbn_input.upper())

    if not clean_isbn:
        return False, "Formato de ISBN no válido."

    encontro_algo = False

    if ISBNLIB_DISPONIBLE:
        try:
            libro = isbnlib.meta(clean_isbn)
            if libro:
                st.session_state["in_titulo"] = libro.get("Title", "")
                
                autores = libro.get("Authors", [])
                autores_fmt = []
                for a in autores:
                    partes = a.split(" ")
                    if len(partes) > 1:
                        apellido = partes[-1]
                        inicial = partes[0][0] + "."
                        autores_fmt.append(f"{apellido}, {inicial}")
                    else:
                        autores_fmt.append(a)
                        
                st.session_state["in_autor"] = ", ".join(autores_fmt)
                
                anio_lib = libro.get("Year", "")
                if anio_lib:
                    st.session_state["in_anio"] = normalizar_fecha_apa(str(anio_lib))
                    
                st.session_state["in_fuente"] = libro.get("Publisher", "")
                st.session_state["in_url"] = f"https://isbnsearch.org/isbn/{clean_isbn}"
                st.session_state["in_tipo_fuente"] = "Libro"
                st.session_state["select_tipo_fuente"] = "Libro"
                encontro_algo = True
        except Exception:
            pass

    headers = {"User-Agent": "OrganizadorAPA_App/1.0"}

    try:
        url_gb = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{clean_isbn}"
        res_gb = requests.get(url_gb, headers=headers, timeout=10)
        if res_gb.status_code == 200:
            data = res_gb.json()
            if data.get("totalItems", 0) > 0:
                book = data["items"][0]["volumeInfo"]
                if not st.session_state.get("in_titulo"):
                    st.session_state["in_titulo"] = book.get("title", "")
                if not st.session_state.get("in_autor"):
                    autores_raw = book.get("authors", [])
                    st.session_state["in_autor"] = ", ".join(autores_raw)
                
                pub_date = book.get("publishedDate", "")
                if pub_date and not st.session_state.get("in_anio"):
                    st.session_state["in_anio"] = normalizar_fecha_apa(pub_date)

                if not st.session_state.get("in_fuente"):
                    st.session_state["in_fuente"] = book.get("publisher", "")
                    
                st.session_state["in_url"] = f"https://isbnsearch.org/isbn/{clean_isbn}"
                st.session_state["in_tipo_fuente"] = "Libro"
                st.session_state["select_tipo_fuente"] = "Libro"
                encontro_algo = True
    except Exception:
        pass

    try:
        url_ol = f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_isbn}&format=json&jscmd=data"
        res_ol = requests.get(url_ol, headers=headers, timeout=10)
        if res_ol.status_code == 200:
            data_ol = res_ol.json()
            key_ol = f"ISBN:{clean_isbn}"
            if key_ol in data_ol:
                info_ol = data_ol[key_ol]
                if not st.session_state.get("in_titulo"):
                    st.session_state["in_titulo"] = info_ol.get("title", "")
                if not st.session_state.get("in_autor"):
                    auts = [a.get("name", "") for a in info_ol.get("authors", [])]
                    st.session_state["in_autor"] = ", ".join(auts)
                if not st.session_state.get("in_fuente"):
                    pubs = [p.get("name", "") for p in info_ol.get("publishers", [])]
                    st.session_state["in_fuente"] = pubs[0] if pubs else ""
                
                pub_date_ol = info_ol.get("publish_date", "")
                if pub_date_ol and not st.session_state.get("in_anio"):
                    st.session_state["in_anio"] = normalizar_fecha_apa(pub_date_ol)
                encontro_algo = True
    except Exception:
        pass

    if not st.session_state.get("in_titulo") or not st.session_state.get("in_anio"):
        if extraer_datos_isbn_web(clean_isbn):
            encontro_algo = True

    if encontro_algo:
        titulo_final = st.session_state.get("in_titulo", "")
        if titulo_final:
            sugerencias = extraer_palabras_clave_texto(titulo_final)
            st.session_state["sugerencias_tags"] = sugerencias[:10]
        return True, "¡Metadatos del libro y fecha normalizada con éxito!"

    return False, "No se encontraron registros completos para este ISBN en las fuentes disponibles."


def extraer_datos_doi(doi_input):
    st.session_state["sugerencias_tags"] = []
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", doi_input)
    if not match:
        return False, "No se reconoció un formato de DOI válido."

    clean_doi = match.group(0)
    try:
        response = requests.get(
            f"https://api.crossref.org/works/{clean_doi}",
            headers={"User-Agent": "APA/1.0"},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()["message"]
            st.session_state["in_titulo"] = data.get("title", [""])[0]

            autores = data.get("author", [])
            autores_fmt = [
                f"{a.get('family', '')}, {a.get('given', '')[0]}."
                for a in autores
                if a.get("family")
            ]
            st.session_state["in_autor"] = ", ".join(autores_fmt)

            published = (
                data.get("published-print")
                or data.get("published-online")
                or data.get("created")
            )
            raw_anio = (
                str(published["date-parts"][0][0])
                if published and "date-parts" in published
                else ""
            )
            st.session_state["in_anio"] = normalizar_fecha_apa(raw_anio)

            container = data.get("container-title", [])
            st.session_state["in_fuente"] = (
                container[0] if container else data.get("publisher", "")
            )
            st.session_state["in_url"] = f"https://doi.org/{clean_doi}"
            st.session_state["in_tipo_fuente"] = "Artículo de Revista"
            st.session_state["select_tipo_fuente"] = "Artículo de Revista"

            subjects = data.get("subject", [])
            sug = []
            for s in subjects[:6]:
                if len(s) > 2:
                    s_trad = traducir_al_espanol(s)
                    clean_s = f"#{re.sub(r'[^\w]', '', s_trad).capitalize()}"
                    if clean_s not in sug:
                        sug.append(clean_s)

            st.session_state["sugerencias_tags"] = sug
            return True, "¡Metadatos DOI extraídos con éxito!"
        return False, "No se encontraron datos en Crossref."
    except Exception as e:
        return False, f"Error: {str(e)}"


def extraer_datos_youtube(url):
    if not YOUTUBE_DISPONIBLE:
        return False, "Falta instalar yt-dlp."

    st.session_state["sugerencias_tags"] = []

    try:
        ydl_opts = {"quiet": True, "extract_flat": False}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_raw = info.get("title", "")
            descripcion_raw = info.get("description", "") or ""

            st.session_state["in_titulo"] = f"{titulo_raw} [Video]"
            st.session_state["in_autor"] = info.get("uploader", "")
            st.session_state["in_fuente"] = "YouTube"
            st.session_state["in_url"] = url

            raw_date = info.get("upload_date", "")
            if raw_date and len(raw_date) == 8:
                meses = {
                    "01": "enero", "02": "febrero", "03": "marzo", "04": "abril",
                    "05": "mayo", "06": "junio", "07": "julio", "08": "agosto",
                    "09": "septiembre", "10": "octubre", "11": "noviembre", "12": "diciembre",
                }
                fecha_formateada = f"{int(raw_date[6:8])} de {meses.get(raw_date[4:6], '')} de {raw_date[:4]}"
                st.session_state["in_anio"] = fecha_formateada
            else:
                st.session_state["in_anio"] = ""

            st.session_state["in_tipo_fuente"] = "Video / Multimedia"
            st.session_state["select_tipo_fuente"] = "Video / Multimedia"

            sugerencias = []
            categories = info.get("categories", []) or []
            for cat in categories:
                cat_es = traducir_al_espanol(cat)
                tag_cat = f"#{re.sub(r'[^\w]', '', cat_es).capitalize()}"
                if tag_cat not in sugerencias:
                    sugerencias.append(tag_cat)

            yt_tags = info.get("tags", []) or []
            for tag in yt_tags[:6]:
                tag_es = traducir_al_espanol(tag)
                clean_tag = re.sub(r"[^\w]", "", tag_es).capitalize()
                if len(clean_tag) > 2 and f"#{clean_tag}" not in sugerencias:
                    sugerencias.append(f"#{clean_tag}")

            texto_para_analizar = f"{titulo_raw} {descripcion_raw[:500]}"
            tags_extraidos = extraer_palabras_clave_texto(texto_para_analizar)

            for tag in tags_extraidos:
                if tag not in sugerencias:
                    sugerencias.append(tag)

            st.session_state["sugerencias_tags"] = sugerencias[:10]

        return True, "¡Metadatos de YouTube extraídos con éxito!"
    except Exception as e:
        return False, f"Error: {str(e)}"


def extraer_datos_web(url):
    st.session_state["sugerencias_tags"] = []
    if not BS4_DISPONIBLE:
        return False, "Falta instalar beautifulsoup4."

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    try:
        session = requests.Session()
        response = session.get(
            url, headers=headers, timeout=12, verify=False, allow_redirects=True
        )

        if response.status_code != 200:
            return False, f"Servidor web no respondió (Código HTTP: {response.status_code})."

        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        titulo = ""
        autores = []
        fuente = ""
        anio = ""

        scripts_jsonld = soup.find_all("script", type="application/ld+json")
        for script in scripts_jsonld:
            try:
                if script.string:
                    data = json.loads(script.string)
                    lista_datos = data if isinstance(data, list) else [data]

                    for item in lista_datos:
                        if isinstance(item, dict):
                            tipo = item.get("@type", "")
                            if tipo in [
                                "Article", "NewsArticle", "BlogPosting", "WebPage"
                            ] or (isinstance(tipo, list) and "Article" in tipo):
                                info_autor = item.get("author")
                                if isinstance(info_autor, list) and info_autor:
                                    info_autor = info_autor[0]
                                if isinstance(info_autor, dict) and info_autor.get("name"):
                                    autores.append(info_autor["name"])
                                elif isinstance(info_autor, str):
                                    autores.append(info_autor)

                                if not anio and item.get("datePublished"):
                                    anio = normalizar_fecha_apa(item.get("datePublished"))
            except Exception:
                continue

        tag_titulo = soup.find(
            "meta", attrs={"name": re.compile(r"citation_title|dc\.title", re.I)}
        ) or soup.find("meta", attrs={"property": "og:title"})
        if tag_titulo and tag_titulo.get("content"):
            titulo = tag_titulo["content"].strip()
        elif soup.find("h1"):
            titulo = soup.find("h1").get_text(strip=True)
        elif soup.title and soup.title.string:
            titulo = soup.title.string.strip()

        if not autores:
            tags_autores = soup.find_all(
                "meta",
                attrs={"name": re.compile(r"citation_author|dc\.creator", re.I)},
            ) or soup.find_all("meta", attrs={"name": re.compile(r"author", re.I)})
            for tag in tags_autores:
                if tag.get("content") and tag["content"].strip() not in autores:
                    autores.append(tag["content"].strip())

        tag_fuente = soup.find(
            "meta",
            attrs={"name": re.compile(r"citation_journal_title|dc\.source", re.I)},
        ) or soup.find("meta", attrs={"property": "og:site_name"})
        if tag_fuente and tag_fuente.get("content"):
            fuente = tag_fuente["content"].strip()

        if not anio:
            tag_fecha = soup.find(
                "meta",
                attrs={
                    "name": re.compile(
                        r"citation_publication_date|citation_date|dc\.date", re.I
                    )
                },
            ) or soup.find("meta", attrs={"property": "article:published_time"})
            if tag_fecha and tag_fecha.get("content"):
                anio = normalizar_fecha_apa(tag_fecha["content"])

        sugerencias_web = []
        meta_kw = soup.find("meta", attrs={"name": re.compile(r"keywords", re.I)})
        if meta_kw and meta_kw.get("content"):
            kws = [k.strip() for k in meta_kw["content"].split(",") if len(k.strip()) > 2]
            for kw in kws[:6]:
                kw_es = traducir_al_espanol(kw)
                clean_kw = f"#{re.sub(r'[^\w]', '', kw_es).capitalize()}"
                if clean_kw not in sugerencias_web:
                    sugerencias_web.append(clean_kw)

        if not sugerencias_web and titulo:
            sugerencias_web = extraer_palabras_clave_texto(titulo)

        st.session_state["sugerencias_tags"] = sugerencias_web[:10]

        if titulo:
            st.session_state["in_titulo"] = titulo
        if autores:
            st.session_state["in_autor"] = ", ".join(autores)
        if fuente:
            st.session_state["in_fuente"] = fuente
        if anio:
            st.session_state["in_anio"] = anio
        st.session_state["in_url"] = url
        st.session_state["in_tipo_fuente"] = "Página Web"
        st.session_state["select_tipo_fuente"] = "Página Web"

        if titulo or autores:
            return True, "¡Metadatos del sitio web extraídos con éxito!"
        else:
            return False, "La página cargó pero no expuso metadatos estándar. Completa los campos faltantes manualmente."

    except Exception as e:
        return False, f"Error al acceder a la página web: {str(e)}"


def procesar_pdf_con_grobid(archivo_pdf_bytes):
    st.session_state["sugerencias_tags"] = []
    try:
        files = {"input": ("documento.pdf", archivo_pdf_bytes, "application/pdf")}
        data = {"consolidateHeader": "1"}

        response = requests.post(GROBID_URL, files=files, data=data, timeout=25)

        if response.status_code != 200:
            return False, f"Servidor GROBID responde con código {response.status_code}."

        xml_data = response.text
        root = ET.fromstring(xml_data)
        ns = {"tei": "http://www.tei-c.org/ns/1.0"}

        title_node = root.find(".//tei:titleStmt/tei:title", ns)
        titulo = title_node.text.strip() if title_node is not None and title_node.text else ""

        autores_list = []
        for author in root.findall(".//tei:analytic/tei:author", ns):
            pers = author.find("tei:persName", ns)
            if pers is not None:
                surname = pers.find("tei:surname", ns)
                forename = pers.find("tei:forename", ns)
                ap = surname.text.strip() if surname is not None and surname.text else ""
                nom = forename.text.strip()[0] + "." if forename is not None and forename.text else ""
                if ap:
                    autores_list.append(f"{ap}, {nom}" if nom else ap)

        autores = ", ".join(autores_list)

        date_node = root.find(".//tei:publicationStmt/tei:date", ns) or root.find(".//tei:monogr/tei:imprint/tei:date", ns)
        anio = ""
        if date_node is not None:
            anio_val = date_node.get("when", date_node.text or "")
            anio = normalizar_fecha_apa(anio_val)

        journal_node = root.find(".//tei:monogr/tei:title", ns)
        fuente = journal_node.text.strip() if journal_node is not None and journal_node.text else ""

        doi_node = root.find('.//tei:idno[@type="DOI"]', ns)
        url = ""
        if doi_node is not None and doi_node.text:
            url = f"https://doi.org/{doi_node.text.strip()}"

        sug_grobid = []
        for kw_node in root.findall(".//tei:profileDesc/tei:textClass/tei:keywords/tei:term", ns):
            if kw_node.text:
                kw_es = traducir_al_espanol(kw_node.text.strip())
                clean_kw = f"#{re.sub(r'[^\w]', '', kw_es).capitalize()}"
                if clean_kw not in sug_grobid:
                    sug_grobid.append(clean_kw)

        abstract_node = root.find(".//tei:profileDesc/tei:abstract", ns)
        abstract_txt = abstract_node.text.strip() if abstract_node is not None and abstract_node.text else ""

        tags_de_texto = extraer_palabras_clave_texto(f"{titulo} {abstract_txt}")
        for t in tags_de_texto:
            if t not in sug_grobid:
                sug_grobid.append(t)

        st.session_state["sugerencias_tags"] = sug_grobid[:10]

        if titulo:
            st.session_state["in_titulo"] = titulo
        if autores:
            st.session_state["in_autor"] = autores
        if fuente:
            st.session_state["in_fuente"] = fuente
        if anio:
            st.session_state["in_anio"] = anio
        if url:
            st.session_state["in_url"] = url
        st.session_state["in_tipo_fuente"] = "Artículo de Revista"
        st.session_state["select_tipo_fuente"] = "Artículo de Revista"

        if titulo or autores:
            return True, "¡Metadatos analizados con éxito mediante GROBID!"
        else:
            return False, "GROBID no detectó suficiente información en la portada."

    except Exception as e:
        return False, f"Error de conexión con GROBID: {str(e)}"


def procesar_pdf_profundo(archivo_pdf_bytes):
    st.session_state["sugerencias_tags"] = []
    if not PDF_DISPONIBLE:
        return False, "Librería pdfplumber no disponible."

    try:
        with pdfplumber.open(io.BytesIO(archivo_pdf_bytes)) as pdf:
            if not pdf.pages:
                return False, "El PDF está vacío."

            primera_pagina = pdf.pages[0]
            texto_completo = primera_pagina.extract_text() or ""

            palabras = primera_pagina.extract_words(
                extra_attrs=["size", "fontname"], keep_blank_chars=False
            )

            if not palabras:
                if OCR_DISPONIBLE:
                    st.session_state["in_titulo"] = "Documento Escaneado"
                    st.session_state["in_fuente"] = "Requiere revisión manual (OCR)"
                    st.session_state["in_tipo_fuente"] = "Artículo de Revista"
                    st.session_state["select_tipo_fuente"] = "Artículo de Revista"
                    return True, "PDF escaneado. Se aplicó OCR básico."
                else:
                    return False, "No se pudo extraer texto legible del PDF."

            palabras_ordenadas = sorted(
                palabras, key=lambda w: (round(w["top"], 1), w["x0"])
            )
            lineas = []
            linea_actual = []
            top_actual = None

            for p in palabras_ordenadas:
                if top_actual is None or abs(p["top"] - top_actual) <= 3:
                    linea_actual.append(p)
                    top_actual = p["top"]
                else:
                    lineas.append(linea_actual)
                    linea_actual = [p]
                    top_actual = p["top"]
            if linea_actual:
                lineas.append(linea_actual)

            bloques = []
            for l in lineas:
                texto_linea = " ".join(w["text"] for w in l).strip()
                if texto_linea:
                    tamano_promedio = sum(w["size"] for w in l) / len(l)
                    bloques.append({
                        "texto": texto_linea,
                        "size": tamano_promedio,
                        "top": l[0]["top"],
                        "bottom": l[0]["bottom"],
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

            bloques_despues_titulo = bloques[indice_titulo_fin + 1 :]
            autores_encontrados = []

            for b in bloques_despues_titulo[:5]:
                texto = b["texto"]
                if re.search(
                    r"http|@|vol|no\.|issn|doi|revista|recibido|aceptado",
                    texto, re.IGNORECASE,
                ):
                    continue
                if len(texto) > 3 and not re.search(r"\b(19\d{2}|20[0-3]\d)\b", texto):
                    autores_encontrados.append(texto)
                    if len(autores_encontrados) >= 2:
                        break

            autor = " / ".join(autores_encontrados) if autores_encontrados else ""

            altura_pagina = primera_pagina.height
            lineas_revista = []

            for b in bloques:
                es_encabezado_o_pie = (b["top"] < 100) or (b["bottom"] > (altura_pagina - 100))
                if es_encabezado_o_pie and re.search(
                    r"revista|iztacala|vol|no\.|issn|pp|\d{4}", b["texto"], re.IGNORECASE
                ):
                    lineas_revista.append(b["texto"])

            revista = " - ".join(lineas_revista) if lineas_revista else "Documento PDF"

            matches_anios = re.findall(r"\b(19\d{2}|20[0-2]\d)\b", texto_completo)
            anio = normalizar_fecha_apa(matches_anios[0]) if matches_anios else ""

            st.session_state["sugerencias_tags"] = (
                extraer_palabras_clave_texto(texto_completo[:1000])[:10]
            )

            st.session_state["in_titulo"] = titulo
            st.session_state["in_autor"] = autor
            st.session_state["in_fuente"] = revista
            st.session_state["in_anio"] = anio
            st.session_state["in_url"] = ""
            st.session_state["in_tipo_fuente"] = "Artículo de Revista"
            st.session_state["select_tipo_fuente"] = "Artículo de Revista"

            return True, "¡Análisis local del PDF completado!"

    except Exception as e:
        return False, f"Error al procesar la estructura del PDF: {str(e)}"


# --- CONTROL DE NAVEGACIÓN DE PESTAÑAS ---
opcion_pestana = st.radio(
    "Navegación",
    ["➕ Crear Cita y Referencia", "🔍 Mis Citas Guardadas"],
    horizontal=True,
    key="pestana_activa",
    label_visibility="collapsed",
)

st.divider()

if opcion_pestana == "➕ Crear Cita y Referencia":
    st.subheader("1. Extraer datos automáticamente")

    input_busqueda = st.text_input(
        "Pega una URL (YouTube/Web), código DOI o número ISBN:",
        key="input_extraer",
        on_change=al_cambiar_input_extraer,
    )
    col_btn, col_file = st.columns([1, 2])

    with col_btn:
        if st.button(
            "🔍 Extraer de URL/DOI/ISBN",
            type="primary",
            key="btn_extraer_main",
        ):
            if input_busqueda:
                if "youtube.com" in input_busqueda or "youtu.be" in input_busqueda:
                    exito, msg = extraer_datos_youtube(input_busqueda)
                elif es_isbn(input_busqueda):
                    exito, msg = extraer_datos_isbn(input_busqueda)
                elif "10." in input_busqueda or "doi.org" in input_busqueda:
                    exito, msg = extraer_datos_doi(input_busqueda)
                else:
                    exito, msg = extraer_datos_web(input_busqueda)

                if exito:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
            else:
                st.warning("Por favor ingresa una URL, DOI o ISBN.")

    with col_file:
        archivo_pdf = st.file_uploader("Sube tu archivo PDF:", type=["pdf"])
        if archivo_pdf is not None:
            if st.button("🚀 Analizar PDF con GROBID", type="secondary"):
                with st.spinner("Procesando documento con el motor GROBID..."):
                    bytes_data = archivo_pdf.read()

                    exito, msg = procesar_pdf_con_grobid(bytes_data)

                    if not exito:
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
    url_in = st.text_input("URL / DOI", key="in_url", on_change=actualizar_tipo_al_tipear)

    st.divider()

    if st.button("📝 Generar Cita y Referencia APA 7", key="btn_generar_cita", type="primary"):
        ref_autores, cita_par, cita_nar, anio_ref = procesar_autores_y_citas(
            autor_in, titulo_in, anio_in
        )

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
            if titulo_in.strip():
                partes_ref.append(f"*{titulo_in.strip()}*.")
            partes_ref.append(f"{anio_ref}.")

        if ref_autores and titulo_in.strip():
            if "YouTube" in fuente_in:
                partes_ref.append(f"{titulo_in.strip()}.")
            else:
                partes_ref.append(f"*{titulo_in.strip()}*.")

        if fuente_in.strip():
            partes_ref.append(f"{fuente_in.strip()}.")
        if url_in.strip():
            partes_ref.append(url_in.strip())

        referencia_final = " ".join(partes_ref)
        st.code(referencia_final, language=None)

        st.session_state["last_cita_in_text"] = f"Par: {cita_par} | Nar: {cita_nar}"
        st.session_state["last_referencia_apa"] = referencia_final

    st.divider()
    st.subheader("📌 Guardar en la Biblioteca y Proyectos")

    opciones_tipo = [
        "Artículo de Revista", "Libro", "Capítulo de Libro",
        "Página Web", "Tesis / Monografía", "Video / Multimedia", "Otro"
    ]

    c_tipo, c_proj, c_fav = st.columns([2, 2, 1])

    with c_tipo:
        tipo_fuente = st.selectbox("Tipo de fuente:", opciones_tipo, key="select_tipo_fuente")

    with c_proj:
        proyecto_input = st.text_input("📁 Proyecto / Trabajo:", key="in_proyecto", placeholder="Ej: Tesis, Ensayo 1")

    with c_fav:
        st.write("")
        st.write("")
        es_fav = st.checkbox("⭐ Favorito")

    tags_input = st.text_input(
        "🏷️️ Etiquetas / Tags (separadas por coma):",
        key="in_tags",
        placeholder="Ej: #Psicometria, #Evaluacion, #Psicologia",
    )

    sugerencias = st.session_state.get("sugerencias_tags", [])
    if sugerencias:
        st.caption("💡 **Sugerencias detectadas (haz clic para agregar):**")
        cols_sug = st.columns(min(len(sugerencias), 5))
        for idx, tag in enumerate(sugerencias):
            col_idx = idx % min(len(sugerencias), 5)
            with cols_sug[col_idx]:
                st.button(
                    tag,
                    key=f"btn_sug_{idx}",
                    on_click=agregar_tag_sugerido,
                    args=(tag,),
                )

    if st.button("💾 Guardar en Base de Datos", key="btn_guardar_db", type="primary", use_container_width=True):
        if titulo_in.strip() or autor_in.strip():
            if "last_referencia_apa" not in st.session_state or "last_cita_in_text" not in st.session_state:
                ref_autores, cita_par, cita_nar, anio_ref = procesar_autores_y_citas(
                    autor_in, titulo_in, anio_in
                )
                partes_ref = []
                if ref_autores:
                    partes_ref.append(f"{ref_autores}")
                    partes_ref.append(f"{anio_ref}.")
                else:
                    if titulo_in.strip():
                        partes_ref.append(f"*{titulo_in.strip()}*.")
                    partes_ref.append(f"{anio_ref}.")

                if ref_autores and titulo_in.strip():
                    if "YouTube" in fuente_in:
                        partes_ref.append(f"{titulo_in.strip()}.")
                    else:
                        partes_ref.append(f"*{titulo_in.strip()}*.")

                if fuente_in.strip():
                    partes_ref.append(f"{fuente_in.strip()}.")
                if url_in.strip():
                    partes_ref.append(url_in.strip())

                cita_apa_val = " ".join(partes_ref)
                cita_in_text_val = f"Par: {cita_par} | Nar: {cita_nar}"
            else:
                cita_apa_val = st.session_state["last_referencia_apa"]
                cita_in_text_val = st.session_state["last_cita_in_text"]

            fav_int = 1 if es_fav else 0
            proj_final = proyecto_input.strip() if proyecto_input.strip() else "General"

            guardar_cita_db(
                autor=autor_in, anio=anio_in, titulo=titulo_in,
                fuente=fuente_in, url=url_in, cita_in_text=cita_in_text_val,
                cita_apa=cita_apa_val, tipo_fuente=tipo_fuente,
                es_favorito=fav_int, tags=tags_input.strip(), proyecto=proj_final,
            )
limpiar_formulario()
            st.session_state["pestana_activa"] = "🔍 Mis Citas Guardadas"
            st.toast("¡Fuente guardada exitosamente! Redirigiendo...", icon="✅")
            st.rerun()
        else:
            st.error("Ingresa al menos el título o autor antes de guardar.")

elif opcion_pestana == "🔍 Mis Citas Guardadas":
    st.subheader("🔍 Biblioteca de Fuentes Guardadas")
    df_citas = obtener_citas_db()

    if df_citas.empty:
        st.info("Aún no has guardado ninguna cita en la base de datos.")
    else:
        df_citas["autor_sort"] = df_citas["autor"].fillna(df_citas["titulo"])
        df_ordenado = df_citas.sort_values(by="autor_sort", ascending=True).drop(columns=["autor_sort"])

        # --- EXTRACCIÓN DE TODAS LAS ETIQUETAS ÚNICAS PARA FILTRADO INTELIGENTE ---
        todas_las_tags = set()
        for t_str in df_ordenado["tags"].dropna():
            for t in str(t_str).split(","):
                clean_t = t.strip()
                if clean_t:
                    todas_las_tags.add(clean_t)
        lista_tags_disponibles = sorted(list(todas_las_tags))

        with st.expander("🎛 Panel de Organización y Filtros Inteligentes", expanded=True):
            col_proj, col_tipo = st.columns(2)
            with col_proj:
                proyectos_disponibles = ["Todos"] + sorted(list(df_ordenado["proyecto"].dropna().unique()))
                filtro_proyecto = st.selectbox("📁 Filtrar por Proyecto / Trabajo:", proyectos_disponibles)
            with col_tipo:
                tipos_disponibles = ["Todos"] + sorted(list(df_ordenado["tipo_fuente"].dropna().unique()))
                filtro_tipo = st.selectbox("📖 Filtrar por Tipo de fuente:", tipos_disponibles)

            col_search, col_tags_filter = st.columns([1, 1])
            with col_search:
                busqueda = st.text_input("🔎 Búsqueda general (Autor, Título, Revista):", key="search_db")
            with col_tags_filter:
                filtro_tags = st.multiselect("🏷️ Filtrar por Etiquetas:", lista_tags_disponibles)

            col_fav, col_vista = st.columns([1, 1])
            with col_fav:
                solo_favs = st.checkbox("⭐ Mostrar solo favoritos")
            with col_vista:
                modo_vista = st.radio("Vista:", ["Tarjetas", "Tabla"], horizontal=True)

        df_filtrado = df_ordenado.copy()

        # Aplicar Filtro de Proyecto
        if filtro_proyecto != "Todos":
            df_filtrado = df_filtrado[df_filtrado["proyecto"] == filtro_proyecto]

        # Aplicar Filtro de Tipo de Fuente
        if filtro_tipo != "Todos":
            df_filtrado = df_filtrado[df_filtrado["tipo_fuente"] == filtro_tipo]

        # Aplicar Filtro de Favoritos
        if solo_favs:
            df_filtrado = df_filtrado[df_filtrado["es_favorito"] == 1]

        # Aplicar Búsqueda General de Texto
        if busqueda:
            mask_busqueda = (
                df_filtrado["titulo"].astype(str).str.contains(busqueda, case=False, na=False)
                | df_filtrado["autor"].astype(str).str.contains(busqueda, case=False, na=False)
                | df_filtrado["anio"].astype(str).str.contains(busqueda, case=False, na=False)
                | df_filtrado["tags"].astype(str).str.contains(busqueda, case=False, na=False)
            )
            df_filtrado = df_filtrado[mask_busqueda]

        # Aplicar Filtro de Etiquetas Múltiples
        if filtro_tags:
            def tiene_tags_seleccionados(tags_row):
                if not pd.notna(tags_row):
                    return False
                tags_en_fila = [t.strip() for t in str(tags_row).split(",")]
                return any(tag in tags_en_fila for tag in filtro_tags)
            
            df_filtrado = df_filtrado[df_filtrado["tags"].apply(tiene_tags_seleccionados)]

        st.caption(f"Mostrando **{len(df_filtrado)}** de **{len(df_citas)}** fuentes en total.")

        col_txt, col_csv = st.columns(2)
        bibliografia_completa = "\n\n".join(df_filtrado["Referencia APA 7"].dropna().tolist())

        with col_txt:
            st.download_button(
                label="📄 Exportar Bibliografía (.TXT)",
                data=bibliografia_completa,
                file_name="bibliografia_filtrada.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with col_csv:
            csv_data = df_filtrado.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📊 Exportar Resultados (.CSV)",
                data=csv_data,
                file_name="citas_filtradas.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.divider()

        if modo_vista == "Tabla":
            st.dataframe(df_filtrado, use_container_width=True)

        else:
            for _, row in df_filtrado.iterrows():
                es_fav = "⭐ " if row.get("es_favorito") == 1 else ""
                proj_tag = f"📂 [{row.get('proyecto', 'General')}] " if pd.notna(row.get('proyecto')) else ""
                autor_head = row["autor"] if pd.notna(row["autor"]) and row["autor"] else "Sin autor"
                anio_head = f"({row['anio']})" if pd.notna(row["anio"]) and row["anio"] else "(s. f.)"
                titulo_head = (
                    str(row["titulo"])[:40] + "..."
                    if len(str(row["titulo"])) > 40
                    else str(row["titulo"])
                )

                expander_label = f"{es_fav}{proj_tag}📖 {autor_head} {anio_head} — {titulo_head}"

                with st.expander(expander_label):
                    col_info, col_del = st.columns([4, 1])

                    with col_info:
                        st.markdown("**Referencia APA 7:**")
                        st.code(row["Referencia APA 7"], language=None)

                        st.markdown("**Cita en texto:**")
                        st.code(row["Cita en Texto"], language=None)

                        c_det1, c_det2 = st.columns(2)
                        with c_det1:
                            st.caption(f"📁 **Proyecto:** `{row.get('proyecto', 'General')}`")
                        with c_det2:
                            st.caption(f"📌 **Tipo:** `{row.get('tipo_fuente', 'General')}`")

                        url_val = row.get("url")
                        if pd.notna(url_val) and str(url_val).strip():
                            st.caption(f"🔗 **Enlace:** [{url_val}]({url_val})")

                        if pd.notna(row.get("tags")) and str(row.get("tags")).strip():
                            st.caption(f"🏷 **Tags:** `{row['tags']}`")

                    with col_del:
                        if st.button("🗑️ Eliminar", key=f"del_{row['id']}", type="secondary"):
                            conn = sqlite3.connect("fuentes_apa.db")
                            c = conn.cursor()
                            c.execute("DELETE FROM citas WHERE id = ?", (row["id"],))
                            conn.commit()
                            conn.close()
                            st.toast("Fuente eliminada correctamente.", icon="🗑️")
                            st.rerun()
