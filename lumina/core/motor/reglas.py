"""Registro único de parámetros de decisión de Lúmina.

Los valores son parámetros de producto, no verdades universales. La función
reglas permite aplicar overrides explícitos sin modificar el código que
consume las reglas.
"""

from __future__ import annotations

from typing import Any, Mapping

DEFAULTS: dict[str, Any] = {
    "card_use_attention": 0.50, "card_use_high": 0.70, "card_use_critical": 0.90,
    "high_monthly_interest": 2.0, "emergency_target_months": 3,
    "minimum_emergency_months": 1.0, "safe_margin_income_pct": 0.10,
    "history_months_high_confidence": 3, "anomaly_multiplier": 1.75,
    "calendar_days_soon": 7, "recurring_amount_tolerance": 0.05,
    "recurring_min_occurrences": 3, "recurring_history_months": 12,
    "forecast_days": 60, "savings_fatigue_ratio": 0.40,
    "savings_fatigue_months": 3, "savings_fatigue_min_free_months": 1.0,
    "recurrencia_min_ocurrencias": 3, "recurrencia_historial_meses": 12,
    "variacion_estable": 0.06, "variacion_moderada": 0.20, "variacion_alta": 0.45,
    "hueco_maximo_meses": 2, "confianza_alta": 0.75, "confianza_media": 0.50,
    "meses_inactivo_para_dudar": 2, "utilizacion_atencion": 0.50,
    "utilizacion_alta": 0.70, "utilizacion_critica": 0.90,
    "carga_fija_alta": 0.50, "carga_fija_atencion": 0.35,
    "deuda_ingreso_alta": 0.35, "tasa_ahorro_saludable": 0.10,
    "meses_emergencia_objetivo": 3, "colchon_minimo_meses": 1.0,
    "margen_seguridad_pct": 0.10, "borde_asequible_pct": 0.85,
    "multiplicador_anomalia": 1.75, "gasto_grande_vs_mediana": 3.0,
    "ventana_duplicado_dias": 3, "salto_gasto_categoria": 0.25,
    "caida_ingreso": 0.15, "fixed_expenses_high_pct": 0.50,
}

def reglas(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    valores = dict(DEFAULTS)
    if overrides:
        desconocidas = set(overrides) - set(DEFAULTS)
        if desconocidas:
            raise KeyError(f"Reglas desconocidas: {sorted(desconocidas)}")
        valores.update(overrides)
    return valores
