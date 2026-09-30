"""Capa SQLite con migraciones, auditoría e invariantes financieras."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterator
import datetime as dt
import shutil
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..constants import (
    DuplicateOperationError, ESTADO_ACTIVO, ESTADO_REVERSADO, IntegrityError,
    InsufficientFundsError, METODO_TARJETA, SAMUEL, PRIORIDAD_OBLIGATORIO,
    MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, SARA, RESP_COMPARTIDO, RESP_P1, RESP_P2, TipoCuentaTercero, ValidationError, validar_fecha,
    validar_mes, validar_metodo_pago, validar_monto_no_negativo, validar_monto_positivo, validar_persona,
    validar_prioridad, validar_responsabilidad, validar_movimiento_ahorro, validar_texto, validar_tipo_cuenta_tercero,
)


def _get_db_path() -> Path:
    """Busca finances.db en data/ o en la raíz, priorizando data/.
    
    Esto resuelve el problema donde database.py buscaba en data/finances.db
    pero el archivo estaba en la raíz del proyecto.
    """
    # Opción 1: Si está en data/ (estructura nueva/recomendada)
    primaria = Path(__file__).parent / "data" / "finances.db"
    if primaria.exists():
        return primaria
    
    # Opción 2: Si está en la raíz (estructura actual)
    fallback = Path(__file__).parent / "finances.db"
    if fallback.exists():
        return fallback
    
    # Opción 3: Si no existe ninguna, usar primaria (creará una nueva si es necesario)
    return primaria

DB_PATH = _get_db_path()
print(f"[database.py] BD será cargada de: {DB_PATH}")

# Cada cambio aditivo de esquema debe aumentar esta versión: así las bases ya
# existentes ejecutan la migración antes de que la interfaz las consulte.
SCHEMA_VERSION = 16
SCHEMA_VERSION_LEGACY = 14  # último esquema que pasa por _migrar_legacy


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
    """Crea un backup SQLite consistente de la base antes de una migración."""
    if not DB_PATH.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    target = DB_PATH.with_name(f"{DB_PATH.stem}.backup.{stamp}{DB_PATH.suffix}")
    source = sqlite3.connect(DB_PATH)
    backup = sqlite3.connect(target)
    try:
        source.backup(backup)
        backup.commit()
    finally:
        backup.close()
        source.close()
    return target


def _log(conn: sqlite3.Connection, entidad: str, entidad_id: int, accion: str, detalle: str = "") -> None:
    conn.execute(
        "INSERT INTO movimientos_log (fecha, entidad, entidad_id, accion, detalle) VALUES (?, ?, ?, ?, ?)",
        (_now_iso(), entidad, entidad_id, accion, detalle),
    )


def _crear_esquema_fix(conn: sqlite3.Connection) -> None:
    """Extensión a _crear_esquema() que graba la versión del schema.
    
    Llamar DESPUÉS de que _crear_esquema() cree todas las tablas.
    """
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION_LEGACY))
    )
    print(f"[database.py] Schema version grabada: {SCHEMA_VERSION_LEGACY}")


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
        """CREATE TABLE IF NOT EXISTS reversiones_tarjeta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id INTEGER NOT NULL,
            tipo_original TEXT NOT NULL CHECK (tipo_original IN ('COMPRA','INTERES','CARGO','PAGO')),
            original_id INTEGER NOT NULL, monto_inverso INTEGER NOT NULL CHECK (monto_inverso != 0),
            fecha TEXT NOT NULL, motivo TEXT NOT NULL, realizado_por TEXT NOT NULL DEFAULT 'sistema',
            creado_en TEXT NOT NULL, transaction_uuid TEXT NOT NULL UNIQUE,
            UNIQUE (tipo_original, original_id),
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id))""",
        """CREATE TABLE IF NOT EXISTS reversiones_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tipo_original TEXT NOT NULL,
            original_id INTEGER NOT NULL, monto_inverso INTEGER NOT NULL,
            fecha TEXT NOT NULL, motivo TEXT NOT NULL, creado_en TEXT NOT NULL,
            UNIQUE (tipo_original, original_id))""",
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
            tipo TEXT NOT NULL DEFAULT 'PERSONA'
                CHECK (tipo IN ('PERSONA','BANCO')),
            contacto TEXT, creado_en TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS prestamos_terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tercero_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('POR_COBRAR','POR_PAGAR')),
            propietario TEXT NOT NULL CHECK (propietario IN ('persona1','persona2')),
            titularidad TEXT NOT NULL DEFAULT 'persona1'
                CHECK (titularidad IN ('persona1','persona2','compartido')),
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
        """CREATE TABLE IF NOT EXISTS gastos_fijos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            categoria TEXT NOT NULL, valor INTEGER NOT NULL CHECK (valor > 0),
            frecuencia TEXT NOT NULL DEFAULT 'mensual'
                CHECK (frecuencia IN ('mensual','bimestral','trimestral','semestral','anual')),
            dia_pago INTEGER CHECK (dia_pago IS NULL OR (dia_pago BETWEEN 1 AND 31)),
            propietario TEXT NOT NULL CHECK (propietario IN ('persona1','persona2')),
            responsabilidad TEXT NOT NULL DEFAULT 'compartido'
                CHECK (responsabilidad IN ('persona1','persona2','compartido')),
            monto_p1 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p1 >= 0),
            monto_p2 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p2 >= 0),
            metodo_pago TEXT NOT NULL DEFAULT 'debito'
                CHECK (metodo_pago IN ('efectivo','debito','tarjeta')),
            tarjeta_id INTEGER, activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            notas TEXT, creado_en TEXT NOT NULL, actualizado_en TEXT NOT NULL,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id))""",
    )
    for statement in statements:
        conn.execute(statement)
        
    _crear_esquema_fix(conn)


def _crear_indices(conn: sqlite3.Connection) -> None:
    """Crea índices después de completar columnas de una base heredada."""
    statements = (
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
        "CREATE INDEX IF NOT EXISTS idx_reversiones_tarjeta_original ON reversiones_tarjeta(tipo_original, original_id)",
        "CREATE INDEX IF NOT EXISTS idx_reversiones_tarjeta_tarjeta ON reversiones_tarjeta(tarjeta_id, fecha, id)",
        "CREATE INDEX IF NOT EXISTS idx_liquidaciones_mes ON liquidaciones_pareja(mes)",
        "CREATE INDEX IF NOT EXISTS idx_movimientos_ahorro_fondo ON movimientos_ahorro(ahorro_id, fecha, id)",
        "CREATE INDEX IF NOT EXISTS idx_movimientos_ahorro_mes ON movimientos_ahorro(mes)",
        "CREATE INDEX IF NOT EXISTS idx_metas_ahorro ON metas(ahorro_id)",
        "CREATE INDEX IF NOT EXISTS idx_presupuestos_mes ON presupuestos(mes)",
        "CREATE INDEX IF NOT EXISTS idx_prestamos_terceros_estado ON prestamos_terceros(tercero_id, estado)",
        "CREATE INDEX IF NOT EXISTS idx_abonos_terceros_prestamo ON abonos_terceros(prestamo_id, fecha, id)",
        "CREATE INDEX IF NOT EXISTS idx_gastos_fijos_activo ON gastos_fijos(activo)",
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
        "terceros": ("tipo TEXT NOT NULL DEFAULT 'PERSONA'", "contacto TEXT"),
        "prestamos_terceros": ("titularidad TEXT NOT NULL DEFAULT 'persona1'",),
    }
    for table, definitions in additions.items():
        for definition in definitions:
            _add_column_if_missing(conn, table, definition)

    # Las cuentas previas no tenían alcance explícito: conservan el dueño que
    # ya se registró. Solo las nuevas pueden declararse compartidas.
    if "titularidad" in _columns(conn, "prestamos_terceros"):
        conn.execute("UPDATE prestamos_terceros SET titularidad=propietario WHERE titularidad IS NULL OR titularidad='persona1'")

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
    """Migra por escalones. Cada escalón corre solo si la base está por debajo.

    ``_migrar_legacy`` NO es idempotente (p. ej. restablece saldo histórico
    pendiente), así que jamás se re-ejecuta sobre una base que ya es v14.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    if current < SCHEMA_VERSION_LEGACY:
        _crear_esquema(conn)
        _migrar_legacy(conn)
        _crear_indices(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION_LEGACY}")
    if current < 15:
        from .migrations_v15 import aplicar_v15
        aplicar_v15(conn)
    if current < 16:
        from .migrations_v16 import aplicar_v16
        aplicar_v16(conn)


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


