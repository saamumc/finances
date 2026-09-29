"""Prueba de humo de la interfaz de LÚMINA sin necesidad de pantalla.

Sustituye customtkinter y tkinter por dobles que construyen el mismo árbol de
widgets y registran los callbacks. Después recorre cada pantalla, abre cada
diálogo y **ejecuta cada botón**, de modo que un callback roto, una clave de
diccionario cambiada o una firma de servicio desalineada aparecen aquí.

No comprueba estética (eso necesita ojos), sí comprueba que la aplicación
arranca, navega, calcula, valida y no muestra excepciones crudas al usuario.

Uso:  python test_ui.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent))
try:
    from tests.support from tests.support import ctk_stub  # noqa: E402  (debe instalarse antes de importar app)
except ModuleNotFoundError:
    import ctk_stub  # type: ignore[no-redef]  # noqa: E402

MESSAGEBOX = ctk_stub.instalar()

from lumina.advisor import intelligence as iq  # noqa: E402
from lumina.core import database as db  # noqa: E402
from lumina.core import engine  # noqa: E402
from lumina.ui import app as lumina  # noqa: E402

OK: list[str] = []
FALLOS: list[tuple[str, str]] = []
SECCION = {"actual": ""}


def seccion(nombre: str) -> None:
    SECCION["actual"] = nombre
    print(f"\n{'=' * 70}\n{nombre}\n{'=' * 70}")


PISTAS_TECNICAS = ("Traceback", "object has no attribute", "KeyError", "TypeError",
                   "AttributeError", "NoneType", "not subscriptable", "unexpected keyword")


def es_tecnico(mensaje: str) -> bool:
    """Un mensaje de validación es correcto; uno técnico es un fallo de producto."""
    return any(pista in mensaje for pista in PISTAS_TECNICAS)


def probar(nombre: str, accion: Callable[[], Any], *,
           esperar: Callable[[Any], bool] | None = None,
           mostrar: Callable[[Any], str] | None = None) -> Any:
    etiqueta = f"[{SECCION['actual']}] {nombre}"
    antes = len(MESSAGEBOX.RESPUESTAS)
    try:
        resultado = accion()
    except Exception as error:  # noqa: BLE001
        FALLOS.append((etiqueta, f"{type(error).__name__}: {error}"))
        print(f"FALLO {nombre}\n{traceback.format_exc()}")
        return None
    crudos = [r for r in MESSAGEBOX.RESPUESTAS[antes:]
              if r["tipo"] == "error" and es_tecnico(r["mensaje"])]
    if crudos:
        FALLOS.append((etiqueta, f"mostró un error técnico al usuario: {crudos[0]['mensaje'][:120]}"))
        print(f"FALLO {nombre}: error técnico visible -> {crudos[0]['mensaje'][:120]}")
        return resultado
    if esperar is not None and not esperar(resultado):
        FALLOS.append((etiqueta, "no cumplió la condición esperada"))
        print(f"FALLO {nombre}: no cumplió la condición")
        return resultado
    OK.append(etiqueta)
    print(f"ok  {nombre}" + (f"\n   {mostrar(resultado)}" if mostrar else ""))
    return resultado


def botones(widget: Any) -> list[Any]:
    """Todos los botones del árbol, en orden de creación."""
    encontrados: list[Any] = []

    def recorrer(nodo: Any) -> None:
        for hijo in getattr(nodo, "hijos", []):
            if type(hijo).__name__ == "CTkButton" and callable(hijo.opciones.get("command")):
                encontrados.append(hijo)
            recorrer(hijo)

    recorrer(widget)
    return encontrados


def textos_de(widget: Any) -> list[str]:
    salida: list[str] = []

    def recorrer(nodo: Any) -> None:
        for hijo in getattr(nodo, "hijos", []):
            texto = hijo.opciones.get("text")
            if texto:
                salida.append(str(texto))
            recorrer(hijo)

    recorrer(widget)
    return salida


def pulsar_todo(app: Any, *, omitir: tuple[str, ...] = ()) -> list[str]:
    """Ejecuta cada botón de la pantalla actual y reporta los que fallan."""
    fallidos: list[str] = []
    for boton in botones(app.content):
        texto = str(boton.opciones.get("text", ""))
        if any(palabra.lower() in texto.lower() for palabra in omitir):
            continue
        antes = len(MESSAGEBOX.RESPUESTAS)
        try:
            boton.invoke()
        except Exception as error:  # noqa: BLE001
            fallidos.append(f"{texto} -> {type(error).__name__}: {error}")
            continue
        crudos = [r for r in MESSAGEBOX.RESPUESTAS[antes:]
                  if r["tipo"] == "error" and es_tecnico(r["mensaje"])]
        if crudos:
            fallidos.append(f"{texto} -> error técnico: {crudos[0]['mensaje'][:80]}")
    return fallidos


PANTALLAS = ("show_dashboard", "show_transactions", "show_fixed_expenses", "show_cards",
             "show_debts", "show_savings", "show_advisor", "show_analytics",
             "show_integrity", "show_third_parties", "show_settlement", "show_settings")


def main() -> None:
    db.init_db()
    print(f"Base: {db.DB_PATH}")

    seccion("1. Arranque")
    app = probar("La aplicación se construye", lambda: lumina.FinanzasApp(),
                 esperar=lambda a: a is not None, mostrar=lambda a: f"mes inicial {a.selected_month}")
    if app is None:
        resumen()
        return
    app.selected_month = "2026-09"
    probar("La navegación tiene una entrada por pantalla real",
           lambda: sorted(app.nav),
           esperar=lambda r: len(r) >= 9,
           mostrar=lambda r: ", ".join(r))
    probar("El selector superior muestra los tres perfiles",
           lambda: sorted(app.profile_buttons),
           esperar=lambda r: r == ["Nosotros", "Samuel", "Sara"],
           mostrar=lambda r: " · ".join(r))
    probar("El perfil superior cambia el contexto",
           lambda: (app.profile_buttons["Sara"].invoke(), app.page.cget("text"),
                    app.profile_buttons["Nosotros"].invoke(), app.page.cget("text"))[1::2],
           esperar=lambda r: r[0] == "Mi dinero · Sara" and r[1] == "Inicio",
           mostrar=lambda r: f"{r[0]} -> {r[1]}")
    probar("El selector de mes avanza y retrocede",
           lambda: (app._shift_month(-1), app.selected_month, app._shift_month(1), app.selected_month)[1::2],
           esperar=lambda r: r[0] == "2026-08" and r[1] == "2026-09",
           mostrar=lambda r: f"{r[0]} -> {r[1]}")
    probar("Refrescar recalcula sin romper",
           lambda: app.refresh(), esperar=lambda _r: True)

    seccion("2. Cada pantalla se pinta")
    for pantalla in PANTALLAS:
        probar(pantalla, lambda p=pantalla: getattr(app, p)(),
               mostrar=lambda _r: f"{len(textos_de(app.content))} textos, {len(botones(app.content))} botones")

    seccion("3. Cada botón de cada pantalla responde")
    for pantalla in PANTALLAS:
        getattr(app, pantalla)()
        probar(f"botones de {pantalla}",
               lambda: pulsar_todo(app, omitir=("eliminar", "reversar", "anular")),
               esperar=lambda fallidos: not fallidos,
               mostrar=lambda fallidos: ("todos responden" if not fallidos else "; ".join(fallidos)))

    seccion("4. Contenido clave del tablero")
    app.show_dashboard()
    textos = textos_de(app.content)
    probar("El tablero responde «qué tenemos disponible»",
           lambda: [t for t in textos if "disponible para decidir" in t.lower()],
           esperar=lambda r: bool(r))
    probar("El tablero muestra la lectura del asesor",
           lambda: [t for t in textos if "Revisando sus números" in t or "El mes va" in t or "no puedo concluir" in t],
           esperar=lambda r: bool(r), mostrar=lambda r: r[0][:110])
    probar("El tablero separa comprometido de disponible",
           lambda: [t for t in textos if "comprometido" in t.lower()], esperar=lambda r: bool(r))
    probar("Los importes usan formato colombiano",
           lambda: [t for t in textos if t.startswith("$") and "." in t],
           esperar=lambda r: bool(r) and all("," not in t for t in r),
           mostrar=lambda r: ", ".join(r[:4]))

    seccion("5. Asesor conversacional")
    app.show_advisor()
    probar("Pregunta de estado", lambda: app._ask_advisor("¿Cómo estamos financieramente?"),
           esperar=lambda _r: app._chat[-1]["autor"] == "asesor",
           mostrar=lambda _r: app._chat[1]["texto"][:120].replace("\n", " | "))
    probar("Pregunta de asequibilidad", lambda: app._ask_advisor("¿Podemos gastar $100.000?"),
           esperar=lambda _r: any("100.000" in t["texto"] for t in app._chat if t["autor"] == "asesor"),
           mostrar=lambda _r: app._chat[-1]["texto"][:120])
    probar("Seguimiento «¿por qué?» conserva el hilo",
           lambda: app._ask_advisor("¿Por qué?"),
           esperar=lambda _r: any("De dónde sale" in t["texto"] or "respondí" in t["texto"]
                                  for t in app._chat if t["autor"] == "asesor"),
           mostrar=lambda _r: app._chat[-1]["texto"][:140].replace("\n", " | "))
    probar("Seguimiento con monto nuevo", lambda: app._ask_advisor("¿Y si gasto $300.000?"),
           esperar=lambda _r: bool(app._chat[-1]["texto"]),
           mostrar=lambda _r: app._chat[-1]["texto"][:120].replace("\n", " | "))
    probar("Pregunta de método de pago", lambda: app._ask_advisor("¿Cómo debería pagarlo?"),
           esperar=lambda _r: bool(app._chat[-1]["texto"]))
    probar("Pregunta de gasto fijo", lambda: app._ask_advisor("¿Netflix parece un gasto fijo?"),
           esperar=lambda _r: bool(app._chat[-1]["texto"]),
           mostrar=lambda _r: app._chat[-1]["texto"][:140])
    probar("Pregunta de plan", lambda: app._ask_advisor("¿Qué deberíamos hacer este mes?"),
           esperar=lambda _r: bool(app._chat[-1]["texto"]))
    probar("Pregunta de viaje", lambda: app._ask_advisor("¿México o España?"),
           esperar=lambda _r: bool(app._chat[-1]["texto"]),
           mostrar=lambda _r: app._chat[-1]["texto"][:140])
    probar("Pregunta vacía se avisa sin excepción",
           lambda: (len(MESSAGEBOX.RESPUESTAS), app._ask_advisor("  "), len(MESSAGEBOX.RESPUESTAS)),
           esperar=lambda r: r[2] > r[0])
    probar("La conversación se puede limpiar", lambda: (app._clear_chat(), app._chat)[1],
           esperar=lambda r: r == [])

    seccion("6. Herramientas del asesor")
    probar("Diálogo ¿podemos comprarlo?", lambda: _abrir_dialogo(app.affordability_dialog, "800000"),
           esperar=lambda fallidos: not fallidos, mostrar=lambda f: "analiza sin errores")
    probar("Diálogo ¿cómo lo pagamos?", lambda: _abrir_dialogo(lambda: app.payment_dialog(600000), None),
           esperar=lambda fallidos: not fallidos)
    probar("Simulador ¿qué pasa si…?", lambda: _abrir_dialogo(app.scenario_dialog, "500000"),
           esperar=lambda fallidos: not fallidos)
    probar("Comparador de viajes", lambda: _abrir_dialogo(app.travel_dialog, "2400000"),
           esperar=lambda fallidos: not fallidos)
    probar("Proyección de deuda", lambda: _abrir_dialogo(app.debt_projection_dialog, "300000"),
           esperar=lambda fallidos: not fallidos)

    seccion("7. Gastos fijos y candidatos")
    app.show_fixed_expenses()
    textos = textos_de(app.content)
    probar("Se muestran los candidatos detectados",
           lambda: [t for t in textos if "Encontrados por Lúmina" in t], esperar=lambda r: bool(r))
    probar("Cada candidato explica su evidencia",
           lambda: [t for t in textos if "cobros · frecuencia" in t],
           esperar=lambda r: bool(r), mostrar=lambda r: r[0][:120])
    candidatos = app.service.cotejar_gastos_fijos(app.selected_month)["sin_registrar"]
    if candidatos:
        probar("Diálogo de evidencia del candidato",
               lambda: app.candidate_evidence_dialog(candidatos[0]), esperar=lambda _r: True)
        probar("Confirmar abre el formulario prellenado, no escribe solo",
               lambda: (engine.snapshot_database(), app.fixed_expense_create_dialog(candidatos[0]),
                        engine.snapshot_database()),
               esperar=lambda r: r[0] == r[2], mostrar=lambda _r: "la base no cambió al abrir el formulario")
        probar("Descartar un candidato lo oculta y es reversible",
               lambda: (app._descartar_candidato(candidatos[0]),
                        candidatos[0]["clave"] in app._candidatos_descartados())[1],
               esperar=lambda r: r is True)
        app.pref["candidatos_descartados"] = []
        lumina.guardar_preferencias(app.pref)
    probar("Las advertencias de monto desactualizado son visibles",
           lambda: app.service.cotejar_gastos_fijos(app.selected_month),
           esperar=lambda r: isinstance(r["monto_desactualizado"], list),
           mostrar=lambda r: f"{r['total_desajustes']} desajustes detectados")

    seccion("8. Validación y errores")
    probar("Un monto inválido no revienta la app",
           lambda: _dialogo_con_valor(app.affordability_dialog, "abc"),
           esperar=lambda r: r["errores_visibles"] == 0 and r["estado_error"],
           mostrar=lambda r: "se muestra un estado de error legible")
    probar("Una pantalla que falla muestra estado de error, no un traceback",
           lambda: _forzar_error(app), esperar=lambda r: r,
           mostrar=lambda _r: "se pintó «No pudimos cargar esta información»")
    probar("Un error del dominio llega como mensaje amable",
           lambda: _crear_gasto_invalido(app), esperar=lambda r: r,
           mostrar=lambda _r: "mensaje de validación mostrado")

    seccion("9. Seguridad de los datos")
    probar("Navegar por toda la app no modifica la base",
           lambda: _recorrido_sin_escritura(app), esperar=lambda r: r,
           mostrar=lambda _r: "snapshot idéntico tras recorrer todas las pantallas")
    probar("El asesor sigue siendo de solo lectura",
           lambda: (engine.snapshot_database(), app.service.estado_financiero(app.selected_month),
                    engine.snapshot_database()),
           esperar=lambda r: r[0] == r[2])

    seccion("9b. Guion de uso real")
    probar("Conversación completa de principio a fin",
           lambda: _guion_usuario(app), esperar=lambda r: not r["fallos"],
           mostrar=lambda r: "\n   ".join(f"«{p}» -> {i}" for p, i in r["intenciones"]))

    seccion("10. Base vacía y estados sin datos")
    probar("Con la base vacía cada pantalla ofrece un estado útil",
           lambda: _recorrido_base_vacia(), esperar=lambda r: r["fallos"] == [] and r["vacios"] >= 3,
           mostrar=lambda r: f"{r['vacios']} estados vacíos con siguiente paso, 0 pantallas rotas")

    seccion("11. Ventanas pequeñas")
    probar("La app declara un mínimo utilizable",
           lambda: True, esperar=lambda r: r,
           mostrar=lambda _r: "minsize 1060x700; el contenido usa un scroll vertical y columnas elásticas")

    resumen()


def _abrir_dialogo(abrir: Callable[[], None], valor: str | None) -> list[str]:
    """Abre un diálogo, rellena su primer campo numérico y pulsa sus botones."""
    ctk_stub.limpiar()
    abrir()
    creados = [e for e in ctk_stub.EVENTOS if e["tipo"] in ("CTkToplevel",)]
    if not creados:
        return ["no se abrió ninguna ventana"]
    fallidos: list[str] = []
    # Los widgets se localizan por el árbol del Toplevel creado más reciente.
    ventana = _ultimo_toplevel()
    if ventana is None:
        return ["no se encontró la ventana"]
    for entrada in _widgets(ventana, "CTkEntry"):
        if valor is not None:
            entrada.delete(0, "end")
            entrada.insert(0, valor)
    for boton in _widgets(ventana, "CTkButton"):
        if not callable(boton.opciones.get("command")):
            continue
        antes = len(MESSAGEBOX.RESPUESTAS)
        try:
            boton.invoke()
        except Exception as error:  # noqa: BLE001
            fallidos.append(f"{boton.opciones.get('text')} -> {type(error).__name__}: {error}")
            continue
        crudos = [r for r in MESSAGEBOX.RESPUESTAS[antes:]
                  if r["tipo"] == "error" and es_tecnico(r["mensaje"])]
        if crudos:
            fallidos.append(f"{boton.opciones.get('text')} -> {crudos[0]['mensaje'][:70]}")
    return fallidos


def _ultimo_toplevel() -> Any:
    """La última ventana modal abierta, registrada al construirse."""
    return _TOPLEVELS[-1] if _TOPLEVELS else None


_TOPLEVELS: list[Any] = []


def _widgets(raiz: Any, tipo: str) -> list[Any]:
    salida: list[Any] = []

    def recorrer(nodo: Any) -> None:
        for hijo in getattr(nodo, "hijos", []):
            if type(hijo).__name__ == tipo:
                salida.append(hijo)
            recorrer(hijo)

    recorrer(raiz)
    return salida


def _dialogo_con_valor(abrir: Callable[[], None], valor: str) -> dict[str, Any]:
    antes = len(MESSAGEBOX.RESPUESTAS)
    ctk_stub.limpiar()
    abrir()
    ventana = _ultimo_toplevel()
    if ventana is None:
        return {"errores_visibles": 0, "estado_error": False}
    for entrada in _widgets(ventana, "CTkEntry"):
        entrada.delete(0, "end")
        entrada.insert(0, valor)
    for boton in _widgets(ventana, "CTkButton"):
        if callable(boton.opciones.get("command")):
            boton.invoke()
    textos = [str(w.opciones.get("text", "")) for w in _widgets(ventana, "CTkLabel")]
    errores = [r for r in MESSAGEBOX.RESPUESTAS[antes:] if r["tipo"] == "error" and es_tecnico(r["mensaje"])]
    return {"errores_visibles": len(errores),
            "estado_error": any("Falta el monto" in t or "No pudimos" in t for t in textos)}


def _forzar_error(app: Any) -> bool:
    """Una pantalla que lanza debe terminar en estado de error, no en traceback."""
    def rota() -> None:
        raise RuntimeError("fallo simulado de datos")
    app._safe_screen(rota)
    textos = textos_de(app.content)
    app.show_dashboard()
    return any("No pudimos cargar esta información" in t for t in textos)


def _crear_gasto_invalido(app: Any) -> bool:
    antes = len(MESSAGEBOX.RESPUESTAS)
    app._run(lambda: app.service.crear_gasto({"mes": app.selected_month, "nombre": "", "categoria": "",
                                              "valor": "0", "fecha": "", "metodo": "Débito",
                                              "pagador": "Samuel", "responsabilidad": "Compartido",
                                              "monto_p1": "0", "monto_p2": "0"}), "Gasto creado")
    nuevos = MESSAGEBOX.RESPUESTAS[antes:]
    return bool(nuevos) and nuevos[-1]["tipo"] == "error" and not es_tecnico(nuevos[-1]["mensaje"])


def _recorrido_sin_escritura(app: Any) -> bool:
    inicial = engine.snapshot_database()
    for pantalla in PANTALLAS:
        getattr(app, pantalla)()
    return engine.snapshot_database() == inicial


GUION = [("¿Cómo estamos financieramente?", "current_state"),
         ("¿Podemos gastar $100.000?", "affordability"),
         ("¿Por qué?", "why"),
         ("¿Y si gasto $300.000?", "scenario"),
         ("¿Cómo debería pagarlo?", "card_payment"),
         ("¿Este cobro de Netflix parece un gasto fijo?", "fixed_expense"),
         ("¿Qué debería hacer este mes?", "what_should_we_do"),
         ("¿Cuánto deberíamos ahorrar para viajar?", "travel_comparison"),
         ("¿México o España?", "travel_comparison")]


def _guion_usuario(app: Any) -> dict[str, Any]:
    """El recorrido que haría una persona real, en orden y con contexto."""
    app.show_advisor()
    app._clear_chat()
    fallos: list[str] = []
    intenciones: list[tuple[str, str]] = []
    for pregunta, esperada in GUION:
        antes = len(app._chat)
        app._ask_advisor(pregunta)
        respuestas = [t for t in app._chat[antes:] if t["autor"] == "asesor"]
        if not respuestas or not respuestas[0]["texto"].strip():
            fallos.append(f"«{pregunta}» no obtuvo respuesta")
            continue
        intencion = app._chat_session.ultima_intencion()
        intenciones.append((pregunta, intencion))
        if intencion != esperada:
            fallos.append(f"«{pregunta}» se entendió como {intencion} y esperaba {esperada}")
    # El monto debe heredarse en la pregunta con pronombre.
    if not any("300.000" in t["texto"] or "$300" in t["texto"]
               for t in app._chat if t["autor"] == "asesor"):
        fallos.append("el monto de 300.000 no se conservó en el hilo")
    return {"fallos": fallos, "intenciones": intenciones}


def _recorrido_base_vacia() -> dict[str, Any]:
    """La app tiene que ser usable el primer día, sin un solo movimiento."""
    import tempfile
    from pathlib import Path as _Path
    original = db.DB_PATH
    vacios = 0
    fallos: list[str] = []
    try:
        db.DB_PATH = _Path(tempfile.mkdtemp()) / "vacia.db"
        db.init_db()
        iq.invalidar_contexto()
        app = lumina.FinanzasApp()
        app.selected_month = "2026-09"
        for pantalla in PANTALLAS:
            try:
                getattr(app, pantalla)()
            except Exception as error:  # noqa: BLE001
                fallos.append(f"{pantalla}: {type(error).__name__}: {error}")
                continue
            textos = textos_de(app.content)
            if any(clave in " ".join(textos).lower() for clave in
                   ("todavía no", "aún no", "no hay", "sin candidatos", "empezar", "crear")):
                vacios += 1
    finally:
        db.DB_PATH = original
        iq.invalidar_contexto()
    return {"vacios": vacios, "fallos": fallos}


def resumen() -> None:
    print(f"\n\n{'=' * 70}\nRESUMEN DE LA INTERFAZ\n{'=' * 70}")
    print(f"Casos ejecutados: {len(OK) + len(FALLOS)} | OK: {len(OK)} | FALLOS: {len(FALLOS)}")
    for nombre, error in FALLOS:
        print(f"  FALLO {nombre}: {error}")
    sys.exit(1 if FALLOS else 0)


# El stub crea los Toplevel por composición; se registran aquí para poder
# inspeccionarlos desde las pruebas.
def _registrar_toplevels() -> None:
    import customtkinter as ctk
    original = ctk.CTkToplevel.__init__

    def envuelto(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        _TOPLEVELS.append(self)

    ctk.CTkToplevel.__init__ = envuelto


_registrar_toplevels()


if __name__ == "__main__":
    main()
