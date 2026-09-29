# Informe de corrección: liquidación y actualización financiera

Fecha: 2026-09-02

## Alcance

Se revisó el flujo de ingresos, gastos, tarjeta, ahorro, dashboard, liquidación y asesor. La fuente de verdad es SQLite; los valores derivados se reconstruyen mediante `calculations.py` y `financial_engine.py`.

## Hallazgo principal

La aplicación no mantenía una caché de cifras financieras. El fallo que impedía abrirla era una invariante del libro de pareja: `I-LEDGER incumplida: la suma de balances es 1138316`.

La diferencia coincidía exactamente con un abono histórico de la tarjeta `rappi samuel`. El cálculo atribuía como consumo histórico únicamente el saldo pendiente, pero también registraba el aporte del abono ya realizado. Faltaba la contrapartida contable de ese abono.

## Corrección

- Se añadió `_consumo_historico_atribuido()` en `calculations.py`.
- El libro ahora usa el saldo inicial histórico como consumo derivado y conserva el saldo pendiente como deuda derivada. De esta manera: consumo original = saldo pendiente + abonos históricos.
- No se modificó ni borró ningún movimiento del usuario.
- `FinanceService.estado_actual()` reconstruye `financial_engine.financial_state()` desde SQLite en cada lectura.
- El dashboard y el asesor usan esa foto de estado; el fondo de emergencia quedó centralizado en el motor.
- El adaptador del dashboard conserva el contrato de interfaz `cajitas.fondos`, aunque el motor use el nombre interno `funds`.

## Semántica de liquidación

La liquidación mide el equilibrio entre Samuel y Sara por gastos compartidos, pagos de tarjeta, responsabilidades y liquidaciones explícitas. Un ingreso personal modifica liquidez, flujo, dashboard y asesor, pero no crea por sí solo una deuda entre personas. La pantalla muestra también la liquidez mensual de cada persona para hacer visible ese cambio.

## Verificación

Con la base existente, después de la corrección:

- `balance_historico_pareja()['cuadra']` devolvió `True`.
- El balance resultante fue Samuel: -1.311.516; Sara: 1.311.516.
- El estado de septiembre leyó ingresos: 8.080.642 y liquidez mensual: 6.064.926.
- El asesor se ejecutó correctamente y generó score y recomendaciones.
- La suite de regresión ejecutó 19 pruebas satisfactorias, incluida una prueba de abono de deuda histórica y una prueba integral de ingreso, gasto, tarjeta, ahorro y reinicio simulado.
- Se verificó además `FinanceService.dashboard('2026-09')` contra la base existente: cargó una cajita y su total sin lanzar excepciones.

## Limitación conocida

La atribución de una deuda histórica sin detalle sigue siendo provisional al titular de la tarjeta. Si desean repartir una deuda histórica entre ambos, debe registrarse un desglose explícito mediante movimientos o un ajuste auditado.
