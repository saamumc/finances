"""Capa SQLite con migraciones, auditoría e invariantes financieras."""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator

from constants import (
    DuplicateOperationError, ESTADO_ACTIVO, ESTADO_REVERSADO, IntegrityError,
    InsufficientFundsError, METODO_TARJETA, PERSONA1, PRIORIDAD_OBLIGATORIO,
    MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, PERSONA2, RESP_COMPARTIDO, RESP_P1, RESP_P2, TipoCuentaTercero, ValidationError, validar_fecha,
    validar_mes, validar_metodo_pago, validar_monto_no_negativo, validar_monto_positivo, validar_persona,
    validar_prioridad, validar_responsabilidad, validar_movimiento_ahorro, validar_texto, validar_tipo_cuenta_tercero,
)

DB_PATH = Path(__file__).parent / "data" / "finances.db"
SCHEMA_VERSION = 9


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def backup_db() -> Path | None:
    """Crea una copia antes de una migración; no modifica la base original."""
    if not DB_PATH.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    target = DB_PATH.with_name(f"{DB_PATH.stem}.backup.{stamp}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, target)
    return target


def _log(conn: sqlite3.Connection, entidad: str, entidad_id: int, accion: str, detalle: str = "") -> None:
    conn.execute(
        "INSERT INTO movimientos_log (fecha, entidad, entidad_id, accion, detalle) VALUES (?, ?, ?, ?, ?)",
        (_now_iso(), entidad, entidad_id, accion, detalle),
    )


def _crear_esquema(conn: sqlite3.Connection) -> None:
    """Crea el esquema actual en orden de dependencias, de forma idempotente."""
    statements = (
        "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)",
        """CREATE TABLE IF NOT EXISTS movimientos_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL,
            entidad TEXT NOT NULL, entidad_id INTEGER, accion TEXT NOT NULL, detalle TEXT)""",
        """CREATE TABLE IF NOT EXISTS tarjetas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            propietario TEXT NOT NULL CHECK (propietario IN ('persona1','persona2')),
            cupo_total INTEGER NOT NULL DEFAULT 0 CHECK (cupo_total >= 0),
            saldo_deuda INTEGER NOT NULL DEFAULT 0 CHECK (saldo_deuda >= 0),
            pago_minimo INTEGER NOT NULL DEFAULT 0 CHECK (pago_minimo >= 0),
            interes_mensual REAL NOT NULL DEFAULT 0 CHECK (interes_mensual >= 0),
            activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            saldo_inicial_historico INTEGER NOT NULL DEFAULT 0 CHECK (saldo_inicial_historico >= 0),
            saldo_historico_pendiente INTEGER NOT NULL DEFAULT 0 CHECK (saldo_historico_pendiente >= 0),
            fecha_saldo_inicial TEXT,
            banco TEXT, tipo TEXT, ultimos_4 TEXT, fecha_corte TEXT,
            fecha_pago TEXT, notas TEXT, color TEXT,
            creado_en TEXT, actualizado_en TEXT)""",
        """CREATE TABLE IF NOT EXISTS ingresos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL,
            persona TEXT NOT NULL CHECK (persona IN ('persona1','persona2')),
            concepto TEXT NOT NULL, valor INTEGER NOT NULL CHECK (valor >= 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT,
            transaction_uuid TEXT NOT NULL UNIQUE)""",
        """CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, nombre TEXT NOT NULL,
            categoria TEXT NOT NULL, valor INTEGER NOT NULL CHECK (valor > 0), fecha TEXT,
            tarjeta_id INTEGER, prioridad TEXT NOT NULL DEFAULT 'obligatorio'
                CHECK (prioridad IN ('obligatorio','discrecional')),
            metodo_pago TEXT NOT NULL DEFAULT 'efectivo'
                CHECK (metodo_pago IN ('efectivo','debito','tarjeta')),
            pagador TEXT NOT NULL DEFAULT 'persona1' CHECK (pagador IN ('persona1','persona2')),
            responsabilidad TEXT NOT NULL DEFAULT 'compartido'
                CHECK (responsabilidad IN ('persona1','persona2','compartido')),
            monto_p1 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p1 >= 0),
            monto_p2 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p2 >= 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id))""",
        """CREATE TABLE IF NOT EXISTS compras_tarjeta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id INTEGER NOT NULL, gasto_id INTEGER,
            descripcion TEXT NOT NULL, valor_original INTEGER NOT NULL CHECK (valor_original > 0),
            fecha TEXT, mes TEXT, tipo TEXT NOT NULL DEFAULT 'COMPRA', periodo TEXT,
            cuotas_totales INTEGER NOT NULL DEFAULT 1 CHECK (cuotas_totales >= 1),
            valor_pendiente INTEGER NOT NULL CHECK (valor_pendiente >= 0 AND valor_pendiente <= valor_original),
            responsabilidad TEXT NOT NULL CHECK (responsabilidad IN ('persona1','persona2','compartido')),
            monto_p1 INTEGER NOT NULL CHECK (monto_p1 >= 0), monto_p2 INTEGER NOT NULL CHECK (monto_p2 >= 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT UNIQUE,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id) ON DELETE CASCADE,
            FOREIGN KEY (gasto_id) REFERENCES gastos(id))""",
        """CREATE TABLE IF NOT EXISTS pagos_deuda (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, fecha TEXT NOT NULL,
            tarjeta_id INTEGER NOT NULL, monto INTEGER NOT NULL CHECK (monto > 0),
            pagador TEXT NOT NULL CHECK (pagador IN ('persona1','persona2')),
            monto_aportado_p1 INTEGER NOT NULL DEFAULT 0 CHECK (monto_aportado_p1 >= 0),
            monto_aportado_p2 INTEGER NOT NULL DEFAULT 0 CHECK (monto_aportado_p2 >= 0),
            monto_historico_aplicado INTEGER NOT NULL DEFAULT 0 CHECK (monto_historico_aplicado >= 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, concepto TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id))""",
        """CREATE TABLE IF NOT EXISTS asignaciones_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pago_id INTEGER NOT NULL, compra_id INTEGER NOT NULL,
            monto_asignado INTEGER NOT NULL CHECK (monto_asignado > 0), fecha TEXT NOT NULL,
            FOREIGN KEY (pago_id) REFERENCES pagos_deuda(id) ON DELETE CASCADE,
            FOREIGN KEY (compra_id) REFERENCES compras_tarjeta(id))""",
        """CREATE TABLE IF NOT EXISTS ajustes_tarjeta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL, saldo_anterior INTEGER NOT NULL,
            saldo_nuevo INTEGER NOT NULL, variacion INTEGER NOT NULL,
            valor_pendiente INTEGER NOT NULL DEFAULT 0 CHECK (valor_pendiente >= 0),
            monto_p1 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p1 >= 0),
            monto_p2 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p2 >= 0),
            motivo TEXT NOT NULL, realizado_por TEXT NOT NULL CHECK (realizado_por IN ('persona1','persona2')),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id))""",
        """CREATE TABLE IF NOT EXISTS asignaciones_pagos_ajustes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pago_id INTEGER NOT NULL, ajuste_id INTEGER NOT NULL,
            monto_asignado INTEGER NOT NULL CHECK (monto_asignado > 0), fecha TEXT NOT NULL,
            FOREIGN KEY (pago_id) REFERENCES pagos_deuda(id) ON DELETE CASCADE,
            FOREIGN KEY (ajuste_id) REFERENCES ajustes_tarjeta(id))""",
        """CREATE TABLE IF NOT EXISTS liquidaciones_pareja (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, fecha TEXT NOT NULL,
            deudor TEXT NOT NULL CHECK (deudor IN ('persona1','persona2')),
            acreedor TEXT NOT NULL CHECK (acreedor IN ('persona1','persona2')),
            monto INTEGER NOT NULL CHECK (monto > 0), concepto TEXT,
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            CHECK (deudor != acreedor))""",
        """CREATE TABLE IF NOT EXISTS ahorros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            descripcion TEXT, propietario TEXT NOT NULL DEFAULT 'persona1'
                CHECK (propietario IN ('persona1','persona2')),
            meta INTEGER NOT NULL DEFAULT 0 CHECK (meta >= 0),
            activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            creado_en TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS metas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            descripcion TEXT, monto_objetivo INTEGER NOT NULL CHECK (monto_objetivo >= 0),
            fecha_objetivo TEXT, prioridad TEXT NOT NULL DEFAULT 'obligatorio'
                CHECK (prioridad IN ('obligatorio','discrecional')),
            icono TEXT NOT NULL DEFAULT '🎯', color TEXT NOT NULL DEFAULT '#5B8DEF',
            ahorro_id INTEGER, activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            creado_en TEXT NOT NULL, FOREIGN KEY (ahorro_id) REFERENCES ahorros(id))""",
        """CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            creado_en TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS presupuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL,
            categoria_id INTEGER NOT NULL, monto INTEGER NOT NULL CHECK (monto > 0),
            UNIQUE (mes, categoria_id),
            FOREIGN KEY (categoria_id) REFERENCES categorias(id))""",
        """CREATE TABLE IF NOT EXISTS movimientos_ahorro (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ahorro_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('DEPOSITO','RETIRO')),
            monto INTEGER NOT NULL CHECK (monto > 0), concepto TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (ahorro_id) REFERENCES ahorros(id))""",
        """CREATE TABLE IF NOT EXISTS terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            contacto TEXT, creado_en TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS prestamos_terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tercero_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('POR_COBRAR','POR_PAGAR')),
            propietario TEXT NOT NULL CHECK (propietario IN ('persona1','persona2')),
            concepto TEXT NOT NULL, monto_original INTEGER NOT NULL CHECK (monto_original > 0),
            saldo_pendiente INTEGER NOT NULL CHECK (saldo_pendiente >= 0 AND saldo_pendiente <= monto_original),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (tercero_id) REFERENCES terceros(id))""",
        """CREATE TABLE IF NOT EXISTS abonos_terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, prestamo_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL, monto INTEGER NOT NULL CHECK (monto > 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (prestamo_id) REFERENCES prestamos_terceros(id))""",
        "CREATE INDEX IF NOT EXISTS idx_ingresos_mes ON ingresos(mes)",
        "CREATE INDEX IF NOT EXISTS idx_gastos_mes ON gastos(mes)",
        "CREATE INDEX IF NOT EXISTS idx_gastos_tarjeta ON gastos(tarjeta_id)",
        "CREATE INDEX IF NOT EXISTS idx_compras_tarjeta_fecha ON compras_tarjeta(tarjeta_id, fecha, id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_compras_tarjeta_transaction_uuid ON compras_tarjeta(transaction_uuid)",
        "CREATE INDEX IF NOT EXISTS idx_pagos_mes ON pagos_deuda(mes)",
        "CREATE INDEX IF NOT EXISTS idx_pagos_tarjeta ON pagos_deuda(tarjeta_id, fecha, id)",
        "CREATE INDEX IF NOT EXISTS idx_asignaciones_pago ON asignaciones_pagos(pago_id)",
        "CREATE INDEX IF NOT EXISTS idx_asignaciones_compra ON asignaciones_pagos(compra_id)",
        "CREATE INDEX IF NOT EXISTS idx_ajustes_tarjeta_fecha ON ajustes_tarjeta(tarjeta_id, fecha, id)",
        "CREATE INDEX IF NOT EXISTS idx_asignaciones_pago_ajuste ON asignaciones_pagos_ajustes(pago_id)",
        "CREATE INDEX IF NOT EXISTS idx_liquidaciones_mes ON liquidaciones_pareja(mes)",
        "CREATE INDEX IF NOT EXISTS idx_movimientos_ahorro_fondo ON movimientos_ahorro(ahorro_id, fecha, id)",
        "CREATE INDEX IF NOT EXISTS idx_movimientos_ahorro_mes ON movimientos_ahorro(mes)",
        "CREATE INDEX IF NOT EXISTS idx_metas_ahorro ON metas(ahorro_id)",
        "CREATE INDEX IF NOT EXISTS idx_presupuestos_mes ON presupuestos(mes)",
        "CREATE INDEX IF NOT EXISTS idx_prestamos_terceros_estado ON prestamos_terceros(tercero_id, estado)",
        "CREATE INDEX IF NOT EXISTS idx_abonos_terceros_prestamo ON abonos_terceros(prestamo_id, fecha, id)",
    )
    for statement in statements:
        conn.execute(statement)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migrar_legacy(conn: sqlite3.Connection) -> None:
    """Completa columnas aditivas y sanea UUIDs de bases v1/v2 existentes."""
    additions = {
        "tarjetas": ("saldo_inicial_historico INTEGER NOT NULL DEFAULT 0", "saldo_historico_pendiente INTEGER NOT NULL DEFAULT 0", "fecha_saldo_inicial TEXT", "banco TEXT", "tipo TEXT", "ultimos_4 TEXT", "fecha_corte TEXT", "fecha_pago TEXT", "notas TEXT", "color TEXT", "creado_en TEXT", "actualizado_en TEXT"),
        "gastos": ("tarjeta_id INTEGER", "monto_p1 INTEGER NOT NULL DEFAULT 0", "monto_p2 INTEGER NOT NULL DEFAULT 0", "estado TEXT NOT NULL DEFAULT 'ACTIVO'", "motivo_reversion TEXT", "fecha_reversion TEXT", "transaction_uuid TEXT"),
        "compras_tarjeta": ("mes TEXT", "tipo TEXT NOT NULL DEFAULT 'COMPRA'", "periodo TEXT", "motivo_reversion TEXT", "fecha_reversion TEXT", "transaction_uuid TEXT"),
        "ingresos": ("estado TEXT NOT NULL DEFAULT 'ACTIVO'", "motivo_reversion TEXT", "fecha_reversion TEXT", "transaction_uuid TEXT"),
        "pagos_deuda": ("monto_aportado_p1 INTEGER NOT NULL DEFAULT 0", "monto_aportado_p2 INTEGER NOT NULL DEFAULT 0", "monto_historico_aplicado INTEGER NOT NULL DEFAULT 0", "estado TEXT NOT NULL DEFAULT 'ACTIVO'", "motivo_reversion TEXT", "fecha_reversion TEXT", "concepto TEXT", "transaction_uuid TEXT"),
        "liquidaciones_pareja": ("estado TEXT NOT NULL DEFAULT 'ACTIVO'", "motivo_reversion TEXT", "fecha_reversion TEXT", "transaction_uuid TEXT"),
        "ahorros": ("propietario TEXT NOT NULL DEFAULT 'persona1'", "meta INTEGER NOT NULL DEFAULT 0",
                     "titular TEXT NOT NULL DEFAULT 'persona1'", "tipo TEXT NOT NULL DEFAULT 'Ahorro'",
                     "prioridad TEXT NOT NULL DEFAULT 'obligatorio'", "icono TEXT NOT NULL DEFAULT '💰'",
                     "color TEXT NOT NULL DEFAULT '#5B8DEF'", "fecha_objetivo TEXT"),
        "movimientos_ahorro": ("aportante TEXT NOT NULL DEFAULT 'persona1'", "gasto_id INTEGER", "motivo TEXT"),
    }
    for table, definitions in additions.items():
        for definition in definitions:
            _add_column_if_missing(conn, table, definition)

    # SQLite permite varios NULL en UNIQUE. Las filas antiguas reciben UUID antes
    # de crear los índices únicos, conservando la idempotencia desde este punto.
    for table in ("ingresos", "gastos", "pagos_deuda", "liquidaciones_pareja", "compras_tarjeta"):
        for row in conn.execute(f"SELECT id FROM {table} WHERE transaction_uuid IS NULL OR transaction_uuid = ''"):
            conn.execute(f"UPDATE {table} SET transaction_uuid=? WHERE id=?", (_new_uuid(), row["id"]))
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_transaction_uuid ON {table}(transaction_uuid)")

    # Los movimientos de tarjeta que ya existían son compras. La fecha se usa
    # únicamente para el análisis mensual; no altera la deuda almacenada.
    conn.execute("UPDATE compras_tarjeta SET tipo='COMPRA' WHERE tipo IS NULL OR tipo='' ")
    conn.execute("UPDATE compras_tarjeta SET mes=substr(fecha, 1, 7) WHERE (mes IS NULL OR mes='') AND fecha IS NOT NULL")
    ahora = _now_iso()
    conn.execute("UPDATE tarjetas SET creado_en=COALESCE(creado_en, ?), actualizado_en=COALESCE(actualizado_en, ?)", (ahora, ahora))

    # Una v2 no tenía saldo histórico pendiente: su saldo inicial es el pendiente.
    conn.execute("""UPDATE tarjetas SET saldo_historico_pendiente = saldo_inicial_historico
                    WHERE saldo_historico_pendiente = 0 AND saldo_inicial_historico > 0""")
    conn.execute("""CREATE TABLE IF NOT EXISTS metas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE, descripcion TEXT,
        monto_objetivo INTEGER NOT NULL CHECK (monto_objetivo >= 0), fecha_objetivo TEXT,
        prioridad TEXT NOT NULL DEFAULT 'obligatorio' CHECK (prioridad IN ('obligatorio','discrecional')),
        icono TEXT NOT NULL DEFAULT '🎯', color TEXT NOT NULL DEFAULT '#5B8DEF', ahorro_id INTEGER,
        activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)), creado_en TEXT NOT NULL,
        FOREIGN KEY (ahorro_id) REFERENCES ahorros(id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metas_ahorro ON metas(ahorro_id)")


def upgrade_db(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    _crear_esquema(conn)
    _migrar_legacy(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def init_db() -> None:
    """Inicializa una base nueva o respalda y migra una base anterior."""
    needs_backup = DB_PATH.exists()
    with get_conn() as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
    if needs_backup and current < SCHEMA_VERSION:
        backup_db()
    with get_conn() as conn:
        upgrade_db(conn)


def _validar_distribucion(valor: int, responsabilidad: str, monto_p1: int, monto_p2: int) -> None:
    if monto_p1 + monto_p2 != valor:
        raise IntegrityError(f"La distribución no cuadra: {monto_p1} + {monto_p2} != {valor}.")
    esperada = {RESP_P1: (valor, 0), RESP_P2: (0, valor)}.get(responsabilidad)
    if esperada is not None and (monto_p1, monto_p2) != esperada:
        raise IntegrityError("La distribución no coincide con la responsabilidad individual.")


def _tarjeta(conn: sqlite3.Connection, tarjeta_id: int) -> sqlite3.Row:
    tarjeta = conn.execute("SELECT * FROM tarjetas WHERE id=?", (tarjeta_id,)).fetchone()
    if tarjeta is None:
        raise ValidationError(f"Tarjeta {tarjeta_id} no encontrada.")
    return tarjeta


def _insert_idempotente(conn: sqlite3.Connection, sql: str, params: tuple, tx_uuid: str) -> int:
    try:
        cursor = conn.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        if "transaction_uuid" in str(exc).lower():
            raise DuplicateOperationError(f"Operación duplicada: {tx_uuid}") from None
        raise
    return cursor.lastrowid


def registrar_tarjeta(nombre: str, propietario: str, cupo_total: object, *, pago_minimo: object = 0,
                      interes_mensual: float = 0, saldo_inicial_historico: object = 0,
                      fecha_saldo_inicial: str | None = None, banco: str | None = None,
                      tipo: str | None = None, ultimos_4: str | None = None,
                      fecha_corte: str | None = None, fecha_pago: str | None = None,
                      notas: str | None = None, color: str | None = None) -> int:
    """Registra una tarjeta; el saldo histórico queda incluido en saldo_deuda."""
    validar_persona(propietario)
    nombre = validar_texto(nombre, "El nombre de la tarjeta")
    cupo = validar_monto_no_negativo(cupo_total)
    minimo = validar_monto_no_negativo(pago_minimo)
    historico = validar_monto_no_negativo(saldo_inicial_historico)
    if historico > cupo:
        raise InsufficientFundsError("El saldo histórico no puede superar el cupo de la tarjeta.")
    try:
        interes = Decimal(str(interes_mensual))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("El interés mensual es inválido.") from None
    if not interes.is_finite() or interes < 0:
        raise ValidationError("El interés mensual no puede ser negativo.")
    if fecha_saldo_inicial:
        fecha_saldo_inicial = validar_fecha(fecha_saldo_inicial)
    if fecha_corte:
        fecha_corte = validar_fecha(fecha_corte)
    if fecha_pago:
        fecha_pago = validar_fecha(fecha_pago)
    banco = validar_texto(banco or "", "El banco", obligatorio=False) or None
    tipo = validar_texto(tipo or "", "El tipo de tarjeta", obligatorio=False) or None
    notas = validar_texto(notas or "", "Las notas", maximo=500, obligatorio=False) or None
    color = validar_texto(color or "", "El color", maximo=20, obligatorio=False) or None
    ultimos_4 = (ultimos_4 or "").strip() or None
    if ultimos_4 and (len(ultimos_4) != 4 or not ultimos_4.isdigit()):
        raise ValidationError("Los últimos cuatro dígitos deben contener exactamente cuatro números.")
    with get_conn() as conn:
        card_id = conn.execute("""INSERT INTO tarjetas
            (nombre, propietario, cupo_total, saldo_deuda, pago_minimo, interes_mensual,
             saldo_inicial_historico, saldo_historico_pendiente, fecha_saldo_inicial,
             banco, tipo, ultimos_4, fecha_corte, fecha_pago, notas, color, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nombre, propietario, cupo, historico, minimo, float(interes), historico, historico, fecha_saldo_inicial,
             banco, tipo, ultimos_4, fecha_corte, fecha_pago, notas, color, _now_iso(), _now_iso())).lastrowid
        _log(conn, "tarjeta", card_id, "CREAR", f"saldo_historico={historico}")
        return card_id


def actualizar_tarjeta(tarjeta_id: int, *, nombre: str, propietario: str, cupo_total: object,
                       pago_minimo: object, interes_mensual: object, activa: bool,
                       banco: str | None = None, tipo: str | None = None, ultimos_4: str | None = None,
                       fecha_corte: str | None = None, fecha_pago: str | None = None,
                       notas: str | None = None, color: str | None = None) -> None:
    """Actualiza configuración, nunca altera silenciosamente la deuda vigente."""
    validar_persona(propietario)
    nombre = validar_texto(nombre, "El nombre de la tarjeta")
    cupo = validar_monto_no_negativo(cupo_total)
    minimo = validar_monto_no_negativo(pago_minimo)
    try:
        interes = Decimal(str(interes_mensual))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("El interés mensual es inválido.") from None
    if not interes.is_finite() or interes < 0:
        raise ValidationError("El interés mensual no puede ser negativo.")
    if fecha_corte:
        fecha_corte = validar_fecha(fecha_corte)
    if fecha_pago:
        fecha_pago = validar_fecha(fecha_pago)
    banco = validar_texto(banco or "", "El banco", obligatorio=False) or None
    tipo = validar_texto(tipo or "", "El tipo de tarjeta", obligatorio=False) or None
    notas = validar_texto(notas or "", "Las notas", maximo=500, obligatorio=False) or None
    color = validar_texto(color or "", "El color", maximo=20, obligatorio=False) or None
    ultimos_4 = (ultimos_4 or "").strip() or None
    if ultimos_4 and (len(ultimos_4) != 4 or not ultimos_4.isdigit()):
        raise ValidationError("Los últimos cuatro dígitos deben contener exactamente cuatro números.")
    with get_conn() as conn:
        tarjeta = _tarjeta(conn, tarjeta_id)
        if cupo < tarjeta["saldo_deuda"]:
            raise InsufficientFundsError("El cupo no puede ser menor que la deuda vigente. Ajusta el saldo primero si corresponde.")
        conn.execute("""UPDATE tarjetas SET nombre=?, propietario=?, cupo_total=?, pago_minimo=?, interes_mensual=?,
                       activa=?, banco=?, tipo=?, ultimos_4=?, fecha_corte=?, fecha_pago=?, notas=?, color=?, actualizado_en=?
                       WHERE id=?""",
                     (nombre, propietario, cupo, minimo, float(interes), int(bool(activa)), banco, tipo, ultimos_4,
                      fecha_corte, fecha_pago, notas, color, _now_iso(), tarjeta_id))
        _log(conn, "tarjeta", tarjeta_id, "EDITAR", "Configuración actualizada; saldo sin cambios.")


def registrar_gasto(mes: str, nombre: str, categoria: str, valor: object, fecha: str | None,
                     metodo_pago: str, pagador: str, responsabilidad: str, monto_p1: object,
                     monto_p2: object, tarjeta_id: int | None = None, cuotas: int = 1,
                     prioridad: str = PRIORIDAD_OBLIGATORIO, transaction_uuid: str | None = None) -> int:
    mes = validar_mes(mes)
    nombre = validar_texto(nombre, "El nombre del gasto")
    categoria = validar_texto(categoria, "La categoría")
    if fecha:
        fecha = validar_fecha(fecha)
    valor = validar_monto_positivo(valor)
    p1, p2 = validar_monto_no_negativo(monto_p1), validar_monto_no_negativo(monto_p2)
    validar_metodo_pago(metodo_pago); validar_persona(pagador)
    validar_responsabilidad(responsabilidad); validar_prioridad(prioridad)
    _validar_distribucion(valor, responsabilidad, p1, p2)
    if not isinstance(cuotas, int) or isinstance(cuotas, bool) or cuotas < 1:
        raise ValidationError("Las cuotas deben ser un entero mayor o igual a uno.")
    if metodo_pago != METODO_TARJETA and tarjeta_id is not None:
        raise ValidationError("Solo los gastos pagados con tarjeta pueden incluir tarjeta_id.")
    if metodo_pago == METODO_TARJETA and tarjeta_id is None:
        raise ValidationError("Un gasto con tarjeta requiere tarjeta_id.")
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        if tarjeta_id is not None:
            card = _tarjeta(conn, tarjeta_id)
            if not card["activa"]:
                raise InsufficientFundsError("La tarjeta está inactiva.")
            if card["saldo_deuda"] + valor > card["cupo_total"]:
                raise InsufficientFundsError("Cupo insuficiente para la compra.")
        gasto_id = _insert_idempotente(conn, """INSERT INTO gastos
            (mes,nombre,categoria,valor,fecha,tarjeta_id,prioridad,metodo_pago,pagador,responsabilidad,monto_p1,monto_p2,estado,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mes, nombre, categoria, valor, fecha, tarjeta_id, prioridad, metodo_pago, pagador, responsabilidad, p1, p2, ESTADO_ACTIVO, tx_uuid), tx_uuid)
        if tarjeta_id is not None:
            conn.execute("""INSERT INTO compras_tarjeta
                (tarjeta_id,gasto_id,descripcion,valor_original,fecha,mes,tipo,cuotas_totales,valor_pendiente,responsabilidad,monto_p1,monto_p2,estado,transaction_uuid)
                VALUES (?, ?, ?, ?, ?, ?, 'COMPRA', ?, ?, ?, ?, ?, ?, ?)""",
                (tarjeta_id, gasto_id, nombre, valor, fecha, mes, cuotas, valor, responsabilidad, p1, p2, ESTADO_ACTIVO, tx_uuid))
            conn.execute("UPDATE tarjetas SET saldo_deuda=saldo_deuda+?, actualizado_en=? WHERE id=?", (valor, _now_iso(), tarjeta_id))
        _log(conn, "gasto", gasto_id, "CREAR", f"valor={valor}; metodo={metodo_pago}")
        return gasto_id


def registrar_movimiento_tarjeta(mes: str, fecha: str, tarjeta_id: int, tipo: str, monto: object,
                                 descripcion: str, responsabilidad: str, monto_p1: object, monto_p2: object,
                                 transaction_uuid: str | None = None, *, periodo: str | None = None) -> int:
    """Registra un interés o cargo sin convertirlo en una compra ni en salida de caja.

    Se conserva el soporte de pago existente en ``compras_tarjeta`` porque un
    pago puede amortizar indistintamente una compra, un interés o un cargo.
    El campo ``tipo`` evita presentarlos como consumo comercial en la UI.
    """
    mes, fecha = validar_mes(mes), validar_fecha(fecha)
    if tipo not in ("INTERES", "CARGO"):
        raise ValidationError("El movimiento de tarjeta debe ser INTERES o CARGO.")
    monto = validar_monto_positivo(monto)
    descripcion = validar_texto(descripcion, "El concepto")
    validar_responsabilidad(responsabilidad)
    p1, p2 = validar_monto_no_negativo(monto_p1), validar_monto_no_negativo(monto_p2)
    _validar_distribucion(monto, responsabilidad, p1, p2)
    periodo = validar_mes(periodo) if periodo else None
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        tarjeta = _tarjeta(conn, tarjeta_id)
        if not tarjeta["activa"]:
            raise ValidationError("La tarjeta está inactiva.")
        if tarjeta["saldo_deuda"] + monto > tarjeta["cupo_total"]:
            raise InsufficientFundsError("El movimiento superaría el cupo registrado de la tarjeta.")
        movimiento_id = _insert_idempotente(conn, """INSERT INTO compras_tarjeta
            (tarjeta_id, gasto_id, descripcion, valor_original, fecha, mes, tipo, periodo, cuotas_totales,
             valor_pendiente, responsabilidad, monto_p1, monto_p2, estado, transaction_uuid)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
            (tarjeta_id, descripcion, monto, fecha, mes, tipo, periodo, monto, responsabilidad, p1, p2,
             ESTADO_ACTIVO, tx_uuid), tx_uuid)
        conn.execute("UPDATE tarjetas SET saldo_deuda=saldo_deuda+?, actualizado_en=? WHERE id=?",
                     (monto, _now_iso(), tarjeta_id))
        _log(conn, "movimiento_tarjeta", movimiento_id, "CREAR", f"tipo={tipo}; monto={monto}; tarjeta={tarjeta_id}")
        return movimiento_id


def reversar_movimiento_tarjeta(movimiento_id: int, motivo: str) -> None:
    """Revierte interés o cargo sin borrar historial; exige que no tenga pagos aplicados."""
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        movimiento = conn.execute("SELECT * FROM compras_tarjeta WHERE id=?", (movimiento_id,)).fetchone()
        if movimiento is None or movimiento["estado"] == ESTADO_REVERSADO:
            raise ValidationError("El movimiento no existe o ya está reversado.")
        if movimiento["tipo"] not in ("INTERES", "CARGO"):
            raise ValidationError("Esta operación solo revierte intereses o cargos registrados directamente.")
        if movimiento["valor_pendiente"] != movimiento["valor_original"]:
            raise ValidationError("Reversa primero los pagos aplicados a este movimiento.")
        tarjeta = _tarjeta(conn, movimiento["tarjeta_id"])
        if tarjeta["saldo_deuda"] < movimiento["valor_original"]:
            raise IntegrityError("El saldo de tarjeta es inconsistente con el movimiento a reversar.")
        conn.execute("""UPDATE compras_tarjeta SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?""",
                     (ESTADO_REVERSADO, motivo, _now_iso(), movimiento_id))
        conn.execute("UPDATE tarjetas SET saldo_deuda=saldo_deuda-?, actualizado_en=? WHERE id=?",
                     (movimiento["valor_original"], _now_iso(), movimiento["tarjeta_id"]))
        _log(conn, "movimiento_tarjeta", movimiento_id, "REVERSAR", motivo)


def ajustar_saldo_tarjeta(tarjeta_id: int, nuevo_saldo: object, motivo: str, fecha: str,
                          realizado_por: str, transaction_uuid: str | None = None) -> int:
    """Corrige explícitamente una deuda sin disfrazar el cambio de compra o pago.

    Un aumento queda como ajuste pendiente y se amortiza por la ruta habitual
    de pagos. Una reducción corrige primero deuda histórica y luego saldos
    pendientes de movimientos, sin crear una salida de efectivo ficticia.
    """
    nuevo_saldo = validar_monto_no_negativo(nuevo_saldo)
    motivo = validar_texto(motivo, "El motivo del ajuste")
    fecha = validar_fecha(fecha)
    realizado_por = validar_persona(realizado_por)
    mes = fecha[:7]
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        tarjeta = _tarjeta(conn, tarjeta_id)
        anterior = tarjeta["saldo_deuda"]
        if nuevo_saldo == anterior:
            raise ValidationError("El nuevo saldo es igual al saldo actual; no hay ajuste que registrar.")
        if nuevo_saldo > tarjeta["cupo_total"]:
            raise InsufficientFundsError("El saldo ajustado no puede superar el cupo registrado.")
        variacion = nuevo_saldo - anterior
        p1 = p2 = 0
        pendiente = 0
        if variacion > 0:
            pendiente = variacion
            if tarjeta["propietario"] == PERSONA1:
                p1 = variacion
            else:
                p2 = variacion
        else:
            por_corregir = -variacion
            historico = min(por_corregir, tarjeta["saldo_historico_pendiente"])
            if historico:
                if tarjeta["propietario"] == PERSONA1:
                    p1 += historico
                else:
                    p2 += historico
                conn.execute("UPDATE tarjetas SET saldo_historico_pendiente=saldo_historico_pendiente-? WHERE id=?",
                             (historico, tarjeta_id))
                por_corregir -= historico
            movimientos = conn.execute("""SELECT * FROM compras_tarjeta WHERE tarjeta_id=? AND estado=?
                                         AND valor_pendiente>0 ORDER BY COALESCE(fecha, ''), id""",
                                        (tarjeta_id, ESTADO_ACTIVO)).fetchall()
            for movimiento in movimientos:
                if not por_corregir:
                    break
                aplicado = min(por_corregir, movimiento["valor_pendiente"])
                proporcion_p1 = Decimal(movimiento["monto_p1"]) / Decimal(movimiento["valor_original"])
                pendiente_antes_p1 = int((Decimal(movimiento["valor_pendiente"]) * proporcion_p1).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                pendiente_despues_p1 = int((Decimal(movimiento["valor_pendiente"] - aplicado) * proporcion_p1).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                corregido_p1 = pendiente_antes_p1 - pendiente_despues_p1
                p1 += corregido_p1
                p2 += aplicado - corregido_p1
                conn.execute("UPDATE compras_tarjeta SET valor_pendiente=valor_pendiente-? WHERE id=?",
                             (aplicado, movimiento["id"]))
                por_corregir -= aplicado
            if por_corregir:
                raise IntegrityError("No hay saldo pendiente suficiente para respaldar el ajuste.")
        ajuste_id = _insert_idempotente(conn, """INSERT INTO ajustes_tarjeta
            (tarjeta_id,mes,fecha,saldo_anterior,saldo_nuevo,variacion,valor_pendiente,monto_p1,monto_p2,motivo,realizado_por,estado,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tarjeta_id, mes, fecha, anterior, nuevo_saldo, variacion, pendiente, p1, p2, motivo,
             realizado_por, ESTADO_ACTIVO, tx_uuid), tx_uuid)
        conn.execute("UPDATE tarjetas SET saldo_deuda=?, actualizado_en=? WHERE id=?", (nuevo_saldo, _now_iso(), tarjeta_id))
        _log(conn, "ajuste_tarjeta", ajuste_id, "CREAR", f"saldo_anterior={anterior}; saldo_nuevo={nuevo_saldo}; motivo={motivo}")
        return ajuste_id


def reversar_gasto(gasto_id: int, motivo: str) -> None:
    """Revierte un gasto sin borrar su rastro; una compra abonada no se puede revertir."""
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        gasto = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
        if gasto is None or gasto["estado"] == ESTADO_REVERSADO:
            raise ValidationError("El gasto no existe o ya está reversado.")
        if gasto["metodo_pago"] == METODO_TARJETA:
            compra = conn.execute("SELECT * FROM compras_tarjeta WHERE gasto_id=? AND estado=?", (gasto_id, ESTADO_ACTIVO)).fetchone()
            if compra is None:
                raise IntegrityError("No existe la compra activa asociada al gasto de tarjeta.")
            if compra["valor_pendiente"] != compra["valor_original"]:
                raise ValidationError("Reversa primero los pagos que fueron aplicados a esta compra.")
            card = _tarjeta(conn, gasto["tarjeta_id"])
            if card["saldo_deuda"] < compra["valor_original"]:
                raise IntegrityError("El saldo de tarjeta es inconsistente con la compra a reversar.")
            conn.execute("UPDATE compras_tarjeta SET estado=? WHERE id=?", (ESTADO_REVERSADO, compra["id"]))
            conn.execute("UPDATE tarjetas SET saldo_deuda=saldo_deuda-? WHERE id=?", (compra["valor_original"], gasto["tarjeta_id"]))
        conn.execute("UPDATE gastos SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?", (ESTADO_REVERSADO, motivo, _now_iso(), gasto_id))
        _log(conn, "gasto", gasto_id, "REVERSAR", motivo)


def registrar_ingreso(mes: str, persona: str, concepto: str, valor: object,
                      transaction_uuid: str | None = None) -> int:
    mes = validar_mes(mes)
    validar_persona(persona)
    monto = validar_monto_positivo(valor)
    concepto = validar_texto(concepto, "El concepto del ingreso")
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        ingreso_id = _insert_idempotente(conn, """INSERT INTO ingresos
            (mes,persona,concepto,valor,estado,transaction_uuid) VALUES (?, ?, ?, ?, ?, ?)""",
            (mes, persona, concepto, monto, ESTADO_ACTIVO, tx_uuid), tx_uuid)
        _log(conn, "ingreso", ingreso_id, "CREAR", f"valor={monto}")
        return ingreso_id


def reversar_ingreso(ingreso_id: int, motivo: str) -> None:
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        ingreso = conn.execute("SELECT estado FROM ingresos WHERE id=?", (ingreso_id,)).fetchone()
        if ingreso is None or ingreso["estado"] == ESTADO_REVERSADO:
            raise ValidationError("El ingreso no existe o ya está reversado.")
        conn.execute("UPDATE ingresos SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?",
                     (ESTADO_REVERSADO, motivo, _now_iso(), ingreso_id))
        _log(conn, "ingreso", ingreso_id, "REVERSAR", motivo)


def registrar_pago_tarjeta(mes: str, fecha: str, tarjeta_id: int, monto: object, pagador: str,
                            monto_aportado_p1: object, monto_aportado_p2: object,
                            transaction_uuid: str | None = None, *, concepto: str | None = None) -> int:
    mes = validar_mes(mes)
    fecha = validar_fecha(fecha)
    monto = validar_monto_positivo(monto)
    p1, p2 = validar_monto_no_negativo(monto_aportado_p1), validar_monto_no_negativo(monto_aportado_p2)
    validar_persona(pagador)
    if p1 + p2 != monto:
        raise IntegrityError("Los aportes al pago deben sumar exactamente el monto.")
    concepto = validar_texto(concepto or "", "El motivo", maximo=500, obligatorio=False) or None
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        card = _tarjeta(conn, tarjeta_id)
        if monto > card["saldo_deuda"]:
            raise InsufficientFundsError("El pago supera la deuda actual de la tarjeta.")
        historico = min(monto, card["saldo_historico_pendiente"])
        pago_id = _insert_idempotente(conn, """INSERT INTO pagos_deuda
            (mes,fecha,tarjeta_id,monto,pagador,monto_aportado_p1,monto_aportado_p2,monto_historico_aplicado,estado,concepto,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mes, fecha, tarjeta_id, monto, pagador, p1, p2, historico, ESTADO_ACTIVO, concepto, tx_uuid), tx_uuid)
        conn.execute("UPDATE tarjetas SET saldo_deuda=saldo_deuda-?, saldo_historico_pendiente=saldo_historico_pendiente-?, actualizado_en=? WHERE id=?", (monto, historico, _now_iso(), tarjeta_id))
        restante = monto - historico
        compras = conn.execute("""SELECT * FROM compras_tarjeta WHERE tarjeta_id=? AND estado=? AND valor_pendiente>0
                                 ORDER BY COALESCE(fecha, ''), id""", (tarjeta_id, ESTADO_ACTIVO)).fetchall()
        for compra in compras:
            if restante == 0:
                break
            aplicado = min(restante, compra["valor_pendiente"])
            conn.execute("INSERT INTO asignaciones_pagos (pago_id,compra_id,monto_asignado,fecha) VALUES (?, ?, ?, ?)", (pago_id, compra["id"], aplicado, fecha))
            conn.execute("UPDATE compras_tarjeta SET valor_pendiente=valor_pendiente-? WHERE id=?", (aplicado, compra["id"]))
            restante -= aplicado
        ajustes = conn.execute("""SELECT * FROM ajustes_tarjeta WHERE tarjeta_id=? AND estado=? AND variacion>0
                                 AND valor_pendiente>0 ORDER BY fecha, id""", (tarjeta_id, ESTADO_ACTIVO)).fetchall()
        for ajuste in ajustes:
            if restante == 0:
                break
            aplicado = min(restante, ajuste["valor_pendiente"])
            conn.execute("INSERT INTO asignaciones_pagos_ajustes (pago_id,ajuste_id,monto_asignado,fecha) VALUES (?, ?, ?, ?)",
                         (pago_id, ajuste["id"], aplicado, fecha))
            conn.execute("UPDATE ajustes_tarjeta SET valor_pendiente=valor_pendiente-? WHERE id=?", (aplicado, ajuste["id"]))
            restante -= aplicado
        if restante:
            raise IntegrityError("La deuda de tarjeta no tiene respaldo histórico ni compras activas.")
        _log(conn, "pago_deuda", pago_id, "CREAR", f"monto={monto}; historico={historico}")
        return pago_id


def reversar_pago_tarjeta(pago_id: int, motivo: str) -> None:
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        pago = conn.execute("SELECT * FROM pagos_deuda WHERE id=?", (pago_id,)).fetchone()
        if pago is None or pago["estado"] == ESTADO_REVERSADO:
            raise ValidationError("El pago no existe o ya está reversado.")
        posterior = conn.execute("""SELECT 1 FROM pagos_deuda WHERE tarjeta_id=? AND estado=?
            AND (fecha > ? OR (fecha = ? AND id > ?))""", (pago["tarjeta_id"], ESTADO_ACTIVO, pago["fecha"], pago["fecha"], pago_id)).fetchone()
        if posterior:
            raise ValidationError("Reversa primero los pagos activos posteriores (LIFO).")
        asignaciones = conn.execute("""SELECT a.*, c.estado AS compra_estado FROM asignaciones_pagos a
            LEFT JOIN compras_tarjeta c ON c.id=a.compra_id WHERE a.pago_id=?""", (pago_id,)).fetchall()
        if any(a["compra_estado"] != ESTADO_ACTIVO for a in asignaciones):
            raise IntegrityError("No se puede reversar: falta una compra asociada o está reversada.")
        asignaciones_ajustes = conn.execute("""SELECT a.*, j.estado AS ajuste_estado FROM asignaciones_pagos_ajustes a
            LEFT JOIN ajustes_tarjeta j ON j.id=a.ajuste_id WHERE a.pago_id=?""", (pago_id,)).fetchall()
        if any(a["ajuste_estado"] != ESTADO_ACTIVO for a in asignaciones_ajustes):
            raise IntegrityError("No se puede reversar: falta un ajuste asociado o está reversado.")
        for asignacion in asignaciones:
            conn.execute("UPDATE compras_tarjeta SET valor_pendiente=valor_pendiente+? WHERE id=?", (asignacion["monto_asignado"], asignacion["compra_id"]))
        for asignacion in asignaciones_ajustes:
            conn.execute("UPDATE ajustes_tarjeta SET valor_pendiente=valor_pendiente+? WHERE id=?", (asignacion["monto_asignado"], asignacion["ajuste_id"]))
        conn.execute("""UPDATE tarjetas SET saldo_deuda=saldo_deuda+?,
            saldo_historico_pendiente=saldo_historico_pendiente+?, actualizado_en=? WHERE id=?""", (pago["monto"], pago["monto_historico_aplicado"], _now_iso(), pago["tarjeta_id"]))
        conn.execute("UPDATE pagos_deuda SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?", (ESTADO_REVERSADO, motivo, _now_iso(), pago_id))
        _log(conn, "pago_deuda", pago_id, "REVERSAR", motivo)


def registrar_liquidacion(mes: str, fecha: str, deudor: str, acreedor: str, monto: object,
                          concepto: str | None = None, transaction_uuid: str | None = None) -> int:
    """Registra una liquidación. Se permite excedente: crea saldo a favor explícito en el cálculo."""
    mes = validar_mes(mes)
    fecha = validar_fecha(fecha)
    validar_persona(deudor); validar_persona(acreedor)
    if deudor == acreedor:
        raise ValidationError("Deudor y acreedor deben ser personas distintas.")
    monto = validar_monto_positivo(monto)
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        liq_id = _insert_idempotente(conn, """INSERT INTO liquidaciones_pareja
            (mes,fecha,deudor,acreedor,monto,concepto,estado,transaction_uuid) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mes, fecha, deudor, acreedor, monto, concepto, ESTADO_ACTIVO, tx_uuid), tx_uuid)
        _log(conn, "liquidacion", liq_id, "CREAR", f"{deudor}->{acreedor}; monto={monto}")
        return liq_id


def reversar_liquidacion(liquidacion_id: int, motivo: str) -> None:
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        liquidacion = conn.execute("SELECT estado FROM liquidaciones_pareja WHERE id=?", (liquidacion_id,)).fetchone()
        if liquidacion is None or liquidacion["estado"] == ESTADO_REVERSADO:
            raise ValidationError("La liquidación no existe o ya está reversada.")
        conn.execute("""UPDATE liquidaciones_pareja
                        SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?""",
                     (ESTADO_REVERSADO, motivo, _now_iso(), liquidacion_id))
        _log(conn, "liquidacion", liquidacion_id, "REVERSAR", motivo)


def crear_ahorro(nombre: str, propietario: str, descripcion: str | None = None, meta: object = 0,
                 *, titular: str | None = None, tipo: str = "Ahorro", prioridad: str = PRIORIDAD_OBLIGATORIO,
                 icono: str = "💰", color: str = "#5B8DEF", fecha_objetivo: str | None = None) -> int:
    """Crea una cajita con dueño; su saldo se deriva de movimientos activos."""
    nombre_limpio = validar_texto(nombre, "El nombre de la cajita")
    # propietario conserva compatibilidad histórica; titular admite "compartido".
    titular = titular or propietario
    if titular != RESP_COMPARTIDO:
        validar_persona(titular)
    validar_persona(propietario if propietario != RESP_COMPARTIDO else PERSONA1)
    descripcion_limpia = validar_texto(descripcion or "", "La descripción", maximo=500, obligatorio=False) or None
    meta = validar_monto_no_negativo(meta)
    prioridad = validar_prioridad(prioridad)
    tipo = validar_texto(tipo, "El tipo de cajita")
    icono = validar_texto(icono, "El icono", maximo=12)
    color = validar_texto(color, "El color", maximo=20)
    if fecha_objetivo:
        fecha_objetivo = validar_fecha(fecha_objetivo)
    with get_conn() as conn:
        try:
            ahorro_id = conn.execute(
                """INSERT INTO ahorros (nombre, descripcion, propietario, meta, titular, tipo, prioridad, icono, color, fecha_objetivo, creado_en)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nombre_limpio, descripcion_limpia, propietario, meta, titular, tipo, prioridad, icono, color, fecha_objetivo, _now_iso()),
            ).lastrowid
        except sqlite3.IntegrityError as exc:
            if "ahorros.nombre" in str(exc).lower():
                raise DuplicateOperationError(f"Ya existe una bolsa llamada {nombre_limpio!r}.") from None
            raise
        _log(conn, "ahorro", ahorro_id, "CREAR", f"nombre={nombre_limpio}; propietario={propietario}; meta={meta}")
        return ahorro_id


def _get_ahorro(conn: sqlite3.Connection, ahorro_id: int) -> sqlite3.Row:
    ahorro = conn.execute("SELECT * FROM ahorros WHERE id=?", (ahorro_id,)).fetchone()
    if ahorro is None:
        raise ValidationError(f"Bolsa de ahorro {ahorro_id} no encontrada.")
    if not ahorro["activa"]:
        raise ValidationError("La bolsa de ahorro está inactiva.")
    return ahorro


def _saldo_ahorro(conn: sqlite3.Connection, ahorro_id: int) -> int:
    fila = conn.execute("""SELECT COALESCE(SUM(CASE tipo WHEN ? THEN monto WHEN ? THEN -monto END), 0) AS saldo
                          FROM movimientos_ahorro WHERE ahorro_id=? AND estado=?""",
                        (MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, ahorro_id, ESTADO_ACTIVO)).fetchone()
    return int(fila["saldo"])


def registrar_movimiento_ahorro(mes: str, fecha: str, ahorro_id: int, tipo: str, monto: object,
                                concepto: str, transaction_uuid: str | None = None, *, aportante: str = PERSONA1,
                                gasto_id: int | None = None, motivo: str | None = None) -> int:
    """Registra un depósito o retiro interno sin convertirlo en ingreso/gasto."""
    mes = validar_mes(mes)
    fecha = validar_fecha(fecha)
    validar_movimiento_ahorro(tipo)
    monto_entero = validar_monto_positivo(monto)
    concepto = validar_texto(concepto, "El concepto del movimiento")
    aportante = validar_persona(aportante)
    motivo = validar_texto(motivo or "", "El motivo", maximo=500, obligatorio=False) or None
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        _get_ahorro(conn, ahorro_id)
        saldo = _saldo_ahorro(conn, ahorro_id)
        if tipo == MOV_AHORRO_RETIRO and monto_entero > saldo:
            raise InsufficientFundsError(f"Retiro ({monto_entero}) supera el saldo disponible ({saldo}).")
        if gasto_id is not None:
            gasto = conn.execute("SELECT id FROM gastos WHERE id=? AND estado=?", (gasto_id, ESTADO_ACTIVO)).fetchone()
            if gasto is None:
                raise ValidationError("El gasto asociado no existe o está reversado.")
        movimiento_id = _insert_idempotente(conn, """INSERT INTO movimientos_ahorro
            (ahorro_id,mes,fecha,tipo,monto,concepto,aportante,gasto_id,motivo,estado,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ahorro_id, mes, fecha, tipo, monto_entero, concepto, aportante, gasto_id, motivo, ESTADO_ACTIVO, tx_uuid), tx_uuid)
        _log(conn, "movimiento_ahorro", movimiento_id, "CREAR", f"tipo={tipo}; monto={monto_entero}; ahorro_id={ahorro_id}")
        return movimiento_id


def reversar_movimiento_ahorro(movimiento_id: int, motivo: str) -> None:
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        movimiento = conn.execute("SELECT * FROM movimientos_ahorro WHERE id=?", (movimiento_id,)).fetchone()
        if movimiento is None or movimiento["estado"] == ESTADO_REVERSADO:
            raise ValidationError("El movimiento de ahorro no existe o ya está reversado.")
        # Revertir un depósito no puede dejar el fondo por debajo de cero.
        if movimiento["tipo"] == MOV_AHORRO_DEPOSITO:
            saldo = _saldo_ahorro(conn, movimiento["ahorro_id"])
            if movimiento["monto"] > saldo:
                raise ValidationError("No se puede reversar el depósito: ya existen retiros posteriores que usan ese saldo.")
        conn.execute("""UPDATE movimientos_ahorro SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?""",
                     (ESTADO_REVERSADO, motivo, _now_iso(), movimiento_id))
        _log(conn, "movimiento_ahorro", movimiento_id, "REVERSAR", motivo)


def crear_meta(nombre: str, monto_objetivo: object, *, descripcion: str | None = None,
               fecha_objetivo: str | None = None, prioridad: str = PRIORIDAD_OBLIGATORIO,
               ahorro_id: int | None = None, icono: str = "🎯", color: str = "#5B8DEF") -> int:
    """Crea un objetivo independiente; puede enlazarse a una cajita existente."""
    nombre = validar_texto(nombre, "El nombre de la meta")
    objetivo = validar_monto_no_negativo(monto_objetivo)
    descripcion = validar_texto(descripcion or "", "La descripción", maximo=500, obligatorio=False) or None
    prioridad = validar_prioridad(prioridad)
    icono, color = validar_texto(icono, "El icono", maximo=12), validar_texto(color, "El color", maximo=20)
    if fecha_objetivo:
        fecha_objetivo = validar_fecha(fecha_objetivo)
    with get_conn() as conn:
        if ahorro_id is not None:
            _get_ahorro(conn, ahorro_id)
        try:
            meta_id = conn.execute("""INSERT INTO metas
                (nombre,descripcion,monto_objetivo,fecha_objetivo,prioridad,icono,color,ahorro_id,creado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nombre, descripcion, objetivo, fecha_objetivo, prioridad, icono, color, ahorro_id, _now_iso())).lastrowid
        except sqlite3.IntegrityError as exc:
            raise DuplicateOperationError(f"Ya existe una meta llamada {nombre!r}.") from exc
        _log(conn, meta_id and "meta", meta_id, "CREAR", f"objetivo={objetivo}; ahorro_id={ahorro_id}")
        return int(meta_id)


def editar_meta(meta_id: int, **cambios: object) -> None:
    permitidos = {"nombre", "descripcion", "monto_objetivo", "fecha_objetivo", "prioridad", "icono", "color", "ahorro_id", "activa"}
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise ValidationError("Campos de meta no válidos.")
    with get_conn() as conn:
        if conn.execute("SELECT id FROM metas WHERE id=?", (meta_id,)).fetchone() is None:
            raise ValidationError("Meta no encontrada.")
        valores: dict[str, object] = {}
        for campo, valor in cambios.items():
            if campo == "nombre": valores[campo] = validar_texto(valor, "El nombre de la meta")
            elif campo == "descripcion": valores[campo] = validar_texto(valor or "", "La descripción", maximo=500, obligatorio=False) or None
            elif campo == "monto_objetivo": valores[campo] = validar_monto_no_negativo(valor)
            elif campo == "fecha_objetivo": valores[campo] = validar_fecha(valor) if valor else None
            elif campo == "prioridad": valores[campo] = validar_prioridad(valor)
            elif campo in ("icono", "color"): valores[campo] = validar_texto(valor, campo, maximo=20)
            elif campo == "ahorro_id":
                if valor is not None: _get_ahorro(conn, int(valor))
                valores[campo] = valor
            else: valores[campo] = 1 if bool(valor) else 0
        if valores:
            asignaciones = ", ".join(f"{campo}=?" for campo in valores)
            conn.execute(f"UPDATE metas SET {asignaciones} WHERE id=?", (*valores.values(), meta_id))
            _log(conn, "meta", meta_id, "EDITAR", ",".join(valores))


def eliminar_meta(meta_id: int) -> None:
    editar_meta(meta_id, activa=False)


def get_metas(solo_activas: bool = True) -> list[dict]:
    query = "SELECT * FROM metas" + (" WHERE activa=1" if solo_activas else "") + " ORDER BY nombre COLLATE NOCASE"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query)]