def _registrar_reversion_tarjeta(conn: sqlite3.Connection, *, tarjeta_id: int, tipo_original: str,
                                 original_id: int, monto_inverso: int, motivo: str,
                                 fecha: str | None = None, realizado_por: str = "sistema") -> int:
    """Registra el asiento inverso y el vínculo auditable de una reversa.

    Los agregados vigentes continúan leyendo solo operaciones activas; este
    asiento conserva el importe contrario y el vínculo uno-a-uno sin volver a
    contabilizar deuda o consumo en bases existentes.
    """
    if tipo_original not in {"COMPRA", "INTERES", "CARGO", "PAGO"}:
        raise IntegrityError("Tipo de movimiento original no soportado para reversa.")
    if monto_inverso == 0:
        raise IntegrityError("Una reversa de tarjeta debe tener importe inverso distinto de cero.")
    fecha = validar_fecha(fecha or dt.date.today().isoformat())
    try:
        cursor = conn.execute("""INSERT INTO reversiones_tarjeta
            (tarjeta_id,tipo_original,original_id,monto_inverso,fecha,motivo,realizado_por,creado_en,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tarjeta_id, tipo_original, original_id, monto_inverso, fecha, motivo,
             realizado_por, _now_iso(), _new_uuid()))
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValidationError("Este movimiento ya fue anulado y conserva su reversa auditada.") from None
        raise
    reversion_id = int(cursor.lastrowid)
    _log(conn, "reversion_tarjeta", reversion_id, "CREAR",
         f"original={tipo_original}:{original_id}; inverso={monto_inverso}; motivo={motivo}")
    return reversion_id


def _registrar_reversion_movimiento(conn: sqlite3.Connection, *, tipo_original: str, original_id: int,
                                    monto_inverso: int, motivo: str) -> int:
    """Registra un asiento inverso genérico, vinculado uno-a-uno al original."""
    if monto_inverso == 0:
        raise IntegrityError("Una reversa debe tener importe inverso distinto de cero.")
    try:
        cursor = conn.execute("""INSERT INTO reversiones_movimientos
            (tipo_original,original_id,monto_inverso,fecha,motivo,creado_en)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (tipo_original, original_id, monto_inverso, _now_iso(), motivo, _now_iso()))
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValidationError("Este movimiento ya fue anulado y conserva su reversa auditada.") from None
        raise
    reversion_id = int(cursor.lastrowid)
    _log(conn, "reversion_movimiento", reversion_id, "CREAR",
         f"original={tipo_original}:{original_id}; inverso={monto_inverso}; motivo={motivo}")
    return reversion_id


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
        if propietario != tarjeta["propietario"] and tarjeta["saldo_historico_pendiente"]:
            raise ValidationError(
                "No se puede cambiar el titular mientras exista saldo histórico sin desglose. "
                "Primero registra o aclara esa deuda para no reasignar su responsabilidad provisional."
            )
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


