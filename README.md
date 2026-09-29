# Finanzas de Samuel & Sara

Aplicación local de escritorio para registrar ingresos, gastos, tarjetas,
pagos de tarjeta y liquidaciones entre pareja. Los datos viven únicamente en
`data/finances.db` (SQLite).

## Ejecutar

En Windows, haz doble clic en `Iniciar Finanzas.cmd`. El lanzador usa el
comando `py` si está disponible y tiene una alternativa para la instalación
local de Python de este equipo.

También puedes ejecutarla desde una terminal con Python configurado, dentro de
esta carpeta. Primero instala las dependencias:

```bash
python -m pip install -r requirements.txt
python main.py
```

Requiere Python 3.12+ y la dependencia `customtkinter`. SQLite y Tkinter forman parte de las instalaciones habituales de Python.

## Arquitectura

```text
constants.py        Valores de dominio y validaciones
database.py         Persistencia, migraciones, reversas e invariantes de escritura
calculations.py     Liquidez, flujo de caja, deuda y balance; solo lectura
financial_engine.py Estado financiero, reglas, diagnóstico, proyecciones y auditoría; solo lectura
financial_advisor.py Adaptación del motor a planes, alertas y recomendaciones para la interfaz
frontend/service.py Adaptador de UI: presenta datos y llama al backend
app.py              Vistas, diálogos, tema y actualización visual
test_domain.py      Pruebas de regresión del dominio
main.py             Punto de entrada
```

La interfaz nunca calcula deuda, liquidez o balances por su cuenta: los obtiene
de `calculations.py`. Cada creación y reversa se delega a `database.py`; los
errores de dominio se muestran como mensajes legibles.

## Reglas importantes

- La liquidez personal se calcula por responsabilidad económica: una compra
  compartida reduce a cada persona solo por su parte, incluso si la tarjeta o
  cuenta que hizo el pago es de la otra persona. La salida real de caja se
  conserva por separado para no duplicar el efecto al pagar la tarjeta.
- Los pagos de tarjeta usan el algoritmo FIFO existente de `database.py`.
- Pagador, responsabilidad económica y titular legal se muestran por separado.
- Los gastos se pueden editar desde **Movimientos** sin crear duplicados. Una
  compra de tarjeta que ya recibió pagos conserva su importe y distribución
  económica: se pueden corregir metadatos como nombre, categoría, fecha o
  pagador, pero no reasignar retrospectivamente la responsabilidad. Para
  corregir importe o distribución después de un pago, primero deben reversarse
  los pagos relacionados.
- Las reversas son *soft-reversals*: el movimiento conserva su historial.
- La deuda histórica se muestra como tal y se atribuye provisionalmente al
  titular, sin inventar compras.

## Cajitas, metas y asesor

- En **Cajitas y ahorro** puedes crear fondos como Universidad, Ginebra o
  Gatas. Cada cajita tiene titular independiente (incluido compartido), tipo,
  prioridad, fecha objetivo, icono y color. Sus saldos se derivan de aportes y
  retiros activos; cada aporte conserva quién lo realizó.
- Las **metas** son objetivos independientes enlazables a una cajita. El avance
  usa exclusivamente el saldo reservado de la cajita enlazada, por lo que no
  se inventa dinero ni se contabiliza un aporte como gasto.
- Un retiro puede enlazarse a un gasto existente. El enlace es trazabilidad:
  no crea un gasto ni vuelve a contar el mismo dinero.
- El **Asesor financiero** funciona sin conexión. Sus reglas analizan flujo,
  liquidez, tarjetas, cajitas y metas y devuelven recomendaciones estructuradas
  con nivel de confianza, impacto y datos explicativos. Las simulaciones son
  puras: no escriben en SQLite.
- El motor centraliza sus referencias en `financial_engine.RULES`: utilización
  de tarjeta (50/70/90%), referencia de emergencia (3 meses), margen de
  seguridad (10% del ingreso registrado) y requisitos de historial. Son
  parámetros de producto visibles y modificables, no verdades universales.
- El diagnóstico devuelve evidencia, causa, riesgo, acción, alternativas,
  consecuencia de no actuar y resultado esperado. La interfaz muestra la
  prioridad principal y hasta tres recomendaciones para no sobrecargar.
- La proyección de deuda supone tasa y pago mensual constantes y excluye
  compras futuras, cargos, seguros y cambios de tasa. Si el pago no amortiza,
  no inventa una fecha de finalización.
- La auditoría interna revisa el balance de pareja, saldos/pedientes de tarjeta
  y coherencia de metas antes de elevar la confianza de una recomendación.
- En **Presupuestos** crea primero categorías y después define un límite por
  mes. El panel avisa cuando una categoría lo excede.

## Limitaciones conocidas del backend

- Una vista de mes combina actividad de ese mes con deuda vigente. No debe
  interpretarse como un cierre histórico exacto sin snapshots.
- El asesor ofrece señales determinísticas, no asesoría financiera profesional;
  si faltan ingresos o historial reduce su confianza en lugar de estimar datos.
