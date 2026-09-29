"""Sistema de diseño de LÚMINA: tokens, tipografía, espaciado y componentes.

Todas las pantallas construyen su interfaz con estas piezas para que la
aplicación se vea y se comporte como un solo producto. Ningún componente de
este módulo consulta la base de datos ni calcula nada financiero: recibe
valores ya formateados o números y los pinta.

Convenciones:

* Un solo acento primario. El color semántico (rojo, ámbar, verde) se reserva
  para estado real: riesgo, atención, bien. No se usa para decorar.
* Ningún dato se comunica solo con color: siempre hay texto o etiqueta.
* Los números financieros dominan visualmente; las etiquetas son secundarias.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

import customtkinter as ctk


class T:
    """Tokens de color. Cada valor es (claro, oscuro).

    Paleta editorial: grafito cálido, blanco hueso y un cian mate usado como
    señal, nunca como adorno. Los colores de estado se mantienen apagados para
    que la lectura financiera siga mandando sobre la decoración.
    """

    BG = ("#F1EFEB", "#151416")
    S = ("#F8F6F2", "#19181B")
    ALT = ("#E9E6E1", "#1D1B1F")
    SUNKEN = ("#DFDBD5", "#242226")
    TXT = ("#252321", "#EAE5DC")
    MUTED = ("#6D6964", "#AAA39C")
    FAINT = ("#89837D", "#7D7775")
    BORDER = ("#D9D5CF", "#302D32")
    PRIMARY = ("#287C82", "#57B8BE")
    PRIMARY_SOFT = ("#E0F0F0", "#1A292B")
    PRIMARY_MID = ("#91C7C9", "#3D7478")
    OK = ("#64735A", "#A9B69E")
    OK_SOFT = ("#E7EAE2", "#20251F")
    WARN = ("#9B7951", "#C1A47B")
    WARN_SOFT = ("#F0E8DE", "#29231E")
    BAD = ("#9C655B", "#C58F82")
    BAD_SOFT = ("#F0E3E0", "#2B201F")
    TERRA = ("#9B7951", "#C1A47B")
    SAVE = ("#3D797A", "#84B7B6")
    SAVE_SOFT = ("#E0EDEC", "#1B2929")
    WHITE = ("#FFFFFF", "#F4EFE7")
    ON_PRIMARY = ("#FFFFFF", "#102426")


class Espacio:
    """Escala de espaciado y geometría. Un solo juego de valores para toda la app.

    Geometría deliberadamente contenida: las superficies grandes casi rectas,
    los controles con un radio pequeño. Nada es una pastilla.
    """

    XS = 4
    SM = 8
    MD = 14
    LG = 20
    XL = 28
    XXL = 44
    PANEL_PAD = 20
    GRID_PAD = 8
    RADIO = 6        # superficies estructurales
    RADIO_SM = 4     # controles, chips, campos
    RADIO_BTN = 5


_FAMILIAS: dict[str, str] = {}
_PREFERIDAS = {
    # Serif contemporánea, sobria: si la primera no está instalada, cae a la siguiente.
    "serif": ("Source Serif 4", "Newsreader", "Iowan Old Style", "Charter", "Georgia", "Cambria"),
    "sans": ("Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue", "Arial"),
}


def _familia(clase: str) -> str | None:
    """Primera familia instalada de la lista; None deja la fuente por defecto."""
    if clase in _FAMILIAS:
        return _FAMILIAS[clase] or None
    elegida = ""
    try:
        import tkinter.font as tkfont
        instaladas = {f.lower(): f for f in tkfont.families()}
        for candidata in _PREFERIDAS[clase]:
            if candidata.lower() in instaladas:
                elegida = instaladas[candidata.lower()]
                break
    except Exception:  # sin display o sin tkinter.font: se usa la fuente del sistema
        elegida = ""
    _FAMILIAS[clase] = elegida
    return elegida or None


def fuente(tamano: int = 13, peso: str = "normal") -> Any:
    familia = _familia("sans")
    return ctk.CTkFont(family=familia, size=tamano, weight=peso) if familia else ctk.CTkFont(size=tamano, weight=peso)


def serif(tamano: int = 20, peso: str = "normal") -> Any:
    """Serif para titulares y frases del asesor; nunca para datos ni controles."""
    familia = _familia("serif")
    return ctk.CTkFont(family=familia, size=tamano, weight=peso) if familia else ctk.CTkFont(size=tamano, weight=peso)


class Tipo:
    """Escala tipográfica. Se crean bajo demanda porque CTkFont necesita raíz.

    Jerarquía: 1 estado principal (serif enorme) · 2 interpretación (serif) ·
    3 información secundaria · 4 detalle · 5 metadato.
    """

    @staticmethod
    def pagina() -> Any:
        return serif(34)

    @staticmethod
    def seccion() -> Any:
        return serif(22)

    @staticmethod
    def dialogo() -> Any:
        """Título de una ventana de diálogo (crear/editar/confirmar)."""
        return serif(24)

    @staticmethod
    def tarjeta() -> Any:
        return fuente(15, "bold")

    @staticmethod
    def numero_grande() -> Any:
        return serif(40)

    @staticmethod
    def numero_heroe() -> Any:
        """La cifra que responde a la pregunta principal de la pantalla."""
        return serif(76)

    @staticmethod
    def numero() -> Any:
        return fuente(22, "bold")

    @staticmethod
    def numero_pequeno() -> Any:
        return fuente(16, "bold")

    @staticmethod
    def numero_libro() -> Any:
        """Cifra de un libro de cuentas: legible, sin gritar."""
        return fuente(18, "bold")

    @staticmethod
    def etiqueta() -> Any:
        return fuente(10, "bold")

    @staticmethod
    def cuerpo() -> Any:
        return fuente(13)

    @staticmethod
    def ayuda() -> Any:
        return fuente(12)

    @staticmethod
    def asesor() -> Any:
        return serif(17)

    @staticmethod
    def editorial() -> Any:
        """Frase interpretativa: lo que significa la cifra."""
        return serif(21)

    @staticmethod
    def indice() -> Any:
        """Numeral de una lista editorial (01, 02…)."""
        return serif(28)


# Colores semánticos por severidad del asesor. El texto siempre acompaña.
SEVERIDAD_COLOR: dict[str, tuple[str, str]] = {
    "critica": T.BAD, "alta": T.BAD, "media": T.WARN, "baja": T.MUTED,
    "info": T.MUTED, "positiva": T.OK,
}
SEVERIDAD_FONDO: dict[str, tuple[str, str]] = {
    "critica": T.BAD_SOFT, "alta": T.BAD_SOFT, "media": T.WARN_SOFT, "baja": T.ALT,
    "info": T.ALT, "positiva": T.OK_SOFT,
}
SEVERIDAD_TEXTO: dict[str, str] = {
    "critica": "Crítico", "alta": "Prioritario", "media": "Atención",
    "baja": "Menor", "info": "Informativo", "positiva": "Va bien",
}


# ---------------------------------------------------------------------------
# Contenedores
# ---------------------------------------------------------------------------

def panel(padre: Any, *, fondo: Any = None, borde: bool = True, radio: int = Espacio.RADIO) -> Any:
    """Superficie base de cualquier bloque de contenido."""
    return ctk.CTkFrame(padre, fg_color=fondo or T.S, corner_radius=radio,
                        border_width=1 if borde else 0, border_color=T.BORDER)


def fila(padre: Any, **kwargs: Any) -> Any:
    """Contenedor horizontal transparente."""
    return ctk.CTkFrame(padre, fg_color="transparent", **kwargs)


def separador(padre: Any, pad: int | tuple[int, int] = Espacio.SM) -> Any:
    linea = ctk.CTkFrame(padre, height=1, fg_color=T.BORDER)
    linea.pack(fill="x", padx=Espacio.PANEL_PAD, pady=pad)
    return linea


def titulo_seccion(padre: Any, texto: str, detalle: str = "") -> Any:
    """Encabezado de una sección dentro de una pantalla."""
    caja = fila(padre)
    ctk.CTkLabel(caja, text=texto, font=Tipo.seccion(), text_color=T.TXT).pack(anchor="w")
    if detalle:
        ctk.CTkLabel(caja, text=detalle, font=Tipo.ayuda(), text_color=T.MUTED,
                     justify="left").pack(anchor="w", pady=(2, 0))
    return caja


def etiqueta(padre: Any, texto: str, color: Any = None) -> Any:
    """Rótulo discreto. Se escribe en minúscula normal: las mayúsculas gritan."""
    return ctk.CTkLabel(padre, text=texto, font=fuente(11), text_color=color or T.MUTED)


def cuerpo(padre: Any, texto: str, *, color: Any = None, ancho: int = 680) -> Any:
    return ctk.CTkLabel(padre, text=texto, font=Tipo.cuerpo(), text_color=color or T.TXT,
                        wraplength=ancho, justify="left")


def ayuda(padre: Any, texto: str, *, ancho: int = 680) -> Any:
    return ctk.CTkLabel(padre, text=texto, font=Tipo.ayuda(), text_color=T.MUTED,
                        wraplength=ancho, justify="left")


# ---------------------------------------------------------------------------
# Componentes de dato
# ---------------------------------------------------------------------------

def metrica(padre: Any, titulo: str, valor: str, detalle: str = "", *,
            color: Any = None, ancho_detalle: int = 240) -> Any:
    """Métrica: etiqueta pequeña arriba, número dominante, contexto debajo."""
    caja = fila(padre)
    ctk.CTkFrame(caja, height=1, fg_color=T.BORDER).pack(fill="x")
    etiqueta(caja, titulo).pack(anchor="w", padx=(0, Espacio.PANEL_PAD), pady=(Espacio.MD, Espacio.XS))
    ctk.CTkLabel(caja, text=valor, font=Tipo.numero(), text_color=color or T.TXT,
                 anchor="w").pack(anchor="w")
    if detalle:
        ctk.CTkLabel(caja, text=detalle, font=Tipo.ayuda(), text_color=T.MUTED,
                     wraplength=ancho_detalle, justify="left").pack(
            anchor="w", pady=(Espacio.XS, Espacio.MD))
    else:
        ctk.CTkLabel(caja, text="", height=4).pack()
    return caja


def celda_metrica(tira: Any, indice: int, titulo: str, valor: str, detalle: str = "", *,
                  color: Any = None, ancho_detalle: int = 210) -> Any:
    """Métrica dentro de una tira: comparte superficie y se separa con una línea fina."""
    columna = indice
    tira.grid_columnconfigure(columna, weight=1, uniform="celda")
    celda = fila(tira)
    celda.grid(row=0, column=columna, sticky="nsew", padx=(0 if indice == 0 else Espacio.LG, 0))
    ctk.CTkFrame(celda, height=1, fg_color=T.BORDER).pack(fill="x")
    etiqueta(celda, titulo).pack(anchor="w", pady=(Espacio.MD, Espacio.XS))
    ctk.CTkLabel(celda, text=valor, font=Tipo.numero(), text_color=color or T.TXT,
                 anchor="w").pack(anchor="w")
    if detalle:
        ctk.CTkLabel(celda, text=detalle, font=Tipo.ayuda(), text_color=T.MUTED,
                     wraplength=ancho_detalle, justify="left").pack(
            anchor="w", pady=(Espacio.XS, Espacio.MD))
    else:
        ctk.CTkLabel(celda, text="", height=4).pack()
    return celda


def composicion(padre: Any, partes: Sequence[tuple[str, float, Any]], *,
                formato: Callable[[Any], str], alto: int = 10) -> Any:
    """Cómo se reparte un total: barra segmentada y cada parte escrita debajo."""
    caja = fila(padre)
    validas = [(n, max(float(v or 0), 0.0), c) for n, v, c in partes]
    total = sum(v for _, v, _ in validas)
    trazo = fila(caja)
    trazo.pack(fill="x")
    if total <= 0:
        ctk.CTkFrame(trazo, height=alto, corner_radius=1, fg_color=T.ALT).pack(fill="x")
    else:
        columna = 0
        for _, valor, color in validas:
            if valor <= 0:
                continue
            trazo.grid_columnconfigure(columna, weight=max(int(valor / total * 1000), 1))
            ctk.CTkFrame(trazo, width=1, height=alto, corner_radius=1,
                         fg_color=color).grid(row=0, column=columna, sticky="ew", padx=(0, 2))
            columna += 1
    leyenda = fila(caja)
    leyenda.pack(fill="x", pady=(Espacio.SM, 0))
    for nombre, valor, color in validas:
        parte = fila(leyenda)
        parte.pack(side="left", padx=(0, Espacio.XL))
        ctk.CTkFrame(parte, width=7, height=7, corner_radius=0, fg_color=color).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(parte, text=nombre, font=Tipo.ayuda(), text_color=T.MUTED).pack(side="left")
        ctk.CTkLabel(parte, text=formato(valor), font=fuente(12, "bold"),
                     text_color=T.TXT).pack(side="left", padx=(6, 0))
    return caja


def dato(padre: Any, etiqueta_texto: str, valor: str, *, color: Any = None) -> Any:
    """Par etiqueta/valor en una línea, alineado a los extremos."""
    linea = fila(padre)
    linea.pack(fill="x", padx=Espacio.PANEL_PAD, pady=3)
    ctk.CTkLabel(linea, text=etiqueta_texto, font=Tipo.ayuda(), text_color=T.MUTED,
                 anchor="w").pack(side="left")
    ctk.CTkLabel(linea, text=valor, font=fuente(13, "bold"), text_color=color or T.TXT,
                 anchor="e").pack(side="right")
    return linea


def insignia(padre: Any, texto: str, *, tono: str = "info") -> Any:
    """Insignia de estado. Siempre lleva texto: el color no informa solo."""
    return ctk.CTkLabel(padre, text=f" {texto} ", font=Tipo.etiqueta(),
                        fg_color=SEVERIDAD_FONDO.get(tono, T.ALT),
                        text_color=SEVERIDAD_COLOR.get(tono, T.MUTED),
                        corner_radius=2, height=20)


def barra(padre: Any, proporcion: float, *, color: Any = None, alto: int = 5,
          pad: tuple[int, int] = (Espacio.PANEL_PAD, 0)) -> Any:
    """Barra de progreso acotada a 0-1 para que nunca se desborde."""
    elemento = ctk.CTkProgressBar(padre, height=alto, progress_color=color or T.PRIMARY,
                                  fg_color=T.ALT, corner_radius=1)
    elemento.pack(fill="x", padx=pad[0], pady=(pad[1], 0))
    try:
        elemento.set(max(0.0, min(float(proporcion or 0), 1.0)))
    except (TypeError, ValueError):
        elemento.set(0.0)
    return elemento


def progreso_con_texto(padre: Any, titulo: str, actual: str, objetivo: str,
                       proporcion: float, detalle: str = "", *, color: Any = None) -> Any:
    """Progreso hacia una meta: cifra, barra y porcentaje escrito."""
    caja = fila(padre)
    caja.pack(fill="x", padx=Espacio.PANEL_PAD, pady=(Espacio.SM, Espacio.XS))
    cabecera = fila(caja)
    cabecera.pack(fill="x")
    ctk.CTkLabel(cabecera, text=titulo, font=fuente(13, "bold"), text_color=T.TXT).pack(side="left")
    ctk.CTkLabel(cabecera, text=f"{actual} de {objetivo}", font=Tipo.ayuda(),
                 text_color=T.MUTED).pack(side="right")
    barra_meta = ctk.CTkProgressBar(caja, height=5, progress_color=color or T.SAVE,
                                    fg_color=T.ALT, corner_radius=1)
    barra_meta.pack(fill="x", pady=(6, 3))
    barra_meta.set(max(0.0, min(float(proporcion or 0), 1.0)))
    if detalle:
        ctk.CTkLabel(caja, text=detalle, font=Tipo.ayuda(), text_color=T.MUTED,
                     anchor="w", justify="left").pack(anchor="w")
    return caja


def boton(padre: Any, texto: str, comando: Callable[[], None], *, tono: str = "primario",
          alto: int = 36, ancho: int | None = None) -> Any:
    """Botón con las tres jerarquías que usa la app: primario, suave y sutil."""
    estilos = {
        "primario": {"fg_color": T.PRIMARY, "text_color": T.ON_PRIMARY, "hover_color": T.SAVE,
                     "border_width": 0},
        "enlace": {"fg_color": "transparent", "text_color": T.PRIMARY, "hover_color": T.PRIMARY_SOFT,
                   "border_width": 0},
        "suave": {"fg_color": T.ALT, "text_color": T.TXT, "hover_color": T.SUNKEN, "border_width": 0},
        "sutil": {"fg_color": "transparent", "text_color": T.MUTED, "hover_color": T.ALT,
                  "border_width": 1, "border_color": T.BORDER},
        "peligro": {"fg_color": "transparent", "text_color": T.BAD, "hover_color": T.BAD_SOFT,
                    "border_width": 1, "border_color": T.BORDER},
    }
    opciones = estilos.get(tono, estilos["primario"])
    if ancho:
        opciones = {**opciones, "width": ancho}
    return ctk.CTkButton(padre, text=texto, command=comando, height=alto,
                         corner_radius=Espacio.RADIO_BTN, font=fuente(13, "bold"), **opciones)


# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------

def estado_vacio(padre: Any, titulo: str, detalle: str, *, accion: str = "",
                 comando: Callable[[], None] | None = None, icono: str = "") -> Any:
    """Estado vacío útil: explica qué falta y ofrece el siguiente paso."""
    caja = panel(padre, fondo=T.ALT, borde=False)
    if icono:
        ctk.CTkLabel(caja, text=icono, font=fuente(30)).pack(pady=(Espacio.XL, 2))
    ctk.CTkLabel(caja, text=titulo, font=Tipo.tarjeta(), text_color=T.TXT).pack(
        pady=(Espacio.LG if not icono else 0, Espacio.XS))
    ctk.CTkLabel(caja, text=detalle, font=Tipo.ayuda(), text_color=T.MUTED,
                 wraplength=520, justify="center").pack(padx=Espacio.LG)
    if accion and comando:
        boton(caja, accion, comando).pack(pady=Espacio.LG)
    else:
        ctk.CTkLabel(caja, text="", height=Espacio.LG).pack()
    return caja


def estado_error(padre: Any, titulo: str, detalle: str = "", *, reintentar: Callable[[], None] | None = None) -> Any:
    """Estado de error legible. El traceback se registra, no se muestra."""
    caja = panel(padre, fondo=T.BAD_SOFT, borde=False)
    ctk.CTkLabel(caja, text=titulo, font=Tipo.tarjeta(), text_color=T.BAD).pack(
        anchor="w", padx=Espacio.PANEL_PAD, pady=(Espacio.LG, 2))
    ctk.CTkLabel(caja, text=detalle or "Revisa los datos e inténtalo de nuevo.",
                 font=Tipo.ayuda(), text_color=T.TXT, wraplength=620, justify="left").pack(
        anchor="w", padx=Espacio.PANEL_PAD)
    if reintentar:
        boton(caja, "Reintentar", reintentar, tono="suave").pack(
            anchor="w", padx=Espacio.PANEL_PAD, pady=Espacio.MD)
    else:
        ctk.CTkLabel(caja, text="", height=Espacio.MD).pack()
    return caja


def cargando(padre: Any, texto: str = "Calculando…") -> Any:
    caja = panel(padre, fondo=T.ALT, borde=False)
    ctk.CTkLabel(caja, text=texto, font=Tipo.cuerpo(), text_color=T.MUTED).pack(
        padx=Espacio.PANEL_PAD, pady=Espacio.LG)
    return caja


# ---------------------------------------------------------------------------
# Asesor
# ---------------------------------------------------------------------------

def hallazgo(padre: Any, datos: dict[str, Any], *, formato_dinero: Callable[[Any], str],
            al_abrir: Callable[[], None] | None = None, etiqueta_accion: str = "") -> Any:
    """Tarjeta de hallazgo con la estructura qué → por qué → acción → impacto."""
    severidad = str(datos.get("prioridad") or datos.get("severidad") or "media")
    color = SEVERIDAD_COLOR.get(severidad, T.MUTED)
    caja = panel(padre)
    cabecera = fila(caja)
    cabecera.pack(fill="x", padx=Espacio.PANEL_PAD, pady=(Espacio.MD, Espacio.SM))
    insignia(cabecera, SEVERIDAD_TEXTO.get(severidad, severidad.capitalize()), tono=severidad).pack(side="left")
    if datos.get("monto_involucrado"):
        ctk.CTkLabel(cabecera, text=formato_dinero(datos["monto_involucrado"]),
                     font=fuente(13, "bold"), text_color=color).pack(side="right")
    ctk.CTkLabel(caja, text=datos.get("titulo", ""), font=Tipo.tarjeta(), text_color=T.TXT,
                 wraplength=660, justify="left").pack(anchor="w", padx=Espacio.PANEL_PAD)
    for rotulo, clave in (("Qué pasa", "que"), ("Por qué", "por_que"), ("Qué haría", "accion")):
        if not datos.get(clave):
            continue
        bloque = fila(caja)
        bloque.pack(fill="x", padx=Espacio.PANEL_PAD, pady=(Espacio.SM, 0))
        etiqueta(bloque, rotulo, color if clave == "accion" else T.MUTED).pack(anchor="w")
        ctk.CTkLabel(bloque, text=datos[clave], font=Tipo.cuerpo(), text_color=T.TXT,
                     wraplength=650, justify="left").pack(anchor="w", pady=(2, 0))
    pie = fila(caja)
    pie.pack(fill="x", padx=Espacio.PANEL_PAD, pady=(Espacio.MD, Espacio.MD))
    detalle = []
    if datos.get("impacto"):
        detalle.append(datos["impacto"])
    if datos.get("confianza"):
        detalle.append(f"Confianza: {datos['confianza']}")
    if detalle:
        ctk.CTkLabel(pie, text="  ·  ".join(detalle), font=Tipo.ayuda(), text_color=T.MUTED,
                     wraplength=520, justify="left").pack(side="left")
    if al_abrir:
        boton(pie, etiqueta_accion or "Abrir", al_abrir, tono="suave", alto=30).pack(side="right")
    return caja


def hallazgo_fila(padre: Any, datos: dict[str, Any], *, formato_dinero: Callable[[Any], str],
                  al_abrir: Callable[[], None] | None = None, etiqueta_accion: str = "",
                  primera: bool = False) -> Any:
    """Hallazgo de segundo nivel: una fila con severidad, título y acción."""
    severidad = str(datos.get("prioridad") or datos.get("severidad") or "media")
    color = SEVERIDAD_COLOR.get(severidad, T.MUTED)
    envoltura = fila(padre)
    envoltura.pack(fill="x")
    if not primera:
        separador(envoltura, pad=0)
    linea = fila(envoltura)
    linea.pack(fill="x", padx=Espacio.PANEL_PAD, pady=Espacio.MD)
    insignia(linea, SEVERIDAD_TEXTO.get(severidad, severidad.capitalize()), tono=severidad).pack(
        side="left", anchor="n", pady=2)
    if al_abrir:
        boton(linea, etiqueta_accion or "Abrir", al_abrir, tono="sutil", alto=28).pack(side="right", anchor="n")
    if datos.get("monto_involucrado"):
        ctk.CTkLabel(linea, text=formato_dinero(datos["monto_involucrado"]), font=fuente(13, "bold"),
                     text_color=color).pack(side="right", padx=Espacio.MD, anchor="n")
    texto = fila(linea)
    texto.pack(side="left", padx=Espacio.MD, fill="x", expand=True)
    ctk.CTkLabel(texto, text=datos.get("titulo", ""), font=fuente(14, "bold"), text_color=T.TXT,
                 wraplength=480, justify="left").pack(anchor="w")
    if datos.get("que"):
        ctk.CTkLabel(texto, text=datos["que"], font=Tipo.ayuda(), text_color=T.MUTED,
                     wraplength=480, justify="left").pack(anchor="w", pady=(2, 0))
    return envoltura


def evidencia(padre: Any, filas: Sequence[tuple[str, str]], *, titulo: str = "De dónde sale") -> Any:
    """Tabla de evidencia: el asesor muestra los números que usó."""
    if not filas:
        return None
    caja = panel(padre, fondo=T.ALT, borde=False, radio=Espacio.RADIO_SM)
    etiqueta(caja, titulo).pack(anchor="w", padx=Espacio.MD, pady=(Espacio.SM, Espacio.XS))
    for nombre, valor in filas:
        linea = fila(caja)
        linea.pack(fill="x", padx=Espacio.MD, pady=2)
        ctk.CTkLabel(linea, text=nombre, font=Tipo.ayuda(), text_color=T.MUTED,
                     wraplength=380, justify="left", anchor="w").pack(side="left")
        ctk.CTkLabel(linea, text=valor, font=fuente(12, "bold"), text_color=T.TXT).pack(side="right")
    ctk.CTkLabel(caja, text="", height=Espacio.XS).pack()
    return caja


def burbuja(padre: Any, texto: str, *, autor: str = "asesor") -> Any:
    """Turno de conversación. El asesor escribe sobre la página; el usuario, en un chip."""
    envoltura = fila(padre)
    envoltura.pack(fill="x", padx=Espacio.MD, pady=Espacio.SM)
    if autor == "usuario":
        caja = ctk.CTkFrame(envoltura, corner_radius=Espacio.RADIO_SM, fg_color=T.PRIMARY_SOFT)
        caja.pack(anchor="e", padx=(80, 0))
        ctk.CTkLabel(caja, text=texto, font=Tipo.cuerpo(), text_color=T.TXT, wraplength=520,
                     justify="left").pack(padx=Espacio.MD, pady=Espacio.SM)
        return caja
    caja = fila(envoltura)
    caja.pack(anchor="w", padx=(0, 60))
    ctk.CTkFrame(caja, width=2, corner_radius=0, fg_color=T.PRIMARY_MID).pack(
        side="left", fill="y", padx=(0, Espacio.MD))
    columna = fila(caja)
    columna.pack(side="left")
    ctk.CTkLabel(columna, text="Lúmina", font=serif(13, "bold"), text_color=T.PRIMARY).pack(anchor="w")
    ctk.CTkLabel(columna, text=texto, font=Tipo.asesor(), text_color=T.TXT, wraplength=580,
                 justify="left").pack(anchor="w", pady=(2, 0))
    return caja


def sugerencias(padre: Any, preguntas: Iterable[str], al_elegir: Callable[[str], None]) -> Any:
    """Atajos de preguntas frecuentes para no dejar el campo en blanco."""
    caja = fila(padre)
    caja.pack(fill="x", padx=Espacio.MD, pady=(0, Espacio.SM))
    for pregunta in preguntas:
        ctk.CTkButton(caja, text=pregunta, height=28, corner_radius=Espacio.RADIO_SM, font=Tipo.ayuda(),
                      fg_color="transparent", border_width=1, border_color=T.BORDER,
                      text_color=T.MUTED, hover_color=T.ALT,
                      command=lambda texto=pregunta: al_elegir(texto)).pack(side="left", padx=(0, 6))
    return caja


# ---------------------------------------------------------------------------
# Composición editorial
# ---------------------------------------------------------------------------

def regla(padre: Any, pad: int | tuple[int, int] = 0) -> Any:
    """Línea fina de página, sin sangría."""
    linea = ctk.CTkFrame(padre, height=1, fg_color=T.BORDER)
    linea.pack(fill="x", pady=pad)
    return linea


def libro(padre: Any, filas: Sequence[tuple[str, str, Any]], resultado: tuple[str, str, Any] | None = None,
          *, ancho_rotulo: int = 300) -> Any:
    """Libro de cuentas: cada línea explica un paso y el resultado cierra con más peso.

    ``filas`` son (rótulo, cifra, color). Sin cajas: solo líneas finas y alineación.
    """
    caja = fila(padre)
    for rotulo, cifra, color in filas:
        linea = fila(caja)
        linea.pack(fill="x", pady=(0, 0))
        ctk.CTkLabel(linea, text=rotulo, font=Tipo.ayuda(), text_color=T.MUTED, anchor="w",
                     wraplength=ancho_rotulo, justify="left").pack(side="left", pady=6)
        ctk.CTkLabel(linea, text=cifra, font=fuente(13, "bold"), text_color=color or T.TXT,
                     anchor="e").pack(side="right", pady=6)
        regla(caja)
    if resultado:
        rotulo, cifra, color = resultado
        linea = fila(caja)
        linea.pack(fill="x")
        ctk.CTkLabel(linea, text=rotulo, font=fuente(13, "bold"), text_color=T.TXT, anchor="w").pack(
            side="left", pady=(8, 0))
        ctk.CTkLabel(linea, text=cifra, font=Tipo.numero_libro(), text_color=color or T.TXT,
                     anchor="e").pack(side="right", pady=(8, 0))
    return caja


def lectura(padre: Any, rotulo: str, frase: str, *, ancho: int = 560, color: Any = None,
            detalle: str = "") -> Any:
    """Frase del asesor sobre la página, con una regla vertical: observa o interpreta."""
    caja = fila(padre)
    ctk.CTkFrame(caja, width=2, corner_radius=0, fg_color=color or T.PRIMARY_MID).pack(
        side="left", fill="y", padx=(0, Espacio.MD))
    columna = fila(caja)
    columna.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(columna, text=rotulo, font=serif(13, "bold"), text_color=color or T.PRIMARY).pack(anchor="w")
    ctk.CTkLabel(columna, text=frase, font=Tipo.asesor(), text_color=T.TXT, wraplength=ancho,
                 justify="left", anchor="w").pack(anchor="w", pady=(3, 0))
    if detalle:
        ctk.CTkLabel(columna, text=detalle, font=Tipo.ayuda(), text_color=T.MUTED, wraplength=ancho,
                     justify="left", anchor="w").pack(anchor="w", pady=(Espacio.SM, 0))
    return caja


def punto_atencion(padre: Any, numero: int, titulo: str, texto: str, *, dato: str = "",
                   accion: str = "", comando: Callable[[], None] | None = None,
                   severidad: str = "media", primero: bool = False, ancho: int = 520) -> Any:
    """Un punto de «Qué necesita atención»: numeral, título, explicación, cifra y acción.

    No es una card: es una fila de informe separada por una línea fina.
    """
    color = SEVERIDAD_COLOR.get(severidad, T.MUTED)
    envoltura = fila(padre)
    envoltura.pack(fill="x")
    regla(envoltura)
    cuerpo_fila = fila(envoltura)
    cuerpo_fila.pack(fill="x", pady=(Espacio.LG, Espacio.LG))
    ctk.CTkLabel(cuerpo_fila, text=f"{numero:02d}", font=Tipo.indice(), text_color=T.FAINT,
                 width=64, anchor="nw").pack(side="left", anchor="n")
    derecha = fila(cuerpo_fila)
    derecha.pack(side="right", anchor="n", padx=(Espacio.LG, 0))
    if dato:
        ctk.CTkLabel(derecha, text=dato, font=Tipo.numero_libro(), text_color=color, anchor="e").pack(anchor="e")
    if accion and comando:
        boton(derecha, accion, comando, tono="enlace", alto=28).pack(anchor="e", pady=(Espacio.XS, 0))
    centro = fila(cuerpo_fila)
    centro.pack(side="left", fill="x", expand=True, anchor="n")
    fila_titulo = fila(centro)
    fila_titulo.pack(anchor="w")
    ctk.CTkLabel(fila_titulo, text=titulo, font=fuente(15, "bold"), text_color=T.TXT, justify="left",
                 wraplength=ancho, anchor="w").pack(side="left")
    if severidad in ("critica", "alta"):
        insignia(fila_titulo, SEVERIDAD_TEXTO.get(severidad, severidad), tono=severidad).pack(
            side="left", padx=(Espacio.SM, 0))
    if texto:
        ctk.CTkLabel(centro, text=texto, font=Tipo.cuerpo(), text_color=T.MUTED, wraplength=ancho,
                     justify="left", anchor="w").pack(anchor="w", pady=(3, 0))
    return envoltura


# ---------------------------------------------------------------------------
# Gráficas mínimas (solo cuando responden una pregunta)
# ---------------------------------------------------------------------------

def barras(padre: Any, series: Sequence[tuple[str, float]], *, formato: Callable[[Any], str],
           color: Any = None, alto: int = 130, comparar: bool = True) -> Any:
    """Serie temporal como barras. El último periodo destaca y se compara con el anterior."""
    caja = fila(padre)
    caja.pack(fill="x", padx=Espacio.PANEL_PAD, pady=(Espacio.SM, Espacio.MD))
    if not series:
        ayuda(caja, "Sin datos suficientes para graficar.").pack(anchor="w")
        return caja
    maximo = max((abs(valor) for _, valor in series), default=0) or 1
    grafica = fila(caja)
    grafica.pack(fill="x")
    ultimo = len(series) - 1
    for indice, (nombre, valor) in enumerate(series):
        columna = fila(grafica)
        columna.pack(side="left", fill="both", expand=True, padx=3)
        proporcion = max(abs(valor) / maximo, 0.02)
        tono = color or (T.PRIMARY if indice == ultimo else T.PRIMARY_MID)
        relleno = ctk.CTkFrame(columna, height=int(alto * proporcion), fg_color=tono, corner_radius=2)
        relleno.pack(side="bottom", fill="x")
        relleno.pack_propagate(False)
        ctk.CTkLabel(columna, text=formato(valor), font=fuente(10, "bold" if indice == ultimo else "normal"),
                     text_color=T.TXT if indice == ultimo else T.MUTED).pack(side="bottom", pady=(0, 3))
        ctk.CTkLabel(columna, text=nombre, font=fuente(10), text_color=T.MUTED).pack(side="bottom")
    if comparar and len(series) >= 2 and series[-2][1]:
        cambio = (series[-1][1] - series[-2][1]) / abs(series[-2][1])
        frase = "sin cambio" if round(cambio * 100) == 0 else f"{cambio:+.0%}"
        ayuda(caja, f"{series[-1][0]} frente a {series[-2][0]}: {frase}").pack(anchor="w", pady=(Espacio.SM, 0))
    return caja


def distribucion(padre: Any, partes: Sequence[tuple[str, float, Any]], *,
                 formato: Callable[[Any], str]) -> Any:
    """Composición de un total como lista ordenada con barra proporcional."""
    caja = fila(padre)
    caja.pack(fill="x", padx=Espacio.PANEL_PAD, pady=(Espacio.SM, Espacio.MD))
    total = sum(max(valor, 0) for _, valor, _ in partes) or 1
    for nombre, valor, color in partes:
        linea = fila(caja)
        linea.pack(fill="x", pady=4)
        ctk.CTkLabel(linea, text=nombre, font=Tipo.ayuda(), text_color=T.TXT,
                     width=150, anchor="w").pack(side="left")
        ctk.CTkLabel(linea, text=formato(valor), font=fuente(12, "bold"),
                     text_color=T.TXT).pack(side="right")
        medidor = ctk.CTkProgressBar(linea, height=5, progress_color=color or T.PRIMARY,
                                     fg_color=T.ALT, corner_radius=1)
        medidor.pack(side="left", fill="x", expand=True, padx=Espacio.MD)
        medidor.set(max(0.0, min(max(valor, 0) / total, 1.0)))
    return caja


def utilizacion_color(utilizacion: float) -> Any:
    """Color semántico de utilización de cupo, alineado con el asesor."""
    if utilizacion >= 0.90:
        return T.BAD
    if utilizacion >= 0.70:
        return T.WARN
    if utilizacion >= 0.50:
        return T.SAVE
    return T.OK


def utilizacion_texto(utilizacion: float) -> str:
    if utilizacion >= 0.90:
        return "Crítica"
    if utilizacion >= 0.70:
        return "Alta"
    if utilizacion >= 0.50:
        return "Moderada"
    return "Saludable"