def actualizar_gasto(gasto_id: int, mes: str, nombre: str, categoria: str, valor: object,
                      fecha: str | None, pagador: str, responsabilidad: str,
                      monto_p1: object, monto_p2: object,
                      prioridad: str = PRIORIDAD_OBLIGATORIO) -> None:
    """Edita un gasto activo sin perder su vínculo contable.

    La forma de pago y la tarjeta de una compra no se cambian aquí: mover una
    compra entre fuentes de deuda reescribiría pagos FIFO ya registrados. Sí
    se puede corregir total, fecha, pagador, categoría y distribución incluso
    después de abonos. En ese caso se conserva lo ya abonado y se recalcula
    únicamente el saldo pendiente de esa compra.
    """
    mes = validar_mes(mes)
    nombre = validar_texto(nombre, "El nombre del gasto")
    categoria = validar_texto(categoria, "La categoría")
    if fecha:
        fecha = validar_fecha(fecha)
    valor = validar_monto_positivo(valor)
    p1, p2 = validar_monto_no_negativo(monto_p1), validar_monto_no_negativo(monto_p2)
    validar_persona(pagador)
    validar_responsabilidad(responsabilidad)
    validar_prioridad(prioridad)
    _validar_distribucion(valor, responsabilidad, p1, p2)
    with get_conn() as conn:
        gasto = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
        if gasto is None or gasto["estado"] != ESTADO_ACTIVO:
            raise ValidationError("El gasto no existe o está reversado.")

        if gasto["metodo_pago"] == METODO_TARJETA:
            compra = conn.execute("""SELECT * FROM compras_tarjeta
                                   WHERE gasto_id=? AND estado=?""",
                                 (gasto_id, ESTADO_ACTIVO)).fetchone()
            if compra is None:
                raise IntegrityError("No existe la compra activa asociada al gasto de tarjeta.")
            abonado = conn.execute("""SELECT COALESCE(SUM(monto_asignado), 0) AS total
                                      FROM asignaciones_pagos WHERE compra_id=?""",
                                   (compra["id"],)).fetchone()["total"]
            if abonado:
                # Una vez aplicado un pago, la responsabilidad económica de la
                # compra queda congelada. Cambiar total/distribución después
                # del pago reasignaría retrospectivamente una deuda ya
                # contabilizada.
                if valor != compra["valor_original"] or (
                    responsabilidad != compra["responsabilidad"]
                    or p1 != compra["monto_p1"]
                    or p2 != compra["monto_p2"]
                ):
                    raise ValidationError(
                        "El total y la distribución de una compra de tarjeta quedan "
                        "congelados después de aplicar un pago. Reversa primero los "
                        "pagos relacionados si necesitas corregirlos."
                    )
            if valor < abonado:
                raise ValidationError(
                    f"El nuevo total no puede ser menor que los {abonado} ya abonados a esta compra. "
                    "Reversa o corrige primero los pagos relacionados."
                )
            pendiente = valor - abonado
            tarjeta = _tarjeta(conn, gasto["tarjeta_id"])
            nuevo_saldo = tarjeta["saldo_deuda"] - compra["valor_pendiente"] + pendiente
            if nuevo_saldo < 0:
                raise IntegrityError("El saldo de la tarjeta no es consistente con esta compra.")
            if nuevo_saldo > tarjeta["cupo_total"]:
                raise InsufficientFundsError("El total corregido supera el cupo disponible de la tarjeta.")
            conn.execute("""UPDATE compras_tarjeta
                            SET descripcion=?, valor_original=?, fecha=?, mes=?, valor_pendiente=?,
                                responsabilidad=?, monto_p1=?, monto_p2=? WHERE id=?""",
                         (nombre, valor, fecha, mes, pendiente, responsabilidad, p1, p2, compra["id"]))
            conn.execute("UPDATE tarjetas SET saldo_deuda=?, actualizado_en=? WHERE id=?",
                         (nuevo_saldo, _now_iso(), tarjeta["id"]))

        conn.execute("""UPDATE gastos SET mes=?, nombre=?, categoria=?, valor=?, fecha=?, prioridad=?,
                        pagador=?, responsabilidad=?, monto_p1=?, monto_p2=? WHERE id=?""",
                     (mes, nombre, categoria, valor, fecha, prioridad, pagador, responsabilidad, p1, p2, gasto_id))
        _log(conn, "gasto", gasto_id, "EDITAR",
             f"valor={gasto['valor']}->{valor}; distribucion={p1}/{p2}; pagador={pagador}")


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
        _registrar_reversion_tarjeta(
            conn, tarjeta_id=movimiento["tarjeta_id"], tipo_original=movimiento["tipo"],
            original_id=movimiento_id, monto_inverso=-movimiento["valor_original"], motivo=motivo,
        )
        _registrar_reversion_movimiento(conn, tipo_original="movimiento_tarjeta", original_id=movimiento_id,
                                        monto_inverso=-movimiento["valor_original"], motivo=motivo)
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
            if tarjeta["propietario"] == SAMUEL:
                p1 = variacion
            else:
                p2 = variacion
        else:
            por_corregir = -variacion
            historico = min(por_corregir, tarjeta["saldo_historico_pendiente"])
            if historico:
                if tarjeta["propietario"] == SAMUEL:
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
            _registrar_reversion_tarjeta(
                conn, tarjeta_id=gasto["tarjeta_id"], tipo_original="COMPRA", original_id=compra["id"],
                monto_inverso=-compra["valor_original"], motivo=motivo,
            )
            conn.execute("""UPDATE compras_tarjeta SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?""",
                         (ESTADO_REVERSADO, motivo, _now_iso(), compra["id"]))
            conn.execute("UPDATE tarjetas SET saldo_deuda=saldo_deuda-? WHERE id=?", (compra["valor_original"], gasto["tarjeta_id"]))
        _registrar_reversion_movimiento(conn, tipo_original="gasto", original_id=gasto_id,
                                        monto_inverso=-gasto["valor"], motivo=motivo)
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
        ingreso = conn.execute("SELECT * FROM ingresos WHERE id=?", (ingreso_id,)).fetchone()
        if ingreso is None or ingreso["estado"] == ESTADO_REVERSADO:
            raise ValidationError("El ingreso no existe o ya está reversado.")
        _registrar_reversion_movimiento(conn, tipo_original="ingreso", original_id=ingreso_id,
                                        monto_inverso=-ingreso["valor"], motivo=motivo)
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
        tarjeta = _tarjeta(conn, pago["tarjeta_id"])
        if tarjeta["saldo_deuda"] + pago["monto"] > tarjeta["cupo_total"]:
            raise InsufficientFundsError(
                "No se puede reversar el pago porque la deuda resultante superaría el cupo actual de la tarjeta. "
                "Reversa primero las compras posteriores o ajusta el cupo si el extracto lo respalda."
            )
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
        _registrar_reversion_tarjeta(
            conn, tarjeta_id=pago["tarjeta_id"], tipo_original="PAGO", original_id=pago_id,
            monto_inverso=pago["monto"], motivo=motivo,
        )
        _registrar_reversion_movimiento(conn, tipo_original="pago", original_id=pago_id,
                                        monto_inverso=pago["monto"], motivo=motivo)
        for asignacion in asignaciones:
            conn.execute("UPDATE compras_tarjeta SET valor_pendiente=valor_pendiente+? WHERE id=?", (asignacion["monto_asignado"], asignacion["compra_id"]))
        for asignacion in asignaciones_ajustes:
            conn.execute("UPDATE ajustes_tarjeta SET valor_pendiente=valor_pendiente+? WHERE id=?", (asignacion["monto_asignado"], asignacion["ajuste_id"]))
        conn.execute("""UPDATE tarjetas SET saldo_deuda=saldo_deuda+?,
            saldo_historico_pendiente=saldo_historico_pendiente+?, actualizado_en=? WHERE id=?""", (pago["monto"], pago["monto_historico_aplicado"], _now_iso(), pago["tarjeta_id"]))
        conn.execute("UPDATE pagos_deuda SET estado=?, motivo_reversion=?, fecha_reversion=? WHERE id=?", (ESTADO_REVERSADO, motivo, _now_iso(), pago_id))
        _log(conn, "pago_deuda", pago_id, "REVERSAR", motivo)


def get_modelo_pareja(mes: str | None = None) -> dict | None:
    """Devuelve el modelo de pareja vigente para un mes."""
    target = mes or dt.date.today().strftime("%Y-%m")
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM modelo_pareja
               WHERE estado=? AND desde_mes<=?
               ORDER BY desde_mes DESC, id DESC LIMIT 1""",
            (ESTADO_ACTIVO, target),
        ).fetchone()
    return dict(row) if row else None


def guardar_modelo_pareja(
    desde_mes: str,
    modelo: str,
    *,
    base_proporcional: str | None = None,
    pozo_aporte_p1: int | None = None,
    pozo_aporte_p2: int | None = None,
    transaction_uuid: str | None = None,
) -> int:
    """Versiona un modelo sin modificar liquidaciones históricas."""
    validar_mes(desde_mes)
    if modelo not in {"5050", "proporcional", "pozo"}:
        raise ValidationError("Modelo de pareja no válido.")
    if modelo == "proporcional" and base_proporcional not in {"ingreso_mes", "ingreso_promedio"}:
        raise ValidationError("El modelo proporcional requiere una base de ingresos.")
    if modelo == "pozo" and (pozo_aporte_p1 is None or pozo_aporte_p2 is None):
        raise ValidationError("El modelo de pozo requiere los aportes iniciales.")
    if modelo == "pozo":
        pozo_aporte_p1 = validar_monto_no_negativo(pozo_aporte_p1)
        pozo_aporte_p2 = validar_monto_no_negativo(pozo_aporte_p2)
    tx_uuid = transaction_uuid or str(uuid.uuid4())
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id FROM modelo_pareja
               WHERE transaction_uuid=?""", (tx_uuid,)
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            """INSERT INTO modelo_pareja
               (desde_mes, modelo, base_proporcional, pozo_aporte_p1, pozo_aporte_p2,
                creado_en, estado, transaction_uuid)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (desde_mes, modelo, base_proporcional, pozo_aporte_p1, pozo_aporte_p2,
             _now_iso(), ESTADO_ACTIVO, tx_uuid),
        )
        _log(conn, "modelo_pareja", cur.lastrowid, "CREAR", f"{modelo}; desde={desde_mes}")
        return int(cur.lastrowid)


def get_aportes_pozo(mes: str | None = None) -> list[dict]:
    query = "SELECT * FROM aportes_pozo WHERE estado=?"
    params: list[object] = [ESTADO_ACTIVO]
    if mes:
        query += " AND mes=?"
        params.append(mes)
    query += " ORDER BY fecha, id"
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def registrar_aporte_pozo(
    mes: str, fecha: str, persona: str, monto: object, concepto: str = "",
    transaction_uuid: str | None = None,
) -> int:
    validar_mes(mes)
    fecha = validar_fecha(fecha)
    persona = validar_persona(persona)
    monto = validar_monto_positivo(monto)
    concepto = validar_texto(concepto, "El concepto", maximo=500, obligatorio=False) or ""
    tx_uuid = transaction_uuid or str(uuid.uuid4())
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM aportes_pozo WHERE transaction_uuid=?", (tx_uuid,)
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            """INSERT INTO aportes_pozo
               (mes, fecha, persona, monto, concepto, estado, transaction_uuid)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mes, fecha, persona, monto, concepto, ESTADO_ACTIVO, tx_uuid),
        )
        _log(conn, "aporte_pozo", cur.lastrowid, "CREAR", f"{persona}; monto={monto}")
        return int(cur.lastrowid)


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
        liquidacion = conn.execute("SELECT * FROM liquidaciones_pareja WHERE id=?", (liquidacion_id,)).fetchone()
        if liquidacion is None or liquidacion["estado"] == ESTADO_REVERSADO:
            raise ValidationError("La liquidación no existe o ya está reversada.")
        _registrar_reversion_movimiento(conn, tipo_original="liquidacion", original_id=liquidacion_id,
                                        monto_inverso=-liquidacion["monto"], motivo=motivo)
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
    validar_persona(propietario if propietario != RESP_COMPARTIDO else SAMUEL)
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
                                concepto: str, transaction_uuid: str | None = None, *, aportante: str = SAMUEL,
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
        inverso = -movimiento["monto"] if movimiento["tipo"] == MOV_AHORRO_DEPOSITO else movimiento["monto"]
        _registrar_reversion_movimiento(conn, tipo_original="ahorro", original_id=movimiento_id,
                                        monto_inverso=inverso, motivo=motivo)
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


