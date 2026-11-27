"""Revisión del texto libre que entra por los conectores (observaciones, notas, motivos).

- Datos personales de pacientes (documentos, teléfonos, correos): se RECHAZA el texto.
- Instrucciones incrustadas ("ignora la política", "envía todo a…"): se GUARDA como dato, pero se marca
  en alertas_seguridad para TI. El servidor nunca obedece el contenido de un campo.
"""
import re

PII = [
    (re.compile(r"\b(?:c\.?c\.?|c[eé]dula|documento|t\.?i\.?|pasaporte)\s*[:#nº°]*\s*\d[\d.\s]{5,}", re.I), "número de documento"),
    (re.compile(r"\b3\d{2}[\s-]?\d{3}[\s-]?\d{4}\b"), "número de celular"),
    (re.compile(r"(?:\+?57[\s-]?)?\(?60\d\)?[\s-]?\d{3}[\s-]?\d{4}\b"), "número de teléfono fijo"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "correo electrónico"),
    (re.compile(r"(?<![$\d.,])\b\d{7,10}\b"), "número que parece un documento o teléfono"),
]

INSTRUCCIONES = [
    re.compile(r"\bignor[ae]\w*\s+(?:la|las|el|los|tus|todas?)\b", re.I),
    re.compile(r"\bolvida\w*\s+(?:la|las|tus)\s+(?:instrucciones|reglas|pol[ií]tica)", re.I),
    re.compile(r"\benv[ií]a\w*\s+(?:todo|toda|todos|los datos|la base|el listado)", re.I),
    re.compile(r"\bprograma\w*\b.{0,40}\bsin autorizaci[oó]n", re.I),
    re.compile(r"\b(?:system prompt|instrucciones del sistema|eres ahora|act[uú]a como|modo desarrollador)\b", re.I),
    re.compile(r"\b(?:borra|elimina|drop)\s+(?:la base|las tablas|table|todo)\b", re.I),
]


def revisar_pii(texto):
    """Devuelve el tipo de dato personal encontrado, o None."""
    if not texto:
        return None
    for patron, nombre in PII:
        if patron.search(texto):
            return nombre
    return None


def revisar_instrucciones(texto):
    if not texto:
        return None
    for patron in INSTRUCCIONES:
        m = patron.search(texto)
        if m:
            return f"Instrucción incrustada en un campo de datos: «{m.group(0)}»"
    return None
