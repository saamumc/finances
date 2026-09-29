"""Doble de prueba de customtkinter/tkinter para verificar la UI sin pantalla.

No dibuja nada: construye el mismo árbol de widgets que construiría la app real
y registra los textos, colores y callbacks. Sirve para ejecutar cada pantalla y
cada acción en un entorno sin display y detectar errores de wiring reales
(AttributeError, KeyError, callbacks rotos, claves de diccionario cambiadas).

Se instala con ``instalar()`` antes de importar app.py.
"""
from __future__ import annotations

import sys
import types
from typing import Any

EVENTOS: list[dict[str, Any]] = []


class _Font:
    def __init__(self, **kwargs: Any) -> None:
        self.opciones = kwargs

    def cget(self, clave: str) -> Any:
        return self.opciones.get(clave)


def _validar_geometria(opciones: dict[str, Any]) -> None:
    """Tk real rechaza márgenes negativos («bad pad value»); el doble debe hacerlo igual."""
    for clave in ("padx", "pady", "ipadx", "ipady"):
        valor = opciones.get(clave)
        for numero in (valor if isinstance(valor, (tuple, list)) else (valor,)):
            if isinstance(numero, (int, float)) and numero < 0:
                raise ValueError(f"bad pad value {numero!r} en {clave}: Tk no admite márgenes negativos")


class _Widget:
    """Widget genérico: acepta cualquier opción y registra su texto."""

    def __init__(self, master: Any = None, **kwargs: Any) -> None:
        self.master = master
        self.opciones: dict[str, Any] = dict(kwargs)
        self.hijos: list[_Widget] = []
        self._valor: str = str(kwargs.get("text", "") or "")
        self._progreso: float = 0.0
        self._destruido = False
        if isinstance(master, _Widget):
            master.hijos.append(self)
        EVENTOS.append({"tipo": type(self).__name__, "texto": self.opciones.get("text", ""),
                        "opciones": self.opciones})

    # --- geometría ---
    def pack(self, **kwargs: Any) -> "_Widget":
        _validar_geometria(kwargs)
        return self

    def grid(self, **kwargs: Any) -> "_Widget":
        _validar_geometria(kwargs)
        return self

    def place(self, **kwargs: Any) -> "_Widget":
        return self

    def grid_configure(self, **kwargs: Any) -> None:
        return None

    def pack_forget(self) -> None:
        return None

    def grid_forget(self) -> None:
        return None

    def grid_columnconfigure(self, *args: Any, **kwargs: Any) -> None:
        return None

    def grid_rowconfigure(self, *args: Any, **kwargs: Any) -> None:
        return None

    def grid_propagate(self, *args: Any) -> None:
        return None

    def pack_propagate(self, *args: Any) -> None:
        return None

    # --- estado ---
    def configure(self, **kwargs: Any) -> None:
        self.opciones.update(kwargs)
        if "text" in kwargs:
            self._valor = str(kwargs["text"] or "")

    config = configure

    def cget(self, clave: str) -> Any:
        return self.opciones.get(clave)

    def winfo_children(self) -> list["_Widget"]:
        return list(self.hijos)

    def winfo_exists(self) -> bool:
        return not self._destruido

    def winfo_width(self) -> int:
        return int(self.opciones.get("width", 600) or 600)

    def winfo_height(self) -> int:
        return int(self.opciones.get("height", 400) or 400)

    def destroy(self) -> None:
        self._destruido = True
        if isinstance(self.master, _Widget) and self in self.master.hijos:
            self.master.hijos.remove(self)
        for hijo in list(self.hijos):
            hijo.destroy()
        self.hijos.clear()

    def update(self) -> None:
        return None

    def update_idletasks(self) -> None:
        return None

    def after(self, _ms: int, funcion: Any = None, *args: Any) -> str:
        # Las tareas diferidas se ejecutan de inmediato para poder verificarlas.
        if callable(funcion):
            funcion(*args)
        return "after#0"

    def after_cancel(self, _identificador: str) -> None:
        return None

    def bind(self, *args: Any, **kwargs: Any) -> None:
        return None

    def focus(self) -> None:
        return None

    focus_set = focus

    def grab_set(self) -> None:
        return None

    def title(self, *args: Any) -> None:
        return None

    def geometry(self, *args: Any) -> None:
        return None

    def minsize(self, *args: Any) -> None:
        return None

    def maxsize(self, *args: Any) -> None:
        return None

    def resizable(self, *args: Any) -> None:
        return None

    def transient(self, *args: Any) -> None:
        return None

    def attributes(self, *args: Any) -> None:
        return None

    def lift(self, *args: Any) -> None:
        return None

    def protocol(self, *args: Any) -> None:
        return None

    def iconbitmap(self, *args: Any) -> None:
        return None

    def mainloop(self) -> None:
        return None

    # --- valores ---
    def get(self) -> Any:
        return self._valor

    def set(self, valor: Any) -> None:
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            self._progreso = float(valor)
        self._valor = str(valor)

    def insert(self, indice: Any, texto: str = "") -> None:
        self._valor = f"{self._valor}{texto}" if self._valor else str(texto)

    def delete(self, *args: Any) -> None:
        self._valor = ""

    def select(self) -> None:
        self._valor = "1"

    def deselect(self) -> None:
        self._valor = "0"

    def invoke(self) -> Any:
        comando = self.opciones.get("command")
        return comando() if callable(comando) else None

    # --- canvas ---
    def create_rectangle(self, *args: Any, **kwargs: Any) -> int:
        return 1

    def create_line(self, *args: Any, **kwargs: Any) -> int:
        return 1

    def create_text(self, *args: Any, **kwargs: Any) -> int:
        return 1

    def create_oval(self, *args: Any, **kwargs: Any) -> int:
        return 1

    def delete_all(self) -> None:
        return None