def get_meta(meta_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM metas WHERE id=?", (meta_id,)).fetchone()
        if row is None: raise ValidationError("Meta no encontrada.")
        return dict(row)


def _tercero_id(conn: sqlite3.Connection, nombre: str) -> int:
    """Obtiene o crea un tercero normalizado dentro de la misma transacción."""
    nombre = validar_texto(nombre, "El nombre del tercero")
    existente = conn.execute("SELECT id FROM terceros WHERE nombre=?", (nombre,)).fetchone()
    if existente is not None:
        return int(existente["id"])
    tercero_id = conn.execute("INSERT INTO terceros (nombre, creado_en) VALUES (?, ?)",
                              (nombre, _now_iso())).lastrowid
    _log(conn, "tercero", tercero_id, "CREAR", nombre)
    return int(tercero_id)


def registrar_prestamo_tercero(mes: str, fecha: str, tercero: str, tipo: str | TipoCuentaTercero,
                               monto: object, propietario: str, concepto: str,
                               transaction_uuid: str | None = None) -> int:
    """Registra una cuenta por cobrar o por pagar frente a un tercero.

    El propietario identifica quién entregó o recibió el dinero. La obligación
    no modifica la liquidación interna de Sara y Yo: se informa aparte como
    activo o pasivo externo.
    """
    mes, fecha = validar_mes(mes), validar_fecha(fecha)
    tipo_valido = validar_tipo_cuenta_tercero(tipo)
    monto_entero = validar_monto_positivo(monto)
    propietario = validar_persona(propietario)
    concepto = validar_texto(concepto, "El concepto del préstamo")
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        tercero_id = _tercero_id(conn, tercero)
        prestamo_id = _insert_idempotente(conn, """INSERT INTO prestamos_terceros
            (tercero_id,mes,fecha,tipo,propietario,concepto,monto_original,saldo_pendiente,estado,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tercero_id, mes, fecha, tipo_valido, propietario, concepto, monto_entero, monto_entero,
             ESTADO_ACTIVO, tx_uuid), tx_uuid)
        _log(conn, "prestamo_tercero", prestamo_id, "CREAR", f"tipo={tipo_valido}; monto={monto_entero}")
        return prestamo_id


def registrar_abono_tercero(mes: str, fecha: str, prestamo_id: int, monto: object,
                             transaction_uuid: str | None = None) -> int:
    """Registra un cobro o pago parcial de una obligación externa."""
    mes, fecha = validar_mes(mes), validar_fecha(fecha)
    monto_entero = validar_monto_positivo(monto)
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        prestamo = conn.execute("SELECT * FROM prestamos_terceros WHERE id=?", (prestamo_id,)).fetchone()
        if prestamo is None or prestamo["estado"] != ESTADO_ACTIVO:
            raise ValidationError("El préstamo no existe o está reversado.")
        if monto_entero > prestamo["saldo_pendiente"]:
            raise InsufficientFundsError("El abono supera el saldo pendiente.")
        abono_id = _insert_idempotente(conn, """INSERT INTO abonos_terceros
            (prestamo_id,mes,fecha,monto,estado,transaction_uuid) VALUES (?, ?, ?, ?, ?, ?)""",
            (prestamo_id, mes, fecha, monto_entero, ESTADO_ACTIVO, tx_uuid), tx_uuid)
        conn.execute("UPDATE prestamos_terceros SET saldo_pendiente=saldo_pendiente-? WHERE id=?",
                     (monto_entero, prestamo_id))
        _log(conn, "abono_tercero", abono_id, "CREAR", f"monto={monto_entero}; prestamo={prestamo_id}")
        return abono_id


def reversar_abono_tercero(abono_id: int, motivo: str) -> None:
    """Revierte un abono conservando el historial y restableciendo su saldo."""
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        abono = conn.execute("SELECT * FROM abonos_terceros WHERE id=?", (abono_id,)).fetchone()
        if abono is None or abono["estado"] != ESTADO_ACTIVO:
            raise ValidationError("El abono no existe o ya está reversado.")
        conn.execute("UPDATE prestamos_terceros SET saldo_pendiente=saldo_pendiente+? WHERE id=?",
                     (abono["monto"], abono["prestamo_id"]))
        conn.execute("""UPDATE abonos_terceros SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?""",
                     (ESTADO_REVERSADO, motivo, _now_iso(), abono_id))
        _log(conn, "abono_tercero", abono_id, "REVERSAR", motivo)


def reversar_prestamo_tercero(prestamo_id: int, motivo: str) -> None:
    """Solo permite reversar un préstamo que no tiene abonos activos."""
    motivo = validar_texto(motivo, "El motivo de reversión")
    with get_conn() as conn:
        prestamo = conn.execute("SELECT * FROM prestamos_terceros WHERE id=?", (prestamo_id,)).fetchone()
        if prestamo is None or prestamo["estado"] != ESTADO_ACTIVO:
            raise ValidationError("El préstamo no existe o ya está reversado.")
        if prestamo["saldo_pendiente"] != prestamo["monto_original"]:
            raise ValidationError("Reversa primero los abonos activos de este préstamo.")
        conn.execute("""UPDATE prestamos_terceros SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?""",
                     (ESTADO_REVERSADO, motivo, _now_iso(), prestamo_id))
        _log(conn, "prestamo_tercero", prestamo_id, "REVERSAR", motivo)


def crear_categoria(nombre: str) -> int:
    """Registra una categoría reutilizable para gastos y presupuestos."""
    nombre = validar_texto(nombre, "El nombre de la categoría")
    with get_conn() as conn:
        try:
            categoria_id = conn.execute("INSERT INTO categorias (nombre, creado_en) VALUES (?, ?)",
                                        (nombre, _now_iso())).lastrowid
        except sqlite3.IntegrityError as exc:
            if "categorias.nombre" in str(exc).lower():
                raise DuplicateOperationError(f"Ya existe una categoría llamada {nombre!r}.") from None
            raise
        _log(conn, "categoria", categoria_id, "CREAR", nombre)
        return categoria_id


def get_categorias(solo_activas: bool = True) -> list[dict]:
    query = "SELECT * FROM categorias" + (" WHERE activa=1" if solo_activas else "") + " ORDER BY nombre COLLATE NOCASE"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query)]


def guardar_presupuesto(mes: str, categoria_id: int, monto: object) -> int:
    """Crea o reemplaza el presupuesto mensual de una categoría."""
    mes = validar_mes(mes)
    monto = validar_monto_positivo(monto)
    with get_conn() as conn:
        categoria = conn.execute("SELECT id FROM categorias WHERE id=? AND activa=1", (categoria_id,)).fetchone()
        if categoria is None:
            raise ValidationError("La categoría seleccionada no existe o está inactiva.")
        conn.execute("""INSERT INTO presupuestos (mes, categoria_id, monto) VALUES (?, ?, ?)
                        ON CONFLICT(mes, categoria_id) DO UPDATE SET monto=excluded.monto""",
                     (mes, categoria_id, monto))
        presupuesto = conn.execute("SELECT id FROM presupuestos WHERE mes=? AND categoria_id=?", (mes, categoria_id)).fetchone()
        _log(conn, "presupuesto", presupuesto["id"], "GUARDAR", f"mes={mes}; monto={monto}")
        return presupuesto["id"]


def get_presupuestos(mes: str) -> list[dict]:
    mes = validar_mes(mes)
    query = """SELECT p.id, p.mes, p.categoria_id, c.nombre AS categoria, p.monto,
                      COALESCE(SUM(CASE WHEN g.estado=? THEN g.valor ELSE 0 END), 0) AS gastado
               FROM presupuestos p JOIN categorias c ON c.id=p.categoria_id
               LEFT JOIN gastos g ON g.mes=p.mes AND g.categoria=c.nombre
               WHERE p.mes=? GROUP BY p.id ORDER BY c.nombre COLLATE NOCASE"""
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, (ESTADO_ACTIVO, mes))]


def get_liquidaciones(mes: str | None = None, incluir_reversados: bool = False) -> list[dict]:
    query, params = "SELECT * FROM liquidaciones_pareja WHERE 1=1", []
    if mes is not None:
        query += " AND mes=?"; params.append(mes)
    if not incluir_reversados:
        query += " AND estado=?"; params.append(ESTADO_ACTIVO)
    query += " ORDER BY fecha, id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def _get_movimientos(tabla: str, mes: str | None, incluir_reversados: bool) -> list[dict]:
    query, params = f"SELECT * FROM {tabla} WHERE 1=1", []
    if mes is not None:
        query += " AND mes=?"; params.append(mes)
    if not incluir_reversados:
        query += " AND estado=?"; params.append(ESTADO_ACTIVO)
    query += " ORDER BY id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_ingresos(mes: str | None = None, incluir_reversados: bool = False) -> list[dict]:
    return _get_movimientos("ingresos", mes, incluir_reversados)


def get_gastos(mes: str | None = None, incluir_reversados: bool = False) -> list[dict]:
    return _get_movimientos("gastos", mes, incluir_reversados)


def get_tarjetas(solo_activas: bool = False) -> list[dict]:
    query = "SELECT * FROM tarjetas" + (" WHERE activa=1" if solo_activas else "") + " ORDER BY id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query)]


def get_asignaciones_pago(pago_id: int) -> list[dict]:
    with get_conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM asignaciones_pagos WHERE pago_id=? ORDER BY id", (pago_id,))]


def get_movimientos_log(entidad: str | None = None, entidad_id: int | None = None, limit: int = 200) -> list[dict]:
    if not isinstance(limit, int) or limit <= 0:
        raise ValidationError("limit debe ser un entero positivo.")
    query, params = "SELECT * FROM movimientos_log WHERE 1=1", []
    if entidad is not None:
        query += " AND entidad=?"; params.append(entidad)
    if entidad_id is not None:
        query += " AND entidad_id=?"; params.append(entidad_id)
    query += " ORDER BY id DESC LIMIT ?"; params.append(limit)
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_compras_tarjeta(tarjeta_id: int | None = None, incluir_reversadas: bool = False) -> list[dict]:
    query, params = "SELECT * FROM compras_tarjeta WHERE 1=1", []
    if tarjeta_id is not None:
        query += " AND tarjeta_id=?"; params.append(tarjeta_id)
    if not incluir_reversadas:
        query += " AND estado=?"; params.append(ESTADO_ACTIVO)
    query += " ORDER BY COALESCE(fecha, ''), id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_pagos_deuda(mes: str | None = None, incluir_reversados: bool = False) -> list[dict]:
    query, params = "SELECT * FROM pagos_deuda WHERE 1=1", []
    if mes is not None:
        query += " AND mes=?"; params.append(mes)
    if not incluir_reversados:
        query += " AND estado=?"; params.append(ESTADO_ACTIVO)
    query += " ORDER BY fecha, id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_ajustes_tarjeta(tarjeta_id: int | None = None, incluir_reversados: bool = False) -> list[dict]:
    query, params = "SELECT * FROM ajustes_tarjeta WHERE 1=1", []
    if tarjeta_id is not None:
        query += " AND tarjeta_id=?"; params.append(tarjeta_id)
    if not incluir_reversados:
        query += " AND estado=?"; params.append(ESTADO_ACTIVO)
    query += " ORDER BY fecha, id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_asignaciones_pago_ajustes(pago_id: int) -> list[dict]:
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM asignaciones_pagos_ajustes WHERE pago_id=? ORDER BY id", (pago_id,)
        )]


def get_prestamos_terceros(incluir_reversados: bool = False) -> list[dict]:
    """Devuelve obligaciones externas con el nombre del tercero."""
    filtro = "" if incluir_reversados else " WHERE p.estado=?"
    query = """SELECT p.*, t.nombre AS tercero FROM prestamos_terceros p
               JOIN terceros t ON t.id=p.tercero_id""" + filtro + " ORDER BY p.fecha DESC, p.id DESC"
    with get_conn() as conn:
        params: tuple[str, ...] = () if incluir_reversados else (ESTADO_ACTIVO,)
        return [dict(row) for row in conn.execute(query, params)]


def get_abonos_tercero(prestamo_id: int, incluir_reversados: bool = False) -> list[dict]:
    query = "SELECT * FROM abonos_terceros WHERE prestamo_id=?"
    params: list[object] = [prestamo_id]
    if not incluir_reversados:
        query += " AND estado=?"
        params.append(ESTADO_ACTIVO)
    query += " ORDER BY fecha, id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_ahorros(solo_activos: bool = True) -> list[dict]:
    """Devuelve bolsas y sus saldos derivados de movimientos activos."""
    filtro = "WHERE a.activa=1" if solo_activos else ""
    query = f"""SELECT a.*, COALESCE(SUM(CASE m.tipo WHEN ? THEN m.monto WHEN ? THEN -m.monto END), 0) AS saldo,
                COALESCE(SUM(CASE WHEN m.tipo=? AND m.aportante=? THEN m.monto ELSE 0 END), 0) AS aporte_p1,
                COALESCE(SUM(CASE WHEN m.tipo=? AND m.aportante=? THEN m.monto ELSE 0 END), 0) AS aporte_p2,
                COALESCE(SUM(CASE WHEN m.tipo=? THEN m.monto ELSE 0 END), 0) AS total_aportado,
                COALESCE(SUM(CASE WHEN m.tipo=? THEN m.monto ELSE 0 END), 0) AS total_retirado
                FROM ahorros a LEFT JOIN movimientos_ahorro m ON m.ahorro_id=a.id AND m.estado=?
                {filtro} GROUP BY a.id ORDER BY a.nombre COLLATE NOCASE"""
    with get_conn() as conn:
        params = (MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, MOV_AHORRO_DEPOSITO, PERSONA1,
                  MOV_AHORRO_DEPOSITO, PERSONA2, MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, ESTADO_ACTIVO)
        return [dict(row) for row in conn.execute(query, params)]


def get_movimientos_ahorro(ahorro_id: int | None = None, mes: str | None = None,
                           incluir_reversados: bool = False) -> list[dict]:
    query, params = "SELECT * FROM movimientos_ahorro WHERE 1=1", []
    if ahorro_id is not None:
        query += " AND ahorro_id=?"; params.append(ahorro_id)
    if mes is not None:
        query += " AND mes=?"; params.append(mes)
    if not incluir_reversados:
        query += " AND estado=?"; params.append(ESTADO_ACTIVO)
    query += " ORDER BY fecha DESC, id DESC"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]
