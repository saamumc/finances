"""Frontend premium, local y accesible para las finanzas de Samuel y Sara."""
from __future__ import annotations
import datetime as dt
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
try:
    import customtkinter as ctk
except ModuleNotFoundError as exc:
    raise RuntimeError("Instala las dependencias con: pip install -r requirements.txt") from exc
import logging
import traceback
from tkinter import messagebox
from ..constants import SAMUEL, SARA
try:  # El proyecto admite service.py en la raíz o dentro del paquete frontend/.
    from .service import FinanceService, NOMBRES, cargar_preferencias, dinero, guardar_preferencias, mes_actual, parsear_dinero
except ModuleNotFoundError:
    from .service import FinanceService, NOMBRES, cargar_preferencias, dinero, guardar_preferencias, mes_actual, parsear_dinero
from . import kit as ui
from .kit import Espacio, Tipo

LOG = logging.getLogger("lumina.ui")

# Los tokens viven en ui_kit para que toda la aplicación use la misma paleta.
T = ui.T


class FinanzasApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(); self.pref=cargar_preferencias(); self.theme=self.pref.get("tema", "dark")
        ctk.set_appearance_mode(self.theme); ctk.set_default_color_theme("blue")
        self.service=FinanceService(); self.selected_month=mes_actual(); self.title("Lúmina · Finanzas de Samuel & Sara")
        self.geometry("1360x860"); self.minsize(1060, 700); self.configure(fg_color=T.BG)
        self._current_screen=None
        self._tiras={}           # fila -> [superficie, celdas]: métricas agrupadas en una sola tira
        self._aviso=None         # toast visible (solo uno a la vez)
        self._chat=[]            # turnos visibles de la conversación con el asesor
        self._chat_session=None  # memoria conversacional del asesor (en RAM)
        self._shell(); self._safe_screen(self.show_dashboard)

    def _shell(self) -> None:
        """Estructura fija: atajos compactos, perfil arriba y lienzo de contenido."""
        self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)
        side=ctk.CTkFrame(self,width=184,corner_radius=0,fg_color=T.S,border_width=0); side.grid(row=0,column=0,sticky="nsew"); side.grid_propagate(False)
        ctk.CTkLabel(side,text="LÚMINA",font=ui.serif(23),text_color=T.TXT).pack(anchor="w",padx=22,pady=(26,0))
        ctk.CTkLabel(side,text="FINANZAS PERSONALES",font=ui.fuente(9,"bold"),text_color=T.FAINT).pack(anchor="w",padx=23,pady=(2,26))
        self.nav={}; self.nav_marca={}
        # Navegación corta, pero explícita: un icono nunca queda sin su nombre.
        atajos=(
            ("Inicio","⌂",self.show_dashboard),
            ("Movimientos","↕",self.show_transactions),
            ("Gastos fijos","▤",self.show_fixed_expenses),
            ("Cajitas","◇",self.show_savings),
            ("Tarjetas","▣",self.show_cards),
            ("Plan financiero","◈",self.show_financial_os),
            ("Inversiones","◎",self.show_investments),
            ("Deudas","⊘",self.show_debts),
            ("Asesor","✦",self.show_advisor),
            ("Análisis","▦",self.show_analytics),
            ("Liquidación","⇄",self.show_settlement),
        )
        lista=ui.fila(side); lista.pack(fill="both",expand=True,padx=12)
        for name,icon,fn in atajos:
            envoltura=ui.fila(lista); envoltura.pack(fill="x",pady=2)
            marca=ctk.CTkFrame(envoltura,width=2,height=30,corner_radius=0,fg_color="transparent"); marca.pack(side="left",padx=(0,7))
            b=ctk.CTkButton(envoltura,text=f"{icon}  {name}",anchor="w",height=36,corner_radius=Espacio.RADIO_SM,
                            fg_color="transparent",hover_color=T.ALT,text_color=T.MUTED,
                            font=ui.fuente(12),command=self._nav(name,fn))
            b.pack(side="left",fill="x",expand=True); self.nav[name]=b; self.nav_marca[name]=marca
        self.settings_b=ctk.CTkButton(side,text="⚙  Ajustes",anchor="w",height=36,corner_radius=Espacio.RADIO_SM,
                                      fg_color="transparent",hover_color=T.ALT,text_color=T.MUTED,font=ui.fuente(12),
                                      command=self._nav("Ajustes",self.show_settings))
        self.settings_b.pack(side="bottom",fill="x",padx=21,pady=20)
        root=ctk.CTkFrame(self,fg_color=T.BG,corner_radius=0); root.grid(row=0,column=1,sticky="nsew"); root.grid_rowconfigure(1,weight=1); root.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(root,height=72,fg_color=T.BG,corner_radius=0); top.grid(row=0,column=0,sticky="ew"); top.grid_propagate(False)
        self.page=ctk.CTkLabel(top,text="",font=ui.fuente(11,"bold"),text_color=T.FAINT,width=96,anchor="w"); self.page.pack(side="left",padx=(28,12))
        perfiles=ctk.CTkFrame(top,fg_color="transparent",corner_radius=Espacio.RADIO_SM,border_width=1,border_color=T.BORDER)
        perfiles.pack(side="left",pady=12)
        self.profile_buttons={}
        for nombre,accion in (
            ("Sara",lambda:self.show_personal_dashboard(SARA)),
            ("Samuel",lambda:self.show_personal_dashboard(SAMUEL)),
            ("Nosotros",self.show_dashboard),
        ):
            boton=ctk.CTkButton(perfiles,text=nombre,width=88 if nombre!="Nosotros" else 104,height=36,
                                corner_radius=Espacio.RADIO_SM,fg_color="transparent",hover_color=T.ALT,
                                text_color=T.MUTED,font=ui.fuente(12,"bold"),command=self._nav(nombre,accion))
            boton.pack(side="left",padx=2,pady=2)
            self.profile_buttons[nombre]=boton
        selector=ctk.CTkFrame(top,fg_color="transparent",corner_radius=Espacio.RADIO_SM,border_width=1,border_color=T.BORDER); selector.pack(side="left",pady=18)
        ctk.CTkButton(selector,text="‹",width=30,height=30,corner_radius=3,fg_color="transparent",text_color=T.TXT,hover_color=T.ALT,command=lambda:self._shift_month(-1)).pack(side="left",padx=(4,0),pady=4)
        self.month_label=ctk.CTkLabel(selector,text=self._month_label(),font=ui.fuente(13,"bold"),text_color=T.TXT,width=150); self.month_label.pack(side="left")
        ctk.CTkButton(selector,text="›",width=30,height=30,corner_radius=3,fg_color="transparent",text_color=T.TXT,hover_color=T.ALT,command=lambda:self._shift_month(1)).pack(side="left",padx=(0,4),pady=4)
        self.add_button=ui.boton(top,"＋ Agregar gasto",self.show_transactions,alto=38); self.add_button.pack(side="right",padx=(8,28),pady=17)
        self.theme_b=ctk.CTkButton(top,text="☀" if self.theme=="dark" else "☾",width=36,height=36,corner_radius=Espacio.RADIO_SM,fg_color=T.ALT,text_color=T.TXT,hover_color=T.SUNKEN,command=self.toggle_theme); self.theme_b.pack(side="right",pady=17)
        self.refresh_b=ctk.CTkButton(top,text="⟳",width=36,height=36,corner_radius=Espacio.RADIO_SM,fg_color=T.ALT,text_color=T.TXT,hover_color=T.SUNKEN,command=self.refresh); self.refresh_b.pack(side="right",padx=6,pady=17)
        self.content=ctk.CTkScrollableFrame(root,fg_color=T.BG,corner_radius=0); self.content.grid(row=1,column=0,sticky="nsew"); self.content.grid_columnconfigure((0,1,2,3),weight=1)

    def _nav(self,nombre:str,funcion:Callable[[],None])->Callable[[],None]:
        """Envuelve la navegación para que un error de una pantalla no tumbe la app."""
        def ir()->None:
            self._safe_screen(funcion)
        return ir

    def _safe_screen(self,funcion:Callable[[],None])->None:
        self._current_screen=funcion
        try:
            funcion()
        except Exception as exc:
            LOG.exception("Fallo al pintar una pantalla")
            for w in self.content.winfo_children(): w.destroy()
            detalle=f"{type(exc).__name__}: {exc}"
            marco=ui.estado_error(self.content,"No pudimos cargar esta información",
                                  "Los datos siguen intactos. Puedes reintentar o revisar los movimientos del mes.",
                                  reintentar=lambda:self._safe_screen(funcion))
            marco.grid(row=0,column=0,columnspan=4,sticky="ew",padx=Espacio.GRID_PAD,pady=Espacio.LG)
            ui.ayuda(marco,f"Detalle técnico: {detalle}").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))

    def refresh(self)->None:
        """Recalcula la foto del asesor y vuelve a pintar la pantalla actual."""
        try:
            self.service.refrescar_asesor()
        except Exception:
            LOG.exception("No se pudo invalidar la caché del asesor")
        pantalla=getattr(self,"_current_screen",None) or self.show_dashboard
        self._safe_screen(pantalla); self._toast("✓  Datos actualizados")

    def _month_label(self)->str:
        meses=("enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre")
        ano,numero=(int(x) for x in self.selected_month.split("-"))
        etiqueta=f"{meses[numero-1].capitalize()} {ano}"
        return etiqueta+("  · actual" if self.selected_month==mes_actual() else "")

    def _shift_month(self,delta:int)->None:
        ano,numero=(int(x) for x in self.selected_month.split("-"))
        indice=ano*12+(numero-1)+delta
        self.selected_month=f"{indice//12:04d}-{indice%12+1:02d}"
        self.month_label.configure(text=self._month_label())
        self._safe_screen(getattr(self,"_current_screen",None) or self.show_dashboard)

    def toggle_theme(self) -> None:
        self.theme="light" if self.theme=="dark" else "dark"; ctk.set_appearance_mode(self.theme); self.pref["tema"]=self.theme; guardar_preferencias(self.pref); self.theme_b.configure(text="☀" if self.theme=="dark" else "☾")
    def _clear(self,title:str,active:str) -> None:
        self.page.configure(text=title); self._tiras={}
        self.add_button.configure(text="＋ Agregar gasto",command=self.show_transactions)
        if hasattr(self,"month_label"): self.month_label.configure(text=self._month_label())
        for w in self.content.winfo_children(): w.destroy()
        for name,b in self.nav.items():
            activo=name==active
            b.configure(fg_color=T.ALT if activo else "transparent",text_color=T.TXT if activo else T.MUTED,
                        font=ui.fuente(12,"bold" if activo else "normal"))
            self.nav_marca[name].configure(fg_color=T.PRIMARY if activo else "transparent")
        self.settings_b.configure(fg_color=T.ALT if active=="Ajustes" else "transparent",
                                  text_color=T.TXT if active=="Ajustes" else T.MUTED)
        perfil="Samuel" if active=="Mi dinero · Samuel" else "Sara" if active=="Mi dinero · Sara" else "Nosotros"
        for nombre,boton in self.profile_buttons.items():
            seleccionado=nombre==perfil
            boton.configure(fg_color=T.PRIMARY_SOFT if seleccionado else "transparent",
                            text_color=T.PRIMARY if seleccionado else T.MUTED)

    def _section(self,row:int,title:str,detail:str="")->Any:
        caja=ui.titulo_seccion(self.content,title,detail); caja.grid(row=row,column=0,columnspan=4,sticky="w",padx=22,pady=(22,4)); return caja

    def _place(self,widget:Any,row:int,col:int,span:int=1)->Any:
        widget.grid(row=row,column=col,columnspan=span,sticky="nsew",padx=(22,22) if span>=4 else (22 if col==0 else 8,8),pady=Espacio.GRID_PAD); return widget

    def _metric(self,row:int,col:int,title:str,value:str,detail:str="",color:Any=None,span:int=1)->Any:
        """Las métricas contiguas de una fila se agrupan en una tira: una superficie, no una card por dato.
        Devuelve el contenedor de la métrica, donde las pantallas pueden seguir añadiendo acciones."""
        if span==1 and col==0:
            tira=ui.fila(self.content); tira.grid_rowconfigure(0,weight=1)
            self._place(tira,row,0,1); self._tiras[row]=[tira,0]
        estado=self._tiras.get(row)
        if span==1 and estado and estado[1]==col:
            tira,n=estado
            celda=ui.celda_metrica(tira,n,title,value,detail,color=color)
            estado[1]=n+1; tira.grid_configure(columnspan=n+1,padx=(22,22) if n+1>=4 else (22,8))
            return celda
        return self._place(ui.metrica(self.content,title,value,detail,color=color),row,col,span)

    def _abierto(self,row:int,col:int,span:int=1)->Any:
        """Zona sin caja: el contenido pertenece a la página y se agrupa con espacio."""
        zona=ui.fila(self.content); zona.grid(row=row,column=col,columnspan=span,sticky="nsew",padx=2,pady=Espacio.GRID_PAD); return zona

    def _open_panel(self,row:int,col:int,span:int=1,fondo:Any=None)->Any:
        return self._place(ui.panel(self.content,fondo=fondo),row,col,span)

    def _heading(self,title:str,subtitle:str) -> None:
        ctk.CTkLabel(self.content,text=title,font=Tipo.pagina(),text_color=T.TXT).grid(row=0,column=0,columnspan=4,sticky="w",padx=22,pady=(24,2))
        ctk.CTkLabel(self.content,text=subtitle,font=Tipo.ayuda(),text_color=T.MUTED,wraplength=860,justify="left").grid(row=1,column=0,columnspan=4,sticky="w",padx=22,pady=(0,14))

    def _panel(self,row:int,col:int,span:int=1) -> Any:
        """Superficie estándar. Toda la app usa el mismo radio, borde y espaciado."""
        return self._place(ui.panel(self.content),row,col,span)

    def _card(self,row:int,col:int,title:str,value:str,sub:str="",color:Any=None,span:int=1) -> Any:
        """Métrica del sistema de diseño; se conserva la firma que usan las pantallas."""
        return self._metric(row,col,title,value,sub,color,span)

    def _entry(self,parent:Any,label:str,value:str="",values:list[str]|None=None)->Any:
        ctk.CTkLabel(parent,text=label,text_color=T.MUTED,font=ui.fuente(12)).pack(anchor="w",pady=(10,4)); w=ctk.CTkOptionMenu(parent,values=values,fg_color=T.ALT,button_color=T.SUNKEN,button_hover_color=T.BORDER,text_color=T.TXT,corner_radius=Espacio.RADIO_SM) if values else ctk.CTkEntry(parent,height=36,fg_color=T.ALT,border_color=T.BORDER,corner_radius=Espacio.RADIO_SM,text_color=T.TXT)
        if values:w.set(value or values[0])
        else:
            w.insert(0,value)
            w.bind("<FocusIn>",lambda _e,x=w:x.configure(border_color=T.PRIMARY)); w.bind("<FocusOut>",lambda _e,x=w:x.configure(border_color=T.BORDER))
        w.pack(fill="x"); return w
    @staticmethod
    def _value(w:Any)->str:return str(w.get()).strip()
    def _distribution(self,total:str,responsabilidad:str,p1:str,p2:str)->tuple[str,str]:
        """Convierte la selección visual a dos montos que siempre suman el total."""
        monto=parsear_dinero(total)
        if responsabilidad=="Samuel": return str(monto),"0"
        if responsabilidad=="Sara": return "0",str(monto)
        if responsabilidad=="Porcentaje personalizado":
            try: porcentaje_p1=Decimal(p1); porcentaje_p2=Decimal(p2)
            except (InvalidOperation, ValueError): raise ValueError("Ingresa porcentajes válidos para Samuel y Sara.") from None
            if porcentaje_p1 < 0 or porcentaje_p2 < 0 or porcentaje_p1+porcentaje_p2 != Decimal("100"):
                raise ValueError("Los porcentajes de Samuel y Sara deben sumar exactamente 100%.")
            a=int((Decimal(monto)*porcentaje_p1/Decimal("100")).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
            return str(a),str(monto-a)
        if not p1 and not p2:return str(monto//2),str(monto-monto//2)
        return p1,p2
    def _run(self,action:Callable[[],None],ok:str="Guardado")->None:
        """Ejecuta una acción del usuario y traduce cualquier fallo a lenguaje claro."""
        try:
            action(); self._toast("✓  "+ok)
        except Exception as exc:
            LOG.exception("Fallo una acción del usuario")
            mensaje=str(exc).strip() or "Revisa los datos e inténtalo de nuevo."
            tecnico=type(exc).__name__ in ("KeyError","AttributeError","TypeError","IndexError")
            if tecnico:
                mensaje=("No pudimos completar la operación con los datos actuales. "
                         "Los movimientos registrados no se modificaron.")
            messagebox.showerror("No se pudo completar",mensaje,parent=self)

    def _toast(self,msg:str)->None:
        """Confirmación breve; reemplaza a la anterior, se va sola y nunca bloquea la interfaz."""
        if self._aviso is not None:
            try: self._aviso.destroy()
            except Exception: pass
        aviso=ctk.CTkFrame(self,fg_color=T.TXT,corner_radius=Espacio.RADIO_SM)
        ctk.CTkLabel(aviso,text=msg,font=ui.fuente(13,"bold"),text_color=T.BG).pack(padx=Espacio.LG,pady=Espacio.MD-4)
        aviso.place(relx=1.0,rely=1.0,anchor="se",x=-28,y=-28); aviso.lift(); self._aviso=aviso
        def quitar()->None:
            try: aviso.destroy()
            except Exception: pass
        self.after(2600,quitar)

    def _empty(self,row:int,title:str,detail:str,fn:Callable[[],None])->None:
        self._place(ui.estado_vacio(self.content,title,detail,accion="Empezar",comando=fn),row,0,4)

    @staticmethod
    def _fecha_corta(iso:str)->str:
        meses=("ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic")
        try:
            fecha=dt.date.fromisoformat(str(iso)); return f"{fecha.day} {meses[fecha.month-1]}"
        except (TypeError,ValueError): return str(iso or "—")

    def show_dashboard(self)->None:
        """Pantalla de decisión: una cifra protagonista, por qué es esa y qué necesita atención."""
        self._clear("Inicio","Inicio")
        hora=dt.datetime.now().hour
        saludo="Buenos días" if hora<12 else "Buenas tardes" if hora<19 else "Buenas noches"
        estado=self.service.estado_financiero(self.selected_month)
        perfil=estado["perfil"]; flujo=perfil["flujo"]; capacidad=estado["capacidad"]
        tarjetas=perfil["tarjetas"]; ahorro=perfil["ahorro"]; ingresos=perfil["ingresos"]
        self._heading(f"{saludo}, Samuel y Sara.","Este es el estado actual de su dinero.")

        # --- 1. Protagonista: disponible para decidir y el libro que lo explica -----------------
        disponible=int(capacidad["maximo_discrecional"]); comprometido=flujo["comprometido"]
        base=int(capacidad.get("base_disponible") or 0); descuento=int(capacidad.get("descuento_total") or 0)
        compromisos=capacidad.get("compromisos") or {}; faltante=max(descuento-base,0)
        zona=ui.fila(self.content); self._place(zona,2,0,4)
        zona.grid_columnconfigure(0,weight=7); zona.grid_columnconfigure(1,weight=4)
        izq=ui.fila(zona); izq.grid(row=0,column=0,sticky="nsew",padx=(0,Espacio.XXL))
        der=ui.fila(zona); der.grid(row=0,column=1,sticky="nsew")
        cabecera=ui.fila(izq); cabecera.pack(fill="x",pady=(Espacio.MD,0))
        ui.etiqueta(cabecera,"Disponible para decidir").pack(side="left")
        ui.ayuda(cabecera,f"Confianza de los datos: {estado['confianza']}").pack(side="right")
        color_heroe=T.BAD if faltante else T.TXT if disponible>0 else T.TERRA
        ctk.CTkLabel(izq,text=dinero(disponible),font=Tipo.numero_heroe(),text_color=color_heroe,anchor="w").pack(anchor="w",pady=(2,0))
        if not ingresos["total"] and not flujo["salidas"]:
            frase="Todavía no hay ingresos ni gastos registrados este mes, así que no hay una cifra que decidir."
        elif disponible>0:
            frase=f"Después de lo ya comprometido, les quedan {dinero(disponible)} para decidir sin tocar nada de lo adquirido."
        elif faltante:
            frase=f"Lo que hay hoy no alcanza para cubrir los compromisos y el margen de seguridad: faltan {dinero(faltante)}. No hay margen para gastos nuevos."
        else:
            frase="Lo que hay hoy alcanza justo para cubrir lo comprometido: no hay margen para gastos nuevos."
        ctk.CTkLabel(izq,text=frase,font=Tipo.editorial(),text_color=T.TXT,wraplength=620,justify="left",anchor="w").pack(anchor="w",pady=(Espacio.SM,0))
        ui.cuerpo(izq,estado["titular"],ancho=620,color=T.MUTED).pack(anchor="w",pady=(Espacio.MD,0))
        acciones=ui.fila(izq); acciones.pack(fill="x",pady=(Espacio.LG,Espacio.SM))
        ui.boton(acciones,"¿Podemos gastar?",self.affordability_dialog).pack(side="left")
        ui.boton(acciones,"¿Qué deberíamos hacer?",self.show_advisor,tono="sutil").pack(side="left",padx=Espacio.SM)

        ui.etiqueta(der,"Cómo se llega a este número").pack(anchor="w",pady=(Espacio.MD,Espacio.SM))
        margen=compromisos.get("margen_seguridad",0)
        ui.libro(der,[("Liquidez operativa registrada",dinero(base),None),
                      ("Pagos mínimos de tarjetas","− "+dinero(compromisos.get("pagos_minimos_tarjetas",0)),T.MUTED),
                      ("Aportes obligatorios a metas","− "+dinero(compromisos.get("metas_obligatorias",0)),T.MUTED),
                      (f"Margen de seguridad ({capacidad.get('margen_porcentaje',0)}% del ingreso)","− "+dinero(margen),T.MUTED)],
                 ("Disponible para decidir",dinero(disponible),color_heroe),ancho_rotulo=250).pack(fill="x")
        ui.ayuda(der,f"Los gastos fijos pendientes ({dinero(flujo['gastos_fijos_pendientes'])}) ya están descontados de la liquidez."
                     +(f" Faltan {dinero(faltante)} para cubrir estos compromisos." if faltante else ""),ancho=330).pack(anchor="w",pady=(Espacio.SM,0))

        # --- 2. Evidencia: cuatro cifras con pesos distintos, sin cajas --------------------------
        figuras=ui.fila(self.content); self._place(figuras,3,0,4)
        def cifra(col:int,peso:int,titulo:str,valor:str,detalle:str,color:Any=None,grande:bool=False,accion:tuple[str,Callable[[],None]]|None=None)->None:
            figuras.grid_columnconfigure(col,weight=peso)
            celda=ui.fila(figuras); celda.grid(row=0,column=col,sticky="nsew",padx=(0 if col==0 else Espacio.XL,0))
            ui.regla(celda); ui.etiqueta(celda,titulo).pack(anchor="w",pady=(Espacio.MD,Espacio.XS))
            ctk.CTkLabel(celda,text=valor,font=ui.fuente(28,"bold") if grande else Tipo.numero(),text_color=color or T.TXT,anchor="w").pack(anchor="w")
            ui.ayuda(celda,detalle,ancho=250).pack(anchor="w",pady=(4,0))
            if accion: ui.boton(celda,accion[0],accion[1],tono="enlace",alto=26).pack(anchor="w",pady=(2,0))
        cifra(0,4,"Ingresos del mes",dinero(ingresos["total"]),f"Samuel {dinero(ingresos['samuel'])} · Sara {dinero(ingresos['sara'])}",grande=True)
        cifra(1,3,"Gastado",dinero(flujo["salidas"]),f"Obligatorio {dinero(perfil['gastos']['obligatorios'])} · discrecional {dinero(perfil['gastos']['discrecionales'])}")
        cifra(2,3,"Comprometido",dinero(comprometido),f"Fijos pendientes {dinero(flujo['gastos_fijos_pendientes'])} · mínimos {dinero(flujo['pagos_minimos'])}")
        cifra(3,4,"Deuda",dinero(perfil["deuda"]["total"]),f"{tarjetas['cantidad']} tarjeta(s) · {tarjetas['utilizacion_global']:.0%} de cupo usado",
              T.BAD if tarjetas["utilizacion_global"]>=.7 else None,grande=True,accion=("Ver deudas",self.show_debts))

        # --- 3. Lúmina interpreta: observa y da su lectura, solo con lo que hay -------------------
        hallazgos=estado["hallazgos"]
        atencion=[h for h in hallazgos if h["prioridad"] in ("critica","alta","media")]
        principal=atencion[0] if atencion else None
        lectura_zona=ui.fila(self.content); self._place(lectura_zona,4,0,4)
        lectura_zona.grid_columnconfigure(0,weight=5); lectura_zona.grid_columnconfigure(1,weight=4)
        obs=ui.fila(lectura_zona); obs.grid(row=0,column=0,sticky="nsew",padx=(0,Espacio.XXL),pady=(Espacio.XL,0))
        lec=ui.fila(lectura_zona); lec.grid(row=0,column=1,sticky="nsew",pady=(Espacio.XL,0))
        uso=tarjetas["utilizacion_global"]
        if principal:
            ui.lectura(obs,"Lúmina observa",principal["titulo"],ancho=560,detalle=principal.get("que",""))
            destino=self._destino_hallazgo(principal)
        else:
            ui.lectura(obs,"Lúmina observa",estado["titular"],ancho=560)
            destino=(self.show_advisor,"Ver en el asesor")
        if tarjetas["cantidad"] and uso>=.5:
            ui.ayuda(obs,f"{tarjetas['cantidad']} tarjeta(s) están usando aproximadamente {uso:.0%} del cupo total.",ancho=520).pack(anchor="w",padx=(Espacio.MD+2,0),pady=(Espacio.SM,0))
        plan=estado.get("plan") or {}
        if principal and principal.get("accion"):
            ui.lectura(lec,"Mi lectura",principal["accion"],ancho=420,color=T.TERRA,detalle=principal.get("por_que",""))
        elif plan.get("resumen"):
            ui.lectura(lec,"Mi lectura",plan["resumen"],ancho=420,color=T.TERRA)
        else:
            ui.lectura(lec,"Mi lectura","Todavía no hay datos suficientes para recomendar algo. Registren movimientos y la lectura se afinará.",ancho=420,color=T.TERRA)
        ui.boton(lec,"Ver qué está pasando  →",destino[0] or self.show_advisor,tono="enlace",alto=28).pack(anchor="w",pady=(Espacio.SM,0))

        # --- 4. Qué necesita atención: informe numerado, no una fila de alertas -------------------
        puntos:list[tuple[str,str,str,Callable[[],None]|None,str,str]]=[]
        for h in atencion[1:]:
            d=self._destino_hallazgo(h)
            puntos.append((h["titulo"],h.get("que",""),dinero(h["monto_involucrado"]) if h.get("monto_involucrado") else "",d[0],d[1],h["prioridad"]))
        for titulo,texto,dest,rotulo in self._avisos(estado):
            puntos.append((titulo,texto,"",dest,rotulo,"media"))
        self._section(5,"Qué necesita atención","Lo más importante primero, con lo que dicen los datos registrados." if puntos else "")
        if not puntos:
            nada=("Con los movimientos registrados no encuentro otras señales que pidan una decisión inmediata."
                  if principal else "Con los movimientos registrados no encuentro señales que pidan una decisión inmediata.")
            self._place(ui.estado_vacio(self.content,"Nada más por ahora",nada,accion="Ver el análisis completo",comando=self.show_advisor),6,0,4)
            fila_siguiente=7
        else:
            lista=self._abierto(6,0,4); fila_siguiente=7
            for i,(titulo,texto,dato,dest,rotulo,sev) in enumerate(puntos[:5]):
                ui.punto_atencion(lista,i+1,titulo,texto,dato=dato,accion=rotulo,comando=dest,severidad=sev,primero=i==0,ancho=640)
            ui.regla(lista)
            if len(puntos)>5:
                ui.boton(lista,f"Ver {len(puntos)-5} más en el asesor",self.show_advisor,tono="enlace",alto=28).pack(anchor="w",pady=Espacio.SM)

        # --- 5. Lo que viene, cómo van y lo reservado: dos columnas de pesos distintos ------------
        resto=ui.fila(self.content); self._place(resto,fila_siguiente,0,4)
        resto.grid_columnconfigure(0,weight=5); resto.grid_columnconfigure(1,weight=4)
        col_a=ui.fila(resto); col_a.grid(row=0,column=0,sticky="nsew",padx=(0,Espacio.XXL),pady=(Espacio.XL,0))
        col_b=ui.fila(resto); col_b.grid(row=0,column=1,sticky="nsew",pady=(Espacio.XL,0))

        ui.titulo_seccion(col_a,"Próximos 30 días","Compromisos con fecha conocida.").pack(anchor="w",pady=(0,Espacio.SM))
        proximos=estado.get("proximos_pagos") or []
        if not proximos:
            ui.ayuda(col_a,"No hay pagos, gastos fijos ni metas con fecha en los próximos 30 días. Si esperan alguno, regístrenlo con su fecha para que aparezca aquí.",ancho=520).pack(anchor="w",pady=Espacio.SM)
        for item in proximos[:5]:
            ui.regla(col_a); linea=ui.fila(col_a); linea.pack(fill="x",pady=Espacio.SM)
            ctk.CTkLabel(linea,text=self._fecha_corta(item["fecha"]),font=Tipo.ayuda(),text_color=T.MUTED,width=56,anchor="w").pack(side="left")
            ctk.CTkLabel(linea,text=dinero(item["monto"]),font=ui.fuente(13,"bold"),text_color=T.TXT).pack(side="right")
            caja_texto=ui.fila(linea); caja_texto.pack(side="left",fill="x",expand=True)
            ctk.CTkLabel(caja_texto,text=item["titulo"],font=ui.fuente(13,"bold"),text_color=T.TXT,anchor="w").pack(anchor="w")
            ui.ayuda(caja_texto,item["tipo"],ancho=360).pack(anchor="w")
        if proximos: ui.regla(col_a)

        ui.titulo_seccion(col_a,"Cómo va cada uno","Aportes de caja del mes. La responsabilidad económica se lee aparte.").pack(anchor="w",pady=(Espacio.XL,Espacio.SM))
        liquidez=perfil["flujo"]["por_persona"]
        maximo=max(1,*[max(x["liquidez"],0) for x in liquidez.values()])
        for persona,nombre,color in ((SAMUEL,"Samuel",T.PRIMARY),(SARA,"Sara",T.TERRA)):
            linea=ui.fila(col_a); linea.pack(fill="x",pady=8)
            ctk.CTkLabel(linea,text=nombre,width=64,anchor="w",font=ui.fuente(13,"bold")).pack(side="left")
            ctk.CTkLabel(linea,text=dinero(liquidez[persona]["liquidez"]),font=Tipo.ayuda(),text_color=T.MUTED).pack(side="right")
            medidor=ctk.CTkProgressBar(linea,height=5,progress_color=color,fg_color=T.ALT,corner_radius=1)
            medidor.pack(side="left",fill="x",expand=True,padx=Espacio.MD); medidor.set(max(liquidez[persona]["liquidez"],0)/maximo)
        balance=estado["pareja"]
        texto_saldo=(f"{balance['deudor']} le debe {dinero(balance['saldo_pendiente'])} a {balance['acreedor']}."
                     if balance["saldo_pendiente"] else "No queda saldo pendiente entre ustedes.")
        ui.ayuda(col_a,texto_saldo,ancho=480).pack(anchor="w",pady=(0,Espacio.XS))
        ui.boton(col_a,"Ver liquidación",self.show_settlement,tono="enlace",alto=28).pack(anchor="w")

        ui.titulo_seccion(col_b,"Reservado en cajitas").pack(anchor="w",pady=(0,Espacio.SM))
        ui.regla(col_b)
        ctk.CTkLabel(col_b,text=dinero(ahorro["total"]),font=Tipo.numero(),text_color=T.SAVE,anchor="w").pack(anchor="w",pady=(Espacio.MD,0))
        emergencia=ahorro["emergencia"]
        ui.ayuda(col_b,f"Fondo de emergencia: {dinero(emergencia['current'])} · cubre {emergencia['coverage_months']:.1f} meses de gasto obligatorio.",ancho=400).pack(anchor="w",pady=(4,0))
        for meta in ahorro["metas"][:2]:
            if meta["monto_objetivo"]:
                cont=ui.fila(col_b); cont.pack(fill="x",pady=(Espacio.SM,0))
                ui.progreso_con_texto(cont,meta["nombre"],dinero(meta["actual"]),dinero(meta["monto_objetivo"]),meta["progreso"],
                                      detalle=("Atrasada frente a su fecha objetivo." if meta.get("atrasada") else ""))
        ui.boton(col_b,"Ver cajitas",self.show_savings,tono="enlace",alto=28).pack(anchor="w",pady=(Espacio.SM,0))

    def _avisos(self,estado:dict[str,Any])->list[tuple[str,str,Callable[[],None],str]]:
        """Pocas señales, solo las que llevan a una pantalla concreta: (título, texto, destino, acción)."""
        avisos:list[tuple[str,str,Callable[[],None],str]]=[]
        cotejo=estado.get("cotejo_gastos_fijos") or {}
        if cotejo.get("sin_registrar"):
            avisos.append(("Gastos fijos sin registrar",f"Encontramos {len(cotejo['sin_registrar'])} cobros que se comportan como gastos fijos y no están registrados.",
                           self.show_fixed_expenses,"Revisar"))
        if cotejo.get("monto_desactualizado"):
            primero=cotejo["monto_desactualizado"][0]
            avisos.append(("Un gasto fijo cambió de monto",primero["detalle"],self.show_fixed_expenses,"Revisar"))
        integridad=estado.get("integridad") or {}
        if integridad.get("descuadre"):
            avisos.append(("El libro no cuadra del todo",f"Hay una diferencia de {dinero(integridad['descuadre_absoluto'])} en la reconstrucción histórica del libro.",
                           self.show_integrity,"Abrir"))
        atrasadas=estado["perfil"]["ahorro"]["metas_atrasadas"]
        if atrasadas:
            avisos.append(("Metas atrasadas",f"Frente a su fecha objetivo: {', '.join(atrasadas)}.",self.show_savings,"Ver metas"))
        creciendo=estado["perfil"]["tarjetas"]["creciendo"]
        if creciendo:
            avisos.append(("La deuda creció este mes",f"Aumentó en: {', '.join(creciendo)}.",self.show_cards,"Ver tarjetas"))
        return avisos[:4]

    def _destino_hallazgo(self,hallazgo:dict[str,Any])->tuple[Callable[[],None]|None,str]:
        """Cada hallazgo abre la pantalla donde se puede actuar sobre él."""
        categoria=hallazgo.get("categoria","")
        mapa={"tarjeta":(self.show_cards,"Ver tarjetas"),"gastos_fijos":(self.show_fixed_expenses,"Ver gastos fijos"),
              "ahorro":(self.show_savings,"Ver cajitas"),"metas":(self.show_savings,"Ver metas"),
              "pareja":(self.show_settlement,"Ver liquidación"),"integridad":(self.show_integrity,"Revisar datos"),
              "flujo":(self.show_transactions,"Ver movimientos"),"tendencia":(self.show_analytics,"Ver análisis"),
              "datos":(self.show_transactions,"Completar datos")}
        return mapa.get(categoria,(self.show_advisor,"Ver en el asesor"))

    def spending_dialog(self)->None:
        """Abre una simulación; nunca registra una compra ni modifica una tarjeta."""
        dialog=ctk.CTkToplevel(self,fg_color=T.BG); dialog.title("¿Podemos gastar?"); dialog.geometry("520x620"); dialog.grab_set()
        body=ctk.CTkFrame(dialog,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="¿Podemos gastar?",font=Tipo.dialogo()).pack(anchor="w",padx=20,pady=(20,2))
        ctk.CTkLabel(body,text="Esta es una simulación: no cambia su base de datos.",text_color=T.MUTED).pack(anchor="w",padx=20)
        amount=self._entry(body,"Monto estimado COP"); method=self._entry(body,"Método de pago","Débito",["Débito","Efectivo","Tarjeta"]); margin=self._entry(body,"Margen de seguridad","10",["5","10","15","20"])
        cards=self.service.tarjetas(); labels=[f"{c['id']} · {c['nombre']} · disponible {dinero(c['cupo_disponible'])}" for c in cards]
        card=self._entry(body,"Tarjeta (solo si aplica)",labels[0] if labels else "Sin tarjetas registradas",labels) if labels else None
        result=ctk.CTkFrame(body,fg_color=T.ALT,corner_radius=Espacio.RADIO_SM); result.pack(fill="x",padx=20,pady=(16,8))
        def analyze()->None:
            for child in result.winfo_children(): child.destroy()
            selected=self._value(method); payment={"Débito":"debito","Efectivo":"efectivo","Tarjeta":"tarjeta"}[selected]
            card_id=self._value(card).split(" · ")[0] if card and payment=="tarjeta" else None
            try: evaluation=self.service.evaluar_gasto(self.selected_month,self._value(amount),payment,card_id,int(self._value(margin)))
            except Exception as exc: messagebox.showerror("No se pudo analizar",str(exc),parent=dialog); return
            color=T.OK if evaluation["estado"]=="si" else T.WARN if evaluation["estado"]=="cuidado" else T.BAD
            ctk.CTkLabel(result,text=evaluation["titulo"],font=ui.serif(19),text_color=color).pack(anchor="w",padx=16,pady=(14,2))
            ctk.CTkLabel(result,text=evaluation["mensaje"],text_color=T.TXT,wraplength=430,justify="left").pack(anchor="w",padx=16)
            ctk.CTkLabel(result,text=f"Límite seguro: {dinero(evaluation['puede_gastar_hasta'])}\nDespués de la salida: {dinero(evaluation['despues'])}\nPagos mínimos reservados: {dinero(evaluation['pagos_minimos_tarjetas'])}\nMetas prioritarias: {dinero(evaluation['metas_prioritarias'])}\nMargen de seguridad: {dinero(evaluation['margen_seguridad'])}",text_color=T.MUTED,justify="left").pack(anchor="w",padx=16,pady=8)
            if evaluation["advertencias"]: ctk.CTkLabel(result,text="⚠ " + " ".join(evaluation["advertencias"]),text_color=T.WARN,wraplength=430).pack(anchor="w",padx=16,pady=(0,8))
            ctk.CTkLabel(result,text="Método sugerido: "+evaluation["metodo_recomendado"],text_color=T.TXT,wraplength=430).pack(anchor="w",padx=16,pady=(0,14))
        ctk.CTkButton(body,text="Analizar salida",fg_color=T.PRIMARY,command=analyze).pack(fill="x",padx=20,pady=(0,20))

    def show_transactions(self)->None:
        self._clear("Movimientos","Movimientos"); self._heading("Movimientos","Registra ingresos y gastos. Si hay un error, anula el movimiento sin perder su historial.")
        form=self._panel(2,0); titulo=ctk.CTkLabel(form,text="＋ Nuevo movimiento",font=ui.serif(17));titulo.pack(anchor="w",padx=18,pady=(16,2))
        kind=self._entry(form,"Tipo de movimiento","Gasto",["Gasto","Ingreso"]); name=self._entry(form,"Descripción / fuente"); amount=self._entry(form,"Monto COP"); cat=self._entry(form,"Categoría")
        method=self._entry(form,"Método","Efectivo",["Efectivo","Débito","Tarjeta"]); payer=self._entry(form,"¿Quién realiza el pago?","Samuel",["Samuel","Sara"])
        resp=self._entry(form,"Responsabilidad económica","Compartido",["Samuel","Sara","Compartido","Personalizado","Porcentaje personalizado"])
        conditional=ctk.CTkFrame(form,fg_color="transparent"); conditional.pack(fill="x",padx=0)
        card_group=ctk.CTkFrame(conditional,fg_color="transparent"); split_group=ctk.CTkFrame(conditional,fg_color="transparent")
        card_labels=[f"{c['id']} · {c['nombre']} · {dinero(c['cupo_disponible'])} disponible" for c in self.service.tarjetas()]
        card=self._entry(card_group,"Tarjeta",card_labels[0],card_labels) if card_labels else None
        instalments=self._entry(card_group,"Cuotas","1") if card_labels else None
        p1=self._entry(split_group,"Samuel asume COP","0"); p2=self._entry(split_group,"Sara asume COP","0")
        def refresh_fields(*_:Any)->None:
            ingreso=self._value(kind)=="Ingreso"
            titulo.configure(text="＋ Registrar ingreso" if ingreso else "＋ Registrar gasto")
            (cat.pack_forget() if ingreso else cat.pack(fill="x"))
            (method.pack_forget() if ingreso else method.pack(fill="x"))
            (resp.pack_forget() if ingreso else resp.pack(fill="x"))
            (conditional.pack_forget() if ingreso else conditional.pack(fill="x"))
            is_card=self._value(method)=="Tarjeta"
            if card and instalments:
                (card_group.pack(fill="x") if is_card else card_group.pack_forget())
            custom=self._value(resp) in ("Compartido","Personalizado","Porcentaje personalizado")
            (split_group.pack(fill="x") if custom else split_group.pack_forget())
        kind.configure(command=refresh_fields); method.configure(command=refresh_fields); resp.configure(command=refresh_fields); refresh_fields()
        def save()->None:
            if self._value(kind)=="Ingreso":
                self.service.crear_ingreso(self.selected_month,self._value(payer),self._value(name),self._value(amount)); self.show_transactions(); return
            total=self._value(amount); r=self._value(resp)
            if r=="Porcentaje personalizado": aportes=self._distribution(total,r,self._value(p1),self._value(p2))
            elif r=="Compartido" and self._value(p1) in ("", "0") and self._value(p2) in ("", "0"):
                aportes=self._distribution(total,r,"","")
            else: aportes=self._distribution(total,r,self._value(p1),self._value(p2))
            tarjeta_id=self._value(card).split(" · ")[0] if card and self._value(method)=="Tarjeta" else ""
            self.service.crear_gasto({"mes":self.selected_month,"nombre":self._value(name),"categoria":self._value(cat),"valor":total,"fecha":dt.date.today().isoformat(),"metodo":self._value(method),"pagador":self._value(payer),"responsabilidad":r,"monto_p1":aportes[0],"monto_p2":aportes[1],"tarjeta_id":tarjeta_id,"cuotas":self._value(instalments) if instalments and tarjeta_id else "1","prioridad":"Obligatorio"}); self.show_transactions()
        ctk.CTkButton(form,text="Guardar movimiento",fg_color=T.PRIMARY,command=lambda:self._run(save,"Movimiento registrado")).pack(fill="x",padx=18,pady=18)
        panel=self._abierto(2,1,3); ctk.CTkLabel(panel,text="Actividad reciente",font=ui.serif(17)).pack(anchor="w",padx=18,pady=(16,4)); ctk.CTkLabel(panel,text="Anular conserva el registro, actualiza los saldos y evita borrar evidencia.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,6)); search=ctk.CTkEntry(panel,placeholder_text="Buscar salario, comida, Samuel…",height=36,fg_color=T.ALT,border_color=T.BORDER); search.pack(fill="x",padx=18,pady=(0,10)); listing=ctk.CTkFrame(panel,fg_color="transparent"); listing.pack(fill="both",expand=True,padx=10,pady=(0,12)); rows=self.service.movimientos(self.selected_month,incluir_reversados=True)
        def reverse(m:dict[str,Any])->None:
            tipos={"interes":"movimiento_tarjeta","cargo":"movimiento_tarjeta"}; dominio=tipos.get(m["tipo"],m["tipo"])
            if dominio not in {"ingreso","gasto","pago","liquidacion","ahorro","movimiento_tarjeta"}: raise ValueError("Este tipo de movimiento no se puede anular desde esta lista.")
            if m.get("tarjeta_id") and m["tipo"] in {"gasto","pago","interes","cargo"}:
                tipo_revision="gasto" if m["tipo"]=="gasto" else "pago" if m["tipo"]=="pago" else "movimiento"
                vista=self.service.revisar_anulacion_tarjeta(int(m["tarjeta_id"]),m["id"],tipo_revision)
                d=ctk.CTkToplevel(self,fg_color=T.BG);d.title("Confirmar anulación de tarjeta");d.geometry("560x590");d.grab_set()
                body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14)
                ctk.CTkLabel(body,text="Revisar impacto antes de anular",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
                ctk.CTkLabel(body,text=f"{vista['tipo_original'].title()} · {vista['descripcion']} · {dinero(vista['monto'])}",text_color=T.MUTED,wraplength=490,justify="left").pack(anchor="w",padx=18,pady=(0,12))
                ahora=vista["situacion_actual"];despues=vista["despues_de_anular"]
                texto=(f"Situación actual\nDeuda: {dinero(ahora['saldo_tarjeta'])} · Uso: {ahora['utilizacion']:.0%}\n"
                       f"Responsabilidad: Samuel {dinero(ahora['responsabilidad_samuel'])} · Sara {dinero(ahora['responsabilidad_sara'])}\n\n"
                       + (f"Después de anular\nDeuda: {dinero(despues['saldo_tarjeta'])} · Uso: {despues['utilizacion']:.0%}\n"
                          f"Responsabilidad: Samuel {dinero(despues['responsabilidad_samuel'])} · Sara {dinero(despues['responsabilidad_sara'])}\n"
                          f"Interés potencial evitable: {dinero(vista['interes_potencial_evitable'])}" if vista["se_puede_anular"] else "No es posible anularla todavía."))
                ctk.CTkLabel(body,text=texto,justify="left",wraplength=490,text_color=T.TXT).pack(anchor="w",padx=18,pady=(0,8))
                if vista["advertencias"]: ctk.CTkLabel(body,text="Advertencia: "+" ".join(vista["advertencias"]),justify="left",wraplength=490,text_color=T.WARN).pack(anchor="w",padx=18,pady=(0,10))
                motivo=self._entry(body,"Motivo de anulación")
                def confirmar()->None:
                    razon=self._value(motivo)
                    if not razon: raise ValueError("Indica el motivo de la anulación.")
                    if not messagebox.askyesno("Confirmar anulación",f"Se conservará el original y se registrará su operación inversa por {dinero(vista['monto'])}.\n\n¿Confirmar?",parent=d): return
                    self.service.reversar(dominio,m["id"],razon);d.destroy();self.show_transactions()
                ctk.CTkButton(body,text="Confirmar anulación" if vista["se_puede_anular"] else "Cerrar",fg_color=T.PRIMARY,command=(lambda:self._run(confirmar,"Movimiento anulado")) if vista["se_puede_anular"] else d.destroy).pack(fill="x",padx=18,pady=20)
                return
            vista=self.service.proponer_anulacion(dominio,m["id"])
            d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Revisar anulación"); d.geometry("560x570"); d.grab_set()
            body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
            ctk.CTkLabel(body,text="Revisar impacto antes de anular",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
            ctk.CTkLabel(body,text=f"{vista['tipo_original'].title()} · {vista['descripcion']} · {dinero(vista['monto'])}",text_color=T.MUTED,wraplength=490,justify="left").pack(anchor="w",padx=18,pady=(0,12))
            ahora=vista["situacion_actual"]; despues=vista["despues_de_anular"]
            texto=(f"Situación actual\nFlujo: {dinero(ahora['flujo'])} · Liquidez: {dinero(ahora['liquidez'])} · Ahorro: {dinero(ahora['ahorro'])}\n"
                   f"Saldo entre ustedes: Samuel {dinero(ahora['saldo_samuel'])} · Sara {dinero(ahora['saldo_sara'])}\n\n"
                   f"Después de anular\nFlujo: {dinero(despues['flujo'])} · Liquidez: {dinero(despues['liquidez'])} · Ahorro: {dinero(despues['ahorro'])}\n"
                   f"Saldo entre ustedes: Samuel {dinero(despues['saldo_samuel'])} · Sara {dinero(despues['saldo_sara'])}")
            ctk.CTkLabel(body,text=texto,justify="left",wraplength=490,text_color=T.TXT).pack(anchor="w",padx=18,pady=(0,8))
            if vista["advertencias"]: ctk.CTkLabel(body,text="Advertencia: "+" ".join(vista["advertencias"]),justify="left",wraplength=490,text_color=T.WARN).pack(anchor="w",padx=18,pady=(0,10))
            motivo=self._entry(body,"Motivo de anulación")
            def confirmar_generica()->None:
                razon=self._value(motivo)
                if not razon: raise ValueError("Indica el motivo de la anulación.")
                if not messagebox.askyesno("Confirmar anulación",f"Se conservará el original y se registrará su operación inversa por {dinero(vista['monto'])}.\n\n¿Confirmar?",parent=d): return
                self.service.reversar(dominio,m["id"],razon); d.destroy(); self.show_transactions()
            ctk.CTkButton(body,text="Confirmar anulación" if vista["se_puede_anular"] else "Cerrar",fg_color=T.PRIMARY,command=(lambda:self._run(confirmar_generica,"Movimiento anulado")) if vista["se_puede_anular"] else d.destroy).pack(fill="x",padx=18,pady=20)
        def render(*_:Any)->None:
            for x in listing.winfo_children():x.destroy()
            found=[m for m in rows if search.get().lower() in (m["descripcion"]+m["persona"]).lower()]
            if not found:ctk.CTkLabel(listing,text="No hay movimientos que coincidan.",text_color=T.MUTED).pack(pady=30)
            for m in found[:30]:
                x=ui.fila(listing); x.pack(fill="x",pady=0); ui.regla(x)
                plus=m["tipo"]=="ingreso"; activo=m["estado"]=="ACTIVO"
                cabecera=ui.fila(x); cabecera.pack(fill="x",padx=Espacio.MD,pady=(Espacio.SM,0))
                ctk.CTkLabel(cabecera,text=m["fecha"],font=Tipo.ayuda(),text_color=T.MUTED,width=88,anchor="w").pack(side="left")
                ctk.CTkLabel(cabecera,text=m["descripcion"]+("  · anulado" if not activo else ""),anchor="w",
                             font=ui.fuente(13,"bold"),text_color=T.TXT if activo else T.MUTED).pack(side="left",fill="x",expand=True)
                ctk.CTkLabel(cabecera,text=("+" if plus else "−")+dinero(m["monto"]),
                             text_color=T.OK if plus and activo else T.TXT if activo else T.MUTED,
                             font=ui.fuente(14,"bold")).pack(side="right")
                # Quién puso el dinero y quién asume el gasto son cosas distintas.
                detalle=[f"Pagó {m['persona']}"]
                if m.get("responsabilidad"): detalle.append(f"Responsabilidad: {m['responsabilidad']}")
                if m.get("categoria"): detalle.append(m["categoria"])
                if m.get("metodo"): detalle.append(m["metodo"])
                if m.get("tarjeta"): detalle.append(m["tarjeta"])
                if m.get("prioridad")=="discrecional": detalle.append("Discrecional")
                ui.ayuda(x,"   ·   ".join(detalle),ancho=760).pack(anchor="w",padx=Espacio.MD,pady=(2,0))
                if m.get("monto_p1") is not None and m.get("responsabilidad")=="Compartido":
                    ui.ayuda(x,f"Reparto: Samuel {dinero(m['monto_p1'])} · Sara {dinero(m['monto_p2'])}",ancho=760).pack(anchor="w",padx=Espacio.MD)
                acciones=ui.fila(x); acciones.pack(fill="x",padx=Espacio.MD,pady=(4,Espacio.SM))
                if activo and m["tipo"]=="gasto": ctk.CTkButton(acciones,text="Editar",width=58,height=28,fg_color="transparent",border_width=1,border_color=T.BORDER,text_color=T.MUTED,command=lambda movimiento=m:self.edit_expense_dialog(movimiento["id"])).pack(side="right",padx=(0,6))
                if activo and m.get("tarjeta_id") and m["tipo"] in {"gasto","pago","interes","cargo"}: ctk.CTkButton(acciones,text="Ver impacto",width=86,height=28,fg_color="transparent",border_width=1,border_color=T.BORDER,text_color=T.MUTED,command=lambda movimiento=m:self._run(lambda:reverse(movimiento),"Impacto revisado")).pack(side="right",padx=(0,6))
                if activo: ctk.CTkButton(acciones,text="Anular",width=64,height=28,fg_color="transparent",border_width=1,border_color=T.BORDER,text_color=T.MUTED,command=lambda movimiento=m:self._run(lambda:reverse(movimiento),"Movimiento anulado")).pack(side="right",padx=(0,10))
        search.bind("<KeyRelease>",render); render()

    def edit_expense_dialog(self,gasto_id:int)->None:
        gasto=self.service.gasto(gasto_id)
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title(f"Editar · {gasto['nombre']}"); d.geometry("530x710"); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="✎ Editar gasto",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
        es_tarjeta=gasto["metodo_pago"]=="tarjeta"
        texto=("Compra con tarjeta: puedes corregir el total y la distribución aun con pagos registrados. "
               "La tarjeta y el método se preservan para proteger el historial FIFO." if es_tarjeta else
               "Corrige el gasto sin crear un registro duplicado. La responsabilidad y quién pagó se conservan por separado.")
        ctk.CTkLabel(body,text=texto,text_color=T.MUTED,wraplength=455,justify="left").pack(anchor="w",padx=18,pady=(0,8))
        mes=self._entry(body,"Mes (AAAA-MM)",gasto["mes"]); fecha=self._entry(body,"Fecha (AAAA-MM-DD)",gasto.get("fecha") or "")
        nombre=self._entry(body,"Descripción",gasto["nombre"]); categoria=self._entry(body,"Categoría",gasto["categoria"]); total=self._entry(body,"Monto total COP",str(gasto["valor"]))
        pagador=self._entry(body,"Quién realizó el pago",NOMBRES[gasto["pagador"]],["Samuel","Sara"])
        if gasto["monto_p1"]==gasto["valor"]: modo_inicial="Samuel"
        elif gasto["monto_p2"]==gasto["valor"]: modo_inicial="Sara"
        elif gasto["monto_p1"]==gasto["monto_p2"]: modo_inicial="Compartido"
        else: modo_inicial="Personalizado"
        resp=self._entry(body,"Responsabilidad económica",modo_inicial,["Samuel","Sara","Compartido","Personalizado","Porcentaje personalizado"])
        p1=self._entry(body,"Samuel asume COP (o %)",str(gasto["monto_p1"])); p2=self._entry(body,"Sara asume COP (o %)",str(gasto["monto_p2"]))
        prioridad=self._entry(body,"Prioridad","Obligatorio" if gasto["prioridad"]=="obligatorio" else "Discrecional",["Obligatorio","Discrecional"])
        if es_tarjeta:
            tarjeta=next((x for x in self.service.tarjetas() if x["id"]==gasto["tarjeta_id"]),None)
            ctk.CTkLabel(body,text=f"Método conservado: Tarjeta · {tarjeta['nombre'] if tarjeta else 'tarjeta histórica'}",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(12,0))
        def save()->None:
            modo=self._value(resp)
            a,b=self._distribution(self._value(total),modo,self._value(p1),self._value(p2))
            self.service.editar_gasto(gasto_id,{"mes":self._value(mes),"fecha":self._value(fecha),"nombre":self._value(nombre),"categoria":self._value(categoria),"valor":self._value(total),"pagador":self._value(pagador),"responsabilidad":modo,"monto_p1":a,"monto_p2":b,"prioridad":self._value(prioridad)})
            d.destroy(); self.show_transactions()
        ctk.CTkButton(body,text="Guardar cambios",height=40,fg_color=T.PRIMARY,command=lambda:self._run(save,"Gasto actualizado")).pack(fill="x",padx=18,pady=22)

    def show_cards(self)->None:
        """Tarjetas con lectura del asesor: uso del cupo, costo y a quién le toca."""
        self._clear("Tarjetas","Tarjetas")
        self._heading("Tarjetas","Cupo, saldo y costo real de cada tarjeta. El cupo disponible no es dinero suyo.")
        self.add_button.configure(text="＋ Nueva tarjeta",command=self.card_create_dialog)
        resumen=self.service.resumen_tarjetas(self.selected_month)
        if not resumen["tarjetas"]:
            self._place(ui.estado_vacio(self.content,"Todavía no hay tarjetas registradas",
                        "Registren una tarjeta para controlar cupo, deuda, pagos, intereses y responsabilidades.",
                        accion="＋ Agregar tarjeta",comando=self.card_create_dialog,icono="▣"),2,0,4); return
        inteligencia=self.service.inteligencia_tarjetas(self.selected_month)
        utilizacion=resumen["utilizacion_global"]
        self._metric(2,0,"Deuda total",dinero(resumen["deuda_total"]),f"Mínimos del mes: {dinero(resumen['pago_minimo_total'])}",T.BAD if utilizacion>=.7 else T.WARN)
        self._metric(2,1,"Utilización global",f"{utilizacion:.0%}",ui.utilizacion_texto(utilizacion),ui.utilizacion_color(utilizacion))
        self._metric(2,2,"Cupo disponible",dinero(resumen["cupo_disponible"]),"Es crédito no usado, no dinero propio",T.MUTED)
        self._metric(2,3,"Interés estimado",dinero(inteligencia["interes_estimado_total"]),"Costo mensual de arrastrar el saldo",T.WARN)
        lectura=self._abierto(3,0,4)
        ui.lectura(lectura,"Lectura de Lúmina",self.service.comparar_tarjetas(self.selected_month).get("lectura","") or "Aún no hay suficientes movimientos para comparar las tarjetas.",
                   ancho=820,detalle=f"Responsabilidad económica del saldo: Samuel {dinero(resumen['deuda_persona1'])} · Sara {dinero(resumen['deuda_persona2'])}. "
                   "El titular responde ante el banco; la responsabilidad se lee de cada movimiento.").pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.SM)
        self._section(4,"Sus tarjetas")
        for indice,tarjeta in enumerate(inteligencia["tarjetas"]):
            caja=self._open_panel(5+indice//2,indice%2*2,2)
            cabecera=ui.fila(caja); cabecera.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ctk.CTkLabel(cabecera,text=tarjeta["nombre"],font=Tipo.tarjeta(),text_color=T.TXT).pack(side="left")
            ui.insignia(cabecera,ui.utilizacion_texto(tarjeta["utilizacion"]),
                        tono="alta" if tarjeta["utilizacion"]>=.7 else "media" if tarjeta["utilizacion"]>=.5 else "positiva").pack(side="right")
            ui.ayuda(caja,f"Titular: {tarjeta['titular']}",ancho=420).pack(anchor="w",padx=Espacio.PANEL_PAD)
            ctk.CTkLabel(caja,text=dinero(tarjeta["saldo"]),font=Tipo.numero(),text_color=ui.utilizacion_color(tarjeta["utilizacion"])).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.SM,0))
            ui.ayuda(caja,f"de {dinero(tarjeta['cupo'])} de cupo · disponible {dinero(tarjeta['disponible'])}",ancho=420).pack(anchor="w",padx=Espacio.PANEL_PAD)
            ui.barra(caja,tarjeta["utilizacion"],color=ui.utilizacion_color(tarjeta["utilizacion"]),alto=9,pad=(Espacio.PANEL_PAD,Espacio.SM))
            ui.dato(caja,"Utilización",f"{tarjeta['utilizacion']:.0%}",color=ui.utilizacion_color(tarjeta["utilizacion"]))
            ui.dato(caja,"Interés mensual",f"{tarjeta['interes_mensual']:.2f}%" if tarjeta["interes_mensual"] else "Sin registrar",
                    color=T.WARN if tarjeta["interes_mensual"] else T.MUTED)
            ui.dato(caja,"Pago mínimo",dinero(tarjeta["pago_minimo"]))
            ui.dato(caja,"Corte / pago",f"{tarjeta.get('fecha_corte') or '—'} · {tarjeta.get('fecha_pago') or '—'}",color=T.MUTED)
            ui.dato(caja,"Responsabilidad",f"Samuel {dinero(tarjeta['deuda_responsabilidad']['Samuel'])} · Sara {dinero(tarjeta['deuda_responsabilidad']['Sara'])}",color=T.MUTED)
            for señal in tarjeta["señales"][:2]:
                tono={"alta":"alta","media":"media","positiva":"positiva","info":"info"}.get(señal["nivel"],"info")
                aviso=ui.panel(caja,fondo=ui.SEVERIDAD_FONDO[tono],borde=False,radio=Espacio.RADIO_SM)
                aviso.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.SM,0))
                ctk.CTkLabel(aviso,text=señal["detalle"],font=Tipo.ayuda(),text_color=T.TXT,wraplength=400,justify="left").pack(anchor="w",padx=Espacio.MD,pady=Espacio.SM)
            acciones=ui.fila(caja); acciones.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,Espacio.MD))
            ui.boton(acciones,"Ver detalle",lambda c=tarjeta:self.show_card_detail(c["id"]),alto=32).pack(side="left")
            ui.boton(acciones,"＋ Compra",lambda c=tarjeta:self.card_purchase_dialog(self._tarjeta_cruda(c["id"])),tono="suave",alto=32).pack(side="left",padx=7)
            ui.boton(acciones,"↓ Pago",lambda c=tarjeta:self.card_payment_dialog(self._tarjeta_cruda(c["id"])),tono="suave",alto=32).pack(side="left")

    def _tarjeta_cruda(self,tarjeta_id:int)->dict[str,Any]:
        """Los diálogos de tarjeta esperan el resumen del dominio, no la vista."""
        return next(t for t in self.service.tarjetas() if t["id"]==tarjeta_id)

    def card_create_dialog(self)->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Nueva tarjeta"); d.geometry("560x760"); d.minsize(460,620); d.grab_set(); body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="＋ Nueva tarjeta",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2));ctk.CTkLabel(body,text="No guardamos número completo, CVV, PIN ni claves.",text_color=T.MUTED).pack(anchor="w",padx=18)
        ctk.CTkLabel(body,text="BÁSICO",text_color=T.PRIMARY,font=ui.fuente(11,"bold")).pack(anchor="w",padx=18,pady=(18,0)); name=self._entry(body,"Nombre de tarjeta"); bank=self._entry(body,"Banco / entidad"); kind=self._entry(body,"Tipo de tarjeta","Crédito"); holder=self._entry(body,"Titular","Samuel",["Samuel","Sara"]); last4=self._entry(body,"Últimos 4 dígitos (opcional)")
        ctk.CTkLabel(body,text="CRÉDITO",text_color=T.PRIMARY,font=ui.fuente(11,"bold")).pack(anchor="w",padx=18,pady=(18,0)); limit=self._entry(body,"Cupo total COP"); minimum=self._entry(body,"Pago mínimo COP","0"); rate=self._entry(body,"Interés mensual (%)","0")
        ctk.CTkLabel(body,text="FECHAS Y SALDO HISTÓRICO",text_color=T.PRIMARY,font=ui.fuente(11,"bold")).pack(anchor="w",padx=18,pady=(18,0)); cut=self._entry(body,"Fecha de corte (AAAA-MM-DD, opcional)"); due=self._entry(body,"Fecha límite de pago (AAAA-MM-DD, opcional)"); historic=self._entry(body,"Saldo inicial histórico COP","0"); historic_date=self._entry(body,"Fecha del saldo histórico (AAAA-MM-DD, opcional)"); notes=self._entry(body,"Notas (opcional)")
        def save()->None:
            self.service.crear_tarjeta(self._value(name),self._value(holder),self._value(limit),self._value(minimum),self._value(rate),self._value(historic),self._value(historic_date) or None,banco=self._value(bank),tipo=self._value(kind),ultimos_4=self._value(last4),fecha_corte=self._value(cut),fecha_pago=self._value(due),notas=self._value(notes)); d.destroy(); self.show_cards()
        ctk.CTkButton(body,text="Crear tarjeta",height=40,fg_color=T.PRIMARY,command=lambda:self._run(save,"Tarjeta creada")).pack(fill="x",padx=18,pady=22)

    def show_card_detail(self,tarjeta_id:int)->None:
        detalle=self.service.tarjeta_detalle(tarjeta_id); tarjeta=detalle["tarjeta"]; self._clear("Tarjetas","Tarjetas"); self._heading(f"▣ {tarjeta['nombre']}",f"Titular: {NOMBRES[tarjeta['propietario']]} · {tarjeta.get('banco') or 'Entidad no registrada'} · {tarjeta.get('tipo') or 'Tarjeta de crédito'}")
        header=ctk.CTkFrame(self.content,fg_color="transparent");header.grid(row=0,column=3,sticky="e",padx=22,pady=(15,0)); ctk.CTkButton(header,text="← Tarjetas",height=32,fg_color=T.ALT,text_color=T.TXT,command=self.show_cards).pack(side="left",padx=4);ctk.CTkButton(header,text="✎ Editar",height=32,fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_edit_dialog(tarjeta)).pack(side="left",padx=4)
        self._card(2,0,"Deuda actual",dinero(tarjeta["saldo_deuda"]),"Lo necesario para salir de la deuda",T.WARN);self._card(2,1,"Cupo disponible",dinero(tarjeta["cupo_disponible"]),f"Cupo total {dinero(tarjeta['cupo_total'])}",T.OK);self._card(2,2,"Utilización",f"{tarjeta['utilizacion']:.0%}","Señal interna, no una regla absoluta",T.BAD if tarjeta["utilizacion"]>=.70 else T.OK);self._card(2,3,"Pago mínimo",dinero(tarjeta["pago_minimo"]),f"Próximo pago: {tarjeta.get('fecha_pago') or 'No registrada'}",T.PRIMARY)
        self._card(3,0,"Interés mensual",f"{tarjeta['interes_mensual']:.2f}%",f"Estimación mensual: {dinero(tarjeta['interes_estimado'])}",T.WARN);self._card(3,1,"Total pagado",dinero(tarjeta["total_pagado"]),"Aportes registrados",T.SAVE);self._card(3,2,"Compras pendientes",str(tarjeta["compras_pendientes"]),f"Cuotas pendientes aproximadas: {tarjeta['cuotas_pendientes']}",T.PRIMARY);self._card(3,3,"Deuda histórica",dinero(tarjeta["deuda_historica"]),"Atribución provisional al titular",T.MUTED)
        uso=self._panel(4,0,4);ctk.CTkLabel(uso,text="UTILIZACIÓN DEL CUPO",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(15,4)); bar=ctk.CTkProgressBar(uso,height=10,progress_color=T.BAD if tarjeta["utilizacion"]>=.70 else T.OK,fg_color=T.ALT);bar.pack(fill="x",padx=18);bar.set(min(tarjeta["utilizacion"],1));ctk.CTkLabel(uso,text=f"{tarjeta['utilizacion']:.0%} utilizado · {dinero(tarjeta['cupo_disponible'])} disponibles. Menos de 30% es una señal saludable; 70% o más merece atención dentro de LÚMINA.",text_color=T.MUTED,wraplength=900).pack(anchor="w",padx=18,pady=(7,15))
        dist=self._panel(5,0,2);ctk.CTkLabel(dist,text="¿QUIÉN DEBE QUÉ?",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(15,5));ctk.CTkLabel(dist,text=f"Samuel  {dinero(tarjeta['deuda_persona1'])}\nSara       {dinero(tarjeta['deuda_persona2'])}",font=ui.serif(17),justify="left").pack(anchor="w",padx=18);ctk.CTkLabel(dist,text=f"Pagado: Samuel {dinero(tarjeta['pagado_persona1'])} · Sara {dinero(tarjeta['pagado_persona2'])}\nTitular legal y responsable económico son conceptos separados.",text_color=T.MUTED,wraplength=430,justify="left").pack(anchor="w",padx=18,pady=(6,16))
        monthly=detalle["mensual"]; mesp=self._panel(5,2,2);ctk.CTkLabel(mesp,text="ESTE MES",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(15,5));ctk.CTkLabel(mesp,text=f"Compras +{dinero(monthly['compras'])}   ·   Intereses +{dinero(monthly['intereses'])}\nCargos +{dinero(monthly['cargos'])}      ·   Pagos −{dinero(monthly['pagos'])}\nVariación {'+' if monthly['variacion']>=0 else '−'}{dinero(abs(monthly['variacion']))}",justify="left",font=ui.fuente(14,"bold")).pack(anchor="w",padx=18);ctk.CTkLabel(mesp,text="Lectura basada en los movimientos registrados del período.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(7,16))
        actions=self._panel(6,0,4);ctk.CTkLabel(actions,text="ACCIONES",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(15,7));row=ctk.CTkFrame(actions,fg_color="transparent");row.pack(fill="x",padx=18,pady=(0,16));ctk.CTkButton(row,text="＋ Registrar compra",fg_color=T.PRIMARY,command=lambda:self.card_purchase_dialog(tarjeta)).pack(side="left",padx=(0,7));ctk.CTkButton(row,text="↓ Registrar pago",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_payment_dialog(tarjeta)).pack(side="left",padx=7);ctk.CTkButton(row,text="＋ Registrar interés",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_movement_dialog(tarjeta,"INTERES")).pack(side="left",padx=7);ctk.CTkButton(row,text="＋ Registrar cargo",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_movement_dialog(tarjeta,"CARGO")).pack(side="left",padx=7);ctk.CTkButton(row,text="Ajustar saldo",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_adjust_dialog(tarjeta)).pack(side="left",padx=7)
        sim=self._panel(7,0,4);ctk.CTkLabel(sim,text="¿QUÉ PASA SI PAGAN...?",font=ui.serif(17)).pack(anchor="w",padx=18,pady=(15,2));ctk.CTkLabel(sim,text="Simulación pura: no modifica SQLite.",text_color=T.MUTED).pack(anchor="w",padx=18); extra=self._entry(sim,"Pago adicional COP","500000"); result=ctk.CTkLabel(sim,text="",text_color=T.MUTED,justify="left",wraplength=840);result.pack(anchor="w",padx=18,pady=(8,4))
        def simulate()->None:
            x=self.service.simular_pago_tarjeta(tarjeta_id,self._value(extra));result.configure(text=f"Deuda {dinero(x['deuda_actual'])} → {dinero(x['deuda_despues'])} · utilización {x['utilizacion_actual']:.0%} → {x['utilizacion_despues']:.0%} · cupo liberado {dinero(x['cupo_liberado'])}\n{x['nota']}")
        ctk.CTkButton(sim,text="Simular pago",height=30,fg_color=T.PRIMARY,command=lambda:self._run(simulate,"Simulación lista")).pack(anchor="w",padx=18,pady=(0,16))
        self._section(8,"Historial de tarjeta")
        if not detalle["historial"]:self._empty(9,"Aún no hay movimientos","Las compras, pagos, intereses, cargos y ajustes aparecerán aquí.",lambda:self.card_purchase_dialog(tarjeta));return
        for i,event in enumerate(detalle["historial"][:60]):
            p=self._panel(9+i,0,4);activo=event["estado"]=="ACTIVO"; color=T.OK if event["tipo"]=="PAGO" else T.WARN if event["tipo"] in ("INTERES","CARGO") else T.BAD;ctk.CTkLabel(p,text=f"{'●' if activo else '↺'}  {event['tipo'].title()}  ·  {event['fecha']}",text_color=color,font=ui.fuente(13,"bold")).pack(anchor="w",padx=18,pady=(12,2));ctk.CTkLabel(p,text=event["descripcion"] + (" · Movimiento reversado" if not activo else ""),text_color=T.TXT if activo else T.MUTED).pack(anchor="w",padx=18)
            detail=f"{'+' if event['efecto_deuda']>=0 else '−'}{dinero(abs(event['monto']))} · efecto en deuda: {'+' if event['efecto_deuda']>=0 else '−'}{dinero(abs(event['efecto_deuda']))}";ctk.CTkLabel(p,text=detail,text_color=T.MUTED).pack(anchor="w",padx=18,pady=(2,2))
            if event["tipo"]=="PAGO":
                origen=f"Aporte real: Samuel {dinero(event.get('aporte_p1',0))} · Sara {dinero(event.get('aporte_p2',0))}"
            else:
                origen=f"Responsabilidad: Samuel {dinero(event.get('monto_p1',0))} · Sara {dinero(event.get('monto_p2',0))}"
                if event.get("valor_pendiente") is not None: origen+=f" · pendiente {dinero(event['valor_pendiente'])}"
            ctk.CTkLabel(p,text=origen,text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,10))

    def _card_distribution(self,total:str,responsabilidad:str,p1:str,p2:str)->tuple[str,str]:
        return self._distribution(total,responsabilidad,p1,p2)

    def card_purchase_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"Compra · {tarjeta['nombre']}");d.geometry("500x700");d.grab_set();body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="＋ Registrar compra",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2));ctk.CTkLabel(body,text=f"{tarjeta['nombre']} · disponible {dinero(tarjeta['cupo_disponible'])}",text_color=T.MUTED).pack(anchor="w",padx=18)
        ctk.CTkLabel(body,text="El banco cobra el total al titular de la tarjeta, pero aquí defines quién asume la compra dentro de la pareja. Si es compartida, la parte de Sara quedará registrada como responsabilidad de Sara.",text_color=T.MUTED,wraplength=440,justify="left").pack(anchor="w",padx=18,pady=(8,3))
        date=self._entry(body,"Fecha",dt.date.today().isoformat());desc=self._entry(body,"Descripción");category=self._entry(body,"Categoría");total=self._entry(body,"Monto total COP");installments=self._entry(body,"Cuotas","1");resp=self._entry(body,"Quién asume la compra","Compartido",["Samuel","Sara","Compartido","Personalizado","Porcentaje personalizado"]);p1=self._entry(body,"Samuel asume COP (o %)","0");p2=self._entry(body,"Sara asume COP (o %)","0")
        def save()->None:
            modo=self._value(resp); a,b=self._card_distribution(self._value(total),modo,self._value(p1) if modo=="Porcentaje personalizado" or self._value(p1)!="0" else "",self._value(p2) if modo=="Porcentaje personalizado" or self._value(p2)!="0" else "");monto=parsear_dinero(self._value(total));after=tarjeta["saldo_deuda"]+monto
            if not messagebox.askyesno("Confirmar compra",f"Vas a registrar una compra de {dinero(monto)} en {tarjeta['nombre']}.\nLa deuda pasará de {dinero(tarjeta['saldo_deuda'])} a {dinero(after)}.\n\n¿Registrar compra?",parent=d):return
            self.service.crear_gasto({"mes":self.selected_month,"nombre":self._value(desc),"categoria":self._value(category),"valor":self._value(total),"fecha":self._value(date),"metodo":"Tarjeta","pagador":NOMBRES[tarjeta['propietario']],"responsabilidad":self._value(resp),"monto_p1":a,"monto_p2":b,"tarjeta_id":str(tarjeta['id']),"cuotas":self._value(installments),"prioridad":"Obligatorio"});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Registrar compra",fg_color=T.PRIMARY,command=lambda:self._run(save,"Compra registrada")).pack(fill="x",padx=18,pady=22)

    def card_payment_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"Pago · {tarjeta['nombre']}");d.geometry("550x690");d.grab_set();body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="↓ Registrar pago",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(20,2));ctk.CTkLabel(body,text=f"Deuda que cobra el banco al titular: {dinero(tarjeta['saldo_deuda'])}",text_color=T.MUTED).pack(anchor="w",padx=18)
        estado=ctk.CTkFrame(body,fg_color=T.ALT,corner_radius=Espacio.RADIO_SM);estado.pack(fill="x",padx=18,pady=(12,3))
        ctk.CTkLabel(estado,text="RESPONSABILIDAD ECONÓMICA PENDIENTE",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=13,pady=(10,2))
        ctk.CTkLabel(estado,text=f"Samuel debe {dinero(tarjeta['deuda_persona1'])}  ·  Sara debe {dinero(tarjeta['deuda_persona2'])}",font=ui.fuente(15,"bold")).pack(anchor="w",padx=13)
        ctk.CTkLabel(estado,text="Esto viene de las compras y no cambia por quién haga la transferencia.",text_color=T.MUTED,wraplength=455,justify="left").pack(anchor="w",padx=13,pady=(2,10))
        date=self._entry(body,"Fecha",dt.date.today().isoformat());total=self._entry(body,"Monto total del pago COP");payer=self._entry(body,"Quién hizo la transferencia","Samuel" if tarjeta["propietario"]==SAMUEL else "Sara",["Samuel","Sara"])
        reparto=self._entry(body,"Cómo se cubrió este pago","Lo pagó quien hizo la transferencia",["Lo pagó quien hizo la transferencia","50/50 entre Samuel y Sara","Según lo que cada quien debe","Monto personalizado"])
        p1=self._entry(body,"Samuel puso COP","0");p2=self._entry(body,"Sara puso COP","0");preview=ctk.CTkLabel(body,text="Escribe el total para calcular los aportes.",text_color=T.MUTED,wraplength=460,justify="left");preview.pack(anchor="w",padx=18,pady=(8,2));concept=self._entry(body,"Motivo (opcional)")
        def poner(campo:Any,valor:int)->None:
            campo.delete(0,"end");campo.insert(0,str(valor))
        def actualizar_aportes(*_:Any)->None:
            try: monto=parsear_dinero(self._value(total))
            except ValueError:
                preview.configure(text="Escribe un monto total válido para preparar los aportes."); return
            modo=self._value(reparto)
            if modo=="Lo pagó quien hizo la transferencia":
                samuel,sara=(monto,0) if self._value(payer)=="Samuel" else (0,monto)
            elif modo=="50/50 entre Samuel y Sara":
                samuel,sara=monto//2,monto-monto//2
            elif modo=="Según lo que cada quien debe":
                deuda_s,deuda_sa=tarjeta["deuda_persona1"],tarjeta["deuda_persona2"]
                if deuda_s+deuda_sa:
                    samuel=round(monto*deuda_s/(deuda_s+deuda_sa)); sara=monto-samuel
                else: samuel,sara=monto//2,monto-monto//2
            else:
                preview.configure(text="Indica manualmente cuánto puso cada uno. Los dos montos deben sumar exactamente el pago."); return
            poner(p1,samuel);poner(p2,sara)
            preview.configure(text=f"Aporte real: Samuel {dinero(samuel)} · Sara {dinero(sara)}. La responsabilidad de las compras se conserva arriba.")
        payer.configure(command=actualizar_aportes); reparto.configure(command=actualizar_aportes); total.bind("<FocusOut>",actualizar_aportes)
        def save()->None:
            monto=parsear_dinero(self._value(total));s=parsear_dinero(self._value(p1));sa=parsear_dinero(self._value(p2))
            if s+sa!=monto:raise ValueError("Samuel + Sara deben sumar exactamente el pago.")
            if not messagebox.askyesno("Confirmar pago",f"Vas a registrar un pago de {dinero(monto)} en {tarjeta['nombre']}.\nAporte real: Samuel {dinero(s)} · Sara {dinero(sa)}\nLa responsabilidad de las compras no cambia.\nLa deuda bancaria pasará de {dinero(tarjeta['saldo_deuda'])} a {dinero(tarjeta['saldo_deuda']-monto)}.\n\n¿Registrar pago?",parent=d):return
            self.service.crear_pago_tarjeta({"mes":self.selected_month,"fecha":self._value(date),"tarjeta_id":str(tarjeta['id']),"monto":self._value(total),"pagador":self._value(payer),"aporte_p1":self._value(p1),"aporte_p2":self._value(p2),"concepto":self._value(concept)});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Registrar pago",fg_color=T.PRIMARY,command=lambda:self._run(save,"Pago registrado")).pack(fill="x",padx=18,pady=22)

    def card_movement_dialog(self,tarjeta:dict[str,Any],tipo:str)->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"{tipo.title()} · {tarjeta['nombre']}");d.geometry("480x560");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14);label="interés financiero" if tipo=="INTERES" else "cargo o comisión";ctk.CTkLabel(body,text=f"＋ Registrar {label}",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(20,2));ctk.CTkLabel(body,text="Aumenta la deuda; no se registra como compra normal.",text_color=T.MUTED).pack(anchor="w",padx=18)
        date=self._entry(body,"Fecha",dt.date.today().isoformat());amount=self._entry(body,"Monto COP");desc=self._entry(body,"Concepto");period=self._entry(body,"Período (AAAA-MM)",self.selected_month);resp=self._entry(body,"Responsable","Compartido",["Samuel","Sara","Compartido","Personalizado","Porcentaje personalizado"]);p1=self._entry(body,"Samuel asume COP (o %)","0");p2=self._entry(body,"Sara asume COP (o %)","0")
        def save()->None:
            modo=self._value(resp); a,b=self._card_distribution(self._value(amount),modo,self._value(p1) if modo=="Porcentaje personalizado" or self._value(p1)!="0" else "",self._value(p2) if modo=="Porcentaje personalizado" or self._value(p2)!="0" else "");monto=parsear_dinero(self._value(amount))
            if not messagebox.askyesno("Confirmar movimiento",f"Vas a registrar {label} por {dinero(monto)}.\nLa deuda pasará de {dinero(tarjeta['saldo_deuda'])} a {dinero(tarjeta['saldo_deuda']+monto)}.\n\n¿Continuar?",parent=d):return
            self.service.crear_movimiento_tarjeta({"mes":self.selected_month,"fecha":self._value(date),"tarjeta_id":str(tarjeta['id']),"tipo":tipo,"monto":self._value(amount),"descripcion":self._value(desc),"periodo":self._value(period),"responsabilidad":self._value(resp),"monto_p1":a,"monto_p2":b});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text=f"Registrar {label}",fg_color=T.PRIMARY,command=lambda:self._run(save,"Movimiento registrado")).pack(fill="x",padx=18,pady=22)

    def card_adjust_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"Ajustar saldo · {tarjeta['nombre']}");d.geometry("460x440");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="Ajustar saldo",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(20,2));ctk.CTkLabel(body,text=f"Saldo actual: {dinero(tarjeta['saldo_deuda'])}. Esto registra una corrección auditada, no borra historial.",text_color=T.MUTED,wraplength=400,justify="left").pack(anchor="w",padx=18)
        saldo=self._entry(body,"Nuevo saldo COP");reason=self._entry(body,"Motivo");date=self._entry(body,"Fecha",dt.date.today().isoformat());person=self._entry(body,"Quién realizó el ajuste","Samuel",["Samuel","Sara"])
        def save()->None:
            nuevo=parsear_dinero(self._value(saldo))
            if not messagebox.askyesno("Confirmar ajuste",f"Vas a ajustar el saldo de {dinero(tarjeta['saldo_deuda'])} a {dinero(nuevo)}.\nMotivo: {self._value(reason)}\n\n¿Registrar ajuste?",parent=d):return
            self.service.ajustar_saldo_tarjeta(tarjeta['id'],self._value(saldo),self._value(reason),self._value(date),self._value(person));d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Registrar ajuste",fg_color=T.PRIMARY,command=lambda:self._run(save,"Ajuste registrado")).pack(fill="x",padx=18,pady=22)

    def card_edit_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"Editar · {tarjeta['nombre']}");d.geometry("520x700");d.grab_set();body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="✎ Editar tarjeta",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2));ctk.CTkLabel(body,text="La deuda no se modifica aquí. Para corregirla, usa Ajustar saldo.",text_color=T.MUTED).pack(anchor="w",padx=18)
        name=self._entry(body,"Nombre",tarjeta["nombre"]);bank=self._entry(body,"Banco",tarjeta.get("banco") or "");kind=self._entry(body,"Tipo",tarjeta.get("tipo") or "Crédito");holder=self._entry(body,"Titular",NOMBRES[tarjeta["propietario"]],["Samuel","Sara"]);limit=self._entry(body,"Cupo total COP",str(tarjeta["cupo_total"]));minimum=self._entry(body,"Pago mínimo COP",str(tarjeta["pago_minimo"]));rate=self._entry(body,"Interés mensual (%)",str(tarjeta["interes_mensual"]));last4=self._entry(body,"Últimos 4",tarjeta.get("ultimos_4") or "");cut=self._entry(body,"Fecha de corte",tarjeta.get("fecha_corte") or "");due=self._entry(body,"Fecha límite de pago",tarjeta.get("fecha_pago") or "");state=self._entry(body,"Estado","Activa" if tarjeta["activa"] else "Inactiva",["Activa","Inactiva"]);notes=self._entry(body,"Notas",tarjeta.get("notas") or "")
        def save()->None:
            self.service.editar_tarjeta(tarjeta["id"],{"nombre":self._value(name),"banco":self._value(bank),"tipo":self._value(kind),"propietario":self._value(holder),"cupo":self._value(limit),"minimo":self._value(minimum),"interes":self._value(rate),"ultimos_4":self._value(last4),"fecha_corte":self._value(cut),"fecha_pago":self._value(due),"activa":self._value(state),"notas":self._value(notes)});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Guardar cambios",fg_color=T.PRIMARY,command=lambda:self._run(save,"Tarjeta actualizada")).pack(fill="x",padx=18,pady=22)

    def show_personal_dashboard(self, persona:str)->None:
        etiqueta=NOMBRES[persona]; activo=f"Mi dinero · {etiqueta}"; self._clear(activo,activo); d=self.service.dashboard_personal(persona,self.selected_month)
        self.add_button.configure(text="＋ Registrar movimiento",command=self.show_transactions)

        # Portada personal: una cifra responde la pregunta y el resto la contextualiza.
        portada=ui.fila(self.content); self._place(portada,0,0,4)
        portada.grid_columnconfigure(0,weight=65); portada.grid_columnconfigure(1,weight=35)
        principal=ui.fila(portada); principal.grid(row=0,column=0,sticky="nsew",padx=(0,Espacio.XXL),pady=(Espacio.XL,0))
        resumen=ui.fila(portada); resumen.grid(row=0,column=1,sticky="nsew",pady=(Espacio.XL,0))
        ui.etiqueta(principal,f"PERFIL PERSONAL  /  {etiqueta.upper()}",color=T.PRIMARY).pack(anchor="w")
        ctk.CTkLabel(principal,text=f"Caja de {etiqueta}",font=Tipo.seccion(),text_color=T.TXT).pack(anchor="w",pady=(Espacio.LG,0))
        ctk.CTkLabel(principal,text=dinero(d["caja_disponible"]),font=ui.serif(60),text_color=T.TXT,anchor="w").pack(anchor="w",pady=(0,Espacio.XS))
        ui.cuerpo(principal,"Lo que realmente entró menos lo que salió de tu bolsillo este mes.",ancho=580,color=T.MUTED).pack(anchor="w")
        acciones=ui.fila(principal); acciones.pack(anchor="w",pady=(Espacio.LG,0))
        ui.boton(acciones,"Reconciliar saldo",lambda:self.reconciliation_dialog(persona),tono="sutil",alto=32).pack(side="left")
        ui.boton(acciones,"Ver movimientos",self.show_transactions,tono="enlace",alto=32).pack(side="left",padx=Espacio.MD)

        def dato_personal(titulo:str,valor:str,detalle:str,color:Any=None)->None:
            ui.regla(resumen); ui.etiqueta(resumen,titulo).pack(anchor="w",pady=(Espacio.MD,Espacio.XS))
            ctk.CTkLabel(resumen,text=valor,font=Tipo.numero(),text_color=color or T.TXT,anchor="w").pack(anchor="w")
            ui.ayuda(resumen,detalle,ancho=330).pack(anchor="w",pady=(2,Espacio.MD))
        dato_personal("Ingresos este mes",dinero(d["ingresos"]),f"Mes anterior: {dinero(d['ingresos_anterior'])}")
        dato_personal("Consumo que asumes",dinero(d["gastos"]),f"Mes anterior: {dinero(d['gastos_anterior'])}",T.WARN)
        dato_personal("Deuda que asumes",dinero(d["deuda"]),"Por responsabilidad económica, no por titularidad.",T.WARN)
        dato_personal("En tus cajitas",dinero(d["ahorrado"]),f"Ahorro del mes: {dinero(d['ahorro_mes'])}",T.SAVE)

        detalle=ui.fila(self.content); self._place(detalle,1,0,4)
        detalle.grid_columnconfigure(0,weight=65); detalle.grid_columnconfigure(1,weight=35)
        gastos=ui.fila(detalle); gastos.grid(row=0,column=0,sticky="nsew",padx=(0,Espacio.XXL),pady=(Espacio.XXL,0))
        tarjetas=ui.fila(detalle); tarjetas.grid(row=0,column=1,sticky="nsew",pady=(Espacio.XXL,0))
        ui.titulo_seccion(gastos,f"Gastos que asume {etiqueta}","La parte del gasto que te corresponde este mes.").pack(anchor="w",pady=(0,Espacio.SM))
        if d["gastos_por_categoria"]:
            for gasto in d["gastos_por_categoria"][:5]:
                ui.regla(gastos); fila=ui.fila(gastos); fila.pack(fill="x",pady=Espacio.SM)
                ctk.CTkLabel(fila,text=gasto["categoria"],font=ui.fuente(13,"bold"),text_color=T.TXT).pack(side="left")
                ctk.CTkLabel(fila,text=dinero(gasto["valor"]),font=ui.fuente(13,"bold"),text_color=T.TXT).pack(side="right")
            ui.regla(gastos)
        else: ui.ayuda(gastos,"Aún no hay gastos atribuidos este mes.",ancho=560).pack(anchor="w",pady=Espacio.SM)
        ui.titulo_seccion(tarjetas,"Tarjetas y responsabilidad").pack(anchor="w",pady=(0,Espacio.SM))
        if d["tarjetas"]:
            for tarjeta in d["tarjetas"]:
                ui.regla(tarjetas)
                ctk.CTkLabel(tarjetas,text=tarjeta["nombre"],font=ui.fuente(13,"bold"),text_color=T.TXT).pack(anchor="w",pady=(Espacio.SM,2))
                ui.ayuda(tarjetas,f"Deuda tuya {dinero(tarjeta['deuda_personal'])} · uso {tarjeta['utilizacion']:.0%} · mínimo {dinero(tarjeta['pago_minimo_personal'])}",ancho=340).pack(anchor="w",pady=(0,Espacio.SM))
            ui.regla(tarjetas)
        else: ui.ayuda(tarjetas,"No hay tarjetas asociadas a tu responsabilidad.",ancho=340).pack(anchor="w",pady=Espacio.SM)

        fila_cajitas=2
        self._section(fila_cajitas,"Mis cajitas","Dinero separado por un propósito."); fila_cajitas+=1
        if not d["cajitas"]:
            self._empty(fila_cajitas,"Aún no tienes cajitas personales",d["nota_cajitas_compartidas"],self.show_savings); fila_cajitas+=1
        else:
            caja_lista=ui.fila(self.content); self._place(caja_lista,fila_cajitas,0,4); fila_cajitas+=1
            for caja in d["cajitas"]:
                ui.regla(caja_lista); bloque=ui.fila(caja_lista); bloque.pack(fill="x",pady=Espacio.MD)
                cabecera=ui.fila(bloque); cabecera.pack(fill="x")
                ctk.CTkLabel(cabecera,text=caja["nombre"],font=ui.fuente(13,"bold"),text_color=T.TXT).pack(side="left")
                ctk.CTkLabel(cabecera,text=dinero(caja["saldo"]),font=Tipo.numero_pequeno(),text_color=T.SAVE).pack(side="right")
                if caja["meta"]:
                    ui.ayuda(bloque,f"Meta {dinero(caja['meta'])} · faltan {dinero(caja['faltante'])}",ancho=700).pack(anchor="w",pady=(2,Espacio.XS))
                    barra=ctk.CTkProgressBar(bloque,height=4,progress_color=T.PRIMARY,fg_color=T.ALT,corner_radius=1)
                    barra.pack(fill="x"); barra.set(max(0.0,min(caja["progreso"] or 0,1.0)))
            ui.regla(caja_lista)
        if d["metas"]:
            self._section(fila_cajitas,"Mis metas"); fila_cajitas+=1
            for i,meta in enumerate(d["metas"]):
                self._metric(fila_cajitas+i,0,meta["nombre"],dinero(meta["actual"]),f"Objetivo {dinero(meta['monto_objetivo'])} · faltan {dinero(meta['faltante'])}",T.SAVE,4)

    def reconciliation_dialog(self,persona:str)->None:
        r=self.service.reconciliacion_personal(persona,self.selected_month);nombre=NOMBRES[persona]
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"Reconciliación · {nombre}");d.geometry("550x620");d.grab_set();body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text=f"Reconciliación de {nombre}",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
        ctk.CTkLabel(body,text="Compara responsabilidad económica con caja que realmente salió. No modifica ningún movimiento.",text_color=T.MUTED,wraplength=470,justify="left").pack(anchor="w",padx=18,pady=(0,14))
        ctk.CTkLabel(body,text=f"Caja reconstruida: {dinero(r['saldo_reconstruido'])}\nLiquidez económica anterior: {dinero(r['saldo_mostrado_anterior'])}\nDiferencia explicada: {dinero(r['diferencia'])}",font=ui.fuente(16,"bold"),justify="left",text_color=T.PRIMARY).pack(anchor="w",padx=18,pady=(0,12))
        ctk.CTkLabel(body,text="MOVIMIENTOS DE CAJA",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(4,3))
        for item in r["componentes"]: ctk.CTkLabel(body,text=f"{item['concepto']}: {dinero(item['efecto_caja'])}",text_color=T.TXT).pack(anchor="w",padx=18,pady=3)
        if r["causas"]:
            ctk.CTkLabel(body,text="POR QUÉ LOS DOS CONCEPTOS DIFIEREN",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(16,3))
            for causa in r["causas"]: ctk.CTkLabel(body,text=f"{dinero(causa['monto'])} · {causa['detalle']}",wraplength=470,justify="left",text_color=T.TXT).pack(anchor="w",padx=18,pady=4)
        ctk.CTkLabel(body,text=r["nota"],wraplength=470,justify="left",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,20))

    def show_savings(self)->None:
        """Cajitas y metas con progreso real y estado explicado."""
        self._clear("Cajitas","Cajitas")
        self._heading("Cajitas y metas","Dinero separado por propósito, con su avance real frente a la fecha objetivo.")
        self.add_button.configure(text="＋ Nueva cajita",command=self.savings_create_dialog)
        ahorro=self.service.inteligencia_ahorro(self.selected_month)
        fondos=ahorro["fondos"]
        emergencia=ahorro["emergencia"]
        self._metric(2,0,"Total reservado",dinero(ahorro["total_reservado"]),f"En {len(fondos)} cajita(s)",T.SAVE)
        self._metric(2,1,"Fondo de emergencia",dinero(emergencia["current"]),
                     f"Cubre {emergencia['coverage_months']:.1f} de {emergencia['target_months']} meses de gasto obligatorio",
                     T.OK if emergencia["coverage_months"]>=1 else T.WARN)
        self._metric(2,2,"Aporte neto del mes",dinero(ahorro["aporte_neto_mes"]),
                     f"Retiros: {dinero(ahorro['retiros_mes'])}" if ahorro["retiros_mes"] else "Sin retiros este mes",
                     T.OK if ahorro["aporte_neto_mes"]>=0 else T.BAD)
        self._metric(2,3,"Aporte que piden las metas",dinero(ahorro["aporte_requerido_total"]),
                     f"El flujo del mes deja {dinero(ahorro['flujo_disponible'])}",
                     T.WARN if ahorro["conflicto_de_metas"] else T.OK)
        if ahorro["señales"]:
            caja=self._abierto(3,0,4)
            ui.etiqueta(caja,"Lo que veo").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
            for señal in ahorro["señales"][:4]:
                ui.cuerpo(caja,"· "+señal,ancho=840).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=2)
            ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()
        fila=4 if ahorro["señales"] else 3
        self._section(fila,"Metas","Progreso frente a la fecha objetivo, no solo el saldo."); fila+=1
        metas=ahorro["metas"]
        if not metas:
            self._place(ui.estado_vacio(self.content,"Todavía no hay metas",
                        "Una meta convierte un saldo en un objetivo con fecha: así puedo decirles si van a tiempo.",
                        accion="Crear una cajita",comando=self.savings_create_dialog,icono="◈"),fila,0,4)
            fila+=1
        else:
            caja=self._open_panel(fila,0,4); fila+=1
            estados={"cumplida":("Cumplida","positiva"),"en_ritmo":("En ritmo","positiva"),
                     "atrasada":("Atrasada","media"),"inactiva":("Sin aportes","media"),("sin_fecha"):("Sin fecha","info")}
            for meta in metas:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.SM)
                cabecera=ui.fila(bloque); cabecera.pack(fill="x")
                ctk.CTkLabel(cabecera,text=meta["nombre"],font=ui.fuente(14,"bold"),text_color=T.TXT).pack(side="left")
                rotulo,tono=estados.get(meta["estado"],("—","info"))
                ui.insignia(cabecera,rotulo,tono=tono).pack(side="left",padx=10)
                ctk.CTkLabel(cabecera,text=f"{dinero(meta['actual'])} de {dinero(meta['objetivo'])}",font=Tipo.ayuda(),text_color=T.MUTED).pack(side="right")
                medidor=ctk.CTkProgressBar(bloque,height=8,progress_color=T.SAVE,fg_color=T.ALT,corner_radius=8)
                medidor.pack(fill="x",pady=(6,3)); medidor.set(max(0.0,min(meta["progreso"] or 0,1.0)))
                detalles=[f"{(meta['progreso'] or 0):.0%} completado"]
                if meta.get("fecha_objetivo"): detalles.append(f"fecha objetivo {meta['fecha_objetivo']}")
                if meta.get("necesario_mensual"): detalles.append(f"necesita {dinero(meta['necesario_mensual'])}/mes")
                if meta.get("aporte_promedio"): detalles.append(f"aportan {dinero(meta['aporte_promedio'])}/mes")
                ui.ayuda(bloque,"  ·  ".join(detalles),ancho=820).pack(anchor="w")
            ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()
        self._section(fila,"Cajitas"); fila+=1
        if not fondos:
            self._place(ui.estado_vacio(self.content,"Aún no tienen cajitas",
                        "Cada reserva empieza con una intención: emergencia, viaje, universidad.",
                        accion="＋ Crear cajita",comando=self.savings_create_dialog,icono="◈"),fila,0,4); return
        for indice,fondo in enumerate(fondos):
            caja=self._open_panel(fila+indice//2,indice%2*2,2)
            cabecera=ui.fila(caja); cabecera.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ctk.CTkLabel(cabecera,text=f"{fondo.get('icono','◈')}  {fondo['nombre']}",font=Tipo.tarjeta(),text_color=T.TXT).pack(side="left")
            titular=fondo.get("titular",fondo["propietario"])
            ui.insignia(cabecera,NOMBRES.get(titular,"Compartido"),tono="info").pack(side="right")
            ctk.CTkLabel(caja,text=dinero(fondo["saldo"]),font=Tipo.numero(),text_color=T.SAVE).pack(anchor="w",padx=Espacio.PANEL_PAD)
            if fondo["meta"]:
                ui.barra(caja,fondo["saldo"]/fondo["meta"] if fondo["meta"] else 0,color=T.SAVE,pad=(Espacio.PANEL_PAD,Espacio.SM))
                ui.ayuda(caja,f"Meta {dinero(fondo['meta'])} · {min(fondo['saldo']/fondo['meta'],1):.0%} alcanzado",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(4,0))
            ui.dato(caja,"Aportes",f"Samuel {dinero(fondo['aporte_p1'])} · Sara {dinero(fondo['aporte_p2'])}",color=T.MUTED)
            ui.boton(caja,"Mover dinero",lambda f=fondo:self.savings_movement_dialog(f),tono="suave",alto=32).pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.SM,Espacio.MD))

    def savings_create_dialog(self)->None:
        """Crea una cajita; las metas con fecha se administran desde la cajita."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Nueva cajita"); d.geometry("480x480"); d.minsize(420,420); d.grab_set()
        body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="＋ Nueva cajita",font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,2))
        ui.ayuda(body,"Separar el dinero por propósito evita que la reserva se gaste sin decidirlo.",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD)
        contenedor=ui.fila(body); contenedor.pack(fill="x",padx=Espacio.PANEL_PAD)
        nombre=self._entry(contenedor,"Nombre","Emergencia")
        meta=self._entry(contenedor,"Meta COP (opcional)","0")
        titular=self._entry(contenedor,"Titular","Compartido",["Samuel","Sara","Compartido"])
        def crear()->None:
            dueno=SAMUEL if self._value(titular)=="Samuel" else SARA if self._value(titular)=="Sara" else "compartido"
            self.service.crear_cajita({"nombre":self._value(nombre),"meta":self._value(meta),"titular":dueno,
                                       "descripcion":"","icono":"◈"})
            d.destroy(); self.show_savings()
        ui.boton(body,"Crear cajita",lambda:self._run(crear,"Cajita creada"),alto=42).pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.LG)

    def savings_movement_dialog(self,b:dict[str,Any])->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title(f"{b['nombre']} · Movimiento");d.geometry("420x410");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14); kind=self._entry(body,"Movimiento","Ingresar dinero",["Ingresar dinero","Retirar dinero"]); amount=self._entry(body,"Monto COP");concept=self._entry(body,"Motivo");person=self._entry(body,"Quién lo realiza","Samuel",["Samuel","Sara"])
        def save()->None:self.service.movimiento_ahorro({"mes":self.selected_month,"fecha":dt.date.today().isoformat(),"ahorro_id":str(b["id"]),"tipo":"Depositar" if self._value(kind).startswith("Ingresar") else "Retirar","monto":self._value(amount),"concepto":self._value(concept),"aportante":SAMUEL if self._value(person)=="Samuel" else SARA});d.destroy();self.show_savings()
        ctk.CTkButton(body,text="Guardar movimiento",fg_color=T.PRIMARY,command=lambda:self._run(save,"Movimiento registrado")).pack(fill="x",pady=20)

    def show_fixed_expenses(self)->None:
        """Compromisos registrados, lo que Lúmina detectó y lo que no coincide."""
        self._clear("Gastos fijos","Gastos fijos")
        self._heading("Gastos fijos","Obligaciones que se repiten: arriendo, servicios, suscripciones y cuotas.")
        resumen=self.service.gastos_fijos(self.selected_month)
        cotejo=self.service.cotejar_gastos_fijos(self.selected_month)
        self.add_button.configure(text="＋ Nuevo gasto fijo",command=self.fixed_expense_create_dialog)
        porcentaje=resumen["porcentaje_ingreso"]
        self._metric(2,0,"Compromiso mensual",dinero(resumen["total_mensual_equivalente"]),
                     f"{resumen['cantidad_activos']} obligación(es) activa(s)",T.WARN)
        self._metric(2,1,"Peso sobre el ingreso",f"{porcentaje:.0%}" if porcentaje is not None else "Sin ingreso registrado",
                     "Entre más alto, menos margen queda antes de decidir",
                     T.BAD if porcentaje and porcentaje>=.5 else T.WARN if porcentaje and porcentaje>=.35 else T.OK)
        self._metric(2,2,"Responsabilidad de Samuel",dinero(resumen["total_mensual_persona1"]),"Equivalente mensual",T.PRIMARY)
        self._metric(2,3,"Responsabilidad de Sara",dinero(resumen["total_mensual_persona2"]),
                     f"Pendiente por registrar este mes: {dinero(resumen['pendiente_este_mes'])}" if resumen["pendiente_este_mes"] else "Todo registrado este mes",T.SAVE)

        avisos=cotejo["monto_desactualizado"]+cotejo["sin_cobros_recientes"]+cotejo["frecuencia_distinta"]+cotejo["responsabilidad_distinta"]
        if avisos:
            self._section(3,"Revisiones sugeridas","Lo registrado y lo que de verdad se está cobrando no coinciden.")
            caja=self._open_panel(4,0,4)
            for aviso in avisos[:6]:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=8)
                texto=ui.fila(bloque); texto.pack(side="left",fill="x",expand=True)
                ctk.CTkLabel(texto,text=aviso["nombre"],font=ui.fuente(13,"bold"),text_color=T.TXT,anchor="w").pack(anchor="w")
                ui.ayuda(texto,aviso["detalle"],ancho=660).pack(anchor="w")
                ui.boton(bloque,"Ajustar",lambda gid=aviso["gasto_fijo_id"]:self.fixed_expense_dialog(gid),tono="suave",alto=30).pack(side="right")
            ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()
        fila=5 if avisos else 3

        candidatos=[c for c in cotejo["sin_registrar"] if c["clave"] not in self._candidatos_descartados()]
        self._section(fila,"Encontrados por Lúmina",
                      "Cobros que se comportan como gastos fijos. No registro ninguno: ustedes confirman." if candidatos else "")
        fila+=1
        if not candidatos:
            self._place(ui.estado_vacio(self.content,"Sin candidatos nuevos",
                        "Cuando un cobro se repita con monto parecido durante varios meses, aparecerá aquí para que lo confirmen.",
                        icono="✓"),fila,0,4)
            fila+=1
        else:
            caja=self._open_panel(fila,0,4); fila+=1
            for candidato in candidatos[:8]:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.SM)
                cabecera=ui.fila(bloque); cabecera.pack(fill="x")
                ctk.CTkLabel(cabecera,text=candidato["nombre"],font=ui.fuente(14,"bold"),text_color=T.TXT).pack(side="left")
                ui.insignia(cabecera,f"Confianza {candidato['confianza'].lower()}",
                            tono="positiva" if candidato["confianza"]=="Alta" else "media").pack(side="left",padx=10)
                ctk.CTkLabel(cabecera,text=f"~{dinero(candidato['monto_promedio'])}/mes",font=Tipo.numero_pequeno(),text_color=T.TXT).pack(side="right")
                ui.ayuda(bloque,f"{candidato['ocurrencias']} cobros · frecuencia {candidato['frecuencia']} · {candidato['detalle']}",ancho=800).pack(anchor="w",pady=(2,0))
                acciones=ui.fila(bloque); acciones.pack(fill="x",pady=(6,0))
                ui.boton(acciones,"Confirmar como fijo",lambda c=candidato:self.fixed_expense_create_dialog(c),alto=30).pack(side="left")
                ui.boton(acciones,"Ver evidencia",lambda c=candidato:self.candidate_evidence_dialog(c),tono="suave",alto=30).pack(side="left",padx=7)
                ui.boton(acciones,"No es fijo",lambda c=candidato:self._descartar_candidato(c),tono="sutil",alto=30).pack(side="left")
                ui.separador(caja,pad=2)
            ui.ayuda(caja,f"Si los registran todos, el compromiso mensual subiría {dinero(cotejo['impacto_no_registrado'])}.",ancho=800).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))

        self._section(fila,"Obligaciones registradas"); fila+=1
        gastos=resumen["gastos"]
        if not gastos:
            self._place(ui.estado_vacio(self.content,"No hay gastos fijos registrados todavía",
                        "Registren arriendo, servicios y suscripciones para que el margen disponible sea realista.",
                        accion="＋ Crear el primero",comando=self.fixed_expense_create_dialog),fila,0,4); return
        caja=self._open_panel(fila,0,4)
        encabezado=ui.fila(caja); encabezado.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
        for texto,ancho in (("Obligación",240),("Categoría",130),("Frecuencia",110),("Titular",90),("Responsabilidad",130),("Método",90)):
            ctk.CTkLabel(encabezado,text=texto.upper(),font=Tipo.etiqueta(),text_color=T.FAINT,width=ancho,anchor="w").pack(side="left")
        ctk.CTkLabel(encabezado,text="EQUIVALENTE MENSUAL",font=Tipo.etiqueta(),text_color=T.FAINT,anchor="e").pack(side="right")
        for gasto in gastos:
            linea=ctk.CTkFrame(caja,fg_color=T.ALT if gasto["activo"] else "transparent",corner_radius=Espacio.RADIO_SM)
            linea.pack(fill="x",padx=Espacio.MD,pady=3)
            cuerpo=ui.fila(linea); cuerpo.pack(fill="x",padx=Espacio.SM,pady=Espacio.SM)
            nombre=gasto["nombre"]+("" if gasto["activo"] else "  · inactivo")
            ctk.CTkLabel(cuerpo,text=nombre,font=ui.fuente(13,"bold"),text_color=T.TXT if gasto["activo"] else T.MUTED,width=240,anchor="w").pack(side="left")
            ctk.CTkLabel(cuerpo,text=gasto["categoria"],font=Tipo.ayuda(),text_color=T.MUTED,width=130,anchor="w").pack(side="left")
            ctk.CTkLabel(cuerpo,text=gasto["frecuencia"].capitalize(),font=Tipo.ayuda(),text_color=T.MUTED,width=110,anchor="w").pack(side="left")
            ctk.CTkLabel(cuerpo,text=NOMBRES[gasto["propietario"]],font=Tipo.ayuda(),text_color=T.MUTED,width=90,anchor="w").pack(side="left")
            responsabilidad={"persona1":"Samuel","persona2":"Sara","compartido":"Compartido"}[gasto["responsabilidad"]]
            ctk.CTkLabel(cuerpo,text=responsabilidad,font=Tipo.ayuda(),text_color=T.MUTED,width=130,anchor="w").pack(side="left")
            metodo={"efectivo":"Efectivo","debito":"Débito","tarjeta":"Tarjeta"}[gasto["metodo_pago"]]
            ctk.CTkLabel(cuerpo,text=metodo,font=Tipo.ayuda(),text_color=T.MUTED,width=90,anchor="w").pack(side="left")
            ui.boton(cuerpo,"Eliminar",lambda g=gasto:self._run(lambda:self._eliminar_gasto_fijo(g["id"]),"Gasto fijo eliminado"),tono="peligro",alto=28,ancho=80).pack(side="right",padx=(6,0))
            ui.boton(cuerpo,"Editar",lambda g=gasto:self.fixed_expense_dialog(g["id"]),tono="sutil",alto=28,ancho=70).pack(side="right",padx=(6,0))
            detalle=[]
            if gasto.get("dia_pago"): detalle.append(f"Se paga el día {gasto['dia_pago']}")
            if gasto.get("notas"): detalle.append(gasto["notas"])
            ctk.CTkLabel(cuerpo,text=f"{dinero(gasto['equivalente_mensual'])}/mes",font=ui.fuente(13,"bold"),text_color=T.TXT,anchor="e").pack(side="right",padx=(6,10))
            if detalle:
                ui.ayuda(linea,"  ·  ".join(detalle),ancho=760).pack(anchor="w",padx=Espacio.MD,pady=(0,Espacio.SM))
        ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()

    def _candidatos_descartados(self)->set[str]:
        return set(self.pref.get("candidatos_descartados") or [])

    def _descartar_candidato(self,candidato:dict[str,Any])->None:
        """Marca un candidato como «no es fijo». No borra ningún movimiento."""
        descartados=self._candidatos_descartados(); descartados.add(candidato["clave"])
        self.pref["candidatos_descartados"]=sorted(descartados); guardar_preferencias(self.pref)
        self._toast("✓  No volveré a sugerirlo"); self.show_fixed_expenses()

    def candidate_evidence_dialog(self,candidato:dict[str,Any])->None:
        """Muestra por qué Lúmina cree que ese cobro es recurrente."""
        try:
            analisis=self.service.podria_ser_gasto_fijo(candidato["nombre"],mes=self.selected_month)
        except Exception as exc:
            LOG.exception("Fallo el análisis de gasto fijo")
            messagebox.showerror("No pudimos analizarlo",str(exc),parent=self); return
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title(f"¿{candidato['nombre']} es un gasto fijo?"); d.geometry("580x620"); d.minsize(480,480); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        tono={"SI":"positiva","PROBABLE / REVISAR":"media","NO":"baja"}.get(analisis["respuesta_corta"],"media")
        ui.insignia(body,analisis["respuesta_corta"],tono=tono).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,4))
        ctk.CTkLabel(body,text=candidato["nombre"],font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.cuerpo(body,analisis["respuesta"],ancho=500).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(6,Espacio.MD))
        ui.evidencia(body,[("Apariciones",str(analisis["ocurrencias"])),
                           ("Frecuencia",analisis["frecuencia"]),
                           ("Promedio",dinero(analisis["monto_promedio"])),
                           ("Rango",f"{dinero(analisis['monto_minimo'])} – {dinero(analisis['monto_maximo'])}"),
                           ("Variación",f"{analisis['variacion_monto']:.1%}"),
                           ("Último cobro",analisis["ultima_aparicion"]),
                           ("Meses detectados",", ".join(analisis["meses_detectados"][-6:])),
                           ("Método de pago",str(analisis["metodo_pago"] or "—")),
                           ("Tarjeta",str(analisis["tarjeta"] or "—")),
                           ("Quien suele pagar",str(analisis["pagador"])),
                           ("Responsabilidad",str(analisis["responsabilidad"])),
                           ("Confianza",f"{analisis['confianza']:.0%}")],
                      titulo="Evidencia").pack(fill="x",padx=Espacio.PANEL_PAD)
        for linea in analisis["evidencia"][:5]:
            ui.ayuda(body,"· "+linea,ancho=500).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(4,0))
        acciones=ui.fila(body); acciones.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.LG)
        ui.boton(acciones,"Confirmar como gasto fijo",lambda:(d.destroy(),self.fixed_expense_create_dialog(candidato)),alto=38).pack(side="left")
        ui.boton(acciones,"No es fijo",lambda:(d.destroy(),self._descartar_candidato(candidato)),tono="sutil",alto=38).pack(side="left",padx=8)

    def fixed_expense_create_dialog(self,candidato:dict[str,Any]|None=None)->None:
        """Crea un gasto fijo, opcionalmente prellenado con un candidato detectado."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Nuevo gasto fijo"); d.geometry("560x780"); d.minsize(480,600); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="＋ Nuevo gasto fijo",font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,2))
        if candidato:
            ui.ayuda(body,f"Prellenado con lo que detecté: {candidato['ocurrencias']} cobros de ~{dinero(candidato['monto_promedio'])}. "
                          "Revisa y ajusta antes de guardar.",ancho=480).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.SM))
        else:
            ui.ayuda(body,"Las obligaciones recurrentes bajan el margen disponible: registrarlas hace que las cifras sean realistas.",ancho=480).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.SM))
        contenedor=ui.fila(body); contenedor.pack(fill="both",expand=True,padx=Espacio.PANEL_PAD)
        frecuencias={"mensual":"Mensual","bimestral":"Bimestral","trimestral":"Trimestral","semestral":"Semestral","anual":"Anual"}
        nombre=self._entry(contenedor,"Nombre",(candidato or {}).get("nombre","Arriendo"))
        categoria=self._entry(contenedor,"Categoría",(candidato or {}).get("categoria","Vivienda"))
        valor=self._entry(contenedor,"Valor COP",str((candidato or {}).get("monto_promedio","0")))
        frecuencia=self._entry(contenedor,"Frecuencia",frecuencias.get((candidato or {}).get("frecuencia",""),"Mensual"),list(frecuencias.values()))
        dia=self._entry(contenedor,"Día de pago (1-31, opcional)","")
        propietario=self._entry(contenedor,"Titular (quién lo paga)","Samuel",["Samuel","Sara"])
        resp=self._entry(contenedor,"Responsabilidad económica (quién lo asume)","Compartido",["Samuel","Sara","Compartido","Personalizado","Porcentaje personalizado"])
        p1=self._entry(contenedor,"Samuel asume COP (o %)","0"); p2=self._entry(contenedor,"Sara asume COP (o %)","0")
        metodo=self._entry(contenedor,"Método de pago","Débito",["Efectivo","Débito","Tarjeta"])
        etiquetas=[f"{c['id']} · {c['nombre']}" for c in self.service.tarjetas()]
        tarjeta=self._entry(contenedor,"Tarjeta (si aplica)",etiquetas[0],etiquetas) if etiquetas else None
        notas=self._entry(contenedor,"Notas (opcional)","Detectado por Lúmina" if candidato else "")
        def guardar()->None:
            modo=self._value(resp); a,b=self._distribution(self._value(valor),modo,self._value(p1),self._value(p2))
            tarjeta_id=self._value(tarjeta).split(" · ")[0] if tarjeta and self._value(metodo)=="Tarjeta" else ""
            self.service.crear_gasto_fijo({"nombre":self._value(nombre),"categoria":self._value(categoria),"valor":self._value(valor),
                "frecuencia":self._value(frecuencia),"dia_pago":self._value(dia),"propietario":self._value(propietario),
                "responsabilidad":modo,"monto_p1":a,"monto_p2":b,"metodo_pago":self._value(metodo),"tarjeta_id":tarjeta_id,
                "notas":self._value(notas),"activo":"Activo"})
            d.destroy(); self.show_fixed_expenses()
        ui.boton(body,"Guardar gasto fijo",lambda:self._run(guardar,"Gasto fijo creado"),alto=42).pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.LG)

    def _eliminar_gasto_fijo(self,gasto_fijo_id:int)->None:
        if not messagebox.askyesno("Eliminar gasto fijo","Esto elimina la plantilla del gasto fijo. Los gastos ya registrados a partir de ella no se modifican.\n\n¿Continuar?",parent=self): return
        self.service.eliminar_gasto_fijo(gasto_fijo_id); self.show_fixed_expenses()
    def fixed_expense_dialog(self,gasto_fijo_id:int)->None:
        g=self.service.gasto_fijo(gasto_fijo_id)
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title(f"Editar · {g['nombre']}"); d.geometry("520x760"); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="✎ Editar gasto fijo",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
        nombre=self._entry(body,"Nombre",g["nombre"]); categoria=self._entry(body,"Categoría",g["categoria"]); valor=self._entry(body,"Valor COP",str(g["valor"]))
        frecuencia=self._entry(body,"Frecuencia",g["frecuencia"].capitalize(),["Mensual","Bimestral","Trimestral","Semestral","Anual"])
        dia=self._entry(body,"Día de pago (1-31, opcional)",str(g["dia_pago"]) if g["dia_pago"] else "")
        propietario=self._entry(body,"Titular",NOMBRES[g["propietario"]],["Samuel","Sara"])
        if g["monto_p1"]==g["valor"]: modo_inicial="Samuel"
        elif g["monto_p2"]==g["valor"]: modo_inicial="Sara"
        elif g["monto_p1"]==g["monto_p2"]: modo_inicial="Compartido"
        else: modo_inicial="Personalizado"
        resp=self._entry(body,"Responsabilidad económica",modo_inicial,["Samuel","Sara","Compartido","Personalizado","Porcentaje personalizado"])
        p1=self._entry(body,"Samuel asume COP (o %)",str(g["monto_p1"])); p2=self._entry(body,"Sara asume COP (o %)",str(g["monto_p2"]))
        metodo=self._entry(body,"Método de pago",{"efectivo":"Efectivo","debito":"Débito","tarjeta":"Tarjeta"}[g["metodo_pago"]],["Efectivo","Débito","Tarjeta"])
        card_labels=[f"{c['id']} · {c['nombre']}" for c in self.service.tarjetas()]
        tarjeta=self._entry(body,"Tarjeta (si aplica)",next((l for l in card_labels if l.startswith(f"{g['tarjeta_id']} ·")),card_labels[0]) if card_labels else "",card_labels) if card_labels else None
        activo=self._entry(body,"Estado","Activo" if g["activo"] else "Inactivo",["Activo","Inactivo"])
        notas=self._entry(body,"Notas",g.get("notas") or "")
        def save()->None:
            modo=self._value(resp); a,b=self._distribution(self._value(valor),modo,self._value(p1),self._value(p2))
            tarjeta_id=self._value(tarjeta).split(" · ")[0] if tarjeta and self._value(metodo)=="Tarjeta" else ""
            self.service.editar_gasto_fijo(gasto_fijo_id,{"nombre":self._value(nombre),"categoria":self._value(categoria),"valor":self._value(valor),
                "frecuencia":self._value(frecuencia),"dia_pago":self._value(dia),"propietario":self._value(propietario),
                "responsabilidad":modo,"monto_p1":a,"monto_p2":b,"metodo_pago":self._value(metodo),"tarjeta_id":tarjeta_id,
                "activo":self._value(activo),"notas":self._value(notas)})
            d.destroy(); self.show_fixed_expenses()
        ctk.CTkButton(body,text="Guardar cambios",height=40,fg_color=T.PRIMARY,command=lambda:self._run(save,"Gasto fijo actualizado")).pack(fill="x",padx=18,pady=22)

    def scenario_dialog(self)->None:
        """Simulador: antes y después en liquidez, deuda, ahorro, cupo y flujo."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("¿Qué pasa si…?"); d.geometry("620x720"); d.minsize(520,560); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="¿Qué pasa si…?",font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,2))
        ui.ayuda(body,"Es un cálculo sobre los datos actuales: no registra movimientos, deuda ni cajitas.",ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD)
        tipos={"Un gasto con dinero disponible":"gasto","Una compra grande":"compra_grande","Una compra con tarjeta":"gasto_tarjeta",
               "Un abono extra a la tarjeta":"pago_tarjeta","No pagar la cuota de una tarjeta":"no_pagar_tarjeta",
               "Un ingreso extra":"ingreso_extra","Un mes con menos ingreso":"perdida_ingresos",
               "Recortar gasto":"reduccion_gastos","Bajar o cancelar un gasto fijo":"reduccion_gasto_fijo",
               "Aumentar el gasto":"aumento_gastos","Aportar de más al ahorro":"ahorro_adicional",
               "Adelantar una meta":"acelerar_meta","Aplazar el aporte de una meta":"aplazar_meta",
               "Retirar de una cajita":"retiro_cajita","Tomar una deuda nueva":"nueva_deuda","El costo de un viaje":"viaje"}
        contenedor=ui.fila(body); contenedor.pack(fill="x",padx=Espacio.PANEL_PAD)
        tipo=self._entry(contenedor,"Escenario",list(tipos)[0],list(tipos))
        monto=self._entry(contenedor,"Monto COP","")
        etiquetas=[f"{c['id']} · {c['nombre']}" for c in self.service.tarjetas()]
        tarjeta=self._entry(contenedor,"Tarjeta (solo para compras con tarjeta)",etiquetas[0],etiquetas) if etiquetas else None
        resultado=ctk.CTkFrame(body,fg_color="transparent"); resultado.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        def correr()->None:
            for w in resultado.winfo_children(): w.destroy()
            valor=parsear_dinero(self._value(monto))
            if valor<=0:
                ui.estado_error(resultado,"Falta el monto","Escribe cuánto quieres simular.").pack(fill="x"); return
            clave=tipos[self._value(tipo)]
            tarjeta_id=self._value(tarjeta).split(" · ")[0] if tarjeta and clave=="gasto_tarjeta" else None
            try:
                escenario=self.service.escenario_detallado(self.selected_month,clave,valor,tarjeta_id)
            except Exception as exc:
                LOG.exception("Fallo la simulación")
                ui.estado_error(resultado,"No pudimos simularlo",str(exc)).pack(fill="x"); return
            tono={"sostenible":"positiva","riesgoso":"media","no_sostenible":"alta"}[escenario["veredicto"]]
            titulo={"sostenible":"Se sostiene","riesgoso":"Es posible, pero aprieta","no_sostenible":"Hoy no se sostiene"}[escenario["veredicto"]]
            cabecera=ui.panel(resultado,fondo=ui.SEVERIDAD_FONDO[tono],borde=False); cabecera.pack(fill="x")
            ctk.CTkLabel(cabecera,text=titulo,font=Tipo.seccion(),text_color=ui.SEVERIDAD_COLOR[tono]).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ui.cuerpo(cabecera,escenario["explicacion"],ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
            tabla=ui.panel(resultado); tabla.pack(fill="x",pady=Espacio.SM)
            ui.etiqueta(tabla,"Antes y después").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
            antes,despues=escenario["antes"],escenario["despues"]
            ui.dato(tabla,"Liquidez",f"{dinero(antes['liquidez'])} → {dinero(despues['liquidez'])}")
            ui.dato(tabla,"Deuda de tarjetas",f"{dinero(antes['deuda_tarjetas'])} → {dinero(despues['deuda_tarjetas'])}")
            ui.dato(tabla,"Deuda total",f"{dinero(antes['deuda_total'])} → {dinero(despues['deuda_total'])}")
            ui.dato(tabla,"Ahorro reservado",f"{dinero(antes['ahorro'])} → {dinero(despues['ahorro'])}")
            ui.dato(tabla,"Utilización de cupo",f"{antes['utilizacion']:.0%} → {despues['utilizacion']:.0%}",
                    color=ui.utilizacion_color(despues["utilizacion"]))
            ui.dato(tabla,"Flujo del mes",f"{dinero(antes['flujo_mensual'])} → {dinero(despues['flujo_mensual'])}")
            if antes.get("progreso_metas") is not None:
                ui.dato(tabla,"Avance de metas",f"{antes['progreso_metas']:.0%} → {despues['progreso_metas']:.0%}")
            ui.dato(tabla,"Margen discrecional",
                    f"{dinero(escenario['margen_discrecional_antes'])} → {dinero(escenario['margen_discrecional_despues'])}",
                    color=T.OK if escenario["margen_discrecional_despues"]>=0 else T.BAD)
            ctk.CTkLabel(tabla,text="",height=Espacio.SM).pack()
            if escenario["riesgos"] or escenario["notas"]:
                notas=ui.panel(resultado,fondo=T.ALT,borde=False); notas.pack(fill="x",pady=Espacio.SM)
                ui.etiqueta(notas,"Consecuencias").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
                for linea in escenario["riesgos"]:
                    ui.cuerpo(notas,"· "+linea,color=T.BAD,ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=2)
                for linea in escenario["notas"]:
                    ui.ayuda(notas,"· "+linea,ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=2)
                ctk.CTkLabel(notas,text="",height=Espacio.SM).pack()
            ui.ayuda(resultado,escenario["nota"],ancho=520).pack(anchor="w",pady=(Espacio.SM,0))
        ui.boton(body,"Simular",correr,alto=40).pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.LG))

    def debt_projection_dialog(self)->None:
        tarjetas=[x for x in self.service.tarjetas() if x["saldo_deuda"]]
        if not tarjetas: messagebox.showinfo("Proyección de deuda","No hay una deuda de tarjeta registrada para proyectar.",parent=self); return
        d=ctk.CTkToplevel(self,fg_color=T.BG);d.title("Proyección de deuda");d.geometry("510x430");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO);body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="Proyección de deuda",font=Tipo.dialogo()).pack(anchor="w",padx=20,pady=(20,2));ctk.CTkLabel(body,text="Estimación: no registra un pago ni modifica tu tarjeta.",text_color=T.MUTED).pack(anchor="w",padx=20)
        etiquetas=[f"{x['id']} · {x['nombre']}" for x in tarjetas];tarjeta=self._entry(body,"Tarjeta",etiquetas[0],etiquetas);extra=self._entry(body,"Pago adicional mensual COP","0");resultado=ctk.CTkLabel(body,text="",wraplength=430,justify="left",text_color=T.TXT);resultado.pack(anchor="w",padx=20,pady=16)
        def calcular()->None:
            x=self.service.proyeccion_deuda(int(self._value(tarjeta).split(" · ")[0]),self._value(extra));actual="no se amortiza con el mínimo registrado" if x["meses_actual"] is None else f"{x['meses_actual']} meses";mejorado="no se amortiza" if x["meses_mejorado"] is None else f"{x['meses_mejorado']} meses"
            resultado.configure(text=f"Escenario actual: {actual} · intereses aprox. {dinero(x['interes_actual_estimado'])}\nCon pago adicional: {mejorado} · intereses aprox. {dinero(x['interes_mejorado_estimado'])}\nTiempo ahorrado: {x['meses_ahorrados'] if x['meses_ahorrados'] is not None else 'no estimable'} meses · interés evitado: {dinero(x['interes_ahorrado_estimado'])}\n{x['nota']}")
        ctk.CTkButton(body,text="Calcular proyección",fg_color=T.PRIMARY,command=lambda:self._run(calcular,"Proyección lista")).pack(fill="x",padx=20,pady=12)

    def show_advisor(self)->None:
        """Centro del producto: lectura, hallazgos, plan y conversación."""
        self._clear("Asesor","Asesor")
        estado=self.service.estado_financiero(self.selected_month)
        perfil=estado["perfil"]; riesgo=estado["riesgo"]; plan=estado["plan"]
        self._heading("Asesor financiero","Esto es lo que veo en sus finanzas, con los números que lo sustentan.")

        resumen=self._abierto(2,0,4)
        ui.etiqueta(resumen,"Lectura del mes").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ctk.CTkLabel(resumen,text=estado["titular"],font=Tipo.editorial(),text_color=T.TXT,wraplength=860,justify="left").pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.ayuda(resumen,plan["resumen"],ancho=860).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(6,0))
        herramientas=ui.fila(resumen); herramientas.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,Espacio.MD))
        for texto,accion in (("¿Podemos gastar?",self.affordability_dialog),("¿Cómo lo pagamos?",self.payment_dialog),
                             ("¿Qué pasa si…?",self.scenario_dialog),("Comparar un viaje",self.travel_dialog),
                             ("Proyectar una deuda",self.debt_projection_dialog)):
            ui.boton(herramientas,texto,accion,tono="suave",alto=32).pack(side="left",padx=(0,7))

        self._metric(3,0,"Riesgo financiero",riesgo["nivel"].capitalize(),
                     "Lo que más pesa: "+", ".join(f.replace("_"," ") for f in riesgo["factores_dominantes"]),
                     T.BAD if riesgo["nivel"]=="alto" else T.WARN if riesgo["nivel"]=="moderado" else T.OK)
        emergencia=perfil["ahorro"]["emergencia"]
        self._metric(3,1,"Fondo de emergencia",dinero(emergencia["current"]),
                     f"Cubre {emergencia['coverage_months']:.1f} de {emergencia['target_months']} meses · faltan {dinero(emergencia['shortfall'])}",T.SAVE)
        self._metric(3,2,"Margen discrecional",dinero(estado["capacidad"]["maximo_discrecional"]),
                     "Después de fijos, mínimos y colchón de seguridad",T.OK)
        self._metric(3,3,"Confianza de los datos",estado["confianza"],
                     perfil["calidad_datos"]["explanation"],T.PRIMARY)

        self._section(4,"Qué haría, en orden","Ordenado por severidad, impacto y urgencia real.")
        plan_panel=self._abierto(5,0,4)
        etiquetas=(("inmediato","Ahora",T.BAD),("este_mes","Este mes",T.WARN),
                   ("proximos_tres_meses","Próximos tres meses",T.PRIMARY),("optimizacion_opcional","Opcional",T.MUTED))
        vacio=True
        for clave,rotulo,color in etiquetas:
            pasos=plan.get(clave) or []
            if not pasos: continue
            vacio=False
            ui.etiqueta(plan_panel,rotulo,color).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            for paso in pasos:
                bloque=ui.fila(plan_panel); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=3)
                cabecera=ui.fila(bloque); cabecera.pack(fill="x")
                ctk.CTkLabel(cabecera,text=paso["accion"],font=ui.fuente(13,"bold"),text_color=T.TXT,wraplength=680,justify="left").pack(side="left")
                if paso.get("monto"):
                    ctk.CTkLabel(cabecera,text=dinero(paso["monto"]),font=Tipo.ayuda(),text_color=color).pack(side="right")
                ui.ayuda(bloque,f"{paso['por_que']} · {paso['impacto_esperado']}",ancho=800).pack(anchor="w")
        if vacio:
            ui.ayuda(plan_panel,"No hay acciones pendientes con los datos registrados.").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=Espacio.LG)
        else:
            ctk.CTkLabel(plan_panel,text="",height=Espacio.SM).pack()

        self._section(6,"Hallazgos","Agrupo los que llevan a la misma decisión para no repetir avisos.")
        bloques=self.service.hallazgos_agrupados(self.selected_month)
        if not bloques:
            self._place(ui.estado_vacio(self.content,"Sin hallazgos","Registren más actividad para enriquecer el análisis.",
                        accion="Ir a movimientos",comando=self.show_transactions),7,0,4)
        fila=7
        for bloque in bloques[:6]:
            if bloque["tipo"]=="grupo":
                caja=self._open_panel(fila,0,4)
                cabecera=ui.fila(caja); cabecera.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
                ui.insignia(cabecera,ui.SEVERIDAD_TEXTO.get(bloque["prioridad"],bloque["prioridad"]),tono=bloque["prioridad"]).pack(side="left")
                ctk.CTkLabel(cabecera,text=dinero(bloque["monto_involucrado"]),font=ui.fuente(13,"bold"),text_color=ui.SEVERIDAD_COLOR.get(bloque["prioridad"],T.MUTED)).pack(side="right")
                ctk.CTkLabel(caja,text=bloque["titulo"],font=Tipo.tarjeta(),text_color=T.TXT).pack(anchor="w",padx=Espacio.PANEL_PAD)
                ui.ayuda(caja,bloque["resumen"],ancho=820).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(2,6))
                for sub in bloque["hallazgos"]:
                    linea=ui.fila(caja); linea.pack(fill="x",padx=Espacio.PANEL_PAD,pady=3)
                    ctk.CTkLabel(linea,text=f"·  {sub['titulo']}: {sub['que']}",font=Tipo.cuerpo(),text_color=T.TXT,wraplength=700,justify="left").pack(side="left")
                    ui.boton(linea,"Evidencia",lambda h=sub:self.evidence_dialog(h),tono="sutil",alto=26).pack(side="right")
                ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()
            else:
                destino=self._destino_hallazgo(bloque)
                caja=ui.hallazgo(self.content,bloque,formato_dinero=dinero,al_abrir=destino[0],etiqueta_accion=destino[1])
                self._place(caja,fila,0,4)
                ui.boton(caja,"Ver evidencia",lambda h=bloque:self.evidence_dialog(h),tono="sutil",alto=26).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
            fila+=1

        oportunidades=estado["oportunidades"]
        if oportunidades:
            self._section(fila,"Oportunidades","Salen de los datos, no de suposiciones optimistas."); fila+=1
            caja=self._open_panel(fila,0,4); fila+=1
            for item in oportunidades[:4]:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=6)
                cabecera=ui.fila(bloque); cabecera.pack(fill="x")
                ctk.CTkLabel(cabecera,text=item["titulo"],font=ui.fuente(13,"bold"),text_color=T.OK).pack(side="left")
                if item["monto_estimado"]:
                    ctk.CTkLabel(cabecera,text=dinero(item["monto_estimado"]),font=Tipo.ayuda(),text_color=T.MUTED).pack(side="right")
                ui.ayuda(bloque,f"{item['detalle']} {item['accion']} ({item['beneficio']})",ancho=820).pack(anchor="w")

        self._section(fila,"Pregúntale a Lúmina","Responde con los movimientos registrados. No modifica nada."); fila+=1
        self._chat_panel=self._abierto(fila,0,4); fila+=1
        self._render_chat()

        anomalias=estado["anomalias"]
        if anomalias:
            self._section(fila,"Posibles anomalías","Señales para confirmar, no errores comprobados."); fila+=1
            caja=self._open_panel(fila,0,4)
            for item in anomalias[:5]:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=6)
                ui.insignia(bloque,item["severidad"].capitalize(),tono="alta" if item["severidad"]=="alta" else "media").pack(side="left",padx=(0,10))
                ctk.CTkLabel(bloque,text=f"{item['titulo']}: {item['detalle']}",font=Tipo.cuerpo(),text_color=T.TXT,wraplength=760,justify="left").pack(side="left")
            ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()

    def _render_chat(self)->None:
        """Pinta la conversación completa y el campo de pregunta."""
        panel=self._chat_panel
        for w in panel.winfo_children(): w.destroy()
        if not self._chat:
            ui.ayuda(panel,"Pregunta con tus palabras: «¿podemos salir este fin de semana?», «¿cómo pagamos esto?», «¿ese cobro parece un gasto fijo?».").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        for turno in self._chat[-10:]:
            ui.burbuja(panel,turno["texto"],autor=turno["autor"])
            if turno.get("evidencia"):
                ui.evidencia(panel,turno["evidencia"]).pack(fill="x",padx=(Espacio.MD,90),pady=(0,Espacio.SM))
        ui.sugerencias(panel,("¿Cómo estamos?","¿Podemos gastar 200.000?","¿Qué deuda atacamos?","¿Dónde se va la plata?","¿Por qué?"),
                       self._ask_advisor)
        entrada=ui.fila(panel); entrada.pack(fill="x",padx=Espacio.MD,pady=(0,Espacio.MD))
        campo=ctk.CTkEntry(entrada,height=40,fg_color=T.ALT,border_color=T.BORDER,corner_radius=10,placeholder_text="Escribe tu pregunta…")
        campo.pack(side="left",fill="x",expand=True)
        self._chat_entry=campo
        ui.boton(entrada,"Preguntar",lambda:self._ask_advisor(self._value(campo)),alto=40).pack(side="left",padx=(8,0))
        if self._chat:
            ui.boton(entrada,"Limpiar",self._clear_chat,tono="sutil",alto=40).pack(side="left",padx=(8,0))

    def _clear_chat(self)->None:
        self._chat=[]; self._chat_session=None; self._render_chat()

    def _ask_advisor(self,pregunta:str)->None:
        """Envía la pregunta conservando el hilo de la conversación."""
        pregunta=(pregunta or "").strip()
        if not pregunta:
            messagebox.showinfo("Escribe una pregunta","Cuéntame qué quieres saber de sus finanzas.",parent=self); return
        if self._chat_session is None:
            self._chat_session=self.service.nueva_sesion_asesor(self.selected_month)
        self._chat.append({"autor":"usuario","texto":pregunta})
        try:
            respuesta=self.service.conversar(self.selected_month,pregunta,sesion=self._chat_session)
        except Exception as exc:
            LOG.exception("Fallo del asesor conversacional")
            self._chat.append({"autor":"asesor","texto":"No pude analizar esa pregunta con los datos actuales. "
                                                        f"Detalle: {type(exc).__name__}."})
            self._render_chat(); return
        self._chat.append({"autor":"asesor","texto":respuesta["respuesta"],
                           "evidencia":self._evidencia_respuesta(respuesta)})
        seguimiento=respuesta.get("preguntas_seguimiento") or []
        if seguimiento:
            self._chat.append({"autor":"asesor","texto":seguimiento[0]})
        self._render_chat()

    def _evidencia_respuesta(self,respuesta:dict[str,Any])->list[tuple[str,str]]:
        """Traduce la estructura del asesor a filas legibles de evidencia."""
        datos=respuesta.get("datos") or {}
        filas:list[tuple[str,str]]=[]
        if not isinstance(datos,dict): return filas
        for clave,rotulo in (("base_disponible","Disponible registrado"),("maximo_discrecional","Margen discrecional"),
                             ("monto","Monto consultado"),("flujo_del_mes","Flujo del mes"),
                             ("restante_despues","Quedaría después")):
            if isinstance(datos.get(clave),(int,float)):
                filas.append((rotulo,dinero(datos[clave])))
        compromisos=datos.get("compromisos")
        if isinstance(compromisos,dict):
            for clave,valor in compromisos.items():
                if valor: filas.append((clave.replace("_"," ").capitalize(),dinero(valor)))
        antes,despues=datos.get("antes"),datos.get("despues")
        if isinstance(antes,dict) and isinstance(despues,dict):
            for clave,rotulo in (("liquidez","Liquidez"),("deuda_tarjetas","Deuda de tarjetas"),("flujo_mensual","Flujo del mes")):
                if clave in antes:
                    filas.append((rotulo,f"{dinero(antes[clave])} → {dinero(despues[clave])}"))
        if isinstance(datos.get("evidencia"),list):
            for linea in datos["evidencia"][:4]:
                filas.append(("Evidencia",str(linea)))
        return filas[:8]

    def evidence_dialog(self,hallazgo:dict[str,Any])->None:
        """Muestra la evidencia cruda de un hallazgo: de dónde salió cada número."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Evidencia"); d.geometry("600x600"); d.minsize(480,460); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        severidad=hallazgo.get("prioridad","media")
        ui.insignia(body,ui.SEVERIDAD_TEXTO.get(severidad,severidad),tono=severidad).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,4))
        ctk.CTkLabel(body,text=hallazgo.get("titulo",""),font=ui.serif(20),wraplength=500,justify="left").pack(anchor="w",padx=Espacio.PANEL_PAD)
        for rotulo,clave in (("Qué pasa","que"),("Por qué","por_que"),("Qué haría","accion"),("Impacto esperado","impacto"),
                             ("Por qué esta severidad","razon_severidad")):
            if hallazgo.get(clave):
                ui.etiqueta(body,rotulo).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
                ui.cuerpo(body,hallazgo[clave],ancho=500).pack(anchor="w",padx=Espacio.PANEL_PAD)
        filas=[]
        for clave,valor in (hallazgo.get("evidencia") or {}).items():
            if isinstance(valor,(int,float)) and not isinstance(valor,bool):
                texto=dinero(valor) if abs(valor)>=1000 else (f"{valor:.0%}" if 0<abs(valor)<=1 else str(valor))
            elif isinstance(valor,(list,tuple)):
                texto=", ".join(str(x) for x in valor[:4]) or "—"
            elif isinstance(valor,dict):
                texto=", ".join(f"{k}: {v}" for k,v in list(valor.items())[:3])
            else:
                texto=str(valor)
            filas.append((clave.replace("_"," ").capitalize(),texto))
        if filas:
            ui.evidencia(body,filas,titulo="Datos usados").pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        if hallazgo.get("confianza"):
            ui.ayuda(body,f"Confianza del hallazgo: {hallazgo['confianza']}.").pack(anchor="w",padx=Espacio.PANEL_PAD)
        destino,etiqueta_boton=self._destino_hallazgo(hallazgo)
        if destino:
            ui.boton(body,etiqueta_boton,lambda:(d.destroy(),destino()),alto=38).pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.LG)

    def advisor_question_dialog(self)->None:
        """Atajo desde otras pantallas: abre el asesor y deja el foco en la conversación."""
        self.show_advisor()

    def affordability_dialog(self)->None:
        """¿Podemos comprarlo? Muestra antes, después y el análisis, no un sí/no."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("¿Podemos comprarlo?"); d.geometry("620x720"); d.minsize(520,600); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="¿Podemos comprarlo?",font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,2))
        ui.ayuda(body,"Es una simulación: no registra ningún movimiento ni cambia la base.",ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD)
        concepto=self._entry(body,"¿Qué quieren comprar?","Una salida")
        monto=self._entry(body,"Monto estimado COP","")
        resultado=ctk.CTkFrame(body,fg_color="transparent"); resultado.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        def analizar()->None:
            for w in resultado.winfo_children(): w.destroy()
            valor=parsear_dinero(self._value(monto))
            if valor<=0:
                ui.estado_error(resultado,"Falta el monto","Escribe cuánto costaría para poder analizarlo.").pack(fill="x"); return
            try:
                analisis=self.service.evaluar_asequibilidad(self.selected_month,valor,self._value(concepto) or "esta compra")
                escenario=self.service.escenario_detallado(self.selected_month,"compra_grande",valor)
            except Exception as exc:
                LOG.exception("Fallo la evaluación de asequibilidad")
                ui.estado_error(resultado,"No pudimos analizarlo",str(exc)).pack(fill="x"); return
            tono={"asequible":"positiva","al_limite":"media","no_asequible":"alta","informativo":"info"}[analisis["veredicto"]]
            titulo={"asequible":"Sí, cabe","al_limite":"Alcanza, pero al límite","no_asequible":"Hoy no",
                    "informativo":"Depende del monto"}[analisis["veredicto"]]
            caja=ui.panel(resultado,fondo=ui.SEVERIDAD_FONDO[tono],borde=False); caja.pack(fill="x")
            ctk.CTkLabel(caja,text=titulo,font=ui.serif(19),text_color=ui.SEVERIDAD_COLOR[tono]).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ui.cuerpo(caja,analisis["mensaje"],ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
            detalle=ui.panel(resultado); detalle.pack(fill="x",pady=Espacio.SM)
            ui.etiqueta(detalle,"Antes y después").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
            ui.dato(detalle,"Disponible registrado",dinero(analisis["base_disponible"]))
            for clave,valor_compromiso in analisis["compromisos"].items():
                if valor_compromiso: ui.dato(detalle,clave.replace("_"," ").capitalize(),"- "+dinero(valor_compromiso),color=T.MUTED)
            ui.dato(detalle,"Margen discrecional hoy",dinero(analisis["maximo_discrecional"]),color=T.OK)
            ui.dato(detalle,"Margen después de la compra",dinero(max(analisis["restante_despues"],0)),
                    color=T.OK if analisis["restante_despues"]>=0 else T.BAD)
            ui.dato(detalle,"Liquidez",f"{dinero(escenario['antes']['liquidez'])} → {dinero(escenario['despues']['liquidez'])}")
            ui.dato(detalle,"Deuda de tarjetas",f"{dinero(escenario['antes']['deuda_tarjetas'])} → {dinero(escenario['despues']['deuda_tarjetas'])}")
            ui.dato(detalle,"Ahorro reservado",f"{dinero(escenario['antes']['ahorro'])} → {dinero(escenario['despues']['ahorro'])}")
            ui.dato(detalle,"Margen de seguridad",dinero(analisis["compromisos"]["margen_seguridad"]))
            ctk.CTkLabel(detalle,text="",height=Espacio.SM).pack()
            analisis_panel=ui.panel(resultado,fondo=T.ALT,borde=False); analisis_panel.pack(fill="x",pady=Espacio.SM)
            ui.etiqueta(analisis_panel,"Análisis de Lúmina").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ui.cuerpo(analisis_panel,escenario["explicacion"],ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD)
            for linea in (escenario["riesgos"]+escenario["notas"])[:3]:
                ui.ayuda(analisis_panel,"· "+linea,ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(4,0))
            for linea in analisis.get("que_cambiaria",[])[:2]:
                ui.ayuda(analisis_panel,"· "+linea,ancho=520).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(4,0))
            ctk.CTkLabel(analisis_panel,text="",height=Espacio.SM).pack()
            ui.boton(resultado,"¿Cómo lo pagamos?",lambda:(d.destroy(),self.payment_dialog(valor)),tono="suave",alto=36).pack(fill="x",pady=Espacio.SM)
        ui.boton(body,"Analizar",analizar,alto=40).pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.LG))

    def payment_dialog(self,monto_inicial:int|None=None)->None:
        """Compara efectivo/débito, cada tarjeta y esperar, con sus consecuencias."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("¿Cómo lo pagamos?"); d.geometry("640x700"); d.minsize(520,560); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="¿Cómo lo pagamos?",font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,2))
        ui.ayuda(body,"El cupo disponible no es dinero suyo: es deuda que todavía no han tomado.",ancho=540).pack(anchor="w",padx=Espacio.PANEL_PAD)
        monto=self._entry(body,"Monto COP",str(monto_inicial or ""))
        resultado=ctk.CTkFrame(body,fg_color="transparent"); resultado.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        def comparar()->None:
            for w in resultado.winfo_children(): w.destroy()
            valor=parsear_dinero(self._value(monto))
            if valor<=0:
                ui.estado_error(resultado,"Falta el monto","Escribe cuánto vas a pagar.").pack(fill="x"); return
            try: opciones=self.service.opciones_de_pago(self.selected_month,valor)
            except Exception as exc:
                LOG.exception("Fallo la recomendación de pago")
                ui.estado_error(resultado,"No pudimos compararlo",str(exc)).pack(fill="x"); return
            cabecera=ui.panel(resultado,fondo=T.PRIMARY_SOFT,borde=False); cabecera.pack(fill="x")
            ui.etiqueta(cabecera,"Recomendación",T.PRIMARY).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ctk.CTkLabel(cabecera,text=opciones["recomendada"],font=ui.serif(17),text_color=T.TXT).pack(anchor="w",padx=Espacio.PANEL_PAD)
            ui.cuerpo(cabecera,opciones["razon"],ancho=540).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(2,Espacio.MD))
            for opcion in opciones["opciones"]:
                caja=ui.panel(resultado); caja.pack(fill="x",pady=6)
                linea=ui.fila(caja); linea.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
                ctk.CTkLabel(linea,text=opcion["opcion"],font=ui.fuente(14,"bold"),
                             text_color=T.TXT if opcion["viable"] else T.MUTED).pack(side="left")
                ui.insignia(linea,"Viable" if opcion["viable"] else "No alcanza",
                            tono="positiva" if opcion["viable"] else "alta").pack(side="right")
                ui.cuerpo(caja,opcion["impacto"],ancho=540).pack(anchor="w",padx=Espacio.PANEL_PAD)
                ui.ayuda(caja,opcion["riesgo"],ancho=540).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(2,0))
                if opcion.get("costo_financiero"):
                    ui.dato(caja,"Costo financiero estimado al mes",dinero(opcion["costo_financiero"]),color=T.WARN)
                if opcion.get("utilizacion_despues") is not None:
                    ui.dato(caja,"Utilización después",f"{opcion['utilizacion_despues']:.0%}",
                            color=ui.utilizacion_color(opcion["utilizacion_despues"]))
                ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()
            ui.ayuda(resultado,opciones["nota"],ancho=540).pack(anchor="w",pady=(Espacio.SM,0))
        ui.boton(body,"Comparar opciones",comparar,alto=40).pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.LG))
        if monto_inicial: comparar()

    def travel_dialog(self)->None:
        """Compara dos destinos con criterio financiero. No inventa precios."""
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Comparar un viaje"); d.geometry("760x780"); d.minsize(620,620); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="Comparar un viaje",font=Tipo.dialogo()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.LG,2))
        ui.ayuda(body,"Los precios de tiquetes y hoteles cambian por fechas y son información externa: pásenlos ustedes "
                      "y yo calculo el costo total, el ahorro mensual necesario y el impacto en sus metas.",ancho=660).pack(anchor="w",padx=Espacio.PANEL_PAD)
        comunes=ui.fila(body); comunes.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,0))
        noches=self._entry(comunes,"Noches","7"); viajeros=self._entry(comunes,"Viajeros","2"); plazo=self._entry(comunes,"¿En cuántos meses viajarían?","12")
        columnas=ui.fila(body); columnas.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        campos={}
        for indice,defecto in enumerate(("México","España")):
            columna=ui.panel(columnas); columna.pack(side="left",fill="both",expand=True,padx=(0,8) if indice==0 else (8,0))
            interior=ui.fila(columna); interior.pack(fill="both",expand=True,padx=Espacio.MD,pady=Espacio.MD)
            campos[indice]={
                "nombre":self._entry(interior,"Destino",defecto),
                "transporte_ida_vuelta":self._entry(interior,"Tiquetes por persona COP","0"),
                "alojamiento_noche":self._entry(interior,"Alojamiento por noche COP","0"),
                "comida_dia":self._entry(interior,"Comida por día y persona COP","0"),
                "transporte_local_dia":self._entry(interior,"Transporte local por día COP","0"),
                "actividades":self._entry(interior,"Actividades (todo el viaje) COP","0"),
                "seguro_visa_otros":self._entry(interior,"Seguro, visa y otros COP","0"),
            }
        resultado=ctk.CTkFrame(body,fg_color="transparent"); resultado.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        def comparar()->None:
            for w in resultado.winfo_children(): w.destroy()
            destinos=[]
            for indice in (0,1):
                datos=campos[indice]
                destino={"nombre":self._value(datos["nombre"]) or f"Destino {indice+1}",
                         "noches":parsear_dinero(self._value(noches)) or 0,
                         "viajeros":parsear_dinero(self._value(viajeros)) or 1}
                for clave in ("transporte_ida_vuelta","alojamiento_noche","comida_dia","transporte_local_dia",
                              "actividades","seguro_visa_otros"):
                    destino[clave]=parsear_dinero(self._value(datos[clave]))
                destinos.append(destino)
            try:
                comparacion=self.service.comparar_destinos(self.selected_month,destinos,parsear_dinero(self._value(plazo)) or None)
            except Exception as exc:
                LOG.exception("Fallo la comparación de destinos")
                ui.estado_error(resultado,"No pudimos comparar",str(exc)).pack(fill="x"); return
            contexto=comparacion["contexto_financiero"]
            resumen=ui.panel(resultado,fondo=T.ALT,borde=False); resumen.pack(fill="x")
            ui.etiqueta(resumen,"Punto de partida").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ui.dato(resumen,"Ahorro libre (fuera del fondo de emergencia)",dinero(contexto["ahorro_libre_para_viaje"]))
            ui.dato(resumen,"Flujo mensual disponible",dinero(contexto["flujo_mensual"]))
            ui.dato(resumen,"Deuda de tarjetas que compite por ese dinero",dinero(contexto["deuda_tarjetas"]),color=T.WARN)
            ctk.CTkLabel(resumen,text="",height=Espacio.SM).pack()
            for destino in comparacion["destinos"]:
                caja=ui.panel(resultado); caja.pack(fill="x",pady=6)
                ctk.CTkLabel(caja,text=destino["nombre"],font=Tipo.tarjeta()).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
                if destino["datos_faltantes"]:
                    ui.ayuda(caja,"Faltan datos: "+", ".join(destino["datos_faltantes"])+". Con eso completo el cálculo.",ancho=620).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
                    continue
                ui.dato(caja,"Costo total estimado",dinero(destino["total"]),color=T.TXT)
                for clave,valor in destino["desglose"].items():
                    if valor: ui.dato(caja,clave.replace("_"," ").capitalize(),dinero(valor),color=T.MUTED)
                ui.dato(caja,"Falta sobre el ahorro libre",dinero(destino["faltante_sobre_ahorro_libre"]),color=T.WARN)
                if destino["ahorro_mensual_requerido"] is not None:
                    ui.dato(caja,"Ahorro mensual necesario",dinero(destino["ahorro_mensual_requerido"]),color=T.SAVE)
                if destino["meses_para_ahorrarlo"] is not None:
                    ui.dato(caja,"Tiempo para financiarlo",f"{destino['meses_para_ahorrarlo']} mes(es)")
                estados={"si":"Alcanza hoy","si_con_ahorro":"Alcanza ahorrando en el plazo",
                         "requiere_tiempo":"Requiere más tiempo","no_calculable":"No calculable con estos datos"}
                ui.dato(caja,"Viabilidad",estados.get(destino["asequible"],destino["asequible"]))
                for impacto in destino["impacto_en_metas"][:2]:
                    ui.ayuda(caja,"· "+impacto["detalle"],ancho=620).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(2,0))
                ui.ayuda(caja,"· "+destino["impacto_deuda"],ancho=620).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(2,Espacio.MD))
            if comparacion["comparacion"]:
                cierre=ui.panel(resultado,fondo=T.PRIMARY_SOFT,borde=False); cierre.pack(fill="x",pady=6)
                ui.etiqueta(cierre,"Diferencia financiera",T.PRIMARY).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
                ui.cuerpo(cierre,comparacion["comparacion"]["lectura"],ancho=640).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
            ui.ayuda(resultado,comparacion["nota"],ancho=640).pack(anchor="w",pady=(Espacio.SM,0))
        ui.boton(body,"Comparar destinos",comparar,alto=40).pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.LG))

    def show_debts(self)->None:
        """Deuda total, por persona y por tarjeta, con las dos estrategias comparadas."""
        self._clear("Deudas","Deudas")
        self._heading("Deudas","Cuánto deben, a qué costo y en qué orden conviene atacarlo.")
        perfil=self.service.perfil_financiero(self.selected_month)
        deuda=perfil["deuda"]; tarjetas=perfil["tarjetas"]
        if not deuda["total"]:
            self._place(ui.estado_vacio(self.content,"No tienen deudas registradas",
                        "Cuando registren una tarjeta con saldo o un préstamo con un tercero, aquí verán el plan de pago.",
                        accion="Ver tarjetas",comando=self.show_cards,icono="✓"),2,0,4); return
        self._metric(2,0,"Deuda total",dinero(deuda["total"]),f"Tarjetas {dinero(deuda['tarjetas'])} · terceros {dinero(deuda['externa_por_pagar'])}",T.BAD)
        self._metric(2,1,"Responsabilidad de Samuel",dinero(deuda["samuel"]),"Según la responsabilidad registrada, no el titular",T.PRIMARY)
        self._metric(2,2,"Responsabilidad de Sara",dinero(deuda["sara"]),"Según la responsabilidad registrada, no el titular",T.SAVE)
        self._metric(2,3,"Interés estimado",dinero(deuda["interes_mensual_estimado"]),
                     f"Al mes · pagos mínimos {dinero(deuda['pagos_minimos'])}",T.WARN)
        carga=deuda["carga_sobre_ingreso"]
        if carga is not None:
            barra_panel=self._open_panel(3,0,4)
            ui.etiqueta(barra_panel,"Peso de la deuda sobre el ingreso").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
            ctk.CTkLabel(barra_panel,text=f"{carga:.0%} del ingreso del mes",font=Tipo.numero_pequeno(),
                         text_color=T.BAD if carga>=.35 else T.WARN if carga>=.2 else T.OK).pack(anchor="w",padx=Espacio.PANEL_PAD)
            ui.barra(barra_panel,min(carga/0.6,1),color=T.BAD if carga>=.35 else T.WARN)
            ui.ayuda(barra_panel,"Referencia de producto: por encima del 35% la deuda empieza a condicionar las decisiones del mes.",ancho=820).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(6,Espacio.MD))
        self._section(4,"Deuda por tarjeta","Ordenadas por costo mensual.")
        detalle=self._open_panel(5,0,4)
        for tarjeta in sorted(tarjetas["tarjetas"],key=lambda t:-t["interes_mensual"]):
            if not tarjeta["saldo_deuda"]: continue
            bloque=ui.fila(detalle); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.SM)
            cabecera=ui.fila(bloque); cabecera.pack(fill="x")
            ctk.CTkLabel(cabecera,text=f"{tarjeta['nombre']} · titular {tarjeta['titular_nombre']}",font=ui.fuente(13,"bold")).pack(side="left")
            ctk.CTkLabel(cabecera,text=dinero(tarjeta["saldo_deuda"]),font=Tipo.numero_pequeno(),text_color=ui.utilizacion_color(tarjeta["utilizacion"])).pack(side="right")
            ui.ayuda(bloque,(f"Tasa {tarjeta['interes_mensual']:.2f}% mensual · interés estimado {dinero(tarjeta['interes_estimado'])} · "
                             f"mínimo {dinero(tarjeta['pago_minimo_efectivo'])} · responsabilidad Samuel {dinero(tarjeta['deuda_persona1'])} / Sara {dinero(tarjeta['deuda_persona2'])}")
                     if tarjeta["interes_mensual"] else
                     f"Sin tasa registrada · mínimo {dinero(tarjeta['pago_minimo_efectivo'])}",ancho=820).pack(anchor="w")
        if deuda["externa_por_pagar"]:
            ui.dato(detalle,"Deuda con terceros",dinero(deuda["externa_por_pagar"]),color=T.WARN)
        ctk.CTkLabel(detalle,text="",height=Espacio.SM).pack()
        self._section(6,"Avalancha o bola de nieve","Misma plata, distinto orden. Esta es la diferencia con sus números.")
        estrategias=self._open_panel(7,0,4)
        avalancha,snowball=deuda["avalancha"],deuda["snowball"]
        ui.ayuda(estrategias,f"Presupuesto usado en la proyección: {dinero(deuda['presupuesto_proyectado'])} al mes "
                             "(excedente del mes más los pagos mínimos).",ancho=820).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,Espacio.SM))
        columnas=ui.fila(estrategias); columnas.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.SM))
        for nombre,datos,orden,explicacion in (("Avalancha",avalancha,deuda["orden_avalancha"],"Primero la tasa más alta: es la que hace crecer la deuda más rápido."),
                                               ("Bola de nieve",snowball,deuda["orden_snowball"],"Primero el saldo más pequeño: cuesta un poco más, pero se cierran deudas antes.")):
            columna=ui.panel(columnas,fondo=T.ALT,borde=False); columna.pack(side="left",fill="both",expand=True,padx=(0,8) if nombre=="Avalancha" else (8,0))
            ctk.CTkLabel(columna,text=nombre,font=Tipo.tarjeta()).pack(anchor="w",padx=Espacio.MD,pady=(Espacio.MD,2))
            if datos.get("viable"):
                ui.dato(columna,"Meses hasta cerrar",str(datos.get("months")))
                ui.dato(columna,"Fecha estimada",str(datos.get("debt_free_date")))
                ui.dato(columna,"Intereses totales",dinero(datos.get("total_interest",0)),color=T.WARN)
            else:
                ui.ayuda(columna,datos.get("note","No es posible proyectar con el presupuesto actual."),ancho=340).pack(anchor="w",padx=Espacio.MD)
            ui.ayuda(columna,"Orden: "+", ".join(t["nombre"] for t in orden) if orden else "Sin tarjetas con saldo.",ancho=340).pack(anchor="w",padx=Espacio.MD,pady=(4,0))
            ui.ayuda(columna,explicacion,ancho=340).pack(anchor="w",padx=Espacio.MD,pady=(4,Espacio.MD))
        if avalancha.get("viable") and snowball.get("viable"):
            diferencia=snowball["total_interest"]-avalancha["total_interest"]
            texto=(f"Avalancha ahorra {dinero(abs(diferencia))} en intereses frente a bola de nieve." if diferencia>0
                   else "Con sus números las dos cuestan prácticamente lo mismo: elijan la que les motive más.")
            ui.cuerpo(estrategias,texto,ancho=820).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
        acciones=ui.fila(estrategias); acciones.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
        ui.boton(acciones,"Proyectar una deuda",self.debt_projection_dialog,tono="suave",alto=32).pack(side="left")
        ui.boton(acciones,"Simular un abono",self.scenario_dialog,tono="sutil",alto=32).pack(side="left",padx=8)

    def show_analytics(self)->None:
        """Tendencias que responden preguntas concretas, no gráficas decorativas."""
        self._clear("Análisis","Análisis")
        self._heading("Análisis","Cómo evolucionan sus números y qué explica los cambios.")
        tendencias=self.service.tendencias(self.selected_month,6)
        comparacion=self.service.comparar_meses(self.selected_month)
        perfil=self.service.perfil_financiero(self.selected_month)
        if tendencias["meses_con_datos"]<2:
            self._place(ui.estado_vacio(self.content,"Todavía no hay historial suficiente",
                        "Con dos meses registrados empiezo a mostrar tendencias reales en vez de suposiciones.",
                        accion="Registrar movimientos",comando=self.show_transactions),2,0,4); return
        ingresos=self._open_panel(2,0,2)
        ui.etiqueta(ingresos,"¿Está entrando más o menos plata?").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
        ctk.CTkLabel(ingresos,text=f"Ingresos: {tendencias['ingresos']['direccion']}",font=Tipo.numero_pequeno(),text_color=T.OK).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.barras(ingresos,[(f["mes"][-2:],f["ingresos"]) for f in tendencias["filas"]],formato=dinero,color=T.OK)
        gastos=self._open_panel(2,2,2)
        ui.etiqueta(gastos,"¿Está saliendo más o menos?").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
        ctk.CTkLabel(gastos,text=f"Gastos: {tendencias['gastos']['direccion']}",font=Tipo.numero_pequeno(),
                     text_color=T.BAD if tendencias["gastos"]["direccion"]=="subiendo" else T.OK).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.barras(gastos,[(f["mes"][-2:],f["gastos"]) for f in tendencias["filas"]],formato=dinero,color=T.BAD)
        flujo=self._open_panel(3,0,2)
        ui.etiqueta(flujo,"¿Está quedando algo al final del mes?").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
        ctk.CTkLabel(flujo,text=f"Flujo: {tendencias['flujo']['direccion']}",font=Tipo.numero_pequeno(),text_color=T.PRIMARY).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.barras(flujo,[(f["mes"][-2:],f["flujo"]) for f in tendencias["filas"]],formato=dinero,color=T.PRIMARY)
        ahorro=self._open_panel(3,2,2)
        ui.etiqueta(ahorro,"¿Estamos reservando?").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
        ctk.CTkLabel(ahorro,text=f"Ahorro: {tendencias['ahorro']['direccion']}",font=Tipo.numero_pequeno(),text_color=T.SAVE).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.barras(ahorro,[(f["mes"][-2:],f["ahorro_neto"]) for f in tendencias["filas"]],formato=dinero,color=T.SAVE)
        self._section(4,"¿En qué se va la plata?","Composición del gasto del mes por categoría.")
        composicion=self._open_panel(5,0,2)
        colores=(T.PRIMARY,T.SAVE,T.WARN,T.OK,T.BAD,T.MUTED)
        partes=[(item["categoria"],item["monto"],colores[i%len(colores)]) for i,item in enumerate(perfil["gastos"]["por_categoria"][:6])]
        if partes: ui.distribucion(composicion,partes,formato=dinero)
        else: ui.ayuda(composicion,"Sin gastos registrados este mes.").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=Espacio.LG)
        fijo_variable=self._open_panel(5,2,2)
        ui.etiqueta(fijo_variable,"Fijo contra variable").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
        fijos=perfil["gastos"]["gastos_fijos_registrados"]["total_mensual_equivalente"]
        variable=max(perfil["gastos"]["total"]-perfil["gastos"]["gastos_fijos_registrados"]["registrado_este_mes"],0)
        ui.distribucion(fijo_variable,[("Compromisos fijos",fijos,T.WARN),("Gasto variable",variable,T.PRIMARY)],formato=dinero)
        carga=perfil["gastos"]["carga_fija"]
        ui.ayuda(fijo_variable,(f"Los fijos son {carga:.0%} del ingreso del mes." if carga is not None
                                else "Sin ingreso registrado no puedo expresarlo como porcentaje."),ancho=380).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
        self._section(6,"Qué cambió frente al mes anterior",
                      f"Comparado con {comparacion['mes_comparado']}." if comparacion["suficiente_historial"] else "")
        cambios=self._open_panel(7,0,4)
        if not comparacion["suficiente_historial"]:
            ui.ayuda(cambios,"No hay datos del mes anterior para comparar.").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=Espacio.LG)
        else:
            ui.dato(cambios,"Ingresos",dinero(comparacion["ingresos"]["diferencia"]),
                    color=T.OK if comparacion["ingresos"]["diferencia"]>=0 else T.BAD)
            ui.dato(cambios,"Gastos",dinero(comparacion["gastos"]["diferencia"]),
                    color=T.BAD if comparacion["gastos"]["diferencia"]>0 else T.OK)
            ui.dato(cambios,"Flujo del mes",dinero(comparacion["flujo"]["diferencia"]),
                    color=T.OK if comparacion["flujo"]["diferencia"]>=0 else T.BAD)
            ui.separador(cambios)
            for item in comparacion["principales_subidas"][:3]:
                ui.dato(cambios,f"{item['categoria']} subió","+"+dinero(item["diferencia"]),color=T.WARN)
            for item in comparacion["principales_bajadas"][:2]:
                ui.dato(cambios,f"{item['categoria']} bajó","-"+dinero(abs(item["diferencia"])),color=T.OK)
            ctk.CTkLabel(cambios,text="",height=Espacio.SM).pack()
        utilizacion=self.service.inteligencia_tarjetas(self.selected_month)
        if utilizacion["tarjetas"]:
            self._section(8,"Utilización de tarjetas","Cupo usado por tarjeta. Cupo disponible no es dinero suyo.")
            caja=self._open_panel(9,0,4)
            for tarjeta in utilizacion["tarjetas"]:
                ui.progreso_con_texto(caja,f"{tarjeta['nombre']} · {tarjeta['titular']}",dinero(tarjeta["saldo"]),
                                      dinero(tarjeta["cupo"]),tarjeta["utilizacion"],
                                      detalle=f"{tarjeta['utilizacion']:.0%} · {ui.utilizacion_texto(tarjeta['utilizacion'])}",
                                      color=ui.utilizacion_color(tarjeta["utilizacion"]))
            ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()

    def show_integrity(self)->None:
        """Centro de integridad: visible, explicado y sin alarmismo."""
        self._clear("Integridad","Integridad")
        self._heading("Integridad de los datos","Qué tan confiable es la reconstrucción histórica del libro.")
        informe=self.service.informe_integridad(self.selected_month)
        estado_ok=informe["cuadra"] and not informe["hallazgos_criticos"]
        cabecera=self._open_panel(2,0,4,fondo=T.OK_SOFT if estado_ok else T.WARN_SOFT)
        cabecera.configure(border_width=0)
        ui.insignia(cabecera,"Todo cuadra" if estado_ok else "Requiere revisión",tono="positiva" if estado_ok else "media").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ctk.CTkLabel(cabecera,text=informe["mensaje"],font=Tipo.asesor(),text_color=T.TXT,wraplength=840,justify="left").pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.ayuda(cabecera,informe["advertencia"],ancho=840).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(6,Espacio.MD))
        self._metric(3,0,"Diferencia histórica",dinero(informe["descuadre_absoluto"]),
                     "Entre aportes, consumos y deuda pendiente",T.WARN if informe["descuadre"] else T.OK)
        self._metric(3,1,"Hallazgos críticos",str(len(informe["hallazgos_criticos"])),"Revisar antes de liquidar entre ustedes",
                     T.BAD if informe["hallazgos_criticos"] else T.OK)
        self._metric(3,2,"Puntos de atención",str(len(informe["hallazgos_atencion"])),"Detalles menores por ordenar",T.WARN if informe["hallazgos_atencion"] else T.OK)
        self._metric(3,3,"Datos faltantes",str(len(informe["datos_faltantes"])),
                     ", ".join(informe["datos_faltantes"]) or "Nada pendiente",T.MUTED)
        if informe["candidatos_descuadre"]:
            self._section(4,"Movimientos que revisaría primero","Son pistas, no culpables: no atribuyo una causa sin evidencia.")
            caja=self._open_panel(5,0,4)
            for candidato in informe["candidatos_descuadre"][:10]:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=6)
                cabecera_item=ui.fila(bloque); cabecera_item.pack(fill="x")
                ctk.CTkLabel(cabecera_item,text=f"{candidato['tipo'].replace('_',' ').capitalize()} #{candidato['id']} · {candidato['nombre']}",
                             font=ui.fuente(13,"bold"),text_color=T.TXT,wraplength=640,justify="left").pack(side="left")
                ctk.CTkLabel(cabecera_item,text=dinero(candidato["valor"]),font=Tipo.ayuda(),text_color=T.MUTED).pack(side="right")
                ui.ayuda(bloque,candidato["motivo"],ancho=820).pack(anchor="w")
            ui.boton(caja,"Ver movimientos",self.show_transactions,tono="suave",alto=32).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.SM,Espacio.MD))
        problemas=informe["hallazgos_criticos"]+informe["hallazgos_atencion"]
        if problemas:
            self._section(6,"Controles automáticos")
            caja=self._open_panel(7,0,4)
            for item in problemas[:10]:
                bloque=ui.fila(caja); bloque.pack(fill="x",padx=Espacio.PANEL_PAD,pady=5)
                ui.insignia(bloque,"Crítico" if item["severity"]=="critical" else "Atención",
                            tono="critica" if item["severity"]=="critical" else "media").pack(side="left",padx=(0,10))
                ctk.CTkLabel(bloque,text=item["detail"],font=Tipo.cuerpo(),text_color=T.TXT,wraplength=720,justify="left").pack(side="left")
            ctk.CTkLabel(caja,text="",height=Espacio.SM).pack()
        controles=self._open_panel(8,0,4)
        ui.etiqueta(controles,"Qué se revisó").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ui.ayuda(controles,", ".join(informe["controles"])+".",ancho=840).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))

    def show_third_parties(self)->None:
        self._clear("Terceros","Terceros"); self.add_button.configure(text="＋ Agregar tercero",command=self.show_third_party_dialog)
        self._heading("Terceros","Registra bancos o personas por separado. Sus obligaciones no se convierten en gastos."); d=self.service.terceros()
        self._card(2,0,"Por cobrar",dinero(d["por_cobrar"]),"Dinero a favor",T.OK,2); self._card(2,2,"Por pagar",dinero(d["por_pagar"]),"Obligaciones pendientes",T.WARN,2)
        acciones=self._panel(3,0,4); ctk.CTkLabel(acciones,text="REGISTRAR SALDOS",font=ui.fuente(12,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        ctk.CTkLabel(acciones,text="Indica si le deben a ustedes o si ustedes le deben al banco o persona. No se registra como gasto.",text_color=T.MUTED).pack(anchor="w",padx=18)
        botones=ctk.CTkFrame(acciones,fg_color="transparent"); botones.pack(anchor="w",padx=18,pady=(10,16))
        ctk.CTkButton(botones,text="＋ Registrar cuánto debemos / nos deben",fg_color=T.PRIMARY,command=self.show_third_party_account_dialog).pack(side="left",padx=(0,8))
        ctk.CTkButton(botones,text="Registrar abono",fg_color=T.ALT,text_color=T.TXT,command=self.show_third_party_payment_dialog).pack(side="left")
        registrados=self._panel(4,0,4); ctk.CTkLabel(registrados,text="BANCOS Y PERSONAS REGISTRADOS",font=ui.fuente(12,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,7))
        if d["registrados"]:
            for tercero in d["registrados"]:
                tipo="Banco" if tercero["tipo"]=="BANCO" else "Persona"; contacto=f" · {tercero['contacto']}" if tercero.get("contacto") else ""
                ctk.CTkLabel(registrados,text=f"{tipo}  ·  {tercero['nombre']}{contacto}",text_color=T.TXT).pack(anchor="w",padx=18,pady=3)
            ctk.CTkFrame(registrados,height=8,fg_color="transparent").pack()
        else: ctk.CTkLabel(registrados,text="Aún no hay terceros. Usa “Agregar tercero” para crear un banco o una persona.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,16))
        inicio=5
        if not d["detalle"]: self._empty(inicio,"No hay obligaciones abiertas","Selecciona “Registrar cuánto debemos / nos deben” para añadir la primera.",self.show_third_party_account_dialog)
        for i,x in enumerate(d["detalle"]): self._card(inicio+i,0,x["tercero"],dinero(x["saldo_pendiente"]),f"{x['tipo_tercero'].title()} · {x['tipo']} · {NOMBRES[x['propietario']]}",T.TXT,4)

    def show_third_party_dialog(self)->None:
        dialog=ctk.CTkToplevel(self,fg_color=T.BG); dialog.title("Agregar tercero"); dialog.geometry("470x410"); dialog.grab_set()
        body=ctk.CTkFrame(dialog,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="Agregar tercero",font=Tipo.dialogo()).pack(anchor="w",padx=20,pady=(20,2))
        ctk.CTkLabel(body,text="Esto solo crea el banco o la persona; no registra un gasto.",text_color=T.MUTED,wraplength=400,justify="left").pack(anchor="w",padx=20)
        nombre=self._entry(body,"Nombre",""); tipo=self._entry(body,"Es un","Persona",["Persona","Banco"]); contacto=self._entry(body,"Contacto o referencia (opcional)","")
        def save()->None:
            self.service.crear_tercero(self._value(nombre),self._value(tipo).upper(),self._value(contacto)); dialog.destroy(); self.show_third_parties()
        ctk.CTkButton(body,text="Guardar tercero",fg_color=T.PRIMARY,command=lambda:self._run(save,"Tercero registrado")).pack(fill="x",padx=20,pady=20)

    def show_third_party_account_dialog(self)->None:
        terceros=self.service.terceros()["registrados"]
        if not terceros:
            messagebox.showinfo("Primero agrega un tercero","Crea primero el banco o la persona a quien corresponde la cuenta.",parent=self); return
        dialog=ctk.CTkToplevel(self,fg_color=T.BG); dialog.title("Registrar cuenta con tercero"); dialog.geometry("510x610"); dialog.grab_set()
        body=ctk.CTkFrame(dialog,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="Registrar cuenta pendiente",font=Tipo.dialogo()).pack(anchor="w",padx=20,pady=(20,2))
        ctk.CTkLabel(body,text="El saldo queda en Terceros y no afecta tus gastos mensuales.",text_color=T.MUTED,wraplength=440,justify="left").pack(anchor="w",padx=20)
        etiquetas=[f"{x['id']} · {x['nombre']} ({'Banco' if x['tipo']=='BANCO' else 'Persona'})" for x in terceros]
        tercero=self._entry(body,"Banco o persona",etiquetas[0],etiquetas); situacion=self._entry(body,"Situación","Le debemos",["Le debemos", "Nos debe"])
        monto=self._entry(body,"Monto inicial COP"); propietario=self._entry(body,"¿De quién es esta cuenta?","Samuel",["Samuel","Sara"]); concepto=self._entry(body,"Concepto","Préstamo / saldo inicial")
        def save()->None:
            seleccionado=next(x for x in terceros if str(x["id"])==self._value(tercero).split(" · ",1)[0])
            self.service.crear_prestamo_tercero({"mes":self.selected_month,"fecha":dt.date.today().isoformat(),"tercero":seleccionado["nombre"],"tipo":"POR_PAGAR" if self._value(situacion)=="Le debemos" else "POR_COBRAR","monto":self._value(monto),"propietario":SAMUEL if self._value(propietario)=="Samuel" else SARA,"concepto":self._value(concepto)})
            dialog.destroy(); self.show_third_parties()
        ctk.CTkButton(body,text="Guardar cuenta pendiente",fg_color=T.PRIMARY,command=lambda:self._run(save,"Cuenta pendiente registrada")).pack(fill="x",padx=20,pady=20)

    def show_third_party_payment_dialog(self)->None:
        cuentas=self.service.terceros()["detalle"]
        if not cuentas:
            messagebox.showinfo("No hay cuentas pendientes","Primero registra cuánto deben o cuánto les deben.",parent=self); return
        dialog=ctk.CTkToplevel(self,fg_color=T.BG); dialog.title("Registrar abono"); dialog.geometry("470x430"); dialog.grab_set()
        body=ctk.CTkFrame(dialog,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="Registrar abono",font=Tipo.dialogo()).pack(anchor="w",padx=20,pady=(20,2))
        ctk.CTkLabel(body,text="Registra un pago o un cobro parcial para reducir el saldo pendiente.",text_color=T.MUTED,wraplength=400,justify="left").pack(anchor="w",padx=20)
        etiquetas=[f"{x['id']} · {x['tercero']} · saldo {dinero(x['saldo_pendiente'])}" for x in cuentas]
        cuenta=self._entry(body,"Cuenta",etiquetas[0],etiquetas); monto=self._entry(body,"Monto del abono COP")
        def save()->None:
            cuenta_id=self._value(cuenta).split(" · ",1)[0]
            self.service.crear_abono_tercero({"mes":self.selected_month,"fecha":dt.date.today().isoformat(),"prestamo_id":cuenta_id,"monto":self._value(monto)})
            dialog.destroy(); self.show_third_parties()
        ctk.CTkButton(body,text="Guardar abono",fg_color=T.PRIMARY,command=lambda:self._run(save,"Abono registrado")).pack(fill="x",padx=20,pady=20)
    def show_settlement(self)->None:
        self._clear("Liquidación","Liquidación");self._heading("Balance de pareja","El saldo entre ustedes usa todo el historial activo. Los ingresos actualizan la liquidez, no crean por sí solos una deuda entre ustedes.");estado=self.service.estado_actual(self.selected_month); explicaciones={persona:self.service.explicacion(persona) for persona in (SAMUEL,SARA)}
        samuel,sara=explicaciones[SAMUEL]["balance_neto"],explicaciones[SARA]["balance_neto"]
        for i,(p,label) in enumerate(((SAMUEL,"Samuel"),(SARA,"Sara"))):v=explicaciones[p]["balance_neto"];self._card(2,i*2,label,dinero(abs(v)),("A favor" if v>0 else "Pendiente de equilibrar" if v<0 else "Equilibrado")+f" · Liquidez del mes: {dinero(estado['liquidity']['by_person'][p]['liquidez'])}",T.OK if v>=0 else T.WARN,2)
        liquidacion:tuple[str,str,int]|None=None
        if samuel>0 and sara<0:
            monto_equilibrio=min(samuel,abs(sara));resumen=f"Sara debería pagarle a Samuel {dinero(monto_equilibrio)} para equilibrar los movimientos registrados.";liquidacion=(SARA,SAMUEL,monto_equilibrio)
        elif sara>0 and samuel<0:
            monto_equilibrio=min(sara,abs(samuel));resumen=f"Samuel debería pagarle a Sara {dinero(monto_equilibrio)} para equilibrar los movimientos registrados.";liquidacion=(SAMUEL,SARA,monto_equilibrio)
        else: resumen="No hay un pago pendiente entre Samuel y Sara con los movimientos registrados."
        decision=self._panel(3,0,4);ctk.CTkLabel(decision,text="RESULTADO",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(15,3));ctk.CTkLabel(decision,text=resumen,font=Tipo.seccion(),text_color=T.OK if liquidacion is None else T.PRIMARY,wraplength=860,justify="left").pack(anchor="w",padx=18,pady=(0,8))
        if liquidacion:
            deudor,acreedor,monto_equilibrio=liquidacion;nombre_deudor=NOMBRES[deudor];nombre_acreedor=NOMBRES[acreedor]
            ctk.CTkLabel(decision,text="Cuando la transferencia ya esté hecha, regístrala aquí. No es un ingreso ni un gasto.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,8))
            def registrar_pago_balance()->None:
                mensaje=(f"Confirma que {nombre_deudor} ya le transfirió {dinero(monto_equilibrio)} a {nombre_acreedor}.\n\n"
                         "Se registrará como pago de balance entre ustedes; no como ingreso ni gasto.")
                if not messagebox.askyesno("Registrar pago de balance",mensaje,parent=self):return
                self.service.crear_liquidacion({"mes":self.selected_month,"fecha":dt.date.today().isoformat(),"deudor":deudor,"acreedor":acreedor,"monto":str(monto_equilibrio),"concepto":"Pago para equilibrar el balance de pareja"})
                self.show_settlement()
            ctk.CTkButton(decision,text=f"Registrar pago a {nombre_acreedor} · {dinero(monto_equilibrio)}",height=34,fg_color=T.PRIMARY,command=lambda:self._run(registrar_pago_balance,"Pago de balance registrado")).pack(anchor="w",padx=18,pady=(0,15))
        else: ctk.CTkFrame(decision,height=7,fg_color="transparent").pack()
        razones=self._panel(4,0,4);ctk.CTkLabel(razones,text="¿POR QUÉ?",text_color=T.MUTED,font=ui.fuente(10,"bold")).pack(anchor="w",padx=18,pady=(15,5));ctk.CTkLabel(razones,text="Cada línea muestra qué pagó o qué le correspondía a cada persona.",text_color=T.MUTED).pack(anchor="w",padx=18)
        for persona,label in ((SAMUEL,"Samuel"),(SARA,"Sara")):
            explicacion=explicaciones[persona]["lineas"]
            if not explicacion: continue
            ctk.CTkLabel(razones,text=label,font=ui.fuente(14,"bold"),text_color=T.PRIMARY if persona==SAMUEL else T.SAVE).pack(anchor="w",padx=18,pady=(12,2))
            for linea in explicacion:
                signo="+" if linea["efecto"]>=0 else "−"
                ctk.CTkLabel(razones,text=f"{signo}{dinero(abs(linea['efecto']))} · {linea['detalle']}",text_color=T.TXT,wraplength=850,justify="left").pack(anchor="w",padx=26,pady=2)
        ctk.CTkFrame(razones,height=10,fg_color="transparent").pack()

    def show_investments(self)->None:
        self._clear("Inversiones","Inversiones")
        self._heading("Inversiones","Capital invertido, movimientos, valor actual y evolución registrada.")
        self.add_button.configure(text="＋ Nueva inversión",command=self.investment_create_dialog)
        resumen=self.service.resumen_inversiones()
        self._metric(2,0,"Valor actual",dinero(resumen["total"]),"Capital + variaciones registradas",T.PRIMARY)
        self._metric(2,1,"Aportes",dinero(resumen["aportes"]),f"{resumen['cantidad']} inversión(es)",T.OK)
        self._metric(2,2,"Retiros",dinero(resumen["retiros"]),"Salidas registradas",T.WARN)
        self._metric(2,3,"Variación",dinero(resumen["valoraciones"]),"Solo valoraciones registradas",T.SAVE if resumen["valoraciones"]>=0 else T.BAD)
        if not resumen["inversiones"]:
            self._place(ui.estado_vacio(self.content,"Todavía no hay inversiones","Crea una inversión para comenzar a registrar aportes, retiros y valoraciones.",accion="＋ Crear inversión",comando=self.investment_create_dialog,icono="◎"),3,0,4)
            return
        self._section(3,"Portafolio","Cada valor se deriva exclusivamente de los movimientos registrados.")
        for indice,inv in enumerate(resumen["inversiones"]):
            caja=self._open_panel(4+indice//2,indice%2*2,2)
            cab=ui.fila(caja); cab.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,2))
            ctk.CTkLabel(cab,text=inv["nombre"],font=Tipo.tarjeta(),text_color=T.TXT).pack(side="left")
            ui.insignia(cab,inv["clase"].upper(),tono="suave").pack(side="right")
            ui.ayuda(caja,f'Titular: {inv["titular"]} · Riesgo: {inv["riesgo"]} · Liquidez: {inv["liquidez"]}',ancho=420).pack(anchor="w",padx=Espacio.PANEL_PAD)
            ctk.CTkLabel(caja,text=dinero(inv["valor_actual"]),font=Tipo.numero(),text_color=T.PRIMARY).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(8,2))
            ctk.CTkLabel(caja,text=f'Aportes {dinero(inv["aportes"])} · retiros {dinero(inv["retiros"])} · variación {dinero(inv["valoraciones"])}',text_color=T.MUTED).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,8))
            fila=ui.fila(caja); fila.pack(fill="x",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
            ctk.CTkButton(fila,text="＋ Aporte",height=32,fg_color=T.PRIMARY,command=lambda x=inv["id"]: self.investment_movement_dialog(x,"APORTE")).pack(side="left",padx=(0,5))
            ctk.CTkButton(fila,text="− Retiro",height=32,fg_color=T.ALT,text_color=T.TXT,command=lambda x=inv["id"]: self.investment_movement_dialog(x,"RETIRO")).pack(side="left",padx=5)
            ctk.CTkButton(fila,text="↗ Valorar",height=32,fg_color=T.ALT,text_color=T.TXT,command=lambda x=inv["id"]: self.investment_movement_dialog(x,"VALORACION")).pack(side="left",padx=5)

    def investment_create_dialog(self)->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title("Nueva inversión"); d.geometry("560x700"); d.minsize(460,600); d.grab_set()
        body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=Espacio.RADIO); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="◎ Nueva inversión",font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
        name=self._entry(body,"Nombre"); clase=self._entry(body,"Clase","cdt",["cdt","fic","etf","accion","bono","efectivo","pension","otro"])
        titular=self._entry(body,"Titular","persona1",["persona1","persona2","compartido"]); riesgo=self._entry(body,"Riesgo","medio",["bajo","medio","alto"])
        liquidez=self._entry(body,"Liquidez","media",["alta","media","baja"]); entidad=self._entry(body,"Entidad (opcional)")
        horizonte=self._entry(body,"Horizonte en meses (opcional)"); comision=self._entry(body,"Comisión anual (puntos básicos)","0")
        apertura=self._entry(body,"Fecha de apertura (AAAA-MM-DD, opcional)"); vencimiento=self._entry(body,"Fecha de vencimiento (AAAA-MM-DD, opcional)")
        def save()->None:
            h=self._value(horizonte); payload={"nombre":self._value(name),"clase":self._value(clase),"titular":self._value(titular),"riesgo":self._value(riesgo),"liquidez":self._value(liquidez),"entidad":self._value(entidad) or None,"horizonte_meses":int(h) if h else None,"comision_pb_anual":int(self._value(comision) or 0),"fecha_apertura":self._value(apertura) or None,"fecha_vencimiento":self._value(vencimiento) or None}
            self.service.crear_inversion(payload); d.destroy(); self.show_investments()
        ctk.CTkButton(body,text="Crear inversión",height=40,fg_color=T.PRIMARY,command=lambda:self._run(save,"Inversión creada")).pack(fill="x",padx=18,pady=22)

    def investment_movement_dialog(self,inversion_id:int,tipo:str)->None:
        d=ctk.CTkToplevel(self,fg_color=T.BG); d.title(tipo.title()); d.geometry("460x360"); d.grab_set()
        body=ctk.CTkFrame(d,fg_color=T.S); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text=tipo.title(),font=Tipo.dialogo()).pack(anchor="w",padx=18,pady=(18,2))
        monto=self._entry(body,"Monto COP"); fecha=self._entry(body,"Fecha (AAAA-MM-DD, opcional)")
        def save()->None:
            self.service.registrar_movimiento_inversion(inversion_id,tipo,self._value(monto),self._value(fecha) or None); d.destroy(); self.show_investments()
        ctk.CTkButton(body,text="Guardar movimiento",height=40,fg_color=T.PRIMARY,command=lambda:self._run(save,"Movimiento registrado")).pack(fill="x",padx=18,pady=22)

    def show_financial_os(self)->None:
        self._clear("Plan financiero","Plan financiero")
        self._heading("Plan financiero","Una sola foto para avanzar desde las deudas hasta la construcción de patrimonio.")
        estado=self.service.financial_os(self.selected_month)
        resumen=estado["resumen"]
        self._card(2,0,"Dinero libre",dinero(resumen["dinero_libre"]),"Margen registrado para decidir",T.OK,2)
        self._card(2,2,"Deuda total",dinero(resumen["deuda_total"]),"Tarjetas + otras deudas",T.WARN,2)
        self._card(3,0,"Fondo de emergencia",f'{resumen["emergencia_cobertura"]:.1f} meses',"Cobertura registrada",T.SAVE,2)
        self._card(3,2,"Inversión actual",dinero(resumen["inversion_actual"]),"Valor derivado de movimientos",T.PRIMARY,2)
        caja=self._panel(4,0,4)
        ctk.CTkLabel(caja,text="ORDEN FINANCIERO",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,8))
        for item in estado["prioridades"]:
            fila=ui.fila(caja); fila.pack(fill="x",padx=18,pady=5)
            ui.insignia(fila,str(item["orden"]),tono="suave").pack(side="left",padx=(0,10))
            ctk.CTkLabel(fila,text=item["titulo"],font=ui.fuente(13,"bold"),text_color=T.TXT).pack(side="left")
            ctk.CTkLabel(fila,text=item["detalle"],text_color=T.MUTED,wraplength=600,justify="left").pack(side="left",padx=12)
        deuda=self._panel(5,0,4)
        ctk.CTkLabel(deuda,text="SALIDA DE DEUDAS",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        p=estado["plan_deuda"]["avalancha"]; s=estado["plan_deuda"]["bola_de_nieve"]
        ctk.CTkLabel(deuda,text=f'Presupuesto mensual: {dinero(estado["plan_deuda"]["presupuesto_mensual"])}',text_color=T.TXT).pack(anchor="w",padx=18)
        meses_avalancha=p.get("meses",p.get("months"))
        texto_avalancha=(f'Avalancha: {meses_avalancha} meses · interés proyectado {dinero(p.get("intereses_proyectados",p.get("total_interest",0)))}' if meses_avalancha else p.get("note",p.get("mensaje","No hay una simulación viable con el presupuesto actual.")))
        meses_nieve=s.get("meses",s.get("months"))
        texto_nieve=(f'Bola de nieve: {meses_nieve} meses · interés proyectado {dinero(s.get("intereses_proyectados",s.get("total_interest",0)))}' if meses_nieve else s.get("note",s.get("mensaje","No hay una simulación viable con el presupuesto actual.")))
        ctk.CTkLabel(deuda,text=texto_avalancha,text_color=T.TXT).pack(anchor="w",padx=18,pady=5)
        ctk.CTkLabel(deuda,text=texto_nieve,text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,14))
        op=self._panel(6,0,4)
        ctk.CTkLabel(op,text="CONTROL OPERATIVO",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,6))
        tarjetas_op=estado.get("tarjetas_operativo",{})
        ctk.CTkLabel(op,text=f'Tarjetas: {dinero(tarjetas_op.get("deuda_total",0))} de deuda · mínimos {dinero(tarjetas_op.get("pago_minimo_total",0))} · pagado este mes {dinero(tarjetas_op.get("pagado_mes",0))}',
                     text_color=T.TXT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=3)
        faltante=tarjetas_op.get("faltante_minimos",0)
        ctk.CTkLabel(op,text=(f'⚠ Faltan {dinero(faltante)} para cubrir mínimos registrados.' if faltante else "✓ Los mínimos registrados de tarjetas están cubiertos."),
                     text_color=T.WARN if faltante else T.OK).pack(anchor="w",padx=18,pady=3)
        asignacion=estado.get("asignacion_margen",{})
        sugerida=asignacion.get("asignacion_sugerida",{})
        ctk.CTkLabel(op,text=f'Margen asignado: mínimos {dinero(sugerida.get("minimos_deuda",0))} · extra deuda {dinero(sugerida.get("extra_deuda",0))} · emergencia {dinero(sugerida.get("emergencia",0))} · inversión {dinero(sugerida.get("inversion",0))}',
                     text_color=T.MUTED,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(3,8))
        radar=estado.get("radar_financiero",{})
        ctk.CTkLabel(op,text="RADAR DEL MES",font=ui.fuente(10,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(4,5))
        fila_radar=ui.fila(op); fila_radar.pack(fill="x",padx=18,pady=(0,7))
        for señal in radar.get("señales",[]):
            tono=T.OK if señal["estado"]=="ok" else T.WARN if señal["estado"]=="atencion" else T.BAD
            celda=ctk.CTkFrame(fila_radar,fg_color=T.SUNKEN,corner_radius=Espacio.RADIO_SM)
            celda.pack(side="left",fill="both",expand=True,padx=(0,6))
            ctk.CTkLabel(celda,text=señal["titulo"],font=ui.fuente(9,"bold"),text_color=T.MUTED).pack(anchor="w",padx=10,pady=(8,1))
            ctk.CTkLabel(celda,text=señal["valor"],font=ui.fuente(11,"bold"),text_color=tono).pack(anchor="w",padx=10)
            ctk.CTkLabel(celda,text=señal["detalle"],font=ui.fuente(9),text_color=T.FAINT,wraplength=150,justify="left").pack(anchor="w",padx=10,pady=(1,8))
        ctk.CTkLabel(op,text=f'→ Próxima acción: {radar.get("proxima_accion","Revisar el mes.")}',
                     font=ui.fuente(10,"bold"),text_color=T.TXT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(2,14))
        emerg=self._panel(7,0,2)
        ctk.CTkLabel(emerg,text="FONDO DE EMERGENCIA",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        e=estado["emergencia"]
        ctk.CTkLabel(emerg,text=f'Actual: {dinero(e["actual"])} · {e["cobertura_meses"]:.1f} meses',font=Tipo.seccion(),text_color=T.SAVE).pack(anchor="w",padx=18,pady=5)
        ctk.CTkLabel(emerg,text=f'Meta base: {dinero(e["objetivos"]["base"])} · faltan {dinero(e["faltante_base"])}',text_color=T.TXT).pack(anchor="w",padx=18,pady=(0,16))
        inv=self._panel(7,2,2)
        ctk.CTkLabel(inv,text="INVERSIONES",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        iv=estado["inversiones"]
        ctk.CTkLabel(inv,text=dinero(iv["total"]),font=Tipo.numero(),text_color=T.PRIMARY).pack(anchor="w",padx=18,pady=5)
        ctk.CTkLabel(inv,text=f'{iv["cantidad"]} inversión(es) · aportes {dinero(iv["aportes"])} · variación registrada {dinero(iv["valoraciones"])}',text_color=T.TXT,wraplength=390,justify="left").pack(anchor="w",padx=18,pady=(0,16))

        decisiones=self._panel(8,0,4)
        ctk.CTkLabel(decisiones,text="DECISIONES DEL MES",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,6))
        mensual=estado.get("plan_mensual_deuda",{})
        alloc=estado.get("asignacion_margen",{}).get("asignacion_sugerida",{})
        ctk.CTkLabel(decisiones,text=f'Capacidad para deuda: {dinero(mensual.get("disponible",0))} · mínimos: {dinero(mensual.get("total_minimos",0))} · extra: {dinero(mensual.get("extra_sobre_minimos",0))}',text_color=T.TXT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=3)
        ctk.CTkLabel(decisiones,text=f'Propuesta: deuda {dinero(alloc.get("extra_deuda",0))} · emergencia {dinero(alloc.get("emergencia",0))} · inversión {dinero(alloc.get("inversion",0))}',text_color=T.MUTED,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=3)
        fases=estado.get("mapa_accion",{}).get("fases",[])
        activa=fases[0] if fases else {}
        ctk.CTkLabel(decisiones,text=f'FASE ACTIVA · {str(activa.get("clave","sin datos")).upper()}',font=ui.fuente(10,"bold"),text_color=T.PRIMARY).pack(anchor="w",padx=18,pady=(6,1))
        ctk.CTkLabel(decisiones,text=activa.get("accion","Revisar el radar del mes."),text_color=T.TXT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(0,14))

        futuro=self._panel(9,0,2)
        ctk.CTkLabel(futuro,text="PROYECCIÓN",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        proy=estado.get("proyeccion",{})
        ctk.CTkLabel(futuro,text=dinero(proy.get("valor_proyectado",0)),font=Tipo.numero(),text_color=T.PRIMARY).pack(anchor="w",padx=18,pady=4)
        ctk.CTkLabel(futuro,text=f'En {proy.get("meses",60)} meses · aportado {dinero(proy.get("aportado",0))} · ganancia modelada {dinero(proy.get("ganancia_proyectada",0))}',text_color=T.TXT,wraplength=390,justify="left").pack(anchor="w",padx=18,pady=(0,4))
        ctk.CTkLabel(futuro,text="Escenario matemático a tasa 0%; no representa un rendimiento garantizado.",text_color=T.FAINT,wraplength=390,justify="left").pack(anchor="w",padx=18,pady=(0,16))

        ready_panel=self._panel(9,2,2)
        ctk.CTkLabel(ready_panel,text="PREPARACIÓN PARA INVERTIR",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        ready=estado.get("preparacion_inversion",{})
        listo=bool(ready.get("listo_para_invertir"))
        ctk.CTkLabel(ready_panel,text="LISTO" if listo else "EN CONSTRUCCIÓN",font=Tipo.numero(),text_color=T.OK if listo else T.WARN).pack(anchor="w",padx=18,pady=4)
        ctk.CTkLabel(ready_panel,text=f'Margen mensual {dinero(ready.get("margen_mensual",0))} · ahorro acumulado {dinero(ready.get("ahorro_acumulado",0))}',text_color=T.TXT,wraplength=390,justify="left").pack(anchor="w",padx=18,pady=(0,4))
        ctk.CTkLabel(ready_panel,text=" · ".join(ready.get("razones",[])) or "No hay bloqueos registrados.",text_color=T.MUTED,wraplength=390,justify="left").pack(anchor="w",padx=18,pady=(0,16))

        patrimonio_panel=self._panel(10,0,4)
        ctk.CTkLabel(patrimonio_panel,text="PATRIMONIO",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        patrimonio=estado.get("patrimonio",{})
        ctk.CTkLabel(patrimonio_panel,text=f'Patrimonio neto: {dinero(patrimonio.get("patrimonio_liquido",0))} · por cobrar: {dinero(patrimonio.get("cuentas_por_cobrar",0))} · por pagar: {dinero(patrimonio.get("cuentas_por_pagar",0))}',text_color=T.TXT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(0,5))
        ctk.CTkLabel(patrimonio_panel,text=f'Liquidez operativa: {dinero(patrimonio.get("liquidez_operativa",0))} · reservado: {dinero(patrimonio.get("reservado_metas",0))} · gastos fijos pendientes: {dinero(patrimonio.get("gastos_fijos_pendientes",0))}',text_color=T.MUTED,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(0,14))

        historial=self._panel(11,0,4)
        ctk.CTkLabel(historial,text="TRAYECTORIA PATRIMONIAL",font=ui.fuente(11,"bold"),text_color=T.MUTED).pack(anchor="w",padx=18,pady=(16,5))
        trayectoria=estado.get("trayectoria_patrimonio",{}).get("meses",[])
        if trayectoria:
            puntos=trayectoria[-3:]
            texto=" · ".join(f'{x.get("mes","")} {dinero(x.get("patrimonio_neto",x.get("neto",0)))}' for x in puntos)
            ctk.CTkLabel(historial,text=texto,text_color=T.TXT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(0,4))
            ctk.CTkLabel(historial,text="Lectura compacta de la evolución patrimonial registrada.",text_color=T.FAINT,wraplength=820,justify="left").pack(anchor="w",padx=18,pady=(0,14))
        else:
            ctk.CTkLabel(historial,text="Todavía no hay suficientes movimientos para formar una trayectoria.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,14))

    def show_settings(self)->None:
        """Preferencias locales. Aquí no se modifica ningún dato financiero."""
        self._clear("Ajustes","Ajustes")
        self._heading("Ajustes","Preferencias de esta aplicación, guardadas solo en este equipo.")
        apariencia=self._open_panel(2,0,2)
        ui.etiqueta(apariencia,"Apariencia").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ui.cuerpo(apariencia,"Una paleta clara y otra pensada para la noche.",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.boton(apariencia,"Cambiar a modo claro" if self.theme=="dark" else "Cambiar a modo oscuro",
                 self.toggle_theme,tono="suave").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=Espacio.MD)

        asesor=self._open_panel(2,2,2)
        ui.etiqueta(asesor,"Asesor").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ui.cuerpo(asesor,"Margen de seguridad que el asesor reserva antes de decir cuánto pueden gastar.",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD)
        margen_actual=str(self.pref.get("margen_seguridad",10))
        margen=self._entry(asesor,"Margen de seguridad (% del ingreso)",margen_actual,["0","5","10","15","20"])
        def guardar_margen()->None:
            self.pref["margen_seguridad"]=int(self._value(margen)); guardar_preferencias(self.pref)
        ui.boton(asesor,"Guardar preferencia",lambda:self._run(guardar_margen,"Preferencia guardada"),tono="suave").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=Espacio.MD)

        candidatos=self._open_panel(3,0,2)
        ui.etiqueta(candidatos,"Sugerencias descartadas").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        descartados=sorted(self._candidatos_descartados())
        if descartados:
            ui.cuerpo(candidatos,"No vuelvo a sugerir: "+", ".join(descartados),ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD)
            def restaurar()->None:
                self.pref["candidatos_descartados"]=[]; guardar_preferencias(self.pref); self.show_settings()
            ui.boton(candidatos,"Volver a sugerirlas",lambda:self._run(restaurar,"Sugerencias restauradas"),tono="sutil").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        else:
            ui.cuerpo(candidatos,"No han descartado ninguna sugerencia de gasto fijo.",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))

        datos=self._open_panel(3,2,2)
        ui.etiqueta(datos,"Datos").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ui.cuerpo(datos,"Todo vive en SQLite en este equipo: sin nube, sin cuentas, sin sincronización.",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD)
        ui.ayuda(datos,"Las acciones que modifican dinero viven en sus pantallas correspondientes, no aquí.",ancho=400).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(4,0))
        acciones=ui.fila(datos); acciones.pack(fill="x",padx=Espacio.PANEL_PAD,pady=Espacio.MD)
        ui.boton(acciones,"Revisar integridad",self.show_integrity,tono="suave").pack(side="left")
        ui.boton(acciones,"Recalcular análisis",self.refresh,tono="sutil").pack(side="left",padx=8)

        personas=self._open_panel(4,0,4)
        ui.etiqueta(personas,"Personas").pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(Espacio.MD,4))
        ui.cuerpo(personas,"La aplicación está construida para dos personas: Samuel y Sara. Cada movimiento distingue "
                           "quién puso el dinero de quién asume el gasto, y esa diferencia no se deduce sola.",ancho=840).pack(anchor="w",padx=Espacio.PANEL_PAD,pady=(0,Espacio.MD))
if __name__ == "__main__":
    app = FinanzasApp()
    app.mainloop()