class _Ventana(_Widget):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(None, **kwargs)


def _stub_ctk() -> types.ModuleType:
    modulo = types.ModuleType("customtkinter")
    nombres = ("CTkFrame", "CTkLabel", "CTkButton", "CTkEntry", "CTkOptionMenu", "CTkComboBox",
               "CTkProgressBar", "CTkScrollableFrame", "CTkTextbox", "CTkCheckBox", "CTkSwitch",
               "CTkSegmentedButton", "CTkTabview", "CTkSlider", "CTkCanvas", "CTkScrollbar",
               "CTkRadioButton")
    for nombre in nombres:
        setattr(modulo, nombre, type(nombre, (_Widget,), {}))
    modulo.CTk = type("CTk", (_Ventana,), {})
    modulo.CTkToplevel = type("CTkToplevel", (_Ventana,), {})
    modulo.CTkFont = _Font
    modulo.set_appearance_mode = lambda *_a, **_k: None
    modulo.set_default_color_theme = lambda *_a, **_k: None
    modulo.set_widget_scaling = lambda *_a, **_k: None
    modulo.deactivate_automatic_dpi_awareness = lambda *_a, **_k: None
    modulo.__version__ = "stub"
    return modulo


def _stub_tkinter() -> tuple[types.ModuleType, types.ModuleType]:
    tk = types.ModuleType("tkinter")
    tk.Canvas = type("Canvas", (_Widget,), {})
    tk.Frame = type("Frame", (_Widget,), {})
    tk.Label = type("Label", (_Widget,), {})
    tk.StringVar = type("StringVar", (_Widget,), {})
    tk.TclError = type("TclError", (Exception,), {})
    tk.END = "end"
    tk.LEFT = "left"
    tk.RIGHT = "right"
    tk.BOTH = "both"

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.RESPUESTAS: list[dict[str, Any]] = []
    messagebox.CONFIRMAR = True

    def showerror(titulo: str = "", mensaje: str = "", **_kwargs: Any) -> None:
        messagebox.RESPUESTAS.append({"tipo": "error", "titulo": titulo, "mensaje": mensaje})

    def showinfo(titulo: str = "", mensaje: str = "", **_kwargs: Any) -> None:
        messagebox.RESPUESTAS.append({"tipo": "info", "titulo": titulo, "mensaje": mensaje})

    def showwarning(titulo: str = "", mensaje: str = "", **_kwargs: Any) -> None:
        messagebox.RESPUESTAS.append({"tipo": "warning", "titulo": titulo, "mensaje": mensaje})

    def askyesno(titulo: str = "", mensaje: str = "", **_kwargs: Any) -> bool:
        messagebox.RESPUESTAS.append({"tipo": "pregunta", "titulo": titulo, "mensaje": mensaje})
        return messagebox.CONFIRMAR

    messagebox.showerror = showerror
    messagebox.showinfo = showinfo
    messagebox.showwarning = showwarning
    messagebox.askyesno = askyesno
    tk.messagebox = messagebox
    return tk, messagebox


def instalar() -> types.ModuleType:
    """Registra los dobles en sys.modules y devuelve el módulo messagebox."""
    EVENTOS.clear()
    sys.modules["customtkinter"] = _stub_ctk()
    tk, messagebox = _stub_tkinter()
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.messagebox"] = messagebox
    return messagebox


def textos() -> list[str]:
    """Todos los textos que la UI pintó, para poder afirmar sobre ellos."""
    return [str(evento["texto"]) for evento in EVENTOS if evento.get("texto")]


def limpiar() -> None:
    EVENTOS.clear()