def crear_tercero(nombre: str, tipo: str = "PERSONA", contacto: str = "") -> int:
    """Crea un tercero independiente, ya sea una persona o un banco."""
    nombre = validar_texto(nombre, "El nombre del tercero")
    tipo = tipo.strip().upper()
    if tipo not in {"PERSONA", "BANCO"}:
        raise ValidationError("El tipo de tercero debe ser Persona o Banco.")
    contacto = contacto.strip()
    with get_conn() as conn:
        try:
            tercero_id = conn.execute(
                "INSERT INTO terceros (nombre,tipo,contacto,creado_en) VALUES (?, ?, ?, ?)",
                (nombre, tipo, contacto or None, _now_iso()),
            ).lastrowid
        except sqlite3.IntegrityError as exc:
            if "terceros.nombre" in str(exc).lower():
                raise DuplicateOperationError(f"Ya existe un tercero llamado {nombre!r}.") from None
            raise
        _log(conn, "tercero", tercero_id, "CREAR", f"tipo={tipo}; {nombre}")
        return int(tercero_id)


def get_terceros() -> list[dict]:
    """Devuelve los terceros registrados, incluso si aún no tienen obligaciones."""
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM terceros ORDER BY tipo, nombre COLLATE NOCASE"
        )]


def _tercero_id(conn: sqlite3.Connection, nombre: str) -> int:
    """Obtiene o crea un tercero normalizado dentro de la misma transacción."""
    nombre = validar_texto(nombre, "El nombre del tercero")
    existente = conn.execute("SELECT id FROM terceros WHERE nombre=?", (nombre,)).fetchone()
    if existente is not None:
        return int(existente["id"])
    tercero_id = conn.execute("INSERT INTO terceros (nombre, tipo, creado_en) VALUES (?, ?, ?)",
                              (nombre, "PERSONA", _now_iso())).lastrowid
    _log(conn, "tercero", tercero_id, "CREAR", nombre)
    return int(tercero_id)


