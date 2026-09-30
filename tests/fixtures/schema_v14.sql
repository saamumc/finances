CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE movimientos_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL,
            entidad TEXT NOT NULL, entidad_id INTEGER, accion TEXT NOT NULL, detalle TEXT);

CREATE TABLE tarjetas (
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
            creado_en TEXT, actualizado_en TEXT);

CREATE TABLE ingresos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL,
            persona TEXT NOT NULL CHECK (persona IN ('persona1','persona2')),
            concepto TEXT NOT NULL, valor INTEGER NOT NULL CHECK (valor >= 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT,
            transaction_uuid TEXT NOT NULL UNIQUE);

CREATE TABLE gastos (
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
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id));

CREATE TABLE compras_tarjeta (
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
            FOREIGN KEY (gasto_id) REFERENCES gastos(id));

CREATE TABLE pagos_deuda (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, fecha TEXT NOT NULL,
            tarjeta_id INTEGER NOT NULL, monto INTEGER NOT NULL CHECK (monto > 0),
            pagador TEXT NOT NULL CHECK (pagador IN ('persona1','persona2')),
            monto_aportado_p1 INTEGER NOT NULL DEFAULT 0 CHECK (monto_aportado_p1 >= 0),
            monto_aportado_p2 INTEGER NOT NULL DEFAULT 0 CHECK (monto_aportado_p2 >= 0),
            monto_historico_aplicado INTEGER NOT NULL DEFAULT 0 CHECK (monto_historico_aplicado >= 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, concepto TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id));

CREATE TABLE asignaciones_pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pago_id INTEGER NOT NULL, compra_id INTEGER NOT NULL,
            monto_asignado INTEGER NOT NULL CHECK (monto_asignado > 0), fecha TEXT NOT NULL,
            FOREIGN KEY (pago_id) REFERENCES pagos_deuda(id) ON DELETE CASCADE,
            FOREIGN KEY (compra_id) REFERENCES compras_tarjeta(id));

CREATE TABLE ajustes_tarjeta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL, saldo_anterior INTEGER NOT NULL,
            saldo_nuevo INTEGER NOT NULL, variacion INTEGER NOT NULL,
            valor_pendiente INTEGER NOT NULL DEFAULT 0 CHECK (valor_pendiente >= 0),
            monto_p1 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p1 >= 0),
            monto_p2 INTEGER NOT NULL DEFAULT 0 CHECK (monto_p2 >= 0),
            motivo TEXT NOT NULL, realizado_por TEXT NOT NULL CHECK (realizado_por IN ('persona1','persona2')),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id));

CREATE TABLE asignaciones_pagos_ajustes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pago_id INTEGER NOT NULL, ajuste_id INTEGER NOT NULL,
            monto_asignado INTEGER NOT NULL CHECK (monto_asignado > 0), fecha TEXT NOT NULL,
            FOREIGN KEY (pago_id) REFERENCES pagos_deuda(id) ON DELETE CASCADE,
            FOREIGN KEY (ajuste_id) REFERENCES ajustes_tarjeta(id));

CREATE TABLE reversiones_tarjeta (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tarjeta_id INTEGER NOT NULL,
            tipo_original TEXT NOT NULL CHECK (tipo_original IN ('COMPRA','INTERES','CARGO','PAGO')),
            original_id INTEGER NOT NULL, monto_inverso INTEGER NOT NULL CHECK (monto_inverso != 0),
            fecha TEXT NOT NULL, motivo TEXT NOT NULL, realizado_por TEXT NOT NULL DEFAULT 'sistema',
            creado_en TEXT NOT NULL, transaction_uuid TEXT NOT NULL UNIQUE,
            UNIQUE (tipo_original, original_id),
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id));

CREATE TABLE reversiones_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tipo_original TEXT NOT NULL,
            original_id INTEGER NOT NULL, monto_inverso INTEGER NOT NULL,
            fecha TEXT NOT NULL, motivo TEXT NOT NULL, creado_en TEXT NOT NULL,
            UNIQUE (tipo_original, original_id));

CREATE TABLE liquidaciones_pareja (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL, fecha TEXT NOT NULL,
            deudor TEXT NOT NULL CHECK (deudor IN ('persona1','persona2')),
            acreedor TEXT NOT NULL CHECK (acreedor IN ('persona1','persona2')),
            monto INTEGER NOT NULL CHECK (monto > 0), concepto TEXT,
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            CHECK (deudor != acreedor));

CREATE TABLE ahorros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            descripcion TEXT, propietario TEXT NOT NULL DEFAULT 'persona1'
                CHECK (propietario IN ('persona1','persona2')),
            meta INTEGER NOT NULL DEFAULT 0 CHECK (meta >= 0),
            activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            creado_en TEXT NOT NULL, titular TEXT NOT NULL DEFAULT 'persona1', tipo TEXT NOT NULL DEFAULT 'Ahorro', prioridad TEXT NOT NULL DEFAULT 'obligatorio', icono TEXT NOT NULL DEFAULT '💰', color TEXT NOT NULL DEFAULT '#5B8DEF', fecha_objetivo TEXT);

