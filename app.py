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


# --- VALIDADOR DE TEXTO ANTI-BASURA ---
def es_texto_valido(texto, min_caracteres=3):
    if not texto or not str(texto).strip():
        return False
    t = str(texto).strip()
    if len(t) < min_caracteres:
        return False
    # Detectar palabras largas aleatorias sin vocales o secuencias caóticas (ej. ajsdhasdhashasd)
    palabras = t.split()
    for p in palabras:
        if len(p) >= 12:
            vocales = len(re.findall(r"[aeiouáéíóúAEIOUÁÉÍÓÚ]", p))
            if vocales == 0 or (len(p) > 14 and vocales / len(p) < 0.15):
                return False
    return True

# --- HELPER ISBN ---
def es_isbn(texto):
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


def formatear_tag(texto_tag):
    if not texto_tag:
        return ""
    trad = traducir_al_espanol(texto_tag.strip())
    palabras = re.findall(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+", trad)
    if not palabras:
        return ""
    camel = "".join(p.capitalize() for p in palabras)
    return f"#{camel}"

def extraer_palabras_clave_texto(texto):
    if not texto:
        return []
    sugerencias = []
    
    # 1. Hashtags directos existentes
    hashtags_directos = re.findall(r"#(\w+)", texto)
    for h in hashtags_directos:
        tag_fmt = formatear_tag(h)
        if tag_fmt and tag_fmt not in sugerencias:
            sugerencias.append(tag_fmt)

    # 2. Búsqueda de bigramas (conceptos de 2 palabras relevantes)
    palabras_raw = re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}\b", texto)
    for i in range(len(palabras_raw) - 1):
        p1, p2 = palabras_raw[i].lower(), palabras_raw[i+1].lower()
        if p1 not in STOPWORDS and p2 not in STOPWORDS and len(p1) > 3 and len(p2) > 3:
            bigrama_tag = formatear_tag(f"{p1} {p2}")
            if bigrama_tag and bigrama_tag not in sugerencias:
                sugerencias.append(bigrama_tag)
            if len(sugerencias) >= 5:
                break

    # 3. Palabras individuales relevantes
    for p in palabras_raw:
        if p.lower() not in STOPWORDS:
            tag_fmt = formatear_tag(p)
            if tag_fmt and tag_fmt not in sugerencias and len(tag_fmt) > 4:
                sugerencias.append(tag_fmt)
            if len(sugerencias) >= 12:
                break

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
    if st.button("Entrar", type="primary") or (clave_ingresada and clave_ingresada == CONTRASEÑA_CORRECTA):
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
        ("tema", "TEXT DEFAULT 'General'"),
        ("notas", "TEXT DEFAULT ''"),
    ]
    for col_nombre, col_tipo in columnas_nuevas:
        try:
            c.execute(f"ALTER TABLE citas ADD COLUMN {col_nombre} {col_tipo}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

init_db()

def guardar_cita_db(autor, anio, titulo, fuente, url, cita_in_text, cita_apa, tipo_fuente="General", es_favorito=0, tags="", proyecto="General", tema="General", notas=""):
    conn = sqlite3.connect("fuentes_apa.db", timeout=15)
    c = conn.cursor()
    
    # Detector de duplicados en el mismo proyecto (por URL o por Título exacto)
    registro_existente = None
    if url and url.strip():
        c.execute("SELECT id FROM citas WHERE url = ? AND proyecto = ?", (url.strip(), proyecto.strip()))
        registro_existente = c.fetchone()
    
    if not registro_existente and titulo and titulo.strip():
        c.execute("SELECT id FROM citas WHERE LOWER(titulo) = LOWER(?) AND proyecto = ?", (titulo.strip(), proyecto.strip()))
        registro_existente = c.fetchone()

    if registro_existente:
        c_id = registro_existente[0]
        c.execute("""
            UPDATE citas 
            SET autor = ?, anio = ?, titulo = ?, fuente = ?, url = ?, 
                cita_in_text = ?, cita_apa = ?, tipo_fuente = ?, 
                es_favorito = ?, tags = ?, tema = ?, notas = ?
            WHERE id = ?
        """, (autor, anio, titulo, fuente, url, cita_in_text, cita_apa, tipo_fuente, es_favorito, tags, tema, notas, c_id))
        conn.commit()
        conn.close()
        return "actualizado"
    else:
        c.execute("""
            INSERT INTO citas (autor, anio, titulo, fuente, url, cita_in_text, cita_apa, tipo_fuente, es_favorito, tags, proyecto, tema, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (autor, anio, titulo, fuente, url, cita_in_text, cita_apa, tipo_fuente, es_favorito, tags, proyecto, tema, notas))
        conn.commit()
        conn.close()
        return "creado"

def obtener_citas_db():
    conn = sqlite3.connect("fuentes_apa.db")
    df = pd.read_sql_query("""
        SELECT id, autor, anio, titulo, fuente, url, 
                  cita_in_text AS 'Cita en Texto', 
                  cita_apa AS 'Referencia APA 7',
                  tipo_fuente, es_favorito, tags, proyecto, tema, notas
           FROM citas ORDER BY id DESC
    """, conn)
    conn.close()
    return df

def render_restaurar_backup_csv():
    with st.expander("📥 Restaurar o Importar Copia de Seguridad (.CSV)", expanded=False):
        st.write("¿Cambiaste de equipo o se reinició tu base de datos? Sube aquí un archivo CSV previamente exportado para recuperar todas tus citas:")
        archivo_csv_subido = st.file_uploader("Selecciona archivo CSV de respaldo:", type=["csv"], key="uploader_restore_csv")
        if archivo_csv_subido is not None:
            if st.button("🔄 Importar todas las citas del CSV", key="btn_ejecutar_import_csv", type="primary"):
                try:
                    df_import = pd.read_csv(archivo_csv_subido)
                    columnas_necesarias = ["autor", "anio", "titulo"]
                    if not all(col in df_import.columns for col in columnas_necesarias):
                        st.error("El archivo CSV no contiene las columnas mínimas requeridas (autor, anio, titulo).")
                    else:
                        importadas = 0
                        actualizadas = 0
                        for _, r in df_import.iterrows():
                            aut = str(r.get("autor", "")).strip() if pd.notna(r.get("autor")) else ""
                            an = str(r.get("anio", "")).strip() if pd.notna(r.get("anio")) else ""
                            tit = str(r.get("titulo", "")).strip() if pd.notna(r.get("titulo")) else ""
                            fuen = str(r.get("fuente", "")).strip() if pd.notna(r.get("fuente")) else ""
                            u = str(r.get("url", "")).strip() if pd.notna(r.get("url")) else ""
                            cin = str(r.get("Cita en Texto", r.get("cita_in_text", ""))).strip()
                            cap = str(r.get("Referencia APA 7", r.get("cita_apa", ""))).strip()
                            tip = str(r.get("tipo_fuente", "General")).strip()
                            fav = int(r.get("es_favorito", 0)) if pd.notna(r.get("es_favorito")) else 0
                            tg = str(r.get("tags", "")).strip() if pd.notna(r.get("tags")) else ""
                            pr = str(r.get("proyecto", "General")).strip() if pd.notna(r.get("proyecto")) else "General"
                            tm = str(r.get("tema", "General")).strip() if pd.notna(r.get("tema")) else "General"
                            nt = str(r.get("notas", "")).strip() if pd.notna(r.get("notas")) else ""

                            if tit or aut:
                                res_g = guardar_cita_db(
                                    autor=aut, anio=an, titulo=tit, fuente=fuen, url=u,
                                    cita_in_text=cin, cita_apa=cap, tipo_fuente=tip,
                                    es_favorito=fav, tags=tg, proyecto=pr, tema=tm, notas=nt
                                )
                                if res_g == "actualizado":
                                    actualizadas += 1
                                else:
                                    importadas += 1

                        st.success(f"¡Proceso completado! Se agregaron {importadas} citas nuevas y se actualizaron {actualizadas}.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Error al importar el archivo CSV: {str(e)}")



# --- INTERFAZ PRINCIPAL Y SESSION STATE ---
# --- CONFIGURACIÓN DE IA EN SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configuración")
    st.markdown("### 🤖 Motor de IA Gemini")
    
    secret_key = ""
    try:
        secret_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        pass
    
    default_key = st.session_state.get("gemini_api_key", secret_key)
    gemini_key = st.text_input(
        "Clave API Gemini (Opcional):",
        value=default_key,
        type="password",
        help="Obtén tu clave gratuita en https://aistudio.google.com/. Permite análisis de PDF ultra-preciso con IA.",
        key="input_gemini_key",
    )
    if gemini_key:
        st.session_state["gemini_api_key"] = gemini_key
        st.success("✨ Gemini 3.8 Flash activo")
    else:
        st.caption("ℹ️ Sin clave: Se usará el motor local + Crossref (100% gratuito).")
    
    st.divider()

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
    st.session_state["in_proyecto"] = ""
if "in_fav" not in st.session_state:
    st.session_state["in_fav"] = False
if "in_tema" not in st.session_state:
    st.session_state["in_tema"] = "General"
if "in_notas" not in st.session_state:
    st.session_state["in_notas"] = ""

TEMAS_DISPONIBLES = [
    "General", "Estadística y Datos", "Ciencia y Metodología",
    "Matemáticas", "Computación y Software", "Psicología y Ciencias Sociales",
    "Salud y Medicina", "Humanidades y Filosofía", "Economía y Negocios", "Otro"
]
if "sugerencias_tags" not in st.session_state:
    st.session_state["sugerencias_tags"] = []
if "input_extraer" not in st.session_state:
    st.session_state["input_extraer"] = ""
if "_input_extraer_tenia_contenido" not in st.session_state:
    st.session_state["_input_extraer_tenia_contenido"] = False
if "pestana_activa" not in st.session_state:
    st.session_state["pestana_activa"] = "➕ Crear Cita y Referencia"


def limpiar_formulario():
    # Se modifican los campos de manera segura; como esta función ahora solo 
    # se llamará dentro de callbacks (on_change o on_click), no lanzará el error.
    keys_string = [
        "in_autor", "in_anio", "in_titulo", "in_fuente", 
        "in_url", "in_tags", "input_extraer", "in_proyecto", "in_notas"
    ]
    for key in keys_string:
        if key in st.session_state:
            st.session_state[key] = ""
            
    if "in_fav" in st.session_state:
        st.session_state["in_fav"] = False
            
    st.session_state["in_tipo_fuente"] = "Artículo de Revista"
    st.session_state["select_tipo_fuente"] = "Artículo de Revista"
    st.session_state["in_tema"] = "General"
    st.session_state["sugerencias_tags"] = []
    
    if "last_referencia_apa" in st.session_state:
        del st.session_state["last_referencia_apa"]
    if "last_cita_in_text" in st.session_state:
        del st.session_state["last_cita_in_text"]
    st.session_state["_input_extraer_tenia_contenido"] = False


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
    texto_actual = st.session_state.get("input_extraer", "").strip()
    if not texto_actual:
        # Solo limpiar si había datos extraídos (título relleno) y el usuario borró la URL
        # Así evitamos limpiar al simplemente cambiar de pestaña o recargar la página
        if st.session_state.get("_input_extraer_tenia_contenido"):
            limpiar_formulario()
        st.session_state["_input_extraer_tenia_contenido"] = False
    else:
        st.session_state["_input_extraer_tenia_contenido"] = True
        actualizar_tipo_al_tipear()

def al_cambiar_pdf():
    # Limpiar solo si el PDF se descartó y había datos extraídos de él (título relleno)
    # Así evitamos limpiar al simplemente entrar a la página sin haber analizado nada
    if st.session_state.get("uploader_pdf") is None:
        if st.session_state.get("in_titulo", "").strip() or st.session_state.get("in_autor", "").strip():
            limpiar_formulario()


def procesar_autores_y_citas(autor_str, titulo_str, anio_str):
    anio_clean = anio_str.strip()
    # Si el año contiene solo letras o texto basura sin dígitos válidos, normalizar a s. f.
    if anio_clean and not re.search(r"\b(18\d{2}|19\d{2}|20[0-3]\d)\b", anio_clean) and "s. f." not in anio_clean.lower():
        anio_clean = "s. f."
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

                # Extraer categorías temáticas oficiales de Google Books
                categories = book.get("categories", [])
                for cat in categories:
                    partes_cat = re.split(r"[/,]", cat)
                    for c_item in partes_cat:
                        tag_cat = formatear_tag(c_item)
                        if tag_cat and tag_cat not in st.session_state["sugerencias_tags"]:
                            st.session_state["sugerencias_tags"].append(tag_cat)
                    
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


# --- INTEGRACIÓN CON GOOGLE GEMINI 3.8 FLASH ---
def extraer_metadatos_con_gemini(bytes_pdf, api_key):
    if not api_key or not str(api_key).strip():
        return False, "No se proporcionó API Key de Gemini."

    texto_muestra = ""
    try:
        with pdfplumber.open(io.BytesIO(bytes_pdf)) as pdf:
            paginas = pdf.pages[:3]
            for p in paginas:
                t = p.extract_text()
                if t:
                    texto_muestra += t + "\n"
    except Exception:
        pass

    if not texto_muestra.strip():
        return False, "No se pudo extraer texto legible del PDF para enviar a Gemini."

    prompt = (
        "Eres un experto bibliotecario y especialista en Normas APA 7.ª edición en español.\n"
        "Analiza el siguiente texto de las primeras páginas de un documento académico o libro y extrae con máxima fidelidad sus metadatos bibliográficos.\n"
        "Responde ÚNICAMENTE con un objeto JSON válido (sin explicaciones adicionales, sin markdown adicional, sin bloques de código) con los siguientes campos:\n\n"
        "{\n"
        '  "titulo": "Título completo y exacto del artículo o libro",\n'
        '  "autores": ["Apellido, N.", "Apellido, N."],\n'
        '  "anio": "Año de publicación (4 dígitos)",\n'
        '  "fuente": "Nombre de la revista académica, editorial, o institución",\n'
        '  "doi_o_url": "DOI (ej. 10.xxxx/...) o enlace URL si está presente en el texto, o vacío",\n'
        '  "tipo_fuente": "Artículo de Revista",\n'
        '  "tema_sugerido": "General",\n'
        '  "tags_sugeridos": ["#Tag1", "#Tag2", "#Tag3"],\n'
        '  "resumen_clave": "Breve resumen de 1 a 2 oraciones sobre el propósito y conclusión del texto."\n'
        "}\n\n"
        'Valores permitidos para "tipo_fuente":\n'
        '["Artículo de Revista", "Libro", "Capítulo de Libro", "Página Web", "Tesis / Monografía", "Video / Multimedia", "Otro"]\n\n'
        'Valores recomendados para "tema_sugerido":\n'
        '["Estadística y Datos", "Ciencia y Metodología", "Matemáticas", "Computación y Software", "Psicología y Ciencias Sociales", "Salud y Medicina", "Humanidades y Filosofía", "Economía y Negocios", "General"]\n\n'
        "Texto del documento:\n"
        + texto_muestra[:5000]
    )

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={str(api_key).strip()}"

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            data_json = res.json()
            candidates = data_json.get("candidates", [])
            if candidates:
                raw_text = candidates[0]["content"]["parts"][0]["text"].strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                
                info = json.loads(raw_text)

                if info.get("titulo"):
                    st.session_state["in_titulo"] = info["titulo"].strip()
                
                autores = info.get("autores", [])
                if isinstance(autores, list) and autores:
                    st.session_state["in_autor"] = ", ".join(autores)
                elif isinstance(autores, str):
                    st.session_state["in_autor"] = autores.strip()

                if info.get("anio"):
                    st.session_state["in_anio"] = str(info["anio"]).strip()
                
                if info.get("fuente"):
                    st.session_state["in_fuente"] = info["fuente"].strip()
                
                if info.get("doi_o_url"):
                    doi_val = info["doi_o_url"].strip()
                    if "10." in doi_val and not doi_val.startswith("http"):
                        doi_val = f"https://doi.org/{doi_val}"
                    st.session_state["in_url"] = doi_val
                
                tipos_validos = [
                    "Artículo de Revista", "Libro", "Capítulo de Libro", 
                    "Página Web", "Tesis / Monografía", "Video / Multimedia", "Otro"
                ]
                if info.get("tipo_fuente") in tipos_validos:
                    st.session_state["in_tipo_fuente"] = info["tipo_fuente"]
                    st.session_state["select_tipo_fuente"] = info["tipo_fuente"]

                if info.get("tema_sugerido") in TEMAS_DISPONIBLES:
                    st.session_state["in_tema"] = info["tema_sugerido"]

                tags = info.get("tags_sugeridos", [])
                if tags:
                    tags_fmt = [formatear_tag(t) for t in tags if formatear_tag(t)]
                    st.session_state["sugerencias_tags"] = tags_fmt[:10]

                # El resumen de Gemini se omite en notas para que el usuario decida qué anotar
                # (info["resumen_clave"] disponible internamente si se necesita en el futuro)

                return True, "¡Metadatos analizados y clasificados con Gemini 3.8 Flash!"
        elif res.status_code == 429:
            return False, "Cuota de Gemini ocupada temporalmente (429). Se usará el motor local."
        else:
            return False, f"Respuesta de Gemini: código {res.status_code}"
    except Exception as e:
        return False, f"Error al consultar Gemini API: {str(e)}"

    return False, "No se pudo interpretar la respuesta de Gemini."

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


def limpiar_cadena_autor(texto_autor):
    limpio = re.sub(r"[\w\.-]+@[\w\.-]+", "", texto_autor)
    limpio = re.sub(r"[\d\*\†\‡\^]+", "", limpio)
    palabras_prohibidas = r"\b(universidad|facultad|instituto|departamento|department|school|faculty|laboratory|center|centro|author|correspondence|abstract|keywords|resumen)\b"
    lineas_limpias = []
    for seg in re.split(r"[,;/\n]", limpio):
        s = seg.strip()
        if len(s) > 2 and not re.search(palabras_prohibidas, s, re.IGNORECASE):
            lineas_limpias.append(s)
    return ", ".join(lineas_limpias[:4])

def procesar_pdf_profundo(archivo_pdf_bytes):
    st.session_state["sugerencias_tags"] = []
    if not PDF_DISPONIBLE:
        return False, "Librería pdfplumber no disponible."

    try:
        with pdfplumber.open(io.BytesIO(archivo_pdf_bytes)) as pdf:
            if not pdf.pages:
                return False, "El PDF está vacío."

            primera_pagina = pdf.pages[0]
            texto_p1 = primera_pagina.extract_text() or ""
            texto_completo = texto_p1

            # 1. Búsqueda inteligente de DOI en el texto del PDF (página 1 y 2)
            match_doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", texto_completo)
            if not match_doi and len(pdf.pages) > 1:
                texto_p2 = pdf.pages[1].extract_text() or ""
                texto_completo += "\n" + texto_p2
                match_doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", texto_p2)

            if match_doi:
                clean_doi = match_doi.group(0).rstrip(".,;)")
                exito_doi, msg_doi = extraer_datos_doi(clean_doi)
                if exito_doi:
                    return True, f"¡DOI detectado ({clean_doi})! Metadatos extraídos de Crossref con precisión."

            # 2. Revisión de metadatos nativos internos del PDF
            meta = pdf.metadata or {}
            meta_titulo = str(meta.get("Title", "")).strip()
            meta_autor = str(meta.get("Author", "")).strip()

            titulo_candidato = ""
            if meta_titulo and len(meta_titulo) > 8 and not re.search(r"(untitled|microsoft word|scan|document)", meta_titulo, re.IGNORECASE):
                titulo_candidato = meta_titulo

            # 3. Heurística visual de bloques de texto por tamaño
            palabras = primera_pagina.extract_words(
                extra_attrs=["size", "fontname"], keep_blank_chars=False
            )

            if not palabras and not titulo_candidato:
                if OCR_DISPONIBLE:
                    st.session_state["in_titulo"] = "Documento Escaneado"
                    st.session_state["in_fuente"] = "Requiere revisión manual"
                    st.session_state["in_tipo_fuente"] = "Artículo de Revista"
                    st.session_state["select_tipo_fuente"] = "Artículo de Revista"
                    return True, "PDF escaneado. Se aplicó lectura básica."
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

            if not titulo_candidato and bloques:
                altura_p = primera_pagina.height
                bloques_cuerpo = [b for b in bloques if b["top"] > 40 and b["bottom"] < (altura_p - 40)]
                candidatos_t = bloques_cuerpo if bloques_cuerpo else bloques
                max_size = max(b["size"] for b in candidatos_t)

                lineas_titulo = []
                indice_fin_tit = 0
                for idx, b in enumerate(candidatos_t):
                    if abs(b["size"] - max_size) <= 1.5 and len(b["texto"]) > 3:
                        lineas_titulo.append(b["texto"])
                        indice_fin_tit = idx
                    elif lineas_titulo:
                        break
                titulo_candidato = " ".join(lineas_titulo).strip()

            titulo_final = titulo_candidato if titulo_candidato else "Documento PDF"

            # 4. Búsqueda inversa en Crossref por título
            crossref_encontrado = False
            if len(titulo_final) > 15 and len(titulo_final.split()) >= 3:
                try:
                    q_clean = requests.utils.quote(titulo_final[:120])
                    res_cr = requests.get(
                        f"https://api.crossref.org/works?query.title={q_clean}&rows=1",
                        headers={"User-Agent": "APA_Tool/1.0"},
                        timeout=5
                    )
                    if res_cr.status_code == 200:
                        items = res_cr.json().get("message", {}).get("items", [])
                        if items:
                            top_item = items[0]
                            t_encontrado = top_item.get("title", [""])[0]
                            w1 = set(re.findall(r"\w{4,}", titulo_final.lower()))
                            w2 = set(re.findall(r"\w{4,}", t_encontrado.lower()))
                            if w1 and len(w1.intersection(w2)) / max(len(w1), 1) >= 0.5:
                                titulo_final = t_encontrado
                                autores_cr = [
                                    f"{a.get('family', '')}, {a.get('given', '')[0]}."
                                    for a in top_item.get("author", [])
                                    if a.get("family") and a.get("given")
                                ]
                                if autores_cr:
                                    st.session_state["in_autor"] = ", ".join(autores_cr)
                                
                                pubs = top_item.get("container-title", [])
                                if pubs:
                                    st.session_state["in_fuente"] = pubs[0]
                                
                                p_date = top_item.get("published-print") or top_item.get("published-online") or top_item.get("created")
                                if p_date and "date-parts" in p_date:
                                    st.session_state["in_anio"] = str(p_date["date-parts"][0][0])

                                if "DOI" in top_item:
                                    st.session_state["in_url"] = f"https://doi.org/{top_item['DOI']}"

                                crossref_encontrado = True
                except Exception:
                    pass

            if not crossref_encontrado:
                if meta_autor and len(meta_autor) > 2 and not re.search(r"(microsoft|scanner|author|user)", meta_autor, re.IGNORECASE):
                    st.session_state["in_autor"] = limpiar_cadena_autor(meta_autor)
                else:
                    autores_raw = []
                    for b in bloques:
                        t = b["texto"]
                        if re.search(r"@(?!\.)|[0-9]{4}|doi|http|issn|vol\.", t, re.IGNORECASE):
                            continue
                        if t != titulo_final and 3 < len(t) < 80:
                            autores_raw.append(t)
                            if len(autores_raw) >= 2:
                                break
                    st.session_state["in_autor"] = limpiar_cadena_autor(" / ".join(autores_raw))

                anios_match = re.findall(r"\b(19\d{2}|20[0-2]\d)\b", texto_completo)
                if anios_match and not st.session_state.get("in_anio"):
                    st.session_state["in_anio"] = normalizar_fecha_apa(anios_match[0])

                if not st.session_state.get("in_fuente"):
                    st.session_state["in_fuente"] = "Documento PDF"

            st.session_state["in_titulo"] = titulo_final
            st.session_state["in_tipo_fuente"] = "Artículo de Revista"
            st.session_state["select_tipo_fuente"] = "Artículo de Revista"
            st.session_state["sugerencias_tags"] = extraer_palabras_clave_texto(f"{titulo_final} {texto_completo[:600]}")[:10]

            msg_res = "¡Documento PDF analizado con éxito!" if not crossref_encontrado else "¡Artículo identificado y verificado en Crossref!"
            return True, msg_res

    except Exception as e:
        return False, f"Error al procesar el PDF: {str(e)}"


# --- FUNCIÓN CALLBACK DE GUARDADO ---
def accion_guardar():
    titulo = st.session_state.get("in_titulo", "").strip()
    autor = st.session_state.get("in_autor", "").strip()
    
    if not titulo and not autor:
        st.session_state["mensaje_alerta"] = ("error", "Ingresa al menos el título o autor antes de guardar.")
        return

    # Sello de seguridad anti-basura (evitar cadenas aleatorias como ajsdhasdhashasd)
    if titulo and not es_texto_valido(titulo, min_caracteres=3):
        st.session_state["mensaje_alerta"] = ("error", "El título ingresado parece inválido o texto de prueba aleatorio. Por favor ingresa un título legible.")
        return

    if autor and not es_texto_valido(autor, min_caracteres=2):
        st.session_state["mensaje_alerta"] = ("error", "El autor ingresado parece inválido o texto de prueba aleatorio. Por favor ingresa un autor legible.")
        return

    anio = st.session_state.get("in_anio", "").strip()
    fuente = st.session_state.get("in_fuente", "").strip()
    url = st.session_state.get("in_url", "").strip()
    tipo = st.session_state.get("select_tipo_fuente", "Artículo de Revista")
    tema = st.session_state.get("in_tema", "General")
    tags_raw = st.session_state.get("in_tags", "")
    proyecto = st.session_state.get("in_proyecto", "").strip()
    notas = st.session_state.get("in_notas", "").strip()
    fav_int = 1 if st.session_state.get("in_fav", False) else 0

    # Limpieza, formateo y deduplicación de etiquetas
    tags_limpias = []
    tags_vistas = set()
    for t_item in tags_raw.split(","):
        t_clean = t_item.strip()
        if t_clean and t_clean != "#":
            t_fmt = formatear_tag(t_clean)
            if t_fmt and t_fmt.lower() not in tags_vistas:
                tags_limpias.append(t_fmt)
                tags_vistas.add(t_fmt.lower())
    tags_final = ", ".join(tags_limpias)

    if "last_referencia_apa" not in st.session_state or "last_cita_in_text" not in st.session_state:
        ref_autores, cita_par, cita_nar, anio_ref = procesar_autores_y_citas(autor, titulo, anio)
        partes_ref = []
        if ref_autores:
            partes_ref.append(f"{ref_autores}")
            partes_ref.append(f"{anio_ref}.")
        else:
            if titulo:
                partes_ref.append(f"*{titulo}*.")
            partes_ref.append(f"{anio_ref}.")

        if ref_autores and titulo:
            if "YouTube" in fuente:
                partes_ref.append(f"{titulo}.")
            else:
                partes_ref.append(f"*{titulo}*.")

        if fuente:
            partes_ref.append(f"{fuente}.")
        if url:
            partes_ref.append(url)

        cita_apa_val = " ".join(partes_ref)
        cita_in_text_val = f"Par: {cita_par} | Nar: {cita_nar}"
    else:
        cita_apa_val = st.session_state["last_referencia_apa"]
        cita_in_text_val = st.session_state["last_cita_in_text"]

    proj_final = proyecto if proyecto else "General"

    # Guardar en base de datos con detección de duplicados
    res_db = guardar_cita_db(
        autor=autor, anio=anio, titulo=titulo, fuente=fuente, url=url,
        cita_in_text=cita_in_text_val, cita_apa=cita_apa_val,
        tipo_fuente=tipo, es_favorito=fav_int, tags=tags_final,
        proyecto=proj_final, tema=tema, notas=notas
    )
    
    limpiar_formulario()
    st.session_state["pestana_activa"] = "🔍 Mis Citas Guardadas"
    if res_db == "actualizado":
        st.session_state["mensaje_alerta"] = ("toast", "🔄 Esta fuente ya existía en el proyecto: se actualizaron sus datos.")
    else:
        st.session_state["mensaje_alerta"] = ("toast", "¡Fuente guardada exitosamente!")


# --- CONTROL DE NAVEGACIÓN DE PESTAÑAS ---
opcion_pestana = st.radio(
    "Navegación",
    ["➕ Crear Cita y Referencia", "🔍 Mis Citas Guardadas"],
    horizontal=True,
    key="pestana_activa",
    label_visibility="collapsed",
)

# Avisos y notificaciones persistentes entre pestañas
if "mensaje_alerta" in st.session_state:
    tipo_alerta, texto_alerta = st.session_state.pop("mensaje_alerta")
    if tipo_alerta == "toast":
        st.toast(texto_alerta, icon="✅")
    elif tipo_alerta == "error":
        st.error(texto_alerta)

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
        archivo_pdf = st.file_uploader(
            "Sube tu archivo PDF:", 
            type=["pdf"], 
            key="uploader_pdf", 
            on_change=al_cambiar_pdf
        )
        if archivo_pdf is not None:
            if st.button("🚀 Extraer datos del PDF", key="btn_analizar_pdf_unico", type="primary", use_container_width=True):
                with st.spinner("Procesando documento PDF..."):
                    bytes_data = archivo_pdf.read()
                    exito = False
                    msg = ""
                    motor_usado = "local"

                    api_key_activa = st.session_state.get("gemini_api_key", "")
                    if not api_key_activa:
                        try:
                            api_key_activa = st.secrets.get("GEMINI_API_KEY", "")
                        except Exception:
                            pass

                    # Prioridad 1: IA con Gemini 3.8 Flash si hay clave
                    if api_key_activa:
                        exito_gem, msg_gem = extraer_metadatos_con_gemini(bytes_data, api_key_activa)
                        if exito_gem:
                            exito = True
                            msg = msg_gem
                            motor_usado = "ia"
                        else:
                            st.info(f"{msg_gem} -> Cambiando automáticamente a motor local...")
                            exito, msg = procesar_pdf_profundo(bytes_data)
                            motor_usado = "local"
                    else:
                        # Prioridad 2: Motor local reforzado con Crossref y metadatos nativos
                        exito, msg = procesar_pdf_profundo(bytes_data)
                        if not exito:
                            exito, msg = procesar_pdf_con_grobid(bytes_data)
                        motor_usado = "local"

                    if exito:
                        if motor_usado == "ia":
                            st.toast("✨ Analizado con IA (Gemini 3.8 Flash)", icon="🤖")
                        else:
                            st.toast("⚙️ Analizado con Motor Local (Crossref)", icon="📚")
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

    c_tipo, c_tema, c_proj, c_fav = st.columns([2, 2, 2, 1])

    with c_tipo:
        tipo_fuente = st.selectbox("Tipo de fuente:", opciones_tipo, key="select_tipo_fuente")

    with c_tema:
        tema_fuente = st.selectbox("🎯 Tema / Disciplina:", TEMAS_DISPONIBLES, key="in_tema")

    with c_proj:
        proyecto_input = st.text_input("📁 Proyecto / Trabajo:", key="in_proyecto", placeholder="Ej: Tesis, Ensayo 1")

    with c_fav:
        st.write("")
        st.write("")
        es_fav = st.checkbox("⭐ Favorito", key="in_fav")

    tags_input = st.text_input(
        "🏷 Etiquetas / Tags (separadas por coma):",
        key="in_tags",
        placeholder="Ej: #Psicometria, #Evaluacion, #Psicologia",
    )

    notas_input = st.text_area(
        "📝 Notas personales / Cita textual clave (Opcional):",
        key="in_notas",
        placeholder="Ej: Pág. 34: 'La estadística descriptiva organiza los datos...', o ideas clave para mi tesis.",
        height=80,
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

    # Mostrar etiquetas más utilizadas en la biblioteca como atajos rápidos
    try:
        df_hist = obtener_citas_db()
        if not df_hist.empty and "tags" in df_hist.columns:
            tags_conteo = {}
            for t_raw in df_hist["tags"].dropna():
                for t_item in str(t_raw).split(","):
                    t_clean = t_item.strip()
                    if t_clean:
                        tags_conteo[t_clean] = tags_conteo.get(t_clean, 0) + 1
            if tags_conteo:
                tags_top = sorted(tags_conteo.items(), key=lambda x: x[1], reverse=True)[:6]
                st.caption("⭐ **Tus etiquetas más frecuentes:**")
                cols_top = st.columns(len(tags_top))
                for idx_top, (tag_top, _) in enumerate(tags_top):
                    with cols_top[idx_top]:
                        st.button(
                            f"{tag_top}",
                            key=f"btn_top_{idx_top}",
                            on_click=agregar_tag_sugerido,
                            args=(tag_top,),
                        )
    except Exception:
        pass

    st.button("💾 Guardar en Base de Datos", key="btn_guardar_db", type="primary", use_container_width=True, on_click=accion_guardar)

elif opcion_pestana == "🔍 Mis Citas Guardadas":
    st.subheader("🔍 Biblioteca de Fuentes Guardadas")
    df_citas = obtener_citas_db()

    if df_citas.empty:
        st.info("Aún no has guardado ninguna cita en la base de datos.")
        render_restaurar_backup_csv()
    else:
        df_citas["autor_sort"] = df_citas["autor"].fillna(df_citas["titulo"])
        df_ordenado = df_citas.sort_values(by="autor_sort", ascending=True).drop(columns=["autor_sort"])

        todas_las_tags = set()
        for t_str in df_ordenado["tags"].dropna():
            for t in str(t_str).split(","):
                clean_t = t.strip()
                if clean_t:
                    todas_las_tags.add(clean_t)
        lista_tags_disponibles = sorted(list(todas_las_tags))

        with st.expander("🎛 Panel de Organización y Filtros Inteligentes", expanded=True):
            col_proj, col_tema, col_tipo = st.columns(3)
            with col_proj:
                proyectos_disponibles = ["Todos"] + sorted(list(df_ordenado["proyecto"].dropna().unique()))
                filtro_proyecto = st.selectbox("📁 Filtrar por Proyecto / Trabajo:", proyectos_disponibles)
            with col_tema:
                col_temas = df_ordenado["tema"].dropna().unique() if "tema" in df_ordenado.columns else []
                temas_disponibles = ["Todos"] + sorted(list(col_temas))
                filtro_tema = st.selectbox("🎯 Filtrar por Tema / Disciplina:", temas_disponibles)
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

        if filtro_proyecto != "Todos":
            df_filtrado = df_filtrado[df_filtrado["proyecto"] == filtro_proyecto]

        if "tema" in df_filtrado.columns and filtro_tema != "Todos":
            df_filtrado = df_filtrado[df_filtrado["tema"] == filtro_tema]

        if filtro_tipo != "Todos":
            df_filtrado = df_filtrado[df_filtrado["tipo_fuente"] == filtro_tipo]

        if solo_favs:
            df_filtrado = df_filtrado[df_filtrado["es_favorito"] == 1]

        if busqueda:
            mask_busqueda = (
                df_filtrado["titulo"].astype(str).str.contains(busqueda, case=False, na=False)
                | df_filtrado["autor"].astype(str).str.contains(busqueda, case=False, na=False)
                | df_filtrado["anio"].astype(str).str.contains(busqueda, case=False, na=False)
                | df_filtrado["tags"].astype(str).str.contains(busqueda, case=False, na=False)
                | (df_filtrado["notas"].astype(str).str.contains(busqueda, case=False, na=False) if "notas" in df_filtrado.columns else False)
            )
            df_filtrado = df_filtrado[mask_busqueda]

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

        render_restaurar_backup_csv()

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

                        c_det1, c_det2, c_det3 = st.columns(3)
                        with c_det1:
                            st.caption(f"📁 **Proyecto:** `{row.get('proyecto', 'General')}`")
                        with c_det2:
                            st.caption(f"🎯 **Tema:** `{row.get('tema', 'General')}`")
                        with c_det3:
                            st.caption(f"📌 **Tipo:** `{row.get('tipo_fuente', 'General')}`")

                        url_val = row.get("url")
                        if pd.notna(url_val) and str(url_val).strip():
                            st.caption(f"🔗 **Enlace:** [{url_val}]({url_val})")

                        if pd.notna(row.get("tags")) and str(row.get("tags")).strip():
                            st.caption(f"🏷 **Tags:** `{row['tags']}`")

                        if "notas" in row and pd.notna(row.get("notas")) and str(row.get("notas")).strip():
                            st.info(f"📝 **Nota personal / Cita clave:**\n\n{row['notas']}")

                    with col_del:
                        if st.button("🗑️ Eliminar", key=f"del_{row['id']}", type="secondary"):
                            conn = sqlite3.connect("fuentes_apa.db")
                            c = conn.cursor()
                            c.execute("DELETE FROM citas WHERE id = ?", (row["id"],))
                            conn.commit()
                            conn.close()
                            st.toast("Fuente eliminada correctamente.", icon="🗑️")
                            st.rerun()