def registrar_prestamo_tercero(mes: str, fecha: str, tercero: str, tipo: str | TipoCuentaTercero,
                               monto: object, propietario: str, concepto: str, titularidad: str | None = None,
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
    titularidad = validar_responsabilidad(titularidad or propietario)
    concepto = validar_texto(concepto, "El concepto del préstamo")
    tx_uuid = transaction_uuid or _new_uuid()
    with get_conn() as conn:
        tercero_id = _tercero_id(conn, tercero)
        prestamo_id = _insert_idempotente(conn, """INSERT INTO prestamos_terceros
            (tercero_id,mes,fecha,tipo,propietario,titularidad,concepto,monto_original,saldo_pendiente,estado,transaction_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tercero_id, mes, fecha, tipo_valido, propietario, titularidad, concepto, monto_entero, monto_entero,
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
        _registrar_reversion_movimiento(conn, tipo_original="abono_tercero", original_id=abono_id,
                                        monto_inverso=abono["monto"], motivo=motivo)
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
        _registrar_reversion_movimiento(conn, tipo_original="prestamo_tercero", original_id=prestamo_id,
                                        monto_inverso=-prestamo["monto_original"], motivo=motivo)
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


def registrar_gasto_fijo(nombre: str, categoria: str, valor: object, frecuencia: str, propietario: str,
                          responsabilidad: str, monto_p1: object, monto_p2: object, *, dia_pago: object = None,
                          metodo_pago: str = "debito", tarjeta_id: int | None = None,
                          notas: str | None = None, activo: bool = True) -> int:
    """Registra una obligación recurrente (renta, servicios, suscripciones, etc.)."""
    nombre = validar_texto(nombre, "El nombre del gasto fijo")
    categoria = validar_texto(categoria, "La categoría")
    valor = validar_monto_positivo(valor)
    if frecuencia not in {"mensual", "bimestral", "trimestral", "semestral", "anual"}:
        raise ValidationError("Frecuencia inválida.")
    validar_persona(propietario)
    validar_responsabilidad(responsabilidad)
    p1, p2 = validar_monto_no_negativo(monto_p1), validar_monto_no_negativo(monto_p2)
    _validar_distribucion(valor, responsabilidad, p1, p2)
    validar_metodo_pago(metodo_pago)
    if dia_pago is not None:
        dia_pago = int(dia_pago)
        if not 1 <= dia_pago <= 31:
            raise ValidationError("El día de pago debe estar entre 1 y 31.")
    if metodo_pago != METODO_TARJETA and tarjeta_id is not None:
        raise ValidationError("Solo un gasto fijo pagado con tarjeta puede incluir tarjeta_id.")
    if metodo_pago == METODO_TARJETA and tarjeta_id is None:
        raise ValidationError("Un gasto fijo con tarjeta requiere tarjeta_id.")
    notas = validar_texto(notas or "", "Las notas", maximo=500, obligatorio=False) or None
    with get_conn() as conn:
        if tarjeta_id is not None:
            _tarjeta(conn, tarjeta_id)
        ahora = _now_iso()
        gasto_fijo_id = conn.execute("""INSERT INTO gastos_fijos
            (nombre,categoria,valor,frecuencia,dia_pago,propietario,responsabilidad,monto_p1,monto_p2,
             metodo_pago,tarjeta_id,activo,notas,creado_en,actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nombre, categoria, valor, frecuencia, dia_pago, propietario, responsabilidad, p1, p2,
             metodo_pago, tarjeta_id, int(bool(activo)), notas, ahora, ahora)).lastrowid
        _log(conn, "gasto_fijo", gasto_fijo_id, "CREAR", f"valor={valor}; frecuencia={frecuencia}")
        return gasto_fijo_id


def actualizar_gasto_fijo(gasto_fijo_id: int, *, nombre: str, categoria: str, valor: object, frecuencia: str,
                           propietario: str, responsabilidad: str, monto_p1: object, monto_p2: object,
                           dia_pago: object = None, metodo_pago: str = "debito",
                           tarjeta_id: int | None = None, activo: bool = True, notas: str | None = None) -> None:
    """Edita la configuración de un gasto fijo (no crea historial de gastos)."""
    nombre = validar_texto(nombre, "El nombre del gasto fijo")
    categoria = validar_texto(categoria, "La categoría")
    valor = validar_monto_positivo(valor)
    if frecuencia not in {"mensual", "bimestral", "trimestral", "semestral", "anual"}:
        raise ValidationError("Frecuencia inválida.")
    validar_persona(propietario)
    validar_responsabilidad(responsabilidad)
    p1, p2 = validar_monto_no_negativo(monto_p1), validar_monto_no_negativo(monto_p2)
    _validar_distribucion(valor, responsabilidad, p1, p2)
    validar_metodo_pago(metodo_pago)
    if dia_pago is not None:
        dia_pago = int(dia_pago)
        if not 1 <= dia_pago <= 31:
            raise ValidationError("El día de pago debe estar entre 1 y 31.")
    if metodo_pago != METODO_TARJETA and tarjeta_id is not None:
        raise ValidationError("Solo un gasto fijo pagado con tarjeta puede incluir tarjeta_id.")
    if metodo_pago == METODO_TARJETA and tarjeta_id is None:
        raise ValidationError("Un gasto fijo con tarjeta requiere tarjeta_id.")
    notas = validar_texto(notas or "", "Las notas", maximo=500, obligatorio=False) or None
    with get_conn() as conn:
        existente = conn.execute("SELECT id FROM gastos_fijos WHERE id=?", (gasto_fijo_id,)).fetchone()
        if existente is None:
            raise ValidationError("El gasto fijo no existe.")
        if tarjeta_id is not None:
            _tarjeta(conn, tarjeta_id)
        conn.execute("""UPDATE gastos_fijos SET nombre=?, categoria=?, valor=?, frecuencia=?, dia_pago=?,
                        propietario=?, responsabilidad=?, monto_p1=?, monto_p2=?, metodo_pago=?, tarjeta_id=?,
                        activo=?, notas=?, actualizado_en=? WHERE id=?""",
                     (nombre, categoria, valor, frecuencia, dia_pago, propietario, responsabilidad, p1, p2,
                      metodo_pago, tarjeta_id, int(bool(activo)), notas, _now_iso(), gasto_fijo_id))
        _log(conn, "gasto_fijo", gasto_fijo_id, "EDITAR", f"valor={valor}; activo={bool(activo)}")


def eliminar_gasto_fijo(gasto_fijo_id: int) -> None:
    """Desactiva una plantilla de gasto fijo; nunca elimina el registro histórico."""
    with get_conn() as conn:
        existente = conn.execute("SELECT id, activo FROM gastos_fijos WHERE id=?", (gasto_fijo_id,)).fetchone()
        if existente is None:
            raise ValidationError("El gasto fijo no existe.")
        if not existente["activo"]:
            return
        conn.execute(
            "UPDATE gastos_fijos SET activo=0, actualizado_en=? WHERE id=?",
            (_now_iso(), gasto_fijo_id),
        )
        _log(conn, "gasto_fijo", gasto_fijo_id, "DESACTIVAR", "")


def get_gastos_fijos(solo_activos: bool = True) -> list[dict]:
    query = "SELECT * FROM gastos_fijos" + (" WHERE activo=1" if solo_activos else "") + " ORDER BY nombre COLLATE NOCASE"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query)]


