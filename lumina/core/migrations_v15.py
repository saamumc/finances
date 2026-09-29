"""Migración v15: cimientos de Financial OS.

Reglas de esta migración (verificadas por tests/test_migracion_v15.py):

* Solo ``CREATE TABLE/INDEX IF NOT EXISTS``. No altera, no borra ni reescribe
  ninguna tabla, fila o índice de v14. Las extensiones de tablas existentes
  (ingresos, metas, fondo de emergencia) viven en tablas auxiliares.
* No duplica tarjetas ni préstamos: el Debt Center los leerá a través de un
  adaptador; ``deudas`` solo recoge lo que hoy no cabe (préstamo bancario,
  libranza, vehículo...).
* Dinero siempre INTEGER (COP). Tasas en puntos básicos INTEGER. Sin REAL.
* Tablas de movimientos: ``estado ACTIVO/REVERSADO`` + ``transaction_uuid UNIQUE``.
  Sin hard delete: corregir = insertar otra fila y reversar la anterior.
* Ninguna tabla se llena aquí y ninguna función nueva se activa sola.
* Es atómica: o quedan todas las tablas y ``user_version = 15``, o ninguna.
"""

from __future__ import annotations

import sqlite3

VERSION = 15

_ESTADO = "estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO'))"
_REVERSION = "motivo_reversion TEXT, fecha_reversion TEXT"
_UUID = "transaction_uuid TEXT NOT NULL UNIQUE"
_PERSONA = "CHECK ({c} IN ('persona1','persona2'))"

TABLAS_V15: tuple[str, ...] = (
    # reglas
    "reglas_usuario", "parametros_externos",
    # pareja
    "modelo_pareja", "aportes_pozo",
    # perfil / riesgo
    "perfil_persona",
    # deuda
    "deudas", "pagos_deudas",
    # emergencia
    "fondo_emergencia_config",
    # metas / ingresos
    "metas_plan", "ingresos_clasificacion", "metas_ingreso",
    # patrimonio
    "activos", "valoraciones_activo", "patrimonio_snapshot",
    # inversiones
    "inversiones", "movimientos_inversion",
    # alertas / escenarios / cooldown
    "alertas_estado", "escenarios_guardados", "compras_en_espera",
)

