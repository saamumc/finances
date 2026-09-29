"""Frontend premium, local y accesible para las finanzas de Samuel y Sara."""
from __future__ import annotations
import datetime as dt
from collections.abc import Callable
from typing import Any
try:
    import customtkinter as ctk
except ModuleNotFoundError as exc:
    raise RuntimeError("Instala las dependencias con: pip install -r requirements.txt") from exc
from tkinter import messagebox
from constants import PERSONA1, PERSONA2
from frontend.service import FinanceService, NOMBRES, cargar_preferencias, dinero, guardar_preferencias, mes_actual, parsear_dinero


class T:
    """Tokens de diseño. Cada valor es (light, dark)."""
    BG=("#F5F7FB", "#10141C"); S=("#FFFFFF", "#19202B"); ALT=("#EDF1F7", "#222C3A")
    TXT=("#172033", "#F2F5FA"); MUTED=("#6D7890", "#9AA7BB"); BORDER=("#E2E7EF", "#2B3748")
    PRIMARY=("#536DFE", "#8496FF"); OK=("#20966B", "#56C997"); WARN=("#C67A16", "#F0B45E"); BAD=("#C84955", "#FF8992"); SAVE=("#7764D8", "#AA9BFF")


class FinanzasApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(); self.pref=cargar_preferencias(); self.theme=self.pref.get("tema", "dark")
        ctk.set_appearance_mode(self.theme); ctk.set_default_color_theme("blue")
        self.service=FinanceService(); self.selected_month=mes_actual(); self.title("Lúmina · Finanzas de Samuel & Sara")
        self.geometry("1360x860"); self.minsize(1060, 700); self.configure(fg_color=T.BG)
        self._shell(); self.show_dashboard()

    def _shell(self) -> None:
        self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)
        side=ctk.CTkFrame(self, width=244, corner_radius=0, fg_color=T.S); side.grid(row=0,column=0,sticky="nsew"); side.grid_propagate(False)
        ctk.CTkLabel(side,text="LÚMINA",font=ctk.CTkFont(size=23,weight="bold"),text_color=T.PRIMARY).pack(anchor="w",padx=26,pady=(30,0))
        ctk.CTkLabel(side,text="Finanzas de nosotros",text_color=T.MUTED).pack(anchor="w",padx=26,pady=(0,28))
        self.nav={}
        for name,icon,fn in (("Inicio","⌂",self.show_dashboard),("Movimientos","↕",self.show_transactions),("Tarjetas","▣",self.show_cards),("Cajitas","◈",self.show_savings),("Análisis","✦",self.show_advisor),("Terceros","◎",self.show_third_parties),("Liquidación","⇄",self.show_settlement),("Ajustes","⚙",self.show_settings)):
            b=ctk.CTkButton(side,text=f"  {icon}   {name}",anchor="w",height=42,corner_radius=11,fg_color="transparent",hover_color=T.ALT,text_color=T.MUTED,command=fn); b.pack(fill="x",padx=14,pady=2); self.nav[name]=b
        ctk.CTkLabel(side,text="LOCAL · SQLITE",text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(side="bottom",anchor="w",padx=26,pady=22)
        root=ctk.CTkFrame(self,fg_color=T.BG,corner_radius=0); root.grid(row=0,column=1,sticky="nsew"); root.grid_rowconfigure(1,weight=1); root.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(root,height=76,fg_color=T.BG,corner_radius=0); top.grid(row=0,column=0,sticky="ew"); top.grid_propagate(False)
        self.page=ctk.CTkLabel(top,text="",font=ctk.CTkFont(size=12,weight="bold"),text_color=T.MUTED); self.page.pack(side="left",padx=30)
        ctk.CTkButton(top,text="＋ Agregar gasto",height=38,corner_radius=12,fg_color=T.PRIMARY,command=self.show_transactions).pack(side="right",padx=(8,28),pady=18)
        self.theme_b=ctk.CTkButton(top,text="☀" if self.theme=="dark" else "☾",width=38,height=38,corner_radius=12,fg_color=T.ALT,text_color=T.TXT,command=self.toggle_theme); self.theme_b.pack(side="right",pady=18)
        self.content=ctk.CTkScrollableFrame(root,fg_color=T.BG,corner_radius=0); self.content.grid(row=1,column=0,sticky="nsew"); self.content.grid_columnconfigure((0,1,2,3),weight=1)

    def toggle_theme(self) -> None:
        self.theme="light" if self.theme=="dark" else "dark"; ctk.set_appearance_mode(self.theme); self.pref["tema"]=self.theme; guardar_preferencias(self.pref); self.theme_b.configure(text="☀" if self.theme=="dark" else "☾")
    def _clear(self,title:str,active:str) -> None:
        self.page.configure(text=title.upper())
        for w in self.content.winfo_children(): w.destroy()
        for name,b in self.nav.items(): b.configure(fg_color=T.ALT if name==active else "transparent",text_color=T.TXT if name==active else T.MUTED)
    def _heading(self,title:str,subtitle:str) -> None:
        ctk.CTkLabel(self.content,text=title,font=ctk.CTkFont(size=30,weight="bold"),text_color=T.TXT).grid(row=0,column=0,columnspan=4,sticky="w",padx=22,pady=(20,2)); ctk.CTkLabel(self.content,text=subtitle,text_color=T.MUTED).grid(row=1,column=0,columnspan=4,sticky="w",padx=22,pady=(0,18))
    def _panel(self,row:int,col:int,span:int=1) -> Any:
        p=ctk.CTkFrame(self.content,fg_color=T.S,corner_radius=18,border_width=1,border_color=T.BORDER); p.grid(row=row,column=col,columnspan=span,sticky="nsew",padx=7,pady=7); return p
    def _card(self,row:int,col:int,title:str,value:str,sub:str="",color:Any=None,span:int=1) -> Any:
        p=self._panel(row,col,span); ctk.CTkLabel(p,text=title.upper(),text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=18,pady=(16,4)); ctk.CTkLabel(p,text=value,text_color=color or T.TXT,font=ctk.CTkFont(size=24,weight="bold")).pack(anchor="w",padx=18)
        if sub: ctk.CTkLabel(p,text=sub,text_color=T.MUTED,wraplength=260).pack(anchor="w",padx=18,pady=(4,15))
        return p
    def _entry(self,parent:Any,label:str,value:str="",values:list[str]|None=None)->Any:
        ctk.CTkLabel(parent,text=label,text_color=T.MUTED,font=ctk.CTkFont(size=12,weight="bold")).pack(anchor="w",pady=(10,4)); w=ctk.CTkOptionMenu(parent,values=values,fg_color=T.ALT,button_color=T.PRIMARY) if values else ctk.CTkEntry(parent,height=36,fg_color=T.ALT,border_color=T.BORDER)
        if values:w.set(value or values[0])
        else:w.insert(0,value)
        w.pack(fill="x"); return w
    @staticmethod
    def _value(w:Any)->str:return str(w.get()).strip()
    def _run(self,action:Callable[[],None],ok:str="Guardado")->None:
        try: action(); self._toast("✓  "+ok)
        except Exception as exc: messagebox.showerror("No se pudo completar",str(exc),parent=self)
    def _toast(self,msg:str)->None:
        t=ctk.CTkLabel(self,text=msg,fg_color=T.OK,corner_radius=12,text_color=("#FFF","#102019")); t.place(relx=.72,rely=.91); self.after(2500,t.destroy)
    def _empty(self,row:int,title:str,detail:str,fn:Callable[[],None])->None:
        p=self._panel(row,0,4); ctk.CTkLabel(p,text=title,font=ctk.CTkFont(size=17,weight="bold")).pack(pady=(20,4)); ctk.CTkLabel(p,text=detail,text_color=T.MUTED).pack(); ctk.CTkButton(p,text="Empezar",fg_color=T.PRIMARY,command=fn).pack(pady=16)

    def show_dashboard(self)->None:
        self._clear("Inicio","Inicio"); hour=dt.datetime.now().hour; greet="Buenos días" if hour<12 else "Buenas tardes" if hour<19 else "Buenas noches"; d=self.service.dashboard(self.selected_month); f=d["flujo"]; liq=d["liquidez"]
        self._heading(f"{greet}, Samuel & Sara 👋","Así va su dinero este mes. Todo lo importante, de un vistazo.")
        hero=self._panel(2,0,4); hero.configure(fg_color=T.PRIMARY,border_width=0); ctk.CTkLabel(hero,text="DINERO DISPONIBLE",text_color="#E8EBFF",font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=24,pady=(20,2)); ctk.CTkLabel(hero,text=dinero(d["patrimonio"]["disponible_gastos_recurrentes"]),text_color="white",font=ctk.CTkFont(size=38,weight="bold")).pack(anchor="w",padx=24); ctk.CTkLabel(hero,text="Disponible después de reservas y compromisos registrados",text_color="#E8EBFF").pack(anchor="w",padx=24,pady=(3,20))
        self._card(3,0,"Ingresos",dinero(f["ingresos"]),"Registrados este mes",T.OK); self._card(3,1,"Gastos",dinero(f["salidas"]),"Salidas reales de caja",T.BAD); self._card(3,2,"Reservado",dinero(d["cajitas"]["total"]),"En sus cajitas",T.SAVE)
        tarjetas_resumen=self.service.resumen_tarjetas(self.selected_month); deuda=self._card(3,3,"Deuda",dinero(tarjetas_resumen["deuda_total"]),f"{len(d['tarjetas'])} tarjetas · {tarjetas_resumen['utilizacion_global']:.0%} utilización · mínimo {dinero(tarjetas_resumen['pago_minimo_total'])}",T.WARN)
        ctk.CTkButton(deuda,text="Ver tarjetas",height=28,fg_color=T.ALT,text_color=T.TXT,command=self.show_cards).pack(anchor="w",padx=18,pady=(0,14))
        seguro=self.service.presupuesto_seguro(self.selected_month)
        decision=self._panel(4,0,4); ctk.CTkLabel(decision,text="💸  DECISIÓN FINANCIERA",text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=20,pady=(15,2)); ctk.CTkLabel(decision,text=f"Pueden gastar hasta {dinero(seguro['puede_gastar_hasta'])}",font=ctk.CTkFont(size=23,weight="bold"),text_color=T.OK).pack(anchor="w",padx=20); ctk.CTkLabel(decision,text="Sin comprometer pagos mínimos, metas prioritarias y su margen de seguridad registrado.",text_color=T.MUTED).pack(side="left",padx=20,pady=(3,15)); ctk.CTkButton(decision,text="¿Podemos salir?",fg_color=T.PRIMARY,height=32,command=self.spending_dialog).pack(side="right",padx=20,pady=(0,15))
        ctk.CTkLabel(self.content,text="¿Cómo vamos?",font=ctk.CTkFont(size=18,weight="bold")).grid(row=5,column=0,columnspan=4,sticky="w",padx=22,pady=(22,1)); pair=self._panel(6,0,2)
        for person,label,color in ((PERSONA1,"Samuel",T.PRIMARY),(PERSONA2,"Sara",T.SAVE)):
            line=ctk.CTkFrame(pair,fg_color="transparent"); line.pack(fill="x",padx=18,pady=12); val=max(liq[person]["liquidez"],0); maximum=max(1,*[max(x["liquidez"],0) for x in liq.values()]); ctk.CTkLabel(line,text=label,width=70,anchor="w").pack(side="left"); bar=ctk.CTkProgressBar(line,height=8,progress_color=color,fg_color=T.ALT); bar.pack(side="left",fill="x",expand=True,padx=10); bar.set(val/maximum); ctk.CTkLabel(line,text=dinero(liq[person]["liquidez"]),text_color=T.MUTED).pack(side="right")
        ad=self._panel(6,2,2); recs=self.service.asesor(self.selected_month)["recomendaciones"]; ctk.CTkLabel(ad,text="✦  Tu asistente financiero",font=ctk.CTkFont(size=16,weight="bold")).pack(anchor="w",padx=18,pady=(16,4)); ctk.CTkLabel(ad,text=recs[0]["mensaje"] if recs else "Todo se ve estable con los datos registrados.",text_color=T.MUTED,wraplength=420,justify="left").pack(anchor="w",padx=18,pady=(0,12)); ctk.CTkButton(ad,text="Ver análisis",height=32,fg_color=T.ALT,text_color=T.TXT,command=self.show_advisor).pack(anchor="w",padx=18,pady=(0,16))
        ctk.CTkLabel(self.content,text="Tus reservas",font=ctk.CTkFont(size=18,weight="bold")).grid(row=7,column=0,columnspan=4,sticky="w",padx=22,pady=(22,1)); boxes=d["cajitas"]["fondos"]
        if not boxes:self._empty(8,"Aún no tienen cajitas","Creen una reserva para empezar a organizar su dinero.",self.show_savings)
        for i,b in enumerate(boxes[:4]):
            p=self._card(8,i,f"{b.get('icono','◈')} {b['nombre']}",dinero(b["saldo"]),f"{b.get('titular',b['propietario'])} · {dinero(b['total_aportado'])} aportado",T.SAVE)
            if b["meta"]: bar=ctk.CTkProgressBar(p,height=6,progress_color=T.SAVE,fg_color=T.ALT); bar.pack(fill="x",padx=18,pady=(0,16)); bar.set(min(b["saldo"]/b["meta"],1))

    def spending_dialog(self)->None:
        """Abre una simulación; nunca registra una compra ni modifica una tarjeta."""
        dialog=ctk.CTkToplevel(self); dialog.title("¿Podemos gastar?"); dialog.geometry("520x620"); dialog.grab_set()
        body=ctk.CTkFrame(dialog,fg_color=T.S,corner_radius=18); body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="¿Podemos gastar?",font=ctk.CTkFont(size=23,weight="bold")).pack(anchor="w",padx=20,pady=(20,2))
        ctk.CTkLabel(body,text="Esta es una simulación: no cambia su base de datos.",text_color=T.MUTED).pack(anchor="w",padx=20)
        amount=self._entry(body,"Monto estimado COP"); method=self._entry(body,"Método de pago","Débito",["Débito","Efectivo","Tarjeta"]); margin=self._entry(body,"Margen de seguridad","10",["5","10","15","20"])
        cards=self.service.tarjetas(); labels=[f"{c['id']} · {c['nombre']} · disponible {dinero(c['cupo_disponible'])}" for c in cards]
        card=self._entry(body,"Tarjeta (solo si aplica)",labels[0] if labels else "Sin tarjetas registradas",labels) if labels else None
        result=ctk.CTkFrame(body,fg_color=T.ALT,corner_radius=13); result.pack(fill="x",padx=20,pady=(16,8))
        def analyze()->None:
            for child in result.winfo_children(): child.destroy()
            selected=self._value(method); payment={"Débito":"debito","Efectivo":"efectivo","Tarjeta":"tarjeta"}[selected]
            card_id=self._value(card).split(" · ")[0] if card and payment=="tarjeta" else None
            try: evaluation=self.service.evaluar_gasto(self.selected_month,self._value(amount),payment,card_id,int(self._value(margin)))
            except Exception as exc: messagebox.showerror("No se pudo analizar",str(exc),parent=dialog); return
            color=T.OK if evaluation["estado"]=="si" else T.WARN if evaluation["estado"]=="cuidado" else T.BAD
            ctk.CTkLabel(result,text=evaluation["titulo"],font=ctk.CTkFont(size=19,weight="bold"),text_color=color).pack(anchor="w",padx=16,pady=(14,2))
            ctk.CTkLabel(result,text=evaluation["mensaje"],text_color=T.TXT,wraplength=430,justify="left").pack(anchor="w",padx=16)
            ctk.CTkLabel(result,text=f"Límite seguro: {dinero(evaluation['puede_gastar_hasta'])}\nDespués de la salida: {dinero(evaluation['despues'])}\nPagos mínimos reservados: {dinero(evaluation['pagos_minimos_tarjetas'])}\nMetas prioritarias: {dinero(evaluation['metas_prioritarias'])}\nMargen de seguridad: {dinero(evaluation['margen_seguridad'])}",text_color=T.MUTED,justify="left").pack(anchor="w",padx=16,pady=8)
            if evaluation["advertencias"]: ctk.CTkLabel(result,text="⚠ " + " ".join(evaluation["advertencias"]),text_color=T.WARN,wraplength=430).pack(anchor="w",padx=16,pady=(0,8))
            ctk.CTkLabel(result,text="Método sugerido: "+evaluation["metodo_recomendado"],text_color=T.TXT,wraplength=430).pack(anchor="w",padx=16,pady=(0,14))
        ctk.CTkButton(body,text="Analizar salida",fg_color=T.PRIMARY,command=analyze).pack(fill="x",padx=20,pady=(0,20))

    def show_transactions(self)->None:
        self._clear("Movimientos","Movimientos"); self._heading("Movimientos","Registra cada salida sin mezclar etiquetas visuales con el dominio financiero.")
        form=self._panel(2,0); ctk.CTkLabel(form,text="＋ Agregar gasto",font=ctk.CTkFont(size=17,weight="bold")).pack(anchor="w",padx=18,pady=(16,2))
        name=self._entry(form,"Descripción"); amount=self._entry(form,"Monto COP"); cat=self._entry(form,"Categoría")
        method=self._entry(form,"Método","Efectivo",["Efectivo","Débito","Tarjeta"]); payer=self._entry(form,"¿Quién realiza el pago?","Samuel",["Samuel","Sara"])
        resp=self._entry(form,"Responsabilidad económica","Compartido",["Samuel","Sara","Compartido","Personalizado"])
        conditional=ctk.CTkFrame(form,fg_color="transparent"); conditional.pack(fill="x",padx=0)
        card_group=ctk.CTkFrame(conditional,fg_color="transparent"); split_group=ctk.CTkFrame(conditional,fg_color="transparent")
        card_labels=[f"{c['id']} · {c['nombre']} · {dinero(c['cupo_disponible'])} disponible" for c in self.service.tarjetas()]
        card=self._entry(card_group,"Tarjeta",card_labels[0],card_labels) if card_labels else None
        instalments=self._entry(card_group,"Cuotas","1") if card_labels else None
        p1=self._entry(split_group,"Samuel asume COP","0"); p2=self._entry(split_group,"Sara asume COP","0")
        def refresh_fields(*_:Any)->None:
            is_card=self._value(method)=="Tarjeta"
            if card and instalments:
                (card_group.pack(fill="x") if is_card else card_group.pack_forget())
            custom=self._value(resp) in ("Compartido","Personalizado")
            (split_group.pack(fill="x") if custom else split_group.pack_forget())
        method.configure(command=refresh_fields); resp.configure(command=refresh_fields); refresh_fields()
        def save()->None:
            total=self._value(amount); entero=parsear_dinero(total); r=self._value(resp)
            if r=="Samuel": aportes=(total,"0")
            elif r=="Sara": aportes=("0",total)
            elif self._value(p1) in ("", "0") and self._value(p2) in ("", "0"):
                aportes=(str(entero//2),str(entero-entero//2))
            else: aportes=(self._value(p1),self._value(p2))
            tarjeta_id=self._value(card).split(" · ")[0] if card and self._value(method)=="Tarjeta" else ""
            self.service.crear_gasto({"mes":self.selected_month,"nombre":self._value(name),"categoria":self._value(cat),"valor":total,"fecha":dt.date.today().isoformat(),"metodo":self._value(method),"pagador":self._value(payer),"responsabilidad":r,"monto_p1":aportes[0],"monto_p2":aportes[1],"tarjeta_id":tarjeta_id,"cuotas":self._value(instalments) if instalments and tarjeta_id else "1","prioridad":"Obligatorio"}); self.show_transactions()
        ctk.CTkButton(form,text="Guardar gasto",fg_color=T.PRIMARY,command=lambda:self._run(save,"Gasto agregado")).pack(fill="x",padx=18,pady=18)
        panel=self._panel(2,1,3); ctk.CTkLabel(panel,text="Actividad reciente",font=ctk.CTkFont(size=17,weight="bold")).pack(anchor="w",padx=18,pady=(16,4)); search=ctk.CTkEntry(panel,placeholder_text="Buscar Ginebra, comida, Samuel…",height=36,fg_color=T.ALT,border_color=T.BORDER); search.pack(fill="x",padx=18,pady=(0,10)); listing=ctk.CTkFrame(panel,fg_color="transparent"); listing.pack(fill="both",expand=True,padx=10,pady=(0,12)); rows=self.service.movimientos(self.selected_month)
        def render(*_:Any)->None:
            for x in listing.winfo_children():x.destroy()
            found=[m for m in rows if search.get().lower() in (m["descripcion"]+m["persona"]).lower()]
            if not found:ctk.CTkLabel(listing,text="No hay movimientos que coincidan.",text_color=T.MUTED).pack(pady=30)
            for m in found[:30]:
                x=ctk.CTkFrame(listing,fg_color=T.ALT,corner_radius=12); x.pack(fill="x",pady=3); plus=m["tipo"]=="ingreso"; ctk.CTkLabel(x,text="●",text_color=T.OK if plus else T.BAD,width=30).pack(side="left",padx=(10,0),pady=10); ctk.CTkLabel(x,text=m["descripcion"],anchor="w",font=ctk.CTkFont(weight="bold")).pack(side="left",fill="x",expand=True); ctk.CTkLabel(x,text=f"{m['persona']} · {m['fecha']}",text_color=T.MUTED).pack(side="left",padx=10); ctk.CTkLabel(x,text=("+" if plus else "−")+dinero(m["monto"]),text_color=T.OK if plus else T.BAD,font=ctk.CTkFont(weight="bold")).pack(side="right",padx=14)
        search.bind("<KeyRelease>",render); render()

    def show_cards(self)->None:
        self._clear("Tarjetas","Tarjetas")
        self._heading("Tarjetas","Todo lo que necesitan saber sobre sus deudas de crédito, en un solo lugar.")
        resumen=self.service.resumen_tarjetas(self.selected_month)
        nuevo=ctk.CTkButton(self.content,text="＋ Nueva tarjeta",height=34,fg_color=T.PRIMARY,command=self.card_create_dialog)
        nuevo.grid(row=0,column=3,sticky="e",padx=22,pady=(18,0))
        self._card(2,0,"Deuda total",dinero(resumen["deuda_total"]),"De todas sus tarjetas",T.WARN)
        self._card(2,1,"Cupo total",dinero(resumen["cupo_total"]),"Crédito registrado",T.PRIMARY)
        self._card(2,2,"Cupo disponible",dinero(resumen["cupo_disponible"]),f"{resumen['utilizacion_global']:.0%} de utilización global",T.OK)
        self._card(2,3,"Pagos realizados",dinero(resumen["pagos_realizados"]),f"Intereses registrados: {dinero(resumen['intereses_registrados'])}",T.SAVE)
        self._card(3,0,"Deuda Samuel",dinero(resumen["deuda_persona1"]),"Responsabilidad económica pendiente",T.PRIMARY,2)
        self._card(3,2,"Deuda Sara",dinero(resumen["deuda_persona2"]),f"Pago mínimo total: {dinero(resumen['pago_minimo_total'])}",T.SAVE,2)
        if not resumen["tarjetas"]:
            p=self._panel(4,0,4); ctk.CTkLabel(p,text="💳",font=ctk.CTkFont(size=34)).pack(pady=(26,2)); ctk.CTkLabel(p,text="Todavía no tienen tarjetas registradas",font=ctk.CTkFont(size=18,weight="bold")).pack(pady=(0,4)); ctk.CTkLabel(p,text="Agreguen una tarjeta para controlar cupo, deuda, pagos, intereses y responsabilidades.",text_color=T.MUTED,wraplength=520,justify="center").pack(); ctk.CTkButton(p,text="＋ Agregar tarjeta",fg_color=T.PRIMARY,command=self.card_create_dialog).pack(pady=20); return
        ctk.CTkLabel(self.content,text="Sus tarjetas",font=ctk.CTkFont(size=18,weight="bold")).grid(row=4,column=0,columnspan=4,sticky="w",padx=22,pady=(22,2))
        for i,tarjeta in enumerate(resumen["tarjetas"]):
            p=self._panel(5+i//2,i%2*2,2); color=T.BAD if tarjeta["utilizacion"]>=.90 else T.WARN if tarjeta["utilizacion"]>=.70 else T.OK
            identificador=" · ".join(x for x in (tarjeta.get("banco"), tarjeta.get("tipo"), f"•••• {tarjeta['ultimos_4']}" if tarjeta.get("ultimos_4") else None) if x)
            ctk.CTkLabel(p,text=f"▣  {tarjeta['nombre']}",font=ctk.CTkFont(size=18,weight="bold")).pack(anchor="w",padx=18,pady=(16,2)); ctk.CTkLabel(p,text=f"Titular: {NOMBRES[tarjeta['propietario']]}{(' · ' + identificador) if identificador else ''}",text_color=T.MUTED).pack(anchor="w",padx=18)
            line=ctk.CTkFrame(p,fg_color="transparent"); line.pack(fill="x",padx=18,pady=(14,2)); ctk.CTkLabel(line,text="Deuda",text_color=T.MUTED).pack(side="left"); ctk.CTkLabel(line,text=dinero(tarjeta["saldo_deuda"]),font=ctk.CTkFont(size=23,weight="bold"),text_color=color).pack(side="right")
            ctk.CTkLabel(p,text=f"Cupo {dinero(tarjeta['cupo_total'])}  ·  Disponible {dinero(tarjeta['cupo_disponible'])}",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,8))
            bar=ctk.CTkProgressBar(p,height=7,progress_color=color,fg_color=T.ALT); bar.pack(fill="x",padx=18); bar.set(min(tarjeta["utilizacion"],1))
            ctk.CTkLabel(p,text=f"{tarjeta['utilizacion']:.0%} utilizado  ·  Samuel {dinero(tarjeta['deuda_persona1'])}  ·  Sara {dinero(tarjeta['deuda_persona2'])}",text_color=T.MUTED,wraplength=450).pack(anchor="w",padx=18,pady=(6,10))
            actions=ctk.CTkFrame(p,fg_color="transparent");actions.pack(fill="x",padx=18,pady=(0,16)); ctk.CTkButton(actions,text="Ver detalle",height=30,fg_color=T.PRIMARY,command=lambda card=tarjeta:self.show_card_detail(card["id"])).pack(side="left"); ctk.CTkButton(actions,text="＋ Compra",height=30,fg_color=T.ALT,text_color=T.TXT,command=lambda card=tarjeta:self.card_purchase_dialog(card)).pack(side="left",padx=7); ctk.CTkButton(actions,text="↓ Pago",height=30,fg_color=T.ALT,text_color=T.TXT,command=lambda card=tarjeta:self.card_payment_dialog(card)).pack(side="left")

    def card_create_dialog(self)->None:
        d=ctk.CTkToplevel(self); d.title("Nueva tarjeta"); d.geometry("560x760"); d.minsize(460,620); d.grab_set(); body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14)
        ctk.CTkLabel(body,text="＋ Nueva tarjeta",font=ctk.CTkFont(size=23,weight="bold")).pack(anchor="w",padx=18,pady=(18,2));ctk.CTkLabel(body,text="No guardamos número completo, CVV, PIN ni claves.",text_color=T.MUTED).pack(anchor="w",padx=18)
        ctk.CTkLabel(body,text="BÁSICO",text_color=T.PRIMARY,font=ctk.CTkFont(size=11,weight="bold")).pack(anchor="w",padx=18,pady=(18,0)); name=self._entry(body,"Nombre de tarjeta"); bank=self._entry(body,"Banco / entidad"); kind=self._entry(body,"Tipo de tarjeta","Crédito"); holder=self._entry(body,"Titular","Samuel",["Samuel","Sara"]); last4=self._entry(body,"Últimos 4 dígitos (opcional)")
        ctk.CTkLabel(body,text="CRÉDITO",text_color=T.PRIMARY,font=ctk.CTkFont(size=11,weight="bold")).pack(anchor="w",padx=18,pady=(18,0)); limit=self._entry(body,"Cupo total COP"); minimum=self._entry(body,"Pago mínimo COP","0"); rate=self._entry(body,"Interés mensual (%)","0")
        ctk.CTkLabel(body,text="FECHAS Y SALDO HISTÓRICO",text_color=T.PRIMARY,font=ctk.CTkFont(size=11,weight="bold")).pack(anchor="w",padx=18,pady=(18,0)); cut=self._entry(body,"Fecha de corte (AAAA-MM-DD, opcional)"); due=self._entry(body,"Fecha límite de pago (AAAA-MM-DD, opcional)"); historic=self._entry(body,"Saldo inicial histórico COP","0"); historic_date=self._entry(body,"Fecha del saldo histórico (AAAA-MM-DD, opcional)"); notes=self._entry(body,"Notas (opcional)")
        def save()->None:
            self.service.crear_tarjeta(self._value(name),self._value(holder),self._value(limit),self._value(minimum),self._value(rate),self._value(historic),self._value(historic_date) or None,banco=self._value(bank),tipo=self._value(kind),ultimos_4=self._value(last4),fecha_corte=self._value(cut),fecha_pago=self._value(due),notas=self._value(notes)); d.destroy(); self.show_cards()
        ctk.CTkButton(body,text="Crear tarjeta",height=40,fg_color=T.PRIMARY,command=lambda:self._run(save,"Tarjeta creada")).pack(fill="x",padx=18,pady=22)

    def show_card_detail(self,tarjeta_id:int)->None:
        detalle=self.service.tarjeta_detalle(tarjeta_id); tarjeta=detalle["tarjeta"]; self._clear("Tarjetas","Tarjetas"); self._heading(f"▣ {tarjeta['nombre']}",f"Titular: {NOMBRES[tarjeta['propietario']]} · {tarjeta.get('banco') or 'Entidad no registrada'} · {tarjeta.get('tipo') or 'Tarjeta de crédito'}")
        header=ctk.CTkFrame(self.content,fg_color="transparent");header.grid(row=0,column=3,sticky="e",padx=22,pady=(15,0)); ctk.CTkButton(header,text="← Tarjetas",height=32,fg_color=T.ALT,text_color=T.TXT,command=self.show_cards).pack(side="left",padx=4);ctk.CTkButton(header,text="✎ Editar",height=32,fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_edit_dialog(tarjeta)).pack(side="left",padx=4)
        self._card(2,0,"Deuda actual",dinero(tarjeta["saldo_deuda"]),"Lo necesario para salir de la deuda",T.WARN);self._card(2,1,"Cupo disponible",dinero(tarjeta["cupo_disponible"]),f"Cupo total {dinero(tarjeta['cupo_total'])}",T.OK);self._card(2,2,"Utilización",f"{tarjeta['utilizacion']:.0%}","Señal interna, no una regla absoluta",T.BAD if tarjeta["utilizacion"]>=.70 else T.OK);self._card(2,3,"Pago mínimo",dinero(tarjeta["pago_minimo"]),f"Próximo pago: {tarjeta.get('fecha_pago') or 'No registrada'}",T.PRIMARY)
        self._card(3,0,"Interés mensual",f"{tarjeta['interes_mensual']:.2f}%",f"Estimación mensual: {dinero(tarjeta['interes_estimado'])}",T.WARN);self._card(3,1,"Total pagado",dinero(tarjeta["total_pagado"]),"Aportes registrados",T.SAVE);self._card(3,2,"Compras pendientes",str(tarjeta["compras_pendientes"]),f"Cuotas pendientes aproximadas: {tarjeta['cuotas_pendientes']}",T.PRIMARY);self._card(3,3,"Deuda histórica",dinero(tarjeta["deuda_historica"]),"Atribución provisional al titular",T.MUTED)
        uso=self._panel(4,0,4);ctk.CTkLabel(uso,text="UTILIZACIÓN DEL CUPO",text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=18,pady=(15,4)); bar=ctk.CTkProgressBar(uso,height=10,progress_color=T.BAD if tarjeta["utilizacion"]>=.70 else T.OK,fg_color=T.ALT);bar.pack(fill="x",padx=18);bar.set(min(tarjeta["utilizacion"],1));ctk.CTkLabel(uso,text=f"{tarjeta['utilizacion']:.0%} utilizado · {dinero(tarjeta['cupo_disponible'])} disponibles. Menos de 30% es una señal saludable; 70% o más merece atención dentro de LÚMINA.",text_color=T.MUTED,wraplength=900).pack(anchor="w",padx=18,pady=(7,15))
        dist=self._panel(5,0,2);ctk.CTkLabel(dist,text="¿QUIÉN DEBE QUÉ?",text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=18,pady=(15,5));ctk.CTkLabel(dist,text=f"Samuel  {dinero(tarjeta['deuda_persona1'])}\nSara       {dinero(tarjeta['deuda_persona2'])}",font=ctk.CTkFont(size=17,weight="bold"),justify="left").pack(anchor="w",padx=18);ctk.CTkLabel(dist,text=f"Pagado: Samuel {dinero(tarjeta['pagado_persona1'])} · Sara {dinero(tarjeta['pagado_persona2'])}\nTitular legal y responsable económico son conceptos separados.",text_color=T.MUTED,wraplength=430,justify="left").pack(anchor="w",padx=18,pady=(6,16))
        monthly=detalle["mensual"]; mesp=self._panel(5,2,2);ctk.CTkLabel(mesp,text="ESTE MES",text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=18,pady=(15,5));ctk.CTkLabel(mesp,text=f"Compras +{dinero(monthly['compras'])}   ·   Intereses +{dinero(monthly['intereses'])}\nCargos +{dinero(monthly['cargos'])}      ·   Pagos −{dinero(monthly['pagos'])}\nVariación {'+' if monthly['variacion']>=0 else '−'}{dinero(abs(monthly['variacion']))}",justify="left",font=ctk.CTkFont(size=14,weight="bold")).pack(anchor="w",padx=18);ctk.CTkLabel(mesp,text="Lectura basada en los movimientos registrados del período.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(7,16))
        actions=self._panel(6,0,4);ctk.CTkLabel(actions,text="ACCIONES",text_color=T.MUTED,font=ctk.CTkFont(size=10,weight="bold")).pack(anchor="w",padx=18,pady=(15,7));row=ctk.CTkFrame(actions,fg_color="transparent");row.pack(fill="x",padx=18,pady=(0,16));ctk.CTkButton(row,text="＋ Registrar compra",fg_color=T.PRIMARY,command=lambda:self.card_purchase_dialog(tarjeta)).pack(side="left",padx=(0,7));ctk.CTkButton(row,text="↓ Registrar pago",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_payment_dialog(tarjeta)).pack(side="left",padx=7);ctk.CTkButton(row,text="＋ Registrar interés",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_movement_dialog(tarjeta,"INTERES")).pack(side="left",padx=7);ctk.CTkButton(row,text="＋ Registrar cargo",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_movement_dialog(tarjeta,"CARGO")).pack(side="left",padx=7);ctk.CTkButton(row,text="Ajustar saldo",fg_color=T.ALT,text_color=T.TXT,command=lambda:self.card_adjust_dialog(tarjeta)).pack(side="left",padx=7)
        sim=self._panel(7,0,4);ctk.CTkLabel(sim,text="¿QUÉ PASA SI PAGAN...?",font=ctk.CTkFont(size=17,weight="bold")).pack(anchor="w",padx=18,pady=(15,2));ctk.CTkLabel(sim,text="Simulación pura: no modifica SQLite.",text_color=T.MUTED).pack(anchor="w",padx=18); extra=self._entry(sim,"Pago adicional COP","500000"); result=ctk.CTkLabel(sim,text="",text_color=T.MUTED,justify="left",wraplength=840);result.pack(anchor="w",padx=18,pady=(8,4))
        def simulate()->None:
            x=self.service.simular_pago_tarjeta(tarjeta_id,self._value(extra));result.configure(text=f"Deuda {dinero(x['deuda_actual'])} → {dinero(x['deuda_despues'])} · utilización {x['utilizacion_actual']:.0%} → {x['utilizacion_despues']:.0%} · cupo liberado {dinero(x['cupo_liberado'])}\n{x['nota']}")
        ctk.CTkButton(sim,text="Simular pago",height=30,fg_color=T.PRIMARY,command=lambda:self._run(simulate,"Simulación lista")).pack(anchor="w",padx=18,pady=(0,16))
        ctk.CTkLabel(self.content,text="Historial de tarjeta",font=ctk.CTkFont(size=18,weight="bold")).grid(row=8,column=0,columnspan=4,sticky="w",padx=22,pady=(22,2))
        if not detalle["historial"]:self._empty(9,"Aún no hay movimientos","Las compras, pagos, intereses, cargos y ajustes aparecerán aquí.",lambda:self.card_purchase_dialog(tarjeta));return
        for i,event in enumerate(detalle["historial"][:60]):
            p=self._panel(9+i,0,4);activo=event["estado"]=="ACTIVO"; color=T.OK if event["tipo"]=="PAGO" else T.WARN if event["tipo"] in ("INTERES","CARGO") else T.BAD;ctk.CTkLabel(p,text=f"{'●' if activo else '↺'}  {event['tipo'].title()}  ·  {event['fecha']}",text_color=color,font=ctk.CTkFont(size=13,weight="bold")).pack(anchor="w",padx=18,pady=(12,2));ctk.CTkLabel(p,text=event["descripcion"] + (" · Movimiento reversado" if not activo else ""),text_color=T.TXT if activo else T.MUTED).pack(anchor="w",padx=18);detail=f"{'+' if event['efecto_deuda']>=0 else '−'}{dinero(abs(event['monto']))} · efecto en deuda: {'+' if event['efecto_deuda']>=0 else '−'}{dinero(abs(event['efecto_deuda']))}";ctk.CTkLabel(p,text=detail,text_color=T.MUTED).pack(anchor="w",padx=18,pady=(2,10))

    def _card_distribution(self,total:str,responsabilidad:str,p1:str,p2:str)->tuple[str,str]:
        monto=parsear_dinero(total)
        if responsabilidad=="Samuel":return total,"0"
        if responsabilidad=="Sara":return "0",total
        if not p1 and not p2:return str(monto//2),str(monto-monto//2)
        return p1,p2

    def card_purchase_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self);d.title(f"Compra · {tarjeta['nombre']}");d.geometry("500x670");d.grab_set();body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="＋ Registrar compra",font=ctk.CTkFont(size=22,weight="bold")).pack(anchor="w",padx=18,pady=(18,2));ctk.CTkLabel(body,text=f"{tarjeta['nombre']} · disponible {dinero(tarjeta['cupo_disponible'])}",text_color=T.MUTED).pack(anchor="w",padx=18)
        date=self._entry(body,"Fecha",dt.date.today().isoformat());desc=self._entry(body,"Descripción");category=self._entry(body,"Categoría");total=self._entry(body,"Monto total COP");installments=self._entry(body,"Cuotas","1");resp=self._entry(body,"Responsable","Compartido",["Samuel","Sara","Compartido","Personalizado"]);p1=self._entry(body,"Samuel asume COP","0");p2=self._entry(body,"Sara asume COP","0")
        def save()->None:
            a,b=self._card_distribution(self._value(total),self._value(resp),self._value(p1) if self._value(p1)!="0" else "",self._value(p2) if self._value(p2)!="0" else "");monto=parsear_dinero(self._value(total));after=tarjeta["saldo_deuda"]+monto
            if not messagebox.askyesno("Confirmar compra",f"Vas a registrar una compra de {dinero(monto)} en {tarjeta['nombre']}.\nLa deuda pasará de {dinero(tarjeta['saldo_deuda'])} a {dinero(after)}.\n\n¿Registrar compra?",parent=d):return
            self.service.crear_gasto({"mes":self.selected_month,"nombre":self._value(desc),"categoria":self._value(category),"valor":self._value(total),"fecha":self._value(date),"metodo":"Tarjeta","pagador":NOMBRES[tarjeta['propietario']],"responsabilidad":self._value(resp),"monto_p1":a,"monto_p2":b,"tarjeta_id":str(tarjeta['id']),"cuotas":self._value(installments),"prioridad":"Obligatorio"});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Registrar compra",fg_color=T.PRIMARY,command=lambda:self._run(save,"Compra registrada")).pack(fill="x",padx=18,pady=22)

    def card_payment_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self);d.title(f"Pago · {tarjeta['nombre']}");d.geometry("480x540");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="↓ Registrar pago",font=ctk.CTkFont(size=22,weight="bold")).pack(anchor="w",padx=18,pady=(20,2));ctk.CTkLabel(body,text=f"Deuda actual: {dinero(tarjeta['saldo_deuda'])}",text_color=T.MUTED).pack(anchor="w",padx=18)
        date=self._entry(body,"Fecha",dt.date.today().isoformat());total=self._entry(body,"Monto total COP");payer=self._entry(body,"Quién realizó el pago","Samuel",["Samuel","Sara"]);p1=self._entry(body,"Aporte Samuel COP","0");p2=self._entry(body,"Aporte Sara COP","0");concept=self._entry(body,"Motivo (opcional)")
        def save()->None:
            monto=parsear_dinero(self._value(total));s=parsear_dinero(self._value(p1));sa=parsear_dinero(self._value(p2))
            if s+sa!=monto:raise ValueError("Samuel + Sara deben sumar exactamente el pago.")
            if not messagebox.askyesno("Confirmar pago",f"Vas a registrar un pago de {dinero(monto)} en {tarjeta['nombre']}.\nSamuel: {dinero(s)} · Sara: {dinero(sa)}\nLa deuda pasará de {dinero(tarjeta['saldo_deuda'])} a {dinero(tarjeta['saldo_deuda']-monto)}.\n\n¿Registrar pago?",parent=d):return
            self.service.crear_pago_tarjeta({"mes":self.selected_month,"fecha":self._value(date),"tarjeta_id":str(tarjeta['id']),"monto":self._value(total),"pagador":self._value(payer),"aporte_p1":self._value(p1),"aporte_p2":self._value(p2),"concepto":self._value(concept)});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Registrar pago",fg_color=T.PRIMARY,command=lambda:self._run(save,"Pago registrado")).pack(fill="x",padx=18,pady=22)

    def card_movement_dialog(self,tarjeta:dict[str,Any],tipo:str)->None:
        d=ctk.CTkToplevel(self);d.title(f"{tipo.title()} · {tarjeta['nombre']}");d.geometry("480x560");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14);label="interés financiero" if tipo=="INTERES" else "cargo o comisión";ctk.CTkLabel(body,text=f"＋ Registrar {label}",font=ctk.CTkFont(size=21,weight="bold")).pack(anchor="w",padx=18,pady=(20,2));ctk.CTkLabel(body,text="Aumenta la deuda; no se registra como compra normal.",text_color=T.MUTED).pack(anchor="w",padx=18)
        date=self._entry(body,"Fecha",dt.date.today().isoformat());amount=self._entry(body,"Monto COP");desc=self._entry(body,"Concepto");period=self._entry(body,"Período (AAAA-MM)",self.selected_month);resp=self._entry(body,"Responsable","Compartido",["Samuel","Sara","Compartido","Personalizado"]);p1=self._entry(body,"Samuel asume COP","0");p2=self._entry(body,"Sara asume COP","0")
        def save()->None:
            a,b=self._card_distribution(self._value(amount),self._value(resp),self._value(p1) if self._value(p1)!="0" else "",self._value(p2) if self._value(p2)!="0" else "");monto=parsear_dinero(self._value(amount))
            if not messagebox.askyesno("Confirmar movimiento",f"Vas a registrar {label} por {dinero(monto)}.\nLa deuda pasará de {dinero(tarjeta['saldo_deuda'])} a {dinero(tarjeta['saldo_deuda']+monto)}.\n\n¿Continuar?",parent=d):return
            self.service.crear_movimiento_tarjeta({"mes":self.selected_month,"fecha":self._value(date),"tarjeta_id":str(tarjeta['id']),"tipo":tipo,"monto":self._value(amount),"descripcion":self._value(desc),"periodo":self._value(period),"responsabilidad":self._value(resp),"monto_p1":a,"monto_p2":b});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text=f"Registrar {label}",fg_color=T.PRIMARY,command=lambda:self._run(save,"Movimiento registrado")).pack(fill="x",padx=18,pady=22)

    def card_adjust_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self);d.title(f"Ajustar saldo · {tarjeta['nombre']}");d.geometry("460x440");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="Ajustar saldo",font=ctk.CTkFont(size=22,weight="bold")).pack(anchor="w",padx=18,pady=(20,2));ctk.CTkLabel(body,text=f"Saldo actual: {dinero(tarjeta['saldo_deuda'])}. Esto registra una corrección auditada, no borra historial.",text_color=T.MUTED,wraplength=400,justify="left").pack(anchor="w",padx=18)
        saldo=self._entry(body,"Nuevo saldo COP");reason=self._entry(body,"Motivo");date=self._entry(body,"Fecha",dt.date.today().isoformat());person=self._entry(body,"Quién realizó el ajuste","Samuel",["Samuel","Sara"])
        def save()->None:
            nuevo=parsear_dinero(self._value(saldo))
            if not messagebox.askyesno("Confirmar ajuste",f"Vas a ajustar el saldo de {dinero(tarjeta['saldo_deuda'])} a {dinero(nuevo)}.\nMotivo: {self._value(reason)}\n\n¿Registrar ajuste?",parent=d):return
            self.service.ajustar_saldo_tarjeta(tarjeta['id'],self._value(saldo),self._value(reason),self._value(date),self._value(person));d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Registrar ajuste",fg_color=T.PRIMARY,command=lambda:self._run(save,"Ajuste registrado")).pack(fill="x",padx=18,pady=22)

    def card_edit_dialog(self,tarjeta:dict[str,Any])->None:
        d=ctk.CTkToplevel(self);d.title(f"Editar · {tarjeta['nombre']}");d.geometry("520x700");d.grab_set();body=ctk.CTkScrollableFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14);ctk.CTkLabel(body,text="✎ Editar tarjeta",font=ctk.CTkFont(size=22,weight="bold")).pack(anchor="w",padx=18,pady=(18,2));ctk.CTkLabel(body,text="La deuda no se modifica aquí. Para corregirla, usa Ajustar saldo.",text_color=T.MUTED).pack(anchor="w",padx=18)
        name=self._entry(body,"Nombre",tarjeta["nombre"]);bank=self._entry(body,"Banco",tarjeta.get("banco") or "");kind=self._entry(body,"Tipo",tarjeta.get("tipo") or "Crédito");holder=self._entry(body,"Titular",NOMBRES[tarjeta["propietario"]],["Samuel","Sara"]);limit=self._entry(body,"Cupo total COP",str(tarjeta["cupo_total"]));minimum=self._entry(body,"Pago mínimo COP",str(tarjeta["pago_minimo"]));rate=self._entry(body,"Interés mensual (%)",str(tarjeta["interes_mensual"]));last4=self._entry(body,"Últimos 4",tarjeta.get("ultimos_4") or "");cut=self._entry(body,"Fecha de corte",tarjeta.get("fecha_corte") or "");due=self._entry(body,"Fecha límite de pago",tarjeta.get("fecha_pago") or "");state=self._entry(body,"Estado","Activa" if tarjeta["activa"] else "Inactiva",["Activa","Inactiva"]);notes=self._entry(body,"Notas",tarjeta.get("notas") or "")
        def save()->None:
            self.service.editar_tarjeta(tarjeta["id"],{"nombre":self._value(name),"banco":self._value(bank),"tipo":self._value(kind),"propietario":self._value(holder),"cupo":self._value(limit),"minimo":self._value(minimum),"interes":self._value(rate),"ultimos_4":self._value(last4),"fecha_corte":self._value(cut),"fecha_pago":self._value(due),"activa":self._value(state),"notas":self._value(notes)});d.destroy();self.show_card_detail(tarjeta["id"])
        ctk.CTkButton(body,text="Guardar cambios",fg_color=T.PRIMARY,command=lambda:self._run(save,"Tarjeta actualizada")).pack(fill="x",padx=18,pady=22)

    def show_savings(self)->None:
        self._clear("Cajitas","Cajitas"); self._heading("Cajitas 💰","Dinero separado, visible y protegido por propósito."); form=self._panel(2,0); ctk.CTkLabel(form,text="Nueva cajita",font=ctk.CTkFont(size=17,weight="bold")).pack(anchor="w",padx=18,pady=(16,2)); name=self._entry(form,"Nombre"); goal=self._entry(form,"Meta COP","0"); owner=self._entry(form,"Titular","Compartido",["Samuel","Sara","Compartido"])
        def create()->None:
            holder=PERSONA1 if self._value(owner)=="Samuel" else PERSONA2 if self._value(owner)=="Sara" else "compartido"; self.service.crear_cajita({"nombre":self._value(name),"meta":self._value(goal),"titular":holder,"descripcion":"","icono":"💰"}); self.show_savings()
        ctk.CTkButton(form,text="Crear cajita",fg_color=T.PRIMARY,command=lambda:self._run(create,"Cajita creada")).pack(fill="x",padx=18,pady=18); boxes=self.service.dashboard(self.selected_month)["cajitas"]["fondos"]
        if not boxes:self._empty(2,"Aún no tienen cajitas","Cada reserva empieza con una intención.",self.show_savings); return
        for i,b in enumerate(boxes):
            p=self._panel(2+i//3,1+i%3); ctk.CTkLabel(p,text=f"{b.get('icono','💰')}  {b['nombre']}",font=ctk.CTkFont(size=16,weight="bold")).pack(anchor="w",padx=16,pady=(16,3)); ctk.CTkLabel(p,text=dinero(b["saldo"]),font=ctk.CTkFont(size=23,weight="bold"),text_color=T.SAVE).pack(anchor="w",padx=16); ctk.CTkLabel(p,text=f"{b.get('titular',b['propietario'])} · Samuel {dinero(b['aporte_p1'])} · Sara {dinero(b['aporte_p2'])}",text_color=T.MUTED,wraplength=230).pack(anchor="w",padx=16,pady=4)
            if b["meta"]:bar=ctk.CTkProgressBar(p,progress_color=T.SAVE,fg_color=T.ALT,height=7);bar.pack(fill="x",padx=16,pady=(5,8));bar.set(min(b["saldo"]/b["meta"],1))
            ctk.CTkButton(p,text="Mover dinero",height=30,fg_color=T.ALT,text_color=T.TXT,command=lambda box=b:self.savings_movement_dialog(box)).pack(fill="x",padx=16,pady=(4,16))
    def savings_movement_dialog(self,b:dict[str,Any])->None:
        d=ctk.CTkToplevel(self);d.title(f"{b['nombre']} · Movimiento");d.geometry("420x410");d.grab_set();body=ctk.CTkFrame(d,fg_color=T.S,corner_radius=18);body.pack(fill="both",expand=True,padx=14,pady=14); kind=self._entry(body,"Movimiento","Ingresar dinero",["Ingresar dinero","Retirar dinero"]); amount=self._entry(body,"Monto COP");concept=self._entry(body,"Motivo");person=self._entry(body,"Quién lo realiza","Samuel",["Samuel","Sara"])
        def save()->None:self.service.movimiento_ahorro({"mes":self.selected_month,"fecha":dt.date.today().isoformat(),"ahorro_id":str(b["id"]),"tipo":"Depositar" if self._value(kind).startswith("Ingresar") else "Retirar","monto":self._value(amount),"concepto":self._value(concept),"aportante":PERSONA1 if self._value(person)=="Samuel" else PERSONA2});d.destroy();self.show_savings()
        ctk.CTkButton(body,text="Guardar movimiento",fg_color=T.PRIMARY,command=lambda:self._run(save,"Movimiento registrado")).pack(fill="x",pady=20)

    def show_advisor(self)->None:
        self._clear("Análisis","Análisis");self._heading("Tu asistente financiero ✦","Señales locales explicables, basadas solo en datos registrados.");a=self.service.asesor(self.selected_month);self._card(2,0,"Lectura del mes",a["resumen"],"Actualizado con sus movimientos",T.PRIMARY,2);self._card(2,2,"Liquidez",dinero(a["liquidez"]),"Con datos registrados",T.OK,2)
        if not a["recomendaciones"]:self._empty(3,"Sin alertas importantes","Registra más actividad para enriquecer el análisis.",self.show_transactions)
        for i,r in enumerate(a["recomendaciones"]):
            p=self._panel(3+i,0,4);color=T.BAD if r["prioridad"]=="alta" else T.WARN if r["prioridad"]=="media" else T.OK;ctk.CTkLabel(p,text=f"{r['prioridad'].upper()}  ·  {r['titulo']}",font=ctk.CTkFont(size=16,weight="bold"),text_color=color).pack(anchor="w",padx=18,pady=(14,3));ctk.CTkLabel(p,text=r["mensaje"],wraplength=880,justify="left").pack(anchor="w",padx=18);ctk.CTkLabel(p,text=f"Confianza {r['confianza']}  ·  Datos: {r['datos']}",text_color=T.MUTED,wraplength=880).pack(anchor="w",padx=18,pady=(5,14))
    def show_third_parties(self)->None:
        self._clear("Terceros","Terceros");self._heading("Cuentas con terceros","Obligaciones externas separadas de su balance como pareja.");d=self.service.terceros();self._card(2,0,"Por cobrar",dinero(d["por_cobrar"]),"Dinero a favor",T.OK,2);self._card(2,2,"Por pagar",dinero(d["por_pagar"]),"Obligaciones pendientes",T.WARN,2)
        if not d["detalle"]:self._empty(3,"No hay obligaciones abiertas","Aquí aparecerán préstamos y sus abonos.",self.show_transactions)
        for i,x in enumerate(d["detalle"]):self._card(3+i,0,x["tercero"],dinero(x["saldo_pendiente"]),f"{x['tipo']} · {NOMBRES[x['propietario']]}",T.TXT,4)
    def show_settlement(self)->None:
        self._clear("Liquidación","Liquidación");self._heading("Balance de pareja","Una vista clara de aportes, consumos y liquidaciones internas.");d=self.service.dashboard(self.selected_month)["balance"]["detalle"]
        for i,(p,label) in enumerate(((PERSONA1,"Samuel"),(PERSONA2,"Sara"))):v=d[p]["balance_neto"];self._card(2,i*2,label,dinero(abs(v)),"A favor" if v>0 else "Pendiente de equilibrar" if v<0 else "Equilibrado",T.OK if v>=0 else T.WARN,2)
    def show_settings(self)->None:
        self._clear("Ajustes","Ajustes");self._heading("Personaliza tu experiencia","Preferencias guardadas localmente en este equipo.");p=self._panel(2,0,2);ctk.CTkLabel(p,text="Apariencia",font=ctk.CTkFont(size=17,weight="bold")).pack(anchor="w",padx=18,pady=(16,3));ctk.CTkLabel(p,text="Una paleta clara y otra diseñada para la noche.",text_color=T.MUTED).pack(anchor="w",padx=18);ctk.CTkButton(p,text="Cambiar a modo claro" if self.theme=="dark" else "Cambiar a modo oscuro",fg_color=T.PRIMARY,command=self.toggle_theme).pack(anchor="w",padx=18,pady=18);q=self._panel(2,2,2);ctk.CTkLabel(q,text="Datos locales",font=ctk.CTkFont(size=17,weight="bold")).pack(anchor="w",padx=18,pady=(16,3));ctk.CTkLabel(q,text="Los datos permanecen en SQLite en este equipo.",text_color=T.MUTED).pack(anchor="w",padx=18,pady=(0,18))

if __name__=="__main__": FinanzasApp().mainloop()