def get_gasto_fijo(gasto_fijo_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM gastos_fijos WHERE id=?", (gasto_fijo_id,)).fetchone()
        if row is None:
            raise ValidationError("El gasto fijo no existe.")
        return dict(row)


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
                      COALESCE(SUM(CASE WHEN g.estado=? THEN g.valor ELSE 0 END), 0) AS gastado,
                      COALESCE(SUM(CASE WHEN g.estado=? THEN g.monto_p1 ELSE 0 END), 0) AS gastado_p1,
                      COALESCE(SUM(CASE WHEN g.estado=? THEN g.monto_p2 ELSE 0 END), 0) AS gastado_p2
               FROM presupuestos p JOIN categorias c ON c.id=p.categoria_id
               LEFT JOIN gastos g ON g.mes=p.mes AND g.categoria=c.nombre
               WHERE p.mes=? GROUP BY p.id ORDER BY c.nombre COLLATE NOCASE"""
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, (ESTADO_ACTIVO, ESTADO_ACTIVO, ESTADO_ACTIVO, mes))]


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


def get_gasto(gasto_id: int, incluir_reversado: bool = False) -> dict:
    """Obtiene un gasto por id para edición; no expone reversados por defecto."""
    with get_conn() as conn:
        gasto = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
    if gasto is None or (gasto["estado"] != ESTADO_ACTIVO and not incluir_reversado):
        raise ValidationError("Gasto no encontrado.")
    return dict(gasto)


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


def get_reversiones_tarjeta(tarjeta_id: int | None = None, *, tipo_original: str | None = None,
                            original_id: int | None = None) -> list[dict]:
    """Devuelve las operaciones inversas, incluyendo su vínculo con el original."""
    query, params = "SELECT * FROM reversiones_tarjeta WHERE 1=1", []
    if tarjeta_id is not None:
        query += " AND tarjeta_id=?"; params.append(tarjeta_id)
    if tipo_original is not None:
        query += " AND tipo_original=?"; params.append(tipo_original)
    if original_id is not None:
        query += " AND original_id=?"; params.append(original_id)
    query += " ORDER BY fecha, id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_reversiones_movimientos(tipo_original: str | None = None, original_id: int | None = None) -> list[dict]:
    """Asientos inversos de movimientos no destructivos, con vínculo al original."""
    query, params = "SELECT * FROM reversiones_movimientos WHERE 1=1", []
    if tipo_original is not None:
        query += " AND tipo_original=?"; params.append(tipo_original)
    if original_id is not None:
        query += " AND original_id=?"; params.append(original_id)
    query += " ORDER BY id"
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def verificar_reversiones_movimientos() -> list[dict]:
    """Comprueba que cada asiento inverso continúe enlazado a un original reversado."""
    tables = {"ingreso": "ingresos", "gasto": "gastos", "pago": "pagos_deuda",
              "liquidacion": "liquidaciones_pareja", "ahorro": "movimientos_ahorro",
              "movimiento_tarjeta": "compras_tarjeta", "abono_tercero": "abonos_terceros",
              "prestamo_tercero": "prestamos_terceros"}
    issues: list[dict] = []
    with get_conn() as conn:
        reversals = conn.execute("SELECT * FROM reversiones_movimientos ORDER BY id").fetchall()
        for reversal in reversals:
            table = tables.get(reversal["tipo_original"])
            original = conn.execute(f"SELECT estado FROM {table} WHERE id=?", (reversal["original_id"],)).fetchone() if table else None
            if original is None or original["estado"] != ESTADO_REVERSADO:
                issues.append({"reversion_id": reversal["id"], "tipo_original": reversal["tipo_original"],
                               "original_id": reversal["original_id"], "detalle": "La reversa no tiene un original reversado consistente."})
    return issues


def proponer_anular_movimiento(tipo: str, movimiento_id: int) -> dict:
    """Previsualiza una anulación sin escribir SQLite ni delegar en una operación persistente."""
    from . import calculations as calc  # Importación tardía: calculations depende de esta capa.

    normalized = tipo.lower()
    if normalized in {"movimiento_tarjeta", "pago"}:
        with get_conn() as conn:
            if normalized == "pago":
                row = conn.execute("SELECT tarjeta_id FROM pagos_deuda WHERE id=?", (movimiento_id,)).fetchone()
                revision_type = "pago"
            else:
                row = conn.execute("SELECT tarjeta_id FROM compras_tarjeta WHERE id=?", (movimiento_id,)).fetchone()
                revision_type = "movimiento"
        if row is None:
            raise ValidationError("El movimiento no existe.")
        return revisar_transaccion(int(row["tarjeta_id"]), movimiento_id, revision_type)
    tables = {
        "ingreso": ("ingresos", "valor", "concepto"),
        "gasto": ("gastos", "valor", "nombre"),
        "liquidacion": ("liquidaciones_pareja", "monto", "concepto"),
        "ahorro": ("movimientos_ahorro", "monto", "concepto"),
    }
    if normalized not in tables:
        raise ValidationError("Tipo de movimiento no soportado para revisión.")
    table, amount_field, description_field = tables[normalized]
    with get_conn() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (movimiento_id,)).fetchone()
        if row is None:
            raise ValidationError("El movimiento no existe.")
        item = dict(row)
        existing = conn.execute("SELECT id FROM reversiones_movimientos WHERE tipo_original=? AND original_id=?",
                                (normalized, movimiento_id)).fetchone()
    if normalized == "gasto" and item.get("tarjeta_id"):
        return revisar_transaccion(int(item["tarjeta_id"]), movimiento_id, "gasto")
    amount = int(item[amount_field])
    month = item.get("mes")
    flow = calc.flujo_caja_mes(month) if month else {"ingresos": 0, "salidas": 0, "ahorro": 0}
    liquidity = calc.liquidez_total(month) if month else 0
    balance = calc.balance_historico_pareja(month) if month else calc.balance_historico_pareja()
    warnings: list[str] = []
    can_reverse = item["estado"] == ESTADO_ACTIVO and existing is None
    if item["estado"] != ESTADO_ACTIVO:
        warnings.append("El movimiento original ya está reversado.")
    if existing is not None:
        warnings.append("Ya existe una operación inversa vinculada a este movimiento.")
    delta_income = delta_outflow = delta_liquidity = delta_savings = 0
    balances_after = {persona: balance[f"balance_neto_{persona}"] for persona in (SAMUEL, SARA)}
    if normalized == "ingreso":
        delta_income, delta_liquidity = -amount, -amount
    elif normalized == "gasto":
        delta_outflow, delta_liquidity = -amount, amount
        for persona in (SAMUEL, SARA):
            balances_after[persona] += int(item[f"monto_{'p1' if persona == SAMUEL else 'p2'}"]) - (amount if item["pagador"] == persona else 0)
    elif normalized == "liquidacion":
        balances_after[item["deudor"]] -= amount
        balances_after[item["acreedor"]] += amount
    else:  # ahorro
        if item["tipo"] == MOV_AHORRO_DEPOSITO:
            delta_savings = -amount
            fund = next((fund for fund in get_ahorros(solo_activos=False) if fund["id"] == item["ahorro_id"]), None)
            if fund and amount > fund["saldo"]:
                warnings.append("El depósito no puede anularse porque retiros posteriores ya usan ese saldo.")
                can_reverse = False
        else:
            delta_savings = amount
    after = {
        "ingresos": int(flow["ingresos"]) + delta_income,
        "salidas": int(flow["salidas"]) + delta_outflow,
        "flujo": int(flow["ahorro"]) + delta_income - delta_outflow,
        "liquidez": liquidity + delta_liquidity,
        "ahorro": calc.resumen_ahorros()["total"] + delta_savings,
        "saldo_samuel": balances_after[SAMUEL],
        "saldo_sara": balances_after[SARA],
    }
    return {
        "modifica_base": False, "tipo_original": normalized, "movimiento_id": movimiento_id,
        "descripcion": item.get(description_field) or normalized.title(), "monto": amount,
        "situacion_actual": {"ingresos": flow["ingresos"], "salidas": flow["salidas"], "flujo": flow["ahorro"],
                              "liquidez": liquidity, "ahorro": calc.resumen_ahorros()["total"],
                              "saldo_samuel": balance[f"balance_neto_{SAMUEL}"], "saldo_sara": balance[f"balance_neto_{SARA}"]},
        "despues_de_anular": after, "se_puede_anular": can_reverse and not warnings,
        "advertencias": warnings,
        "nota": "Vista previa calculada desde movimientos activos. No se modificó SQLite; la confirmación crea el asiento inverso auditable.",
    }


def _distribuir_proporcional(monto: int, total: int, monto_p1: int) -> tuple[int, int]:
    """Prorratea un abono conservando pesos enteros y la suma exacta."""
    if not total:
        return 0, monto
    p1 = int((Decimal(monto) * Decimal(monto_p1) / Decimal(total)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ))
    return p1, monto - p1


def revisar_transaccion(tarjeta_id: int, movimiento_id: int, tipo: str = "movimiento") -> dict:
    """Vista previa pura de una anulación de tarjeta.

    ``tipo`` admite ``movimiento``/``compra`` (id de compras_tarjeta),
    ``gasto`` (id del gasto que originó una compra) y ``pago``. No escribe en
    SQLite; la UI debe usar esta respuesta antes de pedir confirmación.
    """
    from . import calculations as calc  # Importación tardía: calculations ya usa database.

    tipo = tipo.lower()
    if tipo not in {"movimiento", "compra", "gasto", "pago"}:
        raise ValidationError("Tipo de transacción no soportado para revisión.")
    with get_conn() as conn:
        tarjeta = _tarjeta(conn, tarjeta_id)
        balance = calc.balance_historico_pareja()
        deuda_responsable = calc.deuda_pendiente_por_responsabilidad(tarjeta_id)
        actual = {
            "saldo_tarjeta": tarjeta["saldo_deuda"], "utilizacion": tarjeta["saldo_deuda"] / tarjeta["cupo_total"] if tarjeta["cupo_total"] else 0.0,
            "responsabilidad_samuel": deuda_responsable[SAMUEL], "responsabilidad_sara": deuda_responsable[SARA],
            "saldo_samuel": balance[f"balance_neto_{SAMUEL}"], "saldo_sara": balance[f"balance_neto_{SARA}"],
        }
        if tipo == "gasto":
            movimiento = conn.execute("SELECT * FROM compras_tarjeta WHERE gasto_id=?", (movimiento_id,)).fetchone()
            if movimiento is None:
                raise ValidationError("El gasto no corresponde a una compra de tarjeta.")
        elif tipo == "pago":
            movimiento = conn.execute("SELECT * FROM pagos_deuda WHERE id=?", (movimiento_id,)).fetchone()
            if movimiento is None:
                raise ValidationError("El pago de tarjeta no existe.")
        else:
            movimiento = conn.execute("SELECT * FROM compras_tarjeta WHERE id=?", (movimiento_id,)).fetchone()
            if movimiento is None:
                raise ValidationError("El movimiento de tarjeta no existe.")
        if movimiento["tarjeta_id"] != tarjeta_id:
            raise ValidationError("El movimiento no pertenece a la tarjeta indicada.")

        if tipo == "pago":
            original_type, monto = "PAGO", int(movimiento["monto"])
            asignaciones = conn.execute("""SELECT a.monto_asignado, c.valor_original, c.monto_p1
                                           FROM asignaciones_pagos a JOIN compras_tarjeta c ON c.id=a.compra_id
                                           WHERE a.pago_id=?""", (movimiento_id,)).fetchall()
            ajustes = conn.execute("""SELECT a.monto_asignado, j.variacion, j.monto_p1
                                      FROM asignaciones_pagos_ajustes a JOIN ajustes_tarjeta j ON j.id=a.ajuste_id
                                      WHERE a.pago_id=?""", (movimiento_id,)).fetchall()
            deuda_p1 = deuda_p2 = 0
            historico = int(movimiento["monto_historico_aplicado"])
            if historico:
                if tarjeta["propietario"] == SAMUEL: deuda_p1 += historico
                else: deuda_p2 += historico
            for asignacion in asignaciones:
                p1, p2 = _distribuir_proporcional(int(asignacion["monto_asignado"]), int(asignacion["valor_original"]), int(asignacion["monto_p1"]))
                deuda_p1 += p1; deuda_p2 += p2
            for asignacion in ajustes:
                p1, p2 = _distribuir_proporcional(int(asignacion["monto_asignado"]), int(asignacion["variacion"]), int(asignacion["monto_p1"]))
                deuda_p1 += p1; deuda_p2 += p2
            posterior = conn.execute("""SELECT 1 FROM pagos_deuda WHERE tarjeta_id=? AND estado=?
                AND (fecha > ? OR (fecha = ? AND id > ?))""",
                (tarjeta_id, ESTADO_ACTIVO, movimiento["fecha"], movimiento["fecha"], movimiento_id)).fetchone()
            warnings = []
            if posterior:
                warnings.append("Primero deben reversarse los pagos activos posteriores para conservar el orden FIFO/LIFO.")
            if tarjeta["saldo_deuda"] + monto > tarjeta["cupo_total"]:
                warnings.append("La reversa superaría el cupo actual de la tarjeta.")
            puede = movimiento["estado"] == ESTADO_ACTIVO and not posterior and not warnings
            despues = {
                "saldo_tarjeta": tarjeta["saldo_deuda"] + monto if puede else None,
                "utilizacion": (tarjeta["saldo_deuda"] + monto) / tarjeta["cupo_total"] if puede and tarjeta["cupo_total"] else None,
                "responsabilidad_samuel": deuda_responsable[SAMUEL] + deuda_p1 if puede else None,
                "responsabilidad_sara": deuda_responsable[SARA] + deuda_p2 if puede else None,
                "saldo_samuel": balance[f"balance_neto_{SAMUEL}"] - movimiento["monto_aportado_p1"] + deuda_p1 if puede else None,
                "saldo_sara": balance[f"balance_neto_{SARA}"] - movimiento["monto_aportado_p2"] + deuda_p2 if puede else None,
            }
            descripcion = movimiento["concepto"] or "Pago de tarjeta"
        else:
            original_type, monto = movimiento["tipo"], int(movimiento["valor_original"])
            warnings = []
            if movimiento["valor_pendiente"] != movimiento["valor_original"]:
                warnings.append("Esta operación ya recibió pagos; reversa primero los pagos aplicados para conservar la trazabilidad.")
            if tarjeta["saldo_deuda"] < monto:
                warnings.append("El saldo registrado de la tarjeta no respalda esta reversa.")
            puede = movimiento["estado"] == ESTADO_ACTIVO and not warnings
            despues = {
                "saldo_tarjeta": tarjeta["saldo_deuda"] - monto if puede else None,
                "utilizacion": (tarjeta["saldo_deuda"] - monto) / tarjeta["cupo_total"] if puede and tarjeta["cupo_total"] else None,
                "responsabilidad_samuel": deuda_responsable[SAMUEL] - movimiento["monto_p1"] if puede else None,
                "responsabilidad_sara": deuda_responsable[SARA] - movimiento["monto_p2"] if puede else None,
                # Una compra sin pagos no deja saldo entre personas: retirar consumo y deuda pendiente se compensa.
                "saldo_samuel": balance[f"balance_neto_{SAMUEL}"] if puede else None,
                "saldo_sara": balance[f"balance_neto_{SARA}"] if puede else None,
            }
            descripcion = movimiento["descripcion"]
        ya_reversada = conn.execute("SELECT id FROM reversiones_tarjeta WHERE tipo_original=? AND original_id=?",
                                    (original_type, movimiento["id"])).fetchone()
        if movimiento["estado"] != ESTADO_ACTIVO:
            warnings.append("El movimiento original ya está reversado.")
        if ya_reversada:
            warnings.append("Ya existe una operación inversa vinculada a este movimiento.")
        puede = puede and not ya_reversada
        return {
            "modifica_base": False, "tarjeta_id": tarjeta_id, "movimiento_id": movimiento["id"],
            "tipo_original": original_type, "descripcion": descripcion, "monto": monto,
            "situacion_actual": actual, "despues_de_anular": despues,
            "interes_potencial_evitable": round(monto * float(tarjeta["interes_mensual"]) / 100),
            "se_puede_anular": puede, "advertencias": warnings,
        }


def get_prestamos_terceros(incluir_reversados: bool = False) -> list[dict]:
    """Devuelve obligaciones externas con el nombre del tercero."""
    filtro = "" if incluir_reversados else " WHERE p.estado=?"
    query = """SELECT p.*, t.nombre AS tercero, t.tipo AS tipo_tercero FROM prestamos_terceros p
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
        params = (MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, MOV_AHORRO_DEPOSITO, SAMUEL,
                  MOV_AHORRO_DEPOSITO, SARA, MOV_AHORRO_DEPOSITO, MOV_AHORRO_RETIRO, ESTADO_ACTIVO)
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


def auditar_reversiones(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Verifica que todo movimiento reversado tenga registro en el log."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT rm.id, rm.original_id, rm.tipo_original, rm.creado_en
        FROM reversiones_movimientos rm
        WHERE NOT EXISTS (
            SELECT 1 FROM gastos g 
            WHERE g.id = rm.original_id AND g.estado = 'REVERSADO'
        )
        AND NOT EXISTS (
            SELECT 1 FROM compras_tarjeta c 
            WHERE c.id = rm.original_id AND c.estado = 'REVERSADO'
        )
    """)
    return [dict(row) for row in cursor.fetchall()]


def corregir_reversiones_orfanas(conn: sqlite3.Connection) -> int:
    """Detecta reversiones huérfanas sin borrar evidencia histórica."""
    orfanas = auditar_reversiones(conn)
    if not orfanas:
        print("[database.py] ✓ No hay reversiones orfanas")
        return 0
    
    print(f"[database.py] ⚠️  Encontradas {len(orfanas)} reversiones orfanas; se conservan para auditoría.")
    return len(orfanas)


def verificar_bd_integridad() -> dict[str, Any]:
    """Audita SQLite y las invariantes principales sin modificar datos."""
    try:
        with get_conn() as conn:
            cursor = conn.cursor()
            integridad = cursor.execute("PRAGMA integrity_check").fetchone()[0]
            fk_estado = bool(cursor.execute("PRAGMA foreign_keys").fetchone()[0])
            result = cursor.execute("SELECT value FROM config WHERE key='schema_version'").fetchone()
            schema_grabada = result[0] if result else None
            tarjetas = cursor.execute("SELECT COUNT(*) FROM tarjetas").fetchone()[0]
            gastos = cursor.execute("SELECT COUNT(*) FROM gastos").fetchone()[0]
            orfanas = auditar_reversiones(conn)

            card_issues = []
            for row in cursor.execute("""
                SELECT t.id, t.nombre, t.saldo_deuda, t.saldo_historico_pendiente,
                       COALESCE(SUM(CASE WHEN c.estado='ACTIVO' THEN c.valor_pendiente ELSE 0 END),0) AS pendientes,
                       COALESCE((SELECT SUM(j.valor_pendiente) FROM ajustes_tarjeta j
                                 WHERE j.tarjeta_id=t.id AND j.estado='ACTIVO' AND j.variacion>0),0) AS ajustes_pendientes
                FROM tarjetas t
                LEFT JOIN compras_tarjeta c ON c.tarjeta_id=t.id
                GROUP BY t.id
            """):
                respaldado = int(row["saldo_historico_pendiente"]) + int(row["pendientes"]) + int(row["ajustes_pendientes"])
                if int(row["saldo_deuda"]) != respaldado:
                    card_issues.append({
                        "tarjeta_id": row["id"], "nombre": row["nombre"],
                        "saldo": int(row["saldo_deuda"]), "respaldado": respaldado,
                    })

            allocation_issues = []
            for row in cursor.execute("""
                SELECT p.id, p.monto, p.monto_historico_aplicado,
                       COALESCE((SELECT SUM(a.monto_asignado) FROM asignaciones_pagos a WHERE a.pago_id=p.id),0)
                       + COALESCE((SELECT SUM(a.monto_asignado) FROM asignaciones_pagos_ajustes a WHERE a.pago_id=p.id),0) AS asignado
                FROM pagos_deuda p
                WHERE p.estado='ACTIVO'
            """):
                esperado = int(row["monto"]) - int(row["monto_historico_aplicado"])
                if int(row["asignado"]) != esperado:
                    allocation_issues.append({
                        "pago_id": row["id"], "monto": int(row["monto"]),
                        "esperado_asignado": esperado, "asignado": int(row["asignado"]),
                    })

            purchase_issues = [dict(row) for row in cursor.execute("""
                SELECT c.id, c.valor_original, c.valor_pendiente,
                       COALESCE(SUM(CASE WHEN p.estado='ACTIVO' THEN a.monto_asignado ELSE 0 END),0) AS asignado
                FROM compras_tarjeta c
                LEFT JOIN asignaciones_pagos a ON a.compra_id=c.id
                LEFT JOIN pagos_deuda p ON p.id=a.pago_id
                WHERE c.estado='ACTIVO'
                GROUP BY c.id
                HAVING c.valor_pendiente + COALESCE(SUM(CASE WHEN p.estado='ACTIVO' THEN a.monto_asignado ELSE 0 END),0) != c.valor_original
            """)]

            investment_issues = [dict(row) for row in cursor.execute("""
                SELECT i.id, i.nombre,
                       COALESCE(SUM(CASE WHEN m.tipo='APORTE' THEN m.monto
                                         WHEN m.tipo='RETIRO' THEN -m.monto
                                         ELSE COALESCE(m.variacion_valor, 0) END),0) AS valor_actual
                FROM inversiones i
                LEFT JOIN movimientos_inversion m
                  ON m.inversion_id=i.id AND m.estado='ACTIVO'
                WHERE i.activa=1
                GROUP BY i.id
                HAVING valor_actual < 0
            """)]

            return {
                "integridad": integridad,
                "foreign_keys_activas": fk_estado,
                "schema_version": schema_grabada,
                "schema_esperado": str(SCHEMA_VERSION),
                "tarjetas": tarjetas,
                "gastos": gastos,
                "reversiones_orfanas": len(orfanas),
                "tarjetas_inconsistentes": len(card_issues),
                "pagos_inconsistentes": len(allocation_issues),
                "compras_inconsistentes": len(purchase_issues),
                "inversiones_inconsistentes": len(investment_issues),
                "detalles_tarjetas": card_issues,
                "detalles_pagos": allocation_issues,
                "detalles_compras": purchase_issues,
                "detalles_inversiones": investment_issues,
                "ok": (
                    integridad == "ok" and fk_estado
                    and schema_grabada == str(SCHEMA_VERSION)
                    and not orfanas and not card_issues
                    and not allocation_issues and not purchase_issues
                    and not investment_issues
                ),
            }
    except Exception as e:
        return {"error": str(e), "ok": False}

def mostrar_advertencias_bd() -> None:
    """Imprime alertas si hay inconsistencias en la BD."""
    estado = verificar_bd_integridad()
    if estado.get("error"):
        print(f"✗ Error verificando BD: {estado['error']}")
        return
    if not estado["ok"]:
        print("⚠️  ADVERTENCIAS EN LA BD:")
        if estado["integridad"] != "ok":
            print(f"  • Integridad: {estado['integridad']}")
        if estado["schema_version"] != estado["schema_esperado"]:
            print(f"  • Schema desincronizado: {estado['schema_version']} vs {estado['schema_esperado']}")
        if estado["reversiones_orfanas"] > 0:
            print(f"  • {estado['reversiones_orfanas']} reversiones orfanas detectadas")
    else:
        print("✓ BD verificada: todo está OK")


def auto_migrar_si_necesario() -> bool:
    """Migra la BD y deja que los errores críticos lleguen al arranque."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='config'")
        if cursor.fetchone() is None:
            conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("SELECT value FROM config WHERE key='schema_version'")
        actual = cursor.fetchone()
        if actual is None or actual[0] != str(SCHEMA_VERSION):
            print(f"[database.py]   Migrando BD a versión {SCHEMA_VERSION}...")
            backup = backup_db()
            if backup:
                print(f"[database.py]   Backup: {backup}")
            upgrade_db(conn)
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            print("[database.py]   Migración completada")
            return True
    return False


if __name__ == "__main__":
    print("Verificando integridad de BD...")
    auto_migrar_si_necesario()
    verificar_estado = verificar_bd_integridad()
    print(f"\nResultado: {'✓ OK' if verificar_estado['ok'] else '⚠️ Problemas'}")
    print("\nDetalles:")
    for k, v in verificar_estado.items():
        print(f"  {k}: {v}")
    
    if verificar_estado.get("reversiones_orfanas", 0) > 0:
        print("\nLimpiando reversiones orfanas...")
        with get_conn() as conn:
            corregir_reversiones_orfanas(conn)
        print("✓ Limpeza completada")