CREATE TABLE metas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            descripcion TEXT, monto_objetivo INTEGER NOT NULL CHECK (monto_objetivo >= 0),
            fecha_objetivo TEXT, prioridad TEXT NOT NULL DEFAULT 'obligatorio'
                CHECK (prioridad IN ('obligatorio','discrecional')),
            icono TEXT NOT NULL DEFAULT '🎯', color TEXT NOT NULL DEFAULT '#5B8DEF',
            ahorro_id INTEGER, activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            creado_en TEXT NOT NULL, FOREIGN KEY (ahorro_id) REFERENCES ahorros(id));

CREATE TABLE categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
            creado_en TEXT NOT NULL);

CREATE TABLE presupuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mes TEXT NOT NULL,
            categoria_id INTEGER NOT NULL, monto INTEGER NOT NULL CHECK (monto > 0),
            UNIQUE (mes, categoria_id),
            FOREIGN KEY (categoria_id) REFERENCES categorias(id));

CREATE TABLE movimientos_ahorro (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ahorro_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('DEPOSITO','RETIRO')),
            monto INTEGER NOT NULL CHECK (monto > 0), concepto TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE, aportante TEXT NOT NULL DEFAULT 'persona1', gasto_id INTEGER, motivo TEXT,
            FOREIGN KEY (ahorro_id) REFERENCES ahorros(id));

CREATE TABLE terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
            tipo TEXT NOT NULL DEFAULT 'PERSONA'
                CHECK (tipo IN ('PERSONA','BANCO')),
            contacto TEXT, creado_en TEXT NOT NULL);

CREATE TABLE prestamos_terceros (
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
            FOREIGN KEY (tercero_id) REFERENCES terceros(id));

CREATE TABLE abonos_terceros (
            id INTEGER PRIMARY KEY AUTOINCREMENT, prestamo_id INTEGER NOT NULL,
            mes TEXT NOT NULL, fecha TEXT NOT NULL, monto INTEGER NOT NULL CHECK (monto > 0),
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REVERSADO')),
            motivo_reversion TEXT, fecha_reversion TEXT, transaction_uuid TEXT NOT NULL UNIQUE,
            FOREIGN KEY (prestamo_id) REFERENCES prestamos_terceros(id));

CREATE TABLE gastos_fijos (
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
            FOREIGN KEY (tarjeta_id) REFERENCES tarjetas(id));

CREATE UNIQUE INDEX uq_ingresos_transaction_uuid ON ingresos(transaction_uuid);

CREATE UNIQUE INDEX uq_gastos_transaction_uuid ON gastos(transaction_uuid);

CREATE UNIQUE INDEX uq_pagos_deuda_transaction_uuid ON pagos_deuda(transaction_uuid);

CREATE UNIQUE INDEX uq_liquidaciones_pareja_transaction_uuid ON liquidaciones_pareja(transaction_uuid);

CREATE UNIQUE INDEX uq_compras_tarjeta_transaction_uuid ON compras_tarjeta(transaction_uuid);

CREATE INDEX idx_metas_ahorro ON metas(ahorro_id);

CREATE INDEX idx_ingresos_mes ON ingresos(mes);

CREATE INDEX idx_gastos_mes ON gastos(mes);

CREATE INDEX idx_gastos_tarjeta ON gastos(tarjeta_id);

CREATE INDEX idx_compras_tarjeta_fecha ON compras_tarjeta(tarjeta_id, fecha, id);

CREATE INDEX idx_pagos_mes ON pagos_deuda(mes);

CREATE INDEX idx_pagos_tarjeta ON pagos_deuda(tarjeta_id, fecha, id);

CREATE INDEX idx_asignaciones_pago ON asignaciones_pagos(pago_id);

CREATE INDEX idx_asignaciones_compra ON asignaciones_pagos(compra_id);

CREATE INDEX idx_ajustes_tarjeta_fecha ON ajustes_tarjeta(tarjeta_id, fecha, id);

CREATE INDEX idx_asignaciones_pago_ajuste ON asignaciones_pagos_ajustes(pago_id);

CREATE INDEX idx_reversiones_tarjeta_original ON reversiones_tarjeta(tipo_original, original_id);

CREATE INDEX idx_reversiones_tarjeta_tarjeta ON reversiones_tarjeta(tarjeta_id, fecha, id);

CREATE INDEX idx_liquidaciones_mes ON liquidaciones_pareja(mes);

CREATE INDEX idx_movimientos_ahorro_fondo ON movimientos_ahorro(ahorro_id, fecha, id);

CREATE INDEX idx_movimientos_ahorro_mes ON movimientos_ahorro(mes);

CREATE INDEX idx_presupuestos_mes ON presupuestos(mes);

CREATE INDEX idx_prestamos_terceros_estado ON prestamos_terceros(tercero_id, estado);

CREATE INDEX idx_abonos_terceros_prestamo ON abonos_terceros(prestamo_id, fecha, id);

CREATE INDEX idx_gastos_fijos_activo ON gastos_fijos(activo);