_SENTENCIAS: tuple[str, ...] = (
    # --- reglas: overrides del usuario y parámetros externos con vigencia ---
    """CREATE TABLE IF NOT EXISTS reglas_usuario (
        clave TEXT PRIMARY KEY, valor TEXT NOT NULL, actualizado_en TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS parametros_externos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, clave TEXT NOT NULL, valor TEXT NOT NULL,
        vigente_desde TEXT NOT NULL, fuente TEXT, confirmado_en TEXT,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    # --- pareja: modelo versionado; el pasado conserva su modelo ---
    """CREATE TABLE IF NOT EXISTS modelo_pareja (
        id INTEGER PRIMARY KEY AUTOINCREMENT, desde_mes TEXT NOT NULL,
        modelo TEXT NOT NULL CHECK (modelo IN ('5050','proporcional','pozo')),
        base_proporcional TEXT CHECK (base_proporcional IS NULL OR base_proporcional IN ('ingreso_mes','ingreso_promedio')),
        pozo_aporte_p1 INTEGER CHECK (pozo_aporte_p1 IS NULL OR pozo_aporte_p1 >= 0),
        pozo_aporte_p2 INTEGER CHECK (pozo_aporte_p2 IS NULL OR pozo_aporte_p2 >= 0),
        creado_en TEXT NOT NULL, """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    """CREATE TABLE IF NOT EXISTS aportes_pozo (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, fecha TEXT NOT NULL,
        persona TEXT NOT NULL """ + _PERSONA.format(c="persona") + """,
        monto INTEGER NOT NULL CHECK (monto > 0), concepto TEXT,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    # --- perfil: solo preferencias declaradas (lo calculado no se guarda) ---
    """CREATE TABLE IF NOT EXISTS perfil_persona (
        persona TEXT PRIMARY KEY """ + _PERSONA.format(c="persona") + """,
        horizonte_meses INTEGER CHECK (horizonte_meses IS NULL OR horizonte_meses > 0),
        perfil_riesgo TEXT, cuestionario_json TEXT, actualizado_en TEXT NOT NULL)""",
    # --- deuda: solo lo que no es tarjeta ni préstamo entre personas ---
    """CREATE TABLE IF NOT EXISTS deudas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, acreedor TEXT NOT NULL, tipo TEXT NOT NULL,
        titular TEXT NOT NULL CHECK (titular IN ('persona1','persona2','compartido')),
        saldo INTEGER NOT NULL CHECK (saldo >= 0),
        tasa_ea_pb INTEGER NOT NULL DEFAULT 0 CHECK (tasa_ea_pb >= 0),
        pago_minimo INTEGER NOT NULL DEFAULT 0 CHECK (pago_minimo >= 0),
        pago_actual INTEGER NOT NULL DEFAULT 0 CHECK (pago_actual >= 0),
        dia_pago INTEGER CHECK (dia_pago IS NULL OR (dia_pago BETWEEN 1 AND 31)),
        fecha_fin TEXT, activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
        creado_en TEXT NOT NULL, """ + _UUID + """)""",
    """CREATE TABLE IF NOT EXISTS pagos_deudas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, deuda_id INTEGER NOT NULL, mes TEXT NOT NULL, fecha TEXT NOT NULL,
        monto INTEGER NOT NULL CHECK (monto > 0),
        pagador TEXT NOT NULL """ + _PERSONA.format(c="pagador") + """,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """,
        FOREIGN KEY (deuda_id) REFERENCES deudas(id))""",
    # --- emergencia: designación explícita, versionada (reemplaza el "emerg" por nombre) ---
    """CREATE TABLE IF NOT EXISTS fondo_emergencia_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ahorro_id INTEGER NOT NULL,
        meses_minimo INTEGER NOT NULL DEFAULT 1 CHECK (meses_minimo >= 0),
        meses_base INTEGER NOT NULL DEFAULT 3 CHECK (meses_base >= 0),
        meses_robusto INTEGER NOT NULL DEFAULT 6 CHECK (meses_robusto >= 0),
        nivel_elegido TEXT NOT NULL DEFAULT 'base' CHECK (nivel_elegido IN ('minimo','base','robusto')),
        creado_en TEXT NOT NULL, """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """,
        FOREIGN KEY (ahorro_id) REFERENCES ahorros(id))""",
    # --- metas / ingresos: extensiones en tablas auxiliares (v14 intacto) ---
    """CREATE TABLE IF NOT EXISTS metas_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT, meta_id INTEGER NOT NULL,
        aporte_mensual_comprometido INTEGER CHECK (aporte_mensual_comprometido IS NULL OR aporte_mensual_comprometido >= 0),
        prioridad_orden INTEGER CHECK (prioridad_orden IS NULL OR prioridad_orden >= 1),
        creado_en TEXT NOT NULL, """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """,
        FOREIGN KEY (meta_id) REFERENCES metas(id))""",
    """CREATE TABLE IF NOT EXISTS ingresos_clasificacion (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ingreso_id INTEGER NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('fijo','variable')),
        fuente TEXT, creado_en TEXT NOT NULL, """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """,
        FOREIGN KEY (ingreso_id) REFERENCES ingresos(id))""",
    """CREATE TABLE IF NOT EXISTS metas_ingreso (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        persona TEXT CHECK (persona IS NULL OR persona IN ('persona1','persona2')),
        monto_adicional_mensual INTEGER NOT NULL CHECK (monto_adicional_mensual > 0),
        motivo TEXT, fecha_objetivo TEXT, creado_en TEXT NOT NULL,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    # --- patrimonio: activos, valoraciones y fotos mensuales append-only ---
    """CREATE TABLE IF NOT EXISTS activos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, tipo TEXT NOT NULL,
        titular TEXT NOT NULL CHECK (titular IN ('persona1','persona2','compartido')),
        liquidez TEXT NOT NULL CHECK (liquidez IN ('alta','media','baja')),
        activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
        creado_en TEXT NOT NULL, """ + _UUID + """)""",
    """CREATE TABLE IF NOT EXISTS valoraciones_activo (
        id INTEGER PRIMARY KEY AUTOINCREMENT, activo_id INTEGER NOT NULL, fecha TEXT NOT NULL,
        valor INTEGER NOT NULL CHECK (valor >= 0), nota TEXT,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """,
        FOREIGN KEY (activo_id) REFERENCES activos(id))""",
    """CREATE TABLE IF NOT EXISTS patrimonio_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL,
        activos_total INTEGER NOT NULL CHECK (activos_total >= 0),
        pasivos_total INTEGER NOT NULL CHECK (pasivos_total >= 0),
        patrimonio INTEGER NOT NULL, detalle_json TEXT NOT NULL, generado_en TEXT NOT NULL,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    # --- inversiones: registro; capital, valor y rendimiento se derivan ---
    """CREATE TABLE IF NOT EXISTS inversiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
        clase TEXT NOT NULL CHECK (clase IN ('cdt','fic','etf','accion','bono','efectivo','pension','otro')),
        entidad TEXT,
        titular TEXT NOT NULL CHECK (titular IN ('persona1','persona2','compartido')),
        horizonte_meses INTEGER CHECK (horizonte_meses IS NULL OR horizonte_meses > 0),
        liquidez TEXT NOT NULL CHECK (liquidez IN ('alta','media','baja')),
        riesgo TEXT NOT NULL CHECK (riesgo IN ('bajo','medio','alto')),
        comision_pb_anual INTEGER NOT NULL DEFAULT 0 CHECK (comision_pb_anual >= 0),
        fecha_apertura TEXT NOT NULL, fecha_vencimiento TEXT,
        activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
        creado_en TEXT NOT NULL, """ + _UUID + """)""",
    """CREATE TABLE IF NOT EXISTS movimientos_inversion (
        id INTEGER PRIMARY KEY AUTOINCREMENT, inversion_id INTEGER NOT NULL, fecha TEXT NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('APORTE','RETIRO','VALORACION')),
        monto INTEGER NOT NULL CHECK (monto >= 0),
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """,
        FOREIGN KEY (inversion_id) REFERENCES inversiones(id))""",
    # --- alertas, escenarios guardados, cooldown ---
    """CREATE TABLE IF NOT EXISTS alertas_estado (
        clave TEXT PRIMARY KEY, visto_en TEXT, silenciada_hasta TEXT)""",
    """CREATE TABLE IF NOT EXISTS escenarios_guardados (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, tipo TEXT NOT NULL,
        parametros_json TEXT NOT NULL, resultado_json TEXT, creado_en TEXT NOT NULL,
        """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    """CREATE TABLE IF NOT EXISTS compras_en_espera (
        id INTEGER PRIMARY KEY AUTOINCREMENT, descripcion TEXT NOT NULL,
        monto INTEGER NOT NULL CHECK (monto > 0),
        presupuestada INTEGER CHECK (presupuestada IS NULL OR presupuestada IN (0,1)),
        fecha_registro TEXT NOT NULL, revisar_desde TEXT NOT NULL,
        resolucion TEXT CHECK (resolucion IS NULL OR resolucion IN ('COMPRADA','DESCARTADA')),
        fecha_resolucion TEXT, """ + _ESTADO + """, """ + _REVERSION + """, """ + _UUID + """)""",
    # --- índices (todos sobre tablas nuevas) ---
    "CREATE INDEX IF NOT EXISTS idx_modelo_pareja_desde ON modelo_pareja(desde_mes, id)",
    "CREATE INDEX IF NOT EXISTS idx_aportes_pozo_mes ON aportes_pozo(mes)",
    "CREATE INDEX IF NOT EXISTS idx_pagos_deudas_deuda ON pagos_deudas(deuda_id, fecha, id)",
    "CREATE INDEX IF NOT EXISTS idx_metas_plan_meta ON metas_plan(meta_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_ingresos_clasif_ingreso ON ingresos_clasificacion(ingreso_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_valoraciones_activo ON valoraciones_activo(activo_id, fecha, id)",
    "CREATE INDEX IF NOT EXISTS idx_patrimonio_snapshot_mes ON patrimonio_snapshot(mes, id)",
    "CREATE INDEX IF NOT EXISTS idx_mov_inversion ON movimientos_inversion(inversion_id, fecha, id)",
    "CREATE INDEX IF NOT EXISTS idx_parametros_externos_clave ON parametros_externos(clave, vigente_desde)",
)


def aplicar_v15(conn: sqlite3.Connection) -> None:
    """Crea el esquema v15 de forma atómica.

    ``sqlite3`` no abre transacción antes de un DDL, así que se abre
    explícitamente: si algo falla, el rollback del llamador deja la base en v14
    sin tablas a medias.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    for sentencia in _SENTENCIAS:
        conn.execute(sentencia)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('schema_version', ?)", (str(VERSION),))
    conn.execute(f"PRAGMA user_version = {VERSION}")
